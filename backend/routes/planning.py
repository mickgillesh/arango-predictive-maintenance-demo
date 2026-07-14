"""
Agentic maintenance planning endpoints.

POST /api/plan/run          — SSE stream; calls GPT-4o with full fleet graph context,
                              creates work orders + edges deterministically.
POST /api/plan/reset        — delete all planner-generated work orders and their edges.
GET  /api/plan/work-orders  — list all planner work orders with joined data.
POST /api/plan/chat         — SSE; conversational agent with generic graph-editing tools.
POST /api/plan/apply-edits  — apply user-confirmed proposed edits to the graph.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import partial

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_arangodb import ArangoGraph
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.chat_agent_executor import create_react_agent
from pydantic import BaseModel

from backend.aql import (
    Q_CASCADE_DELETE_EDGES_FROM_ID,
    Q_CASCADE_DELETE_EDGES_FROM_IDS,
    Q_CASCADE_DELETE_EDGES_TO_ID,
    Q_CASCADE_DELETE_PERFORMEDBY_TO_PLANNER,
    Q_CASCADE_DELETE_RELATIONSHIP,
    Q_CASCADE_ENGINES_FOR_AIRCRAFT,
    Q_CASCADE_WO_KEYS_FOR_ENGINES,
    Q_CHAT_WO_BY_ENGINE,
    Q_ONTOLOGY_FULL,
    Q_PLAN_COLLECT_IDS,
    Q_PLAN_DELETE_CONSUMED,
    Q_PLAN_DELETE_MAINTAINS,
    Q_PLAN_DELETE_PERFORMED,
    Q_PLAN_DELETE_WOS,
    Q_PLAN_FLEET_CONTEXT,
    Q_PLAN_WORK_ORDERS,
)
from backend.db import get_db

_log = logging.getLogger(__name__)

router = APIRouter()
_run_lock = asyncio.Lock()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# ---------------------------------------------------------------------------
# Chat agent — module-level singletons
# ---------------------------------------------------------------------------

_MUTATING_RE = re.compile(r"\b(INSERT|UPDATE|REPLACE|REMOVE|UPSERT)\b", re.IGNORECASE)
_checkpointer = InMemorySaver()
_chat_agent = None
_chat_agent_lock = asyncio.Lock()

# Map from entity_type (as used by the LLM) to collection name — accept both forms
_COLLECTION_MAP: dict[str, str] = {
    "aircraft":    "aircraft",
    "engine":      "engines",
    "engines":     "engines",
    "technician":  "technicians",
    "technicians": "technicians",
    "part":        "parts",
    "parts":       "parts",
    "workOrder":   "workOrders",
    "workOrders":  "workOrders",
    "subsystem":   "subsystems",
    "subsystems":  "subsystems",
}

# Fields the agent is allowed to set per entity type — enforced server-side
_EDITABLE_FIELDS: dict[str, set[str]] = {
    "aircraft":    {"tailNumber", "base", "flightsPerDay"},
    "engines":     {"model", "riskBucket", "predictedRUL"},
    "technicians": {"name", "homeBase"},
    "parts":       {"name", "stockLevel", "leadTimeDays"},
    "workOrders":  {"status", "deadline", "description"},
    "subsystems":  {"name"},
}

# Edge types the agent may create or delete
_MUTABLE_EDGE_TYPES: set[str] = {
    "installedOn", "performedBy", "certifiedFor", "maintains", "consumed"
}


# ---------------------------------------------------------------------------
# LLM structured output models
# ---------------------------------------------------------------------------

class PlannedEngineItem(BaseModel):
    engine_id: str
    technician_id: str
    has_blocking_parts: bool
    blocking_part_ids: list[str]


class MaintenancePlan(BaseModel):
    work_orders: list[PlannedEngineItem]
    reasoning_summary: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _deadline(engine: dict, has_blocking: bool, max_lead: int) -> tuple[str, str]:
    """Return (procurement_deadline, maintenance_deadline) as ISO date strings."""
    today = date.today()
    if has_blocking:
        proc_dl = (today + timedelta(days=max_lead)).isoformat()
        maint_dl = (today + timedelta(days=max_lead + 3)).isoformat()
        return proc_dl, maint_dl
    fpd = max(engine["aircraft"].get("flightsPerDay", 1), 1)
    days = (engine["predictedRUL"] + fpd - 1) // fpd  # ceiling division
    return "", (today + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# POST /api/plan/run
# ---------------------------------------------------------------------------

@router.post("/run")
async def plan_run() -> StreamingResponse:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return StreamingResponse(
            iter([_sse("error", {"error": "openai_not_configured"})]),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    if _run_lock.locked():
        return StreamingResponse(
            iter([_sse("error", {"error": "plan_already_running"})]),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    async def _generate():
        async with _run_lock:
            try:
                yield _sse("start", {"message": "Fetching fleet context from graph…", "step": 1, "total": 5})

                db = get_db()
                engines = await asyncio.to_thread(
                    lambda: list(db.aql.execute(Q_PLAN_FLEET_CONTEXT))
                )
                if not engines:
                    yield _sse("error", {"error": "no_engines_at_risk"})
                    return

                yield _sse("progress", {
                    "message": f"AI analyzing {len(engines)} engines requiring attention…",
                    "step": 2, "total": 5,
                })

                # Build prompt
                prompt = (
                    "You are an aircraft maintenance scheduler. "
                    "Given the fleet context below, assign each engine to exactly one technician "
                    "from its available list and identify which parts are blocking (stockLevel == 0). "
                    "Prioritize critical engines before warning. Within each tier prioritize lower predictedRUL. "
                    "Return ONLY structured JSON — no explanations.\n\n"
                    f"Fleet context (JSON):\n{json.dumps(engines, indent=2)}"
                )

                llm = ChatOpenAI(
                    model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                    temperature=0,
                    api_key=os.environ["OPENAI_API_KEY"],
                )
                structured = llm.with_structured_output(MaintenancePlan)
                plan: MaintenancePlan = await structured.ainvoke(prompt)  # type: ignore[assignment]

                yield _sse("progress", {"message": "Validating plan and writing work orders…", "step": 3, "total": 5})

                # Build lookup sets for validation
                eng_map = {e["id"]: e for e in engines}
                valid_tech_ids = {t["id"] for e in engines for t in e["technicians"]}
                valid_part_ids = {p["id"] for e in engines for p in e["parts"]}

                total_wo = maint_count = proc_count = 0
                planned_engines: set[str] = set()
                now_iso = datetime.now(timezone.utc).isoformat()

                for item in plan.work_orders:
                    engine = eng_map.get(item.engine_id)
                    if not engine:
                        continue

                    # Validate / fall back technician
                    if item.technician_id in valid_tech_ids:
                        tech_id = item.technician_id
                    elif engine["technicians"]:
                        tech_id = engine["technicians"][0]["id"]
                    else:
                        yield _sse("progress", {
                            "message": f"Engine {item.engine_id}: no technician at base, skipping.",
                            "step": 3, "total": 5,
                        })
                        continue

                    tech_name = next(
                        (t["name"] for t in engine["technicians"] if t["id"] == tech_id),
                        tech_id,
                    )

                    # Validate blocking parts
                    blocking_ids = [p for p in item.blocking_part_ids if p in valid_part_ids]
                    has_blocking = item.has_blocking_parts and bool(blocking_ids)
                    max_lead = max(
                        (p["leadTimeDays"] for p in engine["parts"] if p["id"] in blocking_ids),
                        default=0,
                    )
                    proc_dl, maint_dl = _deadline(engine, has_blocking, max_lead)

                    yield _sse("progress", {
                        "message": (
                            f"Engine #{item.engine_id} ({engine['riskBucket']}, "
                            f"RUL={engine['predictedRUL']}, {engine['aircraft']['tailNumber']}) "
                            f"→ {tech_name}"
                        ),
                        "step": 3, "total": 5,
                    })

                    def _write_wo(wo_doc: dict, engine_id: str, tech_id: str,
                                  part_ids: list[str]) -> None:
                        wo_key = wo_doc["_key"]
                        db.collection("workOrders").insert(wo_doc)
                        db.collection("maintains").insert(
                            {"_from": f"workOrders/{wo_key}", "_to": f"engines/{engine_id}"}
                        )
                        db.collection("performedBy").insert(
                            {"_from": f"workOrders/{wo_key}", "_to": f"technicians/{tech_id}"}
                        )
                        for pid in part_ids:
                            db.collection("consumed").insert(
                                {"_from": f"workOrders/{wo_key}", "_to": f"parts/{pid}"}
                            )

                    # Procurement work order (if blocking parts)
                    if has_blocking:
                        wo_key = f"PLN-{uuid.uuid4().hex[:8]}"
                        wo_doc = {
                            "_key": wo_key,
                            "generatedByPlanner": True,
                            "type": "procurement",
                            "engineId": item.engine_id,
                            "technicianId": tech_id,
                            "deadline": proc_dl,
                            "riskBucket": engine["riskBucket"],
                            "status": "open",
                            "createdAt": now_iso,
                            "description": (
                                f"Procure blocking parts for engine #{item.engine_id} "
                                f"({', '.join(engine.get('driverSubsystems', []))})"
                            ),
                        }
                        await asyncio.to_thread(
                            partial(_write_wo, wo_doc, item.engine_id, tech_id, blocking_ids)
                        )
                        yield _sse("work_order", {
                            "woKey": wo_key, "type": "procurement",
                            "engineId": item.engine_id, "technicianName": tech_name,
                            "deadline": proc_dl, "status": "open",
                            "description": wo_doc["description"],
                        })
                        total_wo += 1
                        proc_count += 1

                    # Maintenance work order
                    wo_key = f"PLN-{uuid.uuid4().hex[:8]}"
                    maint_status = "pending-parts" if has_blocking else "open"
                    wo_doc = {
                        "_key": wo_key,
                        "generatedByPlanner": True,
                        "type": "maintenance",
                        "engineId": item.engine_id,
                        "technicianId": tech_id,
                        "deadline": maint_dl,
                        "riskBucket": engine["riskBucket"],
                        "status": maint_status,
                        "createdAt": now_iso,
                        "description": (
                            f"Scheduled maintenance: {', '.join(engine.get('driverSubsystems', []))} "
                            f"(engine #{item.engine_id})"
                        ),
                    }
                    await asyncio.to_thread(
                        partial(_write_wo, wo_doc, item.engine_id, tech_id, [])
                    )
                    yield _sse("work_order", {
                        "woKey": wo_key, "type": "maintenance",
                        "engineId": item.engine_id, "technicianName": tech_name,
                        "deadline": maint_dl, "status": maint_status,
                        "description": wo_doc["description"],
                    })
                    total_wo += 1
                    maint_count += 1
                    planned_engines.add(item.engine_id)

                yield _sse("summary", {
                    "totalWorkOrders": total_wo,
                    "maintenanceOrders": maint_count,
                    "procurementOrders": proc_count,
                    "enginesPlanned": len(planned_engines),
                    "reasoningSummary": plan.reasoning_summary,
                })
                yield _sse("done", {})

            except Exception as exc:
                _log.exception("plan_run error: %s", exc)
                yield _sse("error", {"error": "internal_error", "detail": str(exc)})

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ---------------------------------------------------------------------------
# POST /api/plan/reset
# ---------------------------------------------------------------------------

@router.post("/reset")
async def plan_reset() -> JSONResponse:
    db = get_db()

    def _do_reset() -> dict[str, int]:
        ids = list(db.aql.execute(Q_PLAN_COLLECT_IDS))
        counts: dict[str, int] = {"workOrders": 0, "maintains": 0, "performedBy": 0, "consumed": 0}
        if ids:
            bind = {"ids": ids}
            for q, key in [
                (Q_PLAN_DELETE_MAINTAINS, "maintains"),
                (Q_PLAN_DELETE_PERFORMED, "performedBy"),
                (Q_PLAN_DELETE_CONSUMED, "consumed"),
            ]:
                db.aql.execute(q, bind_vars=bind)
            db.aql.execute(Q_PLAN_DELETE_WOS)
            counts["workOrders"] = len(ids)
        return counts

    counts = await asyncio.to_thread(_do_reset)
    return JSONResponse({"deleted": counts})


# ---------------------------------------------------------------------------
# GET /api/plan/work-orders
# ---------------------------------------------------------------------------

@router.get("/work-orders")
async def plan_work_orders() -> JSONResponse:
    db = get_db()
    wos = await asyncio.to_thread(lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS)))
    return JSONResponse({"workOrders": wos})


# ---------------------------------------------------------------------------
# Chat agent tools
# ---------------------------------------------------------------------------

@tool
async def query_ontology() -> str:
    """Return the full ontology: which entity types and relationships the agent may create, update, or delete, and what cascades on delete."""
    db = get_db()
    result = await asyncio.to_thread(lambda: list(db.aql.execute(Q_ONTOLOGY_FULL)))
    return json.dumps(result[0] if result else {"nodes": [], "edges": []})


@tool
async def read_graph(aql_query: str) -> str:
    """Execute a read-only AQL query. Use to look up entity keys, check counts, preview cascade impact, or inspect relationships before proposing changes."""
    if _MUTATING_RE.search(aql_query):
        return json.dumps({"error": "Mutating queries are not allowed via this tool."})
    db = get_db()
    try:
        rows = await asyncio.to_thread(lambda: list(db.aql.execute(aql_query)))
        return json.dumps(rows[:50])
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
async def get_work_orders(engine_id: str = "") -> str:
    """Return current planner-generated work orders, optionally filtered by engine_id."""
    db = get_db()
    if engine_id:
        rows = await asyncio.to_thread(
            lambda: list(db.aql.execute(Q_CHAT_WO_BY_ENGINE, bind_vars={"eid": engine_id}))
        )
    else:
        rows = await asyncio.to_thread(lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS)))
    return json.dumps(rows[:100])


@tool
def propose_create_entity(entity_type: str, fields: dict) -> str:
    """Propose creating a new entity in the fleet graph (aircraft, engine, technician, part, workOrder).
    Call query_ontology() first to see required fields. Does NOT write to the DB — returns a proposal."""
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": f"Create new {entity_type}: {fields}",
        "operation": {"type": "create_entity", "entity_type": entity_type, "fields": fields},
    })


@tool
def propose_update_entity(entity_type: str, entity_key: str, fields: dict) -> str:
    """Propose updating an existing entity. Only editable fields (per ontology) are applied.
    entity_type: aircraft | engine | technician | part | workOrder | subsystem.
    Does NOT write to the DB — returns a proposal."""
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": f"Update {entity_type} {entity_key}: {fields}",
        "operation": {
            "type": "update_entity",
            "entity_type": entity_type,
            "entity_key": entity_key,
            "fields": fields,
        },
    })


@tool
def propose_delete_entity(entity_type: str, entity_key: str, cascade_preview: str = "") -> str:
    """Propose deleting an entity and all connected data (cascade).
    IMPORTANT: call read_graph() first to find what will cascade, then pass a
    human-readable cascade_preview. Example: 'Will also delete: 1 engine (#42), 3 work orders, 12 edges'.
    Does NOT write to the DB — returns a proposal."""
    desc = f"Delete {entity_type} {entity_key}"
    if cascade_preview:
        desc += f". {cascade_preview}"
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": desc,
        "operation": {
            "type": "delete_entity",
            "entity_type": entity_type,
            "entity_key": entity_key,
        },
    })


@tool
def propose_create_relationship(edge_type: str, from_id: str, to_id: str) -> str:
    """Propose creating an edge between two existing entities.
    edge_type: installedOn | performedBy | certifiedFor | maintains | consumed.
    from_id / to_id: full ArangoDB document IDs e.g. 'engines/42'.
    Does NOT write to the DB — returns a proposal."""
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": f"Create {edge_type} edge: {from_id} → {to_id}",
        "operation": {
            "type": "create_relationship",
            "edge_type": edge_type,
            "from_id": from_id,
            "to_id": to_id,
        },
    })


@tool
def propose_delete_relationship(edge_type: str, from_id: str, to_id: str) -> str:
    """Propose deleting an edge between two entities.
    edge_type: installedOn | performedBy | certifiedFor | maintains | consumed.
    Does NOT write to the DB — returns a proposal."""
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": f"Delete {edge_type} edge: {from_id} → {to_id}",
        "operation": {
            "type": "delete_relationship",
            "edge_type": edge_type,
            "from_id": from_id,
            "to_id": to_id,
        },
    })


_PLANNING_TOOLS = [
    query_ontology,
    read_graph,
    get_work_orders,
    propose_create_entity,
    propose_update_entity,
    propose_delete_entity,
    propose_create_relationship,
    propose_delete_relationship,
]

_SYSTEM_PROMPT = """You are AeroFleet Planning Assistant, helping maintenance schedulers manage fleet operations.

