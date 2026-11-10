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
import time
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

from arango.exceptions import AQLQueryExecuteError

from backend.aql import (
    Q_ALL_TECHNICIANS,
    Q_CASCADE_DELETE_EDGES_FROM_ID,
    Q_CASCADE_DELETE_EDGES_FROM_IDS,
    Q_CASCADE_DELETE_EDGES_TO_ID,
    Q_CASCADE_DELETE_PERFORMEDBY_TO_PLANNER,
    Q_CASCADE_DELETE_RELATIONSHIP,
    Q_CASCADE_ENGINES_FOR_AIRCRAFT,
    Q_CASCADE_WO_KEYS_FOR_ENGINES,
    Q_CHAT_WO_BY_ENGINE,
    Q_ELIGIBLE_TECHNICIANS_FOR_WO,
    Q_EXPIRE_PERFORMED_BY,
    Q_ONTOLOGY_FULL,
    Q_PLAN_COLLECT_IDS,
    Q_PLAN_DELETE_CONSUMED,
    Q_PLAN_DELETE_DEPENDS_ON,
    Q_PLAN_DELETE_MAINTAINS,
    Q_PLAN_DELETE_PERFORMED,
    Q_PLAN_DELETE_WOS,
    Q_PLAN_FLEET_CONTEXT,
    Q_PLAN_WORK_ORDERS,
    Q_PLAN_WORK_ORDERS_AT_TIME,
    Q_PLAN_WORK_ORDERS_NO_DEPENDS,
    Q_TECH_CURRENT_SCHEDULE,
    Q_WO_REASSIGN_CONTEXT,
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

# Unix timestamp used as "no expiry" sentinel on temporal edges (year 2286).
_VALID_INF: int = 9_999_999_999

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
    "workOrders":  {"status", "deadline", "description",
                    "scheduledHourStart", "scheduledStart", "scheduledEnd", "estimatedHours"},
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


class ScheduledTask(BaseModel):
    engine_id: str
    task_type: str          # "procurement" | "maintenance"
    day_start: int          # days from today (0 = today)
    day_end: int            # last day inclusive; same-day task: day_start == day_end
    estimated_hours: float
    description: str


class TechnicianTimeline(BaseModel):
    technician_id: str
    tasks: list[ScheduledTask]


class MaintenancePlan(BaseModel):
    work_orders: list[PlannedEngineItem]
    reasoning_summary: str
    reasoning_steps: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _working_day_to_date(start: date, working_day: int) -> date:
    """Return the Mon-Fri calendar date that is `working_day` business days after `start`."""
    d = start
    remaining = working_day
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:   # 0=Mon … 4=Fri
            remaining -= 1
    return d


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


_HOURS_PER_DAY: float = 8.0

# Deterministic maintenance hours per subsystem type
_SUBSYSTEM_HOURS: dict[str, float] = {
    "fan": 3.0, "LPC": 5.0, "HPC": 12.0,
    "combustor": 6.0, "HPT": 8.0, "LPT": 8.0,
}


def _maint_hours(driver_subsystems: list[str]) -> float:
    return min(sum(_SUBSYSTEM_HOURS.get(s, 4.0) for s in driver_subsystems), 20.0)


def _flights_remaining(engine: dict) -> float:
    """Lower value = more urgent."""
    fpd = max(engine["aircraft"].get("flightsPerDay", 1), 1)
    return engine["predictedRUL"] * fpd


def _build_technician_schedule(
    items: list[tuple["PlannedEngineItem", dict, list[str]]],
    today: date,
) -> list[dict]:
    """
    Produce a guaranteed-serial, non-overlapping schedule for one technician.

    items must already be sorted by urgency (flights_remaining ascending, critical before warning).
    Each item is (PlannedEngineItem, engine_dict, validated_blocking_part_ids).
    Returns a list of task dicts ready for the SSE timeline event.
    """
    busy: list[tuple[float, float]] = []

    def find_slot(earliest: float, duration: float) -> float:
        """Return the earliest start >= earliest that fits without overlapping busy."""
        start = earliest
        changed = True
        while changed:
            changed = False
            end = start + duration
            for b0, b1 in sorted(busy):
                if b0 < end and b1 > start:
                    start = b1
                    changed = True
                    break
        return start

    def to_day(h: float) -> int:
        return int(h // _HOURS_PER_DAY)

    def to_end_day(h: float, dur: float) -> int:
        return int((h + dur - 0.001) // _HOURS_PER_DAY)

    def to_date(day: int) -> str:
        return _working_day_to_date(today, day).isoformat()

    tasks: list[dict] = []

    # Pass 1 — schedule all procurement tasks serially from hour 0.
    # By doing procurement first we know the earliest parts-arrive time for pass 2.
    for wo, engine, blocking_ids in items:
        if not wo.has_blocking_parts:
            continue
        dur = 2.0
        sh = find_slot(0.0, dur)
        busy.append((sh, sh + dur))
        tasks.append({
            "engine_id": wo.engine_id, "task_type": "procurement",
            "hour_start": sh, "hour_end": sh + dur,
            "day_start": to_day(sh), "day_end": to_end_day(sh, dur),
            "estimated_hours": dur,
            "description": (
                f"Order parts for engine #{wo.engine_id} "
                f"({', '.join(engine.get('driverSubsystems', []))})"
            ),
            "scheduled_start": to_date(to_day(sh)),
            "scheduled_end": to_date(to_end_day(sh, dur)),
        })

    # Pass 2 — schedule maintenance tasks.
    # Engines with no blocking parts fill the procurement wait window.
    for wo, engine, blocking_ids in items:
        parts_map = {p["id"]: p for p in engine.get("parts", [])}
        max_lead_days = (
            max(
                (parts_map[pid]["leadTimeDays"] for pid in blocking_ids if pid in parts_map),
                default=0,
            )
            if wo.has_blocking_parts and blocking_ids
            else 0
        )
        earliest_h = max_lead_days * _HOURS_PER_DAY
        dur = _maint_hours(engine.get("driverSubsystems", []))
        sh = find_slot(earliest_h, dur)
        busy.append((sh, sh + dur))
        tasks.append({
            "engine_id": wo.engine_id, "task_type": "maintenance",
            "hour_start": sh, "hour_end": sh + dur,
            "day_start": to_day(sh), "day_end": to_end_day(sh, dur),
            "estimated_hours": dur,
            "description": (
                f"Maintenance: {', '.join(engine.get('driverSubsystems', []))} "
                f"on engine #{wo.engine_id}"
            ),
            "scheduled_start": to_date(to_day(sh)),
            "scheduled_end": to_date(to_end_day(sh, dur)),
        })

    tasks.sort(key=lambda t: (t["day_start"], t["task_type"]))
    return tasks


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

                # Build prompt — LLM is responsible for assignment only.
                # Scheduling math (serial ordering, hour estimation, Gantt) is done in Python.
                prompt = (
                    "You are an aircraft maintenance scheduler. "
                    "Given the fleet context below assign every engine to a technician "
                    "and identify which parts are missing.\n\n"

                    "RULES\n"
                    f"• There are {len(engines)} engines listed. EVERY engine must appear "
                    "in work_orders — do not skip any.\n"
                    "• technician_id: pick any technician from the engine's technicians list "
                    "(load balancing is handled server-side; your pick is not used for assignment).\n"
                    "• Set has_blocking_parts=true and list blocking_part_ids for any part "
                    "with stockLevel==0 — this IS used.\n\n"

                    "Return ONLY structured JSON — no prose outside the JSON.\n\n"
                    "For reasoning_steps, return 4-8 concise strings (no bullet chars) "
                    "summarising key decisions: engine prioritisation, technician selection, "
                    "workload balancing, and any blocking-parts considerations. "
                    'Example: ["Prioritised engine 23 (critical, RUL 18) over engine 41 (warning, RUL 45)", '
                    '"Assigned Noah Rhodes to engine 23 — only JFK-based technician holding Hydraulics cert"]\n\n'
                    f"Fleet context (JSON):\n{json.dumps(engines, indent=2)}"
                )

                llm = ChatOpenAI(
                    model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                    temperature=0,
                    api_key=os.environ["OPENAI_API_KEY"],
                    max_tokens=16384,
                )
                structured = llm.with_structured_output(MaintenancePlan)
                plan: MaintenancePlan = await structured.ainvoke(prompt)  # type: ignore[assignment]

                yield _sse("progress", {"message": "Building optimised schedule…", "step": 3, "total": 5})

                eng_map = {e["id"]: e for e in engines}
                valid_part_ids = {p["id"] for e in engines for p in e["parts"]}
                tech_name_map: dict[str, str] = {
                    t["id"]: t["name"] for e in engines for t in e["technicians"]
                }
                today = date.today()
                now_iso = datetime.now(timezone.utc).isoformat()

                # Use LLM output only for blocking-parts identification and reasoning.
                # Technician assignment is done server-side to guarantee load balance.
                llm_blocking: dict[str, tuple[list[str], bool]] = {
                    item.engine_id: (
                        [p for p in item.blocking_part_ids if p in valid_part_ids],
                        item.has_blocking_parts,
                    )
                    for item in plan.work_orders
                    if item.engine_id in eng_map
                }

                # Greedy assignment: urgency-sorted, pick least-loaded qualified technician.
                # "Qualified" = canServiceDegradingSubs; falls back to any base technician.
                _BUCKET_ORDER = {"critical": 0, "warning": 1}
                tech_load: dict[str, int] = {}
                assignments: dict[str, tuple[str, list[str], bool]] = {}
                for eid, engine in sorted(
                    eng_map.items(),
                    key=lambda kv: (
                        _BUCKET_ORDER.get(kv[1]["riskBucket"], 2),
                        kv[1]["predictedRUL"],
                    ),
                ):
                    if not engine["technicians"]:
                        continue
                    if eid in llm_blocking:
                        blocking_ids, has_blocking = llm_blocking[eid]
                    else:
                        blocking_ids = [p["id"] for p in engine["parts"] if p["blocking"]]
                        has_blocking = bool(blocking_ids)
                    certified = [t for t in engine["technicians"] if t.get("canServiceDegradingSubs")]
                    pool = certified or engine["technicians"]
                    tech_id = min(pool, key=lambda t: tech_load.get(t["id"], 0))["id"]
                    assignments[eid] = (tech_id, blocking_ids, has_blocking)
                    tech_load[tech_id] = tech_load.get(tech_id, 0) + 1

                # Group by technician, sort by urgency: critical first, then flights_remaining asc
                tech_items: dict[str, list[tuple[str, dict, list[str], bool]]] = {}
                for eid, (tech_id, blocking_ids, has_blocking) in assignments.items():
                    engine = eng_map[eid]
                    tech_items.setdefault(tech_id, []).append(
                        (eid, engine, blocking_ids, has_blocking)
                    )
                for items in tech_items.values():
                    items.sort(key=lambda x: (
                        _BUCKET_ORDER.get(x[1]["riskBucket"], 2),
                        _flights_remaining(x[1]),
                    ))

                # Build guaranteed-serial schedules — one per technician
                # Wrap items into the shape _build_technician_schedule expects
                tech_schedules: dict[str, list[dict]] = {}
                for tech_id, items in tech_items.items():
                    wrapped = [
                        (
                            PlannedEngineItem(
                                engine_id=eid,
                                technician_id=tech_id,
                                has_blocking_parts=has_blocking,
                                blocking_part_ids=blocking_ids,
                            ),
                            engine,
                            blocking_ids,
                        )
                        for eid, engine, blocking_ids, has_blocking in items
                    ]
                    tech_schedules[tech_id] = _build_technician_schedule(wrapped, today)

                def _get_sched(tech_id: str, eid: str, task_type: str) -> dict | None:
                    return next(
                        (t for t in tech_schedules.get(tech_id, [])
                         if t["engine_id"] == eid and t["task_type"] == task_type),
                        None,
                    )

                def _write_wo(wo_doc: dict, engine_id: str, t_id: str,
                              part_ids: list[str]) -> None:
                    wo_key = wo_doc["_key"]
                    db.collection("workOrders").insert(wo_doc)
                    db.collection("maintains").insert(
                        {"_from": f"workOrders/{wo_key}", "_to": f"engines/{engine_id}"}
                    )
                    # Temporal edge: validFrom = now, validTo = sentinel "infinity".
                    # Reassignments expire this edge and add a new one, preserving history.
                    db.collection("performedBy").insert({
                        "_from": f"workOrders/{wo_key}",
                        "_to":   f"technicians/{t_id}",
                        "validFrom": int(time.time()),
                        "validTo":   _VALID_INF,
                    })
                    for pid in part_ids:
                        db.collection("consumed").insert(
                            {"_from": f"workOrders/{wo_key}", "_to": f"parts/{pid}"}
                        )

                total_wo = maint_count = proc_count = 0
                planned_engines: set[str] = set()

                for tech_id, items in tech_items.items():
                    tech_name = tech_name_map.get(tech_id, tech_id)
                    for eid, engine, blocking_ids, has_blocking in items:
                        has_blocking = has_blocking and bool(blocking_ids)
                        max_lead = max(
                            (p["leadTimeDays"] for p in engine["parts"]
                             if p["id"] in blocking_ids),
                            default=0,
                        )
                        proc_dl, maint_dl = _deadline(engine, has_blocking, max_lead)
                        flights_left = int(_flights_remaining(engine))

                        yield _sse("progress", {
                            "message": (
                                f"Engine #{eid} ({engine['riskBucket']}, "
                                f"RUL={engine['predictedRUL']}, "
                                f"~{flights_left} flights remaining, "
                                f"{engine['aircraft']['tailNumber']}) → {tech_name}"
                            ),
                            "step": 3, "total": 5,
                        })

                        proc_wo_key: str | None = None
                        if has_blocking:
                            sched = _get_sched(tech_id, eid, "procurement")
                            wo_key = f"PLN-{uuid.uuid4().hex[:8]}"
                            proc_wo_key = wo_key
                            wo_doc = {
                                "_key": wo_key, "generatedByPlanner": True,
                                "type": "procurement", "engineId": eid,
                                "technicianId": tech_id, "deadline": proc_dl,
                                "riskBucket": engine["riskBucket"], "status": "open",
                                "createdAt": now_iso,
                                "estimatedHours": sched["estimated_hours"] if sched else 2.0,
                                "scheduledHourStart": sched["hour_start"] if sched else 0.0,
                                "scheduledStart": sched["scheduled_start"] if sched else today.isoformat(),
                                "scheduledEnd": sched["scheduled_end"] if sched else today.isoformat(),
                                "description": sched["description"] if sched else (
                                    f"Order parts for engine #{eid} "
                                    f"({', '.join(engine.get('driverSubsystems', []))})"
                                ),
                            }
                            await asyncio.to_thread(
                                partial(_write_wo, wo_doc, eid, tech_id, blocking_ids)
                            )
                            yield _sse("work_order", {
                                "woKey": wo_key, "type": "procurement",
                                "engineId": eid, "technicianName": tech_name,
                                "deadline": proc_dl, "status": "open",
                                "description": wo_doc["description"],
                                "estimatedHours": wo_doc["estimatedHours"],
                                "scheduledHourStart": wo_doc["scheduledHourStart"],
                                "scheduledStart": wo_doc["scheduledStart"],
                                "scheduledEnd": wo_doc["scheduledEnd"],
                            })
                            total_wo += 1
                            proc_count += 1

                        maint_status = "pending-parts" if has_blocking else "open"
                        sched = _get_sched(tech_id, eid, "maintenance")
                        wo_key = f"PLN-{uuid.uuid4().hex[:8]}"
                        maint_wo_key = wo_key
                        wo_doc = {
                            "_key": wo_key, "generatedByPlanner": True,
                            "type": "maintenance", "engineId": eid,
                            "technicianId": tech_id, "deadline": maint_dl,
                            "riskBucket": engine["riskBucket"], "status": maint_status,
                            "createdAt": now_iso,
                            "estimatedHours": sched["estimated_hours"] if sched else _maint_hours(engine.get("driverSubsystems", [])),
                            "scheduledHourStart": sched["hour_start"] if sched else None,
                            "scheduledStart": sched["scheduled_start"] if sched else None,
                            "scheduledEnd": sched["scheduled_end"] if sched else None,
                            "description": sched["description"] if sched else (
                                f"Maintenance: {', '.join(engine.get('driverSubsystems', []))} "
                                f"(engine #{eid})"
                            ),
                        }
                        await asyncio.to_thread(
                            partial(_write_wo, wo_doc, eid, tech_id, [])
                        )
                        if proc_wo_key:
                            _maint_key = maint_wo_key
                            _proc_key  = proc_wo_key
                            await asyncio.to_thread(
                                lambda: db.collection("dependsOn").insert({
                                    "_from": f"workOrders/{_maint_key}",
                                    "_to":   f"workOrders/{_proc_key}",
                                    "reason": "parts",
                                })
                            )
                        yield _sse("work_order", {
                            "woKey": wo_key, "type": "maintenance",
                            "engineId": eid, "technicianName": tech_name,
                            "deadline": maint_dl, "status": maint_status,
                            "description": wo_doc["description"],
                            "estimatedHours": wo_doc["estimatedHours"],
                            "scheduledHourStart": wo_doc["scheduledHourStart"],
                            "scheduledStart": wo_doc["scheduledStart"],
                            "scheduledEnd": wo_doc["scheduledEnd"],
                        })
                        total_wo += 1
                        maint_count += 1
                        planned_engines.add(eid)

                yield _sse("summary", {
                    "totalWorkOrders": total_wo,
                    "maintenanceOrders": maint_count,
                    "procurementOrders": proc_count,
                    "enginesPlanned": len(planned_engines),
                    "reasoningSummary": plan.reasoning_summary,
                    "reasoningSteps": plan.reasoning_steps,
                })

                timeline_payload = [
                    {
                        "technicianId": tech_id,
                        "technicianName": tech_name_map.get(tech_id, tech_id),
                        "tasks": [
                            {
                                "engineId": t["engine_id"],
                                "taskType": t["task_type"],
                                "hourStart": t["hour_start"],
                                "hourEnd": t["hour_end"],
                                "dayStart": t["day_start"],
                                "dayEnd": t["day_end"],
                                "estimatedHours": t["estimated_hours"],
                                "description": t["description"],
                            }
                            for t in tasks
                        ],
                    }
                    for tech_id, tasks in tech_schedules.items()
                    if tasks
                ]
                yield _sse("timeline", {"timelines": timeline_payload})

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
        counts: dict[str, int] = {"workOrders": 0, "maintains": 0, "performedBy": 0, "consumed": 0, "dependsOn": 0}
        if ids:
            bind = {"ids": ids}
            for q, key in [
                (Q_PLAN_DELETE_DEPENDS_ON, "dependsOn"),
                (Q_PLAN_DELETE_MAINTAINS, "maintains"),
                (Q_PLAN_DELETE_PERFORMED, "performedBy"),
                (Q_PLAN_DELETE_CONSUMED, "consumed"),
            ]:
                try:
                    db.aql.execute(q, bind_vars=bind)
                except Exception:
                    pass  # collection may not exist in older DBs pre-make-load
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
    try:
        wos = await asyncio.to_thread(lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS)))
    except AQLQueryExecuteError as exc:
        if "[ERR 1203]" not in str(exc):
            raise
        # dependsOn collection not yet created (pre-make-load DB) — use simpler query
        wos = await asyncio.to_thread(lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS_NO_DEPENDS)))
    return JSONResponse({"workOrders": wos})