You can help with any entity in the fleet graph:
- Work orders: create, reassign (delete+create performedBy), update deadlines/status, delete planner WOs
- Personnel: update technician homeBase or name; add/remove certifications via certifiedFor edges
- Aircraft: update base airport (rerouting), retire from fleet (propose_delete_entity with cascade)
- Engines: add to fleet, update model, decommission (propose_delete_entity with cascade)
- Parts: update stockLevel when parts arrive, update leadTimeDays when supplier changes

Workflow for EVERY change:
1. Call query_ontology() to confirm the operation and editable fields are allowed
2. Call read_graph() to find exact entity keys and preview what will cascade on delete
3. For deletions: always populate cascade_preview in propose_delete_entity so the user sees full impact
4. Stage changes with propose_* tools — you NEVER write directly to the database
5. Tell the user to confirm in the Pending Changes panel

Always respond in English. For destructive operations, describe the cascade impact before proposing.

## Database Schema — use these EXACT collection names in AQL

Vertex collections:
- `aircraft`    — fields: _key, tailNumber, base, flightsPerDay
- `engines`     — fields: _key, engineId, model, riskBucket, predictedRUL, entryIntoService, healthIndex, riskScore
- `technicians` — fields: _key, name, homeBase
- `parts`       — fields: _key, name, stockLevel, leadTimeDays
- `workOrders`  — fields: _key, type, status, deadline, description, engineId, technicianId, generatedByPlanner, createdAt
- `subsystems`  — fields: _key, name
- `sensors`     — fields: _key, sensorId, type