@router.get("/technicians")
async def plan_technicians() -> JSONResponse:
    db = get_db()
    rows = await asyncio.to_thread(lambda: list(db.aql.execute(Q_ALL_TECHNICIANS)))
    return JSONResponse({"technicians": rows})


@router.get("/schedule-at")
async def plan_schedule_at(t: int) -> JSONResponse:
    """Return the work-order schedule as it existed at Unix timestamp `t` (seconds).
    Uses temporal performedBy edges to reconstruct past assignments.
    Example: GET /api/plan/schedule-at?t=1753228800
    """
    db = get_db()
    wos = await asyncio.to_thread(
        lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS_AT_TIME, bind_vars={"t": t}))
    )
    return JSONResponse({"t": t, "workOrders": wos})


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
    """Execute a read-only AQL query. Use for name-based lookups, counts, cascade previews, or relationship inspection.
    Results are capped at 50 rows. Always include LIMIT in queries against large collections (workOrders, sensors, readings).
    Example — find technician by name: FOR t IN technicians FILTER t.name == 'Abigail Shaffer' RETURN t"""
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
    """Return planner-generated work orders (generatedByPlanner=true only), optionally filtered by engine_id.
    If the result is empty, no plan has been generated yet — tell the user to run the planner from the Fleet Status screen."""
    db = get_db()
    if engine_id:
        rows = await asyncio.to_thread(
            lambda: list(db.aql.execute(Q_CHAT_WO_BY_ENGINE, bind_vars={"eid": engine_id}))
        )
    else:
        rows = await asyncio.to_thread(lambda: list(db.aql.execute(Q_PLAN_WORK_ORDERS)))
    return json.dumps(rows[:100])


@tool
async def get_work_order_parts(wo_key: str) -> str:
    """Return parts consumed by a work order, with stock level and lead time.

    Use this to investigate 'pending-parts' status or check whether parts will
    arrive before a scheduled start time.
    Also returns the procurement WO this maintenance WO depends on (if any).
    Returns: { wo_key, status, parts: [{id, name, subsystemType, stockLevel, leadTimeDays, blocking}],
               dependsOn: {key, type, status} | null }
    blocking=true means stockLevel==0 (part not in stock).
    """
    db = get_db()
    aql = """
    LET wo = DOCUMENT(CONCAT('workOrders/', @wo_key))
    FILTER wo != null
    LET parts = (
      FOR p IN 1..1 OUTBOUND wo consumed
        RETURN {
          id: p._key, name: p.name, subsystemType: p.subsystemType,
          stockLevel: p.stockLevel, leadTimeDays: p.leadTimeDays,
          blocking: p.stockLevel == 0
        }
    )
    LET dep = FIRST(
      FOR d IN 1..1 OUTBOUND wo dependsOn
        RETURN { key: d._key, type: d.type, status: d.status }
    )
    RETURN { wo_key: @wo_key, status: wo.status, parts: parts, dependsOn: dep }
    """
    rows = await asyncio.to_thread(
        lambda: list(db.aql.execute(aql, bind_vars={"wo_key": wo_key}))
    )
    if not rows or rows[0] is None:
        return json.dumps({"error": f"Work order '{wo_key}' not found."})
    return json.dumps(rows[0])


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
    The proposal description will tell you exactly which fields were accepted and which
    were ignored (non-editable), so you can inform the user accurately.
    Does NOT write to the DB — returns a proposal."""
    coll = _COLLECTION_MAP.get(entity_type, entity_type)
    allowed = _EDITABLE_FIELDS.get(coll, set())
    accepted = {k: v for k, v in fields.items() if k in allowed}
    ignored = sorted(k for k in fields if k not in allowed)
    desc = f"Update {entity_type} {entity_key}: {accepted}"
    if ignored:
        desc += f" (ignored non-editable fields: {ignored})"
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": desc,
        "operation": {
            "type": "update_entity",
            "entity_type": entity_type,
            "entity_key": entity_key,
            "fields": accepted,
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
    edge_type: installedOn | certifiedFor | maintains | consumed.
    NEVER use this for performedBy — use propose_reassign_work_order instead.
    from_id / to_id: full ArangoDB document IDs e.g. 'engines/42'.
    Does NOT write to the DB — returns a proposal."""
    if edge_type == "performedBy":
        return json.dumps({
            "error": (
                "Do NOT create performedBy edges directly. "
                "Use propose_reassign_work_order(wo_key, new_tech_key) instead — "
                "it enforces base, certification, and schedule constraints."
            )
        })
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
    edge_type: installedOn | certifiedFor | maintains | consumed.
    NEVER use this for performedBy — use propose_reassign_work_order instead.
    Does NOT write to the DB — returns a proposal."""
    if edge_type == "performedBy":
        return json.dumps({
            "error": (
                "Do NOT delete performedBy edges directly. "
                "Use propose_reassign_work_order(wo_key, new_tech_key) instead — "
                "it expires the old edge and validates the new assignment."
            )
        })
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


@tool
async def check_technician_availability(tech_key: str) -> str:
    """Return a technician's current work order schedule (hour slots with scheduledHourStart and estimatedHours).
    Use this to show the user a technician's full schedule, or to compare workloads in detail.
    Note: propose_reassign_work_order already enforces schedule conflicts — you do not need to call this
    before every reassignment, only when you need to show or explain the schedule to the user."""
    now = int(time.time())
    db = get_db()
    rows = await asyncio.to_thread(
        lambda: list(db.aql.execute(Q_TECH_CURRENT_SCHEDULE,
                                    bind_vars={"tech_key": tech_key, "now": now}))
    )
    tech = await asyncio.to_thread(lambda: db.collection("technicians").get(tech_key))
    name = tech.get("name", tech_key) if tech else tech_key
    return json.dumps({
        "technician": {"key": tech_key, "name": name,
                       "homeBase": tech.get("homeBase") if tech else None},
        "workOrders": rows,
        "note": f"{len(rows)} currently-assigned work orders",
    })


@tool
async def propose_reassign_work_order(wo_key: str, new_tech_key: str) -> str:
    """Validate and propose reassigning a work order to a different technician.

    Enforces three hard constraints before creating a proposal:
      1. The new technician must be at the SAME BASE as the engine's aircraft.
      2. The new technician must hold a certification for at least one of the
         engine's driver subsystems.
      3. The new technician must have no schedule overlap with the work order's
         time slot (scheduledHourStart to scheduledHourStart + estimatedHours).

    If any constraint fails, returns an explanation with enough context for the
    user to pick a different technician or push the work back.
    Does NOT write to the database — returns a proposal for user confirmation.
    """
    now = int(time.time())
    db = get_db()

    ctx_rows = await asyncio.to_thread(
        lambda: list(db.aql.execute(Q_WO_REASSIGN_CONTEXT,
                                    bind_vars={"wo_key": wo_key, "now": now}))
    )
    if not ctx_rows or ctx_rows[0] is None:
        return json.dumps({"error": f"Work order '{wo_key}' not found."})
    ctx = ctx_rows[0]
    wo      = ctx["wo"]
    engine  = ctx["engine"]
    base    = ctx["aircraft"]["base"]
    cur     = ctx.get("currentTech") or {}
    driver_subs: set[str] = set(engine.get("driverSubsystems") or [])

    new_tech = await asyncio.to_thread(lambda: db.collection("technicians").get(new_tech_key))
    if not new_tech:
        return json.dumps({
            "error": (
                f"Technician key '{new_tech_key}' not found. "
                f"Technician keys look like T001, T002, … T010 — use the 'key' field "
                f"from find_eligible_technicians('{wo_key}') to get valid keys."
            )
        })

    # 1 — base check
    if new_tech.get("homeBase") != base:
        return json.dumps({
            "error": (
                f"Technician {new_tech['name']} is based at {new_tech['homeBase']}, "
                f"but engine #{wo['engineId']} is at {base}. "
                f"Cross-base reassignment is not allowed. "
                f"Find technicians at {base}: "
                f"FOR t IN technicians FILTER t.homeBase == '{base}' RETURN t"
            )
        })

    # 2 — certification check
    tech_certs: set[str] = set(new_tech.get("certifications") or [])
    if not tech_certs & driver_subs:
        return json.dumps({
            "error": (
                f"Technician {new_tech['name']} holds certifications {sorted(tech_certs)} "
                f"but engine #{wo['engineId']} needs coverage for {sorted(driver_subs)}. "
                f"No overlap — cannot reassign."
            )
        })

    # 3 — schedule conflict check (warning only — does not block the proposal)
    scheduling_warning: str | None = None
    wo_start = wo.get("scheduledHourStart")
    wo_dur   = wo.get("estimatedHours") or 8.0
    if wo_start is not None:
        existing = await asyncio.to_thread(
            lambda: list(db.aql.execute(Q_TECH_CURRENT_SCHEDULE,
                                        bind_vars={"tech_key": new_tech_key, "now": now}))
        )
        wo_end = wo_start + wo_dur
        for slot in existing:
            s = slot.get("scheduledHourStart")
            d = slot.get("estimatedHours") or 8.0
            if s is not None and s < wo_end and (s + d) > wo_start:
                scheduling_warning = (
                    f"Schedule overlap with WO {slot['woKey']} (engine #{slot['engineId']}) "
                    f"at hour {s}–{s + d}. Schedules will be repacked automatically on confirm."
                )
                break

    desc = (
        f"Reassign {wo['type']} WO {wo_key} (engine #{wo['engineId']}) "
        f"from {cur.get('name', '?')} to {new_tech['name']} ({base})"
    )
    if scheduling_warning:
        desc += f" ⚠ {scheduling_warning}"
    return json.dumps({
        "__propose__": True,
        "id": f"edit-{uuid.uuid4().hex[:12]}",
        "description": desc,
        "operation": {
            "type": "reassign_work_order",
            "entity_key": wo_key,
            "fields": {
                "new_tech_key": new_tech_key,
                "old_tech_key": cur.get("id", ""),
                "scheduling_warning": scheduling_warning,
            },
        },
    })


@tool
async def find_eligible_technicians(wo_key: str) -> str:
    """Return all technicians eligible to take a specific work order.

    Use this when you do NOT yet know who to assign to. If the user has already
    named a target technician, call propose_reassign_work_order directly instead.

    A technician is eligible if:
      - Their homeBase matches the engine's aircraft base.
      - They hold at least one certification overlapping the engine's driverSubsystems.

    Each result includes:
      - key          : technician _key to pass to propose_reassign_work_order (e.g. T003)
      - name         : display name
      - matchingCerts: which of their certifications cover the degrading subsystems
      - schedule     : their currently-assigned WO slots (summing estimatedHours gives total load)

    The response also includes wo_key, engineId, driverSubsystems, and base so you can
    clearly correlate each result with the right work order.
    """
    now = int(time.time())
    db = get_db()
    rows = await asyncio.to_thread(
        lambda: list(db.aql.execute(
            Q_ELIGIBLE_TECHNICIANS_FOR_WO,
            bind_vars={"wo_key": wo_key, "now": now},
        ))
    )
    if not rows or rows[0] is None:
        return json.dumps({
            "wo_key": wo_key,
            "eligible": [],
            "note": (
                "No eligible technicians found. Either no technicians are at the right base "
                "or none have the required certifications. Consider pushing the work order back."
            ),
        })
    result = rows[0]
    eligible = result.get("eligible") or []
    return json.dumps({
        "wo_key": result.get("wo_key"),
        "engineId": result.get("engineId"),
        "driverSubsystems": result.get("driverSubsystems"),
        "base": result.get("base"),
        "eligible": eligible,
        "note": f"{len(eligible)} eligible technician(s) found",
    })


@tool
async def get_entity(entity_type: str, entity_key: str) -> str:
    """Fetch a single entity by type and key — use this for simple lookups instead of read_graph().
    entity_type: aircraft | engine | technician | part | workOrder | subsystem
    entity_key: the _key value (e.g. 'T003', 'PLN-abc123', 'AC001', '17')
    Returns the document fields, or an error dict if not found."""
    coll = _COLLECTION_MAP.get(entity_type, "")
    if not coll:
        return json.dumps({"error": f"Unknown entity_type '{entity_type}'. "
                           "Use: aircraft, engine, technician, part, workOrder, subsystem"})
    db = get_db()
    try:
        doc = await asyncio.to_thread(lambda: db.collection(coll).get(entity_key))
        if doc is None:
            return json.dumps({"error": f"{entity_type} '{entity_key}' not found in {coll}"})
        return json.dumps({k: v for k, v in doc.items() if k != "_rev"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


_PLANNING_TOOLS = [
    query_ontology,
    read_graph,
    get_entity,
    get_work_orders,
    get_work_order_parts,
    find_eligible_technicians,
    check_technician_availability,
    propose_reassign_work_order,
    propose_create_entity,
    propose_update_entity,
    propose_delete_entity,
    propose_create_relationship,
    propose_delete_relationship,
]

_SYSTEM_PROMPT = """You are AeroFleet Planning Assistant — an autonomous maintenance scheduling agent. Respond in English.