Edge collections (all use `_from` / `_to` full document IDs like `engines/E001`):
- `installedOn`  : engines → aircraft     (engine is installed on aircraft)
- `partOf`       : subsystems → engines   (subsystem is part of engine)
- `monitors`     : sensors → subsystems   (sensor monitors subsystem)
- `requiredBy`   : parts → subsystems     (part required by subsystem)
- `certifiedFor` : technicians → subsystems (technician certified for subsystem)
- `maintains`    : workOrders → engines   (work order maintains engine)
- `performedBy`  : workOrders → technicians (work order performed by technician)
- `consumed`     : workOrders → parts     (work order consumes part)

Traversal direction rules — the arrow shows _from → _to:
- To find engines belonging to an aircraft: traverse INBOUND on `installedOn` from the aircraft
- To find the aircraft an engine is on: traverse OUTBOUND on `installedOn` from the engine
- To find work orders for an engine: traverse INBOUND on `maintains` from the engine  (or filter workOrders by engineId field)
- To find the technician on a work order: traverse OUTBOUND on `performedBy` from the work order
- To find subsystems of an engine: traverse INBOUND on `partOf` from the engine
- To find certifications for a technician: traverse OUTBOUND on `certifiedFor` from the technician

AQL examples (copy these patterns):
  -- engines on aircraft key AC001:
  FOR e IN 1..1 INBOUND 'aircraft/AC001' installedOn RETURN e
  -- work orders for engine E042:
  FOR wo IN workOrders FILTER wo.engineId == 'E042' RETURN wo
  -- technician assigned to a work order:
  FOR t IN 1..1 OUTBOUND 'workOrders/PLN-abc123' performedBy RETURN t
  -- technicians at base LHR:
  FOR t IN technicians FILTER t.homeBase == 'LHR' RETURN t
  -- count engines for an aircraft:
  RETURN LENGTH(FOR e IN 1..1 INBOUND 'aircraft/AC001' installedOn RETURN 1)"""


async def _get_chat_agent():
    global _chat_agent
    if _chat_agent is not None:
        return _chat_agent
    async with _chat_agent_lock:
        if _chat_agent is not None:
            return _chat_agent
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        llm = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            temperature=0,
            api_key=api_key,
        )
        # Fetch the live ArangoDB graph schema (field names, types) and append it
        # to the prompt so the LLM writes correct AQL without guessing field names.
        try:
            arango_graph = await asyncio.to_thread(lambda: ArangoGraph(db=get_db()))
            live_schema = (
                "\n\n## Live field schema (from database introspection)\n"
                + arango_graph.schema
            )
        except Exception as exc:
            _log.warning("Could not fetch ArangoGraph schema: %s", exc)
            live_schema = ""

        _chat_agent = create_react_agent(
            llm,
            tools=_PLANNING_TOOLS,
            prompt=_SYSTEM_PROMPT + live_schema,
            checkpointer=_checkpointer,
        )
        return _chat_agent


# ---------------------------------------------------------------------------
# POST /api/plan/chat
# ---------------------------------------------------------------------------

class PlanChatRequest(BaseModel):
    message: str
    session_id: str


@router.post("/chat")
async def plan_chat(body: PlanChatRequest) -> StreamingResponse:
    async def _generate():
        yield _sse("chat_start", {})
        agent = await _get_chat_agent()
        if agent is None:
            yield _sse("error", {"error": "openai_not_configured"})
            yield _sse("chat_done", {})
            return

        config = {"configurable": {"thread_id": body.session_id}}
        inputs = {"messages": [{"role": "human", "content": body.message}]}

        try:
            async for chunk in agent.astream(inputs, config=config, stream_mode="updates"):
                for _, node_output in chunk.items():
                    for msg in node_output.get("messages", []):
                        tool_calls = getattr(msg, "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                yield _sse("thinking", {"message": f"Calling {tc['name']}…"})
                                yield _sse("tool_call", {
                                    "tool": tc["name"],
                                    "input": json.dumps(tc["args"])[:400],
                                })
                        elif hasattr(msg, "tool_call_id"):
                            raw = msg.content
                            try:
                                parsed = json.loads(raw) if isinstance(raw, str) else raw
                            except Exception:
                                parsed = None
                            if isinstance(parsed, dict) and parsed.get("__propose__"):
                                proposal = {k: v for k, v in parsed.items() if k != "__propose__"}
                                yield _sse("propose", proposal)
                            else:
                                snippet = (raw[:400] if isinstance(raw, str)
                                           else json.dumps(parsed)[:400])
                                yield _sse("tool_result", {
                                    "tool": getattr(msg, "name", ""),
                                    "result": snippet,
                                })
                        elif hasattr(msg, "content") and not tool_calls:
                            content = msg.content
                            if content and isinstance(content, str) and content.strip():
                                yield _sse("answer", {"text": content})
        except Exception as exc:
            _log.exception("plan_chat error: %s", exc)
            yield _sse("error", {"error": "internal_error", "detail": str(exc)})

        yield _sse("chat_done", {})

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ---------------------------------------------------------------------------
# POST /api/plan/apply-edits
# ---------------------------------------------------------------------------

class EditOperation(BaseModel):
    type: str
    entity_type: str | None = None
    entity_key: str | None = None
    fields: dict | None = None
    edge_type: str | None = None
    from_id: str | None = None
    to_id: str | None = None


class PendingEdit(BaseModel):
    id: str
    operation: EditOperation


class ApplyEditsRequest(BaseModel):
    edits: list[PendingEdit]


@router.post("/apply-edits")
async def plan_apply_edits(body: ApplyEditsRequest) -> JSONResponse:
    db = get_db()
    errors: list[dict] = []
    applied = 0
    for edit in body.edits:
        try:
            await asyncio.to_thread(_apply_single_edit, db, edit.operation)
            applied += 1
        except Exception as exc:
            _log.exception("apply-edits error for %s: %s", edit.id, exc)
            errors.append({"id": edit.id, "error": str(exc)})
    return JSONResponse({"applied": applied, "errors": errors})


def _apply_single_edit(db, op: EditOperation) -> None:
    """Synchronous; called via asyncio.to_thread for each confirmed edit."""
    if op.type == "create_entity":
        coll_name = _COLLECTION_MAP.get(op.entity_type or "", "")
        if not coll_name:
            raise ValueError(f"Unknown entity_type: {op.entity_type}")
        doc: dict = dict(op.fields or {})
        if coll_name == "workOrders":
            doc["_key"] = f"PLN-{uuid.uuid4().hex[:8]}"
            doc["generatedByPlanner"] = True
            doc.setdefault("status", "open")
            doc["createdAt"] = datetime.now(timezone.utc).isoformat()
        db.collection(coll_name).insert(doc)

    elif op.type == "update_entity":
        coll_name = _COLLECTION_MAP.get(op.entity_type or "", "")
        if not coll_name:
            raise ValueError(f"Unknown entity_type: {op.entity_type}")
        allowed = _EDITABLE_FIELDS.get(coll_name, set())
        safe = {k: v for k, v in (op.fields or {}).items() if k in allowed}
        if safe and op.entity_key:
            db.collection(coll_name).update(op.entity_key, safe)

    elif op.type == "delete_entity":
        _cascade_delete(db, op.entity_type or "", op.entity_key or "")

    elif op.type == "create_relationship":
        if op.edge_type not in _MUTABLE_EDGE_TYPES:
            raise ValueError(f"Edge type '{op.edge_type}' is not mutable")
        db.collection(op.edge_type).insert({"_from": op.from_id, "_to": op.to_id})

    elif op.type == "delete_relationship":
        if op.edge_type not in _MUTABLE_EDGE_TYPES:
            raise ValueError(f"Edge type '{op.edge_type}' is not mutable")
        db.aql.execute(
            Q_CASCADE_DELETE_RELATIONSHIP,
            bind_vars={"@coll": op.edge_type, "from": op.from_id, "to": op.to_id},
        )

    else:
        raise ValueError(f"Unknown operation type: {op.type}")


def _cascade_delete_wos_for_engines(db, engine_keys: list[str]) -> None:
    """Delete all work orders (and their edges) for a list of engine keys."""
    wo_keys: list[str] = list(db.aql.execute(
        Q_CASCADE_WO_KEYS_FOR_ENGINES, bind_vars={"engine_keys": engine_keys}
    ))
    if not wo_keys:
        return
    wo_ids = [f"workOrders/{k}" for k in wo_keys]
    for coll in ("maintains", "performedBy", "consumed"):
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_FROM_IDS,
            bind_vars={"@coll": coll, "ids": wo_ids},
        )
    for k in wo_keys:
        db.collection("workOrders").delete(k, ignore_missing=True)


def _cascade_delete(db, entity_type: str, entity_key: str) -> None:
    """Execute a cascade delete for the given entity type. All AQL is bind-parameterised."""
    coll_name = _COLLECTION_MAP.get(entity_type, "")
    if not coll_name:
        raise ValueError(f"Unknown entity_type for cascade: {entity_type}")

    if coll_name == "aircraft":
        aircraft_id = f"aircraft/{entity_key}"
        engine_rows: list[dict] = list(db.aql.execute(
            Q_CASCADE_ENGINES_FOR_AIRCRAFT, bind_vars={"aircraft_id": aircraft_id}
        ))
        engine_keys = [r["key"] for r in engine_rows]
        if engine_keys:
            _cascade_delete_wos_for_engines(db, engine_keys)
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_TO_ID,
            bind_vars={"@coll": "installedOn", "id": aircraft_id},
        )
        for ek in engine_keys:
            db.collection("engines").delete(ek, ignore_missing=True)
        db.collection("aircraft").delete(entity_key, ignore_missing=True)

    elif coll_name == "engines":
        engine_id = f"engines/{entity_key}"
        _cascade_delete_wos_for_engines(db, [entity_key])
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_FROM_ID,
            bind_vars={"@coll": "installedOn", "id": engine_id},
        )
        db.collection("engines").delete(entity_key, ignore_missing=True)

    elif coll_name == "technicians":
        tech_id = f"technicians/{entity_key}"
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_FROM_ID,
            bind_vars={"@coll": "certifiedFor", "id": tech_id},
        )
        db.aql.execute(
            Q_CASCADE_DELETE_PERFORMEDBY_TO_PLANNER,
            bind_vars={"id": tech_id},
        )
        db.collection("technicians").delete(entity_key, ignore_missing=True)

    elif coll_name == "parts":
        part_id = f"parts/{entity_key}"
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_FROM_ID,
            bind_vars={"@coll": "requiredBy", "id": part_id},
        )
        db.aql.execute(
            Q_CASCADE_DELETE_EDGES_TO_ID,
            bind_vars={"@coll": "consumed", "id": part_id},
        )
        db.collection("parts").delete(entity_key, ignore_missing=True)

    elif coll_name == "workOrders":
        wo_id = f"workOrders/{entity_key}"
        for coll in ("maintains", "performedBy", "consumed"):
            db.aql.execute(
                Q_CASCADE_DELETE_EDGES_FROM_ID,
                bind_vars={"@coll": coll, "id": wo_id},
            )
        db.collection("workOrders").delete(entity_key, ignore_missing=True)

    else:
        raise ValueError(f"Cascade delete not supported for collection: {coll_name}")