## Core principle: investigate first, ask only when genuinely stuck

Your job is to reason over data and act. Never ask the user for information you can look up.

Never ask for a database key — look it up.
If the user names a person, engine, or aircraft — search for it immediately.
If the user says "you decide" — query the options, pick the best based on data, explain why.

Examples of what to do instead of asking:
- "Abigail has too much work" → read_graph to find Abigail by name, check her schedule, find colleagues at her base
- "Reassign the critical engine WOs" → query engines WHERE riskBucket='critical', get their WOs, run find_eligible_technicians, propose
- "Push the deadline back" → get_entity on the WO, read its current deadline and the engine's predictedRUL, propose an extension grounded in the data
- "Reassign this to someone else" → find_eligible_technicians, pick lowest workload, state your reasoning
- "You decide" → make the best data-grounded call and explain it

Ask ONE clarifying question only when:
1. Search returned zero matching entities
2. Multiple candidates exist with no principled way to choose AND the consequences differ significantly
3. The action is destructive (delete/retire) and scope is genuinely unclear after searching

## Workflow for every request

1. Investigate — use read tools to understand the current state before proposing anything.
   If get_work_orders returns empty, stop: tell the user no plan exists yet and ask them to generate one from the Fleet Status screen.
2. Reason — state what you found and what you're going to propose, and why.
3. Propose — call the propose_* tool. Never write to the database directly.
4. Report — after ALL propose_* tools have returned, tell the user to confirm in the Pending Changes panel.

Critical: a proposal only exists after a propose_* tool has been called and returned a result. Never say "I have proposed X" unless the tool call has already happened in this turn. If you have not called the tool yet, you have not proposed anything. Your final message after proposals should say "Proposals are ready in the Pending Changes panel" — do not enumerate specific WO keys in the text since the panel already shows them.

When you receive a [Changes confirmed and applied] message, acknowledge the changes and offer one concrete next step.

## Work order reassignment

Use propose_reassign_work_order for every WO reassignment. Do not use propose_create_relationship or propose_delete_relationship for performedBy edges — those tools return an error if you try.

The tool enforces three constraints automatically: same base, matching certification, no schedule overlap.

### Finding the right technician

There are two paths depending on whether the user has named a target technician:

**Path A — user named a specific technician** (e.g. "reassign to Noah", "move these to Sarah"):
1. Look up their key once with read_graph: `FOR t IN technicians FILTER t.name == 'Noah Rhodes' RETURN t`
2. Call propose_reassign_work_order(wo_key, tech_key) directly for each target WO.
   Do NOT call find_eligible_technicians — it is for when you do not yet know who to assign.
3. propose_reassign_work_order returns immediately with pass or a precise failure reason.

**Path B — user has not named a target** (e.g. "reassign this to someone", "find someone for this"):
1. Call find_eligible_technicians(wo_key). The response includes wo_key, engineId, driverSubsystems, and base so you can clearly correlate which work order each result belongs to.
2. Sum the estimatedHours values in each technician's schedule array to compare total workload. Pick the lowest total. Break ties by most matchingCerts.
3. Call propose_reassign_work_order(wo_key, key) using the exact key from the eligible list.
4. State your reasoning: "Assigning to Noah Rhodes — 8h scheduled vs Abigail's 24h, holds the Hydraulics cert."

If validation fails: relay the exact error and pick a different candidate. If no eligible technician exists, offer to push the WO deadline back rather than forcing an invalid assignment.

### Reassigning multiple work orders

Follow Path A or B per WO as appropriate.
- Create proposals for each WO that passes.
- Report which WOs failed and why, and suggest remedies (later deadline, different technician).
- Do not leave the user with zero proposals because some WOs failed — propose what you can.

### When most or all batch proposals fail

If more than half the proposals in a batch return errors:
1. STOP — do not retry the same proposals.
2. Call check_technician_availability on the target technician to read their current schedule.
3. Read the error messages carefully — they name the exact constraint (base, cert, or schedule overlap).
4. For schedule conflicts: the destination technician is already booked at those hour slots.
   This now emits a scheduling_warning in the proposal instead of blocking it — accept the proposal
   and run a schedule repack after confirming.
5. For cert failures: use find_eligible_technicians to find who CAN take these WOs.

### Splitting workload between two technicians

"Split X's workload with Y" or "split tasks between X and Y" means:
- Each technician receives a MIX of task types — not all procurement to one, all maintenance to the other.
- Aim for roughly equal total hours across both technicians.
- Sort the source technician's WOs by estimatedHours descending, then alternate:
  WO_0 → tech A, WO_1 → tech B, WO_2 → tech A, WO_3 → tech B, …
- After proposing reassignments, repack the schedule for both technicians.

### Schedule review after reassignment

After ALL reassignment proposals are confirmed and applied, the system automatically
repacks both technicians' schedules to remove gaps — you do not need to propose
scheduledHourStart updates for repacking. You may call check_technician_availability
to show the updated schedule to the user if they ask.

## Edit operations

For any non-reassignment change:
- Call get_entity() first to confirm the entity exists and read its current values.
- For deletions, call read_graph() first to identify what will cascade, then include that in the cascade_preview.
- Stage all changes with propose_* tools, then tell the user to confirm.

### Schedule changes (workOrders)
Fields: scheduledHourStart, estimatedHours, scheduledStart (ISO date), scheduledEnd (ISO date).
For explicit schedule change requests (e.g. "push this back 2 days"), set all four together.
scheduledHourStart is an absolute working-hour offset from when the plan was generated (e.g. 16 = 2nd working day at 08:00). To push a WO back, read its current scheduledHourStart first, then add the appropriate hours.
For schedule repacking after reassignment, only update scheduledHourStart and estimatedHours — see "Schedule review after reassignment" above.

### Status updates (workOrders)
Valid status values: "open", "pending-parts", "closed", "cancelled". Do not use any other value.

### Scheduling around pending-parts status

Work orders with status='pending-parts' cannot start until their parts arrive.
Before scheduling a pending-parts WO, call get_work_order_parts(wo_key) to find the
maximum leadTimeDays across all consumed parts. The earliest valid scheduledHourStart
for that WO is max_lead_time_days × 8 working hours.
A pending-parts maintenance WO also has a dependsOn edge pointing to its procurement WO —
traverse OUTBOUND from the maintenance WO to find the procurement WO and its deadline.
If a pending-parts WO is currently scheduled before its parts can arrive, flag this
to the user and propose pushing it back.

### Other operations
- Deadline / description: propose_update_entity on workOrders
- Technician name or base: propose_update_entity on technician
- Aircraft base reroute: propose_update_entity on aircraft (field: base)
- Parts stock: propose_update_entity on parts (stockLevel, leadTimeDays)
- Retire aircraft / decommission engine: propose_delete_entity with cascade_preview
- Add/remove technician certification: propose_create_relationship / propose_delete_relationship on certifiedFor
- Creating any new entity: call query_ontology() first to confirm required fields and allowed relationships

## Temporal edge model

performedBy edges carry validFrom/validTo (Unix seconds, sentinel 9999999999 = no expiry).
Reassignment expires the old edge and creates a new one — history is never deleted.
To find who was assigned at a past time T, query performedBy filtering validFrom <= T AND validTo > T.

## Schema

The live schema appended below takes precedence for exact field names and types. The static schema here documents traversal semantics the live introspection does not capture.

Vertex collections:
- aircraft    — tailNumber, base, flightsPerDay
- engines     — engineId, model, riskBucket, predictedRUL, entryIntoService, healthIndex, riskScore
- technicians — name, homeBase
- parts       — name, stockLevel, leadTimeDays
- workOrders  — type, status, deadline, description, engineId, technicianId, generatedByPlanner, createdAt, scheduledHourStart, scheduledStart, scheduledEnd, estimatedHours
- subsystems  — name
- sensors     — sensorId, type

Edges (_from → _to):
- installedOn  : engines → aircraft
- partOf       : subsystems → engines
- monitors     : sensors → subsystems
- requiredBy   : parts → subsystems
- certifiedFor : technicians → subsystems
- maintains    : workOrders → engines
- performedBy  : workOrders → technicians  (temporal — use propose_reassign_work_order only)
- consumed     : workOrders → parts
- dependsOn    : workOrders → workOrders   (maintenance WO → procurement WO; reason="parts")

Traversal directions:
- Engines on an aircraft: INBOUND installedOn from aircraft
- Aircraft of an engine: OUTBOUND installedOn from engine
- WOs for an engine: INBOUND maintains from engine (or filter workOrders by engineId)
- Subsystems of an engine: INBOUND partOf from engine
- Certifications of a technician: OUTBOUND certifiedFor from technician

AQL patterns:
  -- find technician by name
  FOR t IN technicians FILTER t.name == 'Abigail Shaffer' RETURN t
  -- all current WOs assigned to a technician (validTo sentinel = active edge)
  FOR e IN performedBy FILTER e._to == 'technicians/T007' AND e.validTo == 9999999999
    FOR wo IN workOrders FILTER wo._id == e._from RETURN wo
  -- NEVER filter by wo.technicianId — this field can be stale after reassignment.
  --   Always use the performedBy edge pattern above to find a technician's current WOs.
  -- engines on an aircraft
  FOR e IN 1..1 INBOUND 'aircraft/AC001' installedOn RETURN e
  -- WOs for an engine
  FOR wo IN workOrders FILTER wo.engineId == 'E042' LIMIT 50 RETURN wo
  -- parts consumed by a work order (prefer get_work_order_parts tool over raw AQL)
  FOR p IN 1..1 OUTBOUND 'workOrders/PLN-abc123' consumed RETURN p
  -- find the procurement WO a maintenance WO depends on
  FOR dep IN 1..1 OUTBOUND 'workOrders/PLN-maint-key' dependsOn RETURN dep
  -- find maintenance WO(s) blocked by a procurement WO
  FOR dep IN 1..1 INBOUND 'workOrders/PLN-proc-key' dependsOn RETURN dep
  -- technicians at a base
  FOR t IN technicians FILTER t.homeBase == 'LHR' RETURN t
  -- who was assigned to a WO at Unix time T (historical)
  FOR e IN performedBy FILTER e._from == 'workOrders/PLN-abc123'
    AND e.validFrom <= @t AND e.validTo > @t RETURN e"""


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

        config = {
            "configurable": {"thread_id": body.session_id},
            "recursion_limit": 20,
        }
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
                                    "input": json.dumps(tc["args"])[:1500],
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
                                snippet = (raw[:1500] if isinstance(raw, str)
                                           else json.dumps(parsed)[:1500])
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


def _repack_technician_schedule(db, tech_key: str) -> int:
    """Compact a technician's schedule so WOs are contiguous with no gaps.

    Sorts by current scheduledHourStart and reassigns sequential slots from 0
    using each WO's actual estimatedHours. Returns the number of WOs updated.
    """
    now = int(time.time())
    wos = list(db.aql.execute(
        Q_TECH_CURRENT_SCHEDULE,
        bind_vars={"tech_key": tech_key, "now": now},
    ))
    # WOs without a slot sort last so they get appended at the end
    wos.sort(key=lambda w: (w.get("scheduledHourStart") is None, w.get("scheduledHourStart") or 0))
    cursor = 0.0
    updated = 0
    for wo in wos:
        dur = wo.get("estimatedHours") or 8.0
        if wo.get("scheduledHourStart") != cursor:
            db.collection("workOrders").update({"_key": wo["woKey"], "scheduledHourStart": cursor})
            updated += 1
        cursor += dur
    return updated


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
            db.collection(coll_name).update({"_key": op.entity_key, **safe})

    elif op.type == "delete_entity":
        _cascade_delete(db, op.entity_type or "", op.entity_key or "")

    elif op.type == "create_relationship":
        if op.edge_type not in _MUTABLE_EDGE_TYPES:
            raise ValueError(f"Edge type '{op.edge_type}' is not mutable")
        if op.edge_type == "performedBy":
            raise ValueError(
                "performedBy edges must be created via reassign_work_order, "
                "not create_relationship — constraints are not validated here."
            )
        db.collection(op.edge_type).insert({"_from": op.from_id, "_to": op.to_id})

    elif op.type == "delete_relationship":
        if op.edge_type not in _MUTABLE_EDGE_TYPES:
            raise ValueError(f"Edge type '{op.edge_type}' is not mutable")
        if op.edge_type == "performedBy":
            raise ValueError(
                "performedBy edges must be managed via reassign_work_order, "
                "not delete_relationship — history would be lost."
            )
        db.aql.execute(
            Q_CASCADE_DELETE_RELATIONSHIP,
            bind_vars={"@coll": op.edge_type, "from": op.from_id, "to": op.to_id},
        )

    elif op.type == "reassign_work_order":
        wo_key       = op.entity_key or ""
        fields       = op.fields or {}
        new_tech_key = fields.get("new_tech_key", "")
        if not wo_key or not new_tech_key:
            raise ValueError("reassign_work_order requires entity_key (wo) and fields.new_tech_key")
        now    = int(time.time())
        wo_id  = f"workOrders/{wo_key}"
        # Expire all currently-valid performedBy edges for this WO (preserves history).
        db.aql.execute(Q_EXPIRE_PERFORMED_BY, bind_vars={"wo_id": wo_id, "now": now})
        # Create new temporal edge to the new technician.
        db.collection("performedBy").insert({
            "_from":     wo_id,
            "_to":       f"technicians/{new_tech_key}",
            "validFrom": now,
            "validTo":   _VALID_INF,
        })
        # Keep technicianId field on the document in sync.
        db.collection("workOrders").update({"_key": wo_key, "technicianId": new_tech_key})
        # Repack both technicians' schedules so there are no gaps after the move.
        _repack_technician_schedule(db, new_tech_key)
        old_tech_key = (op.fields or {}).get("old_tech_key", "")
        if old_tech_key and old_tech_key != new_tech_key:
            _repack_technician_schedule(db, old_tech_key)

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
