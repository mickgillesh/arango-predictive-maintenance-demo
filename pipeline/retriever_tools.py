"""
Custom Retriever tool definitions for the AeroFleet AutoGraph / AutoRAG demo.

The AI Suite Retriever's "Custom Retriever" mode (query_type 4) runs tools
that are stored as documents in a Tools collection. Each tool names a
collection to search (lexical / semantic / hybrid) and an AQL template that
expands the hits. Tools are described in natural language, so Deep Search
(query_type 3) can choose between them automatically.

Four tools are defined, two per graph:

  Unstructured — AutoGraph-generated graph built from corpus/
    aerofleet_techdocs_v2       hybrid search on chunks -> source document
    aerofleet_kg_entities_v1    hybrid search on entities -> chunks + related entities

  Structured — AeroFleet fleet graph loaded by pipeline/loader.py
    aerofleet_fleet_engine_v2   engines matching a model / fault description
    aerofleet_fleet_status_v5   critical and warning engines

No partition_id tagging: earlier versions of this demo copied the AutoGraph
partition id onto every engine document so the Retriever's partition filter
would not drop structured results. That workaround is removed; notebook §7
verifies the fleet tools return engines without it (see ENABLEMENT.md).

All AQL lives in backend/aql.py.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from backend.aql import Q_TOOL_FLEET_EXPAND, Q_TOOL_KG_ENTITIES, Q_TOOL_TECHDOCS

TECHDOCS_TOOL_ID = "aerofleet_techdocs_v2"
KG_TOOL_ID = "aerofleet_kg_entities_v1"
FLEET_ENGINE_TOOL_ID = "aerofleet_fleet_engine_v2"
FLEET_STATUS_TOOL_ID = "aerofleet_fleet_status_v5"

# Bind parameters the Retriever always supplies (verified on the platform,
# 2026-11-11). Every template must reference all of them, or ArangoDB rejects the
# query and the Retriever silently falls back to the plain search hits. Tools
# must not give them defaults in bind_params.
RETRIEVER_BIND_PARAMS = {"nodes", "@relations_collection", "top_k"}

_SUFFIXES = ("Chunks", "Documents", "Relations", "Entities")


@dataclass(frozen=True)
class AutographCollections:
    """Names of the collections AutoGraph created for one project."""

    chunks: str
    documents: str
    relations: str
    entities: str | None = None


def default_tools_collection() -> str:
    """Tools collection the Retriever reads: ``TOOLS_COLLECTION`` env var, else ``Tools``.

    Verified on the platform (2026-11-11): the Retriever reads the database-wide
    ``Tools`` collection, not ``{project}_Tools`` as the docs say. It is shared
    by every project in the database; tool ids are unique, so registering
    upserts them against the current project's AutoGraph collections.
    """
    return os.environ.get("TOOLS_COLLECTION", "").strip() or "Tools"


def discover_autograph_collections(
    collection_names: list[str], project: str
) -> AutographCollections:
    """Find the AutoGraph collections for ``project`` among ``collection_names``.

    Prefers the exact ``{project}_<Suffix>`` name. If AutoGraph created several
    candidates (for example one set per partition), raises ``ValueError``
    listing them so the caller can choose explicitly instead of guessing.
    """
    found: dict[str, str | None] = {}
    for suffix in _SUFFIXES:
        exact = f"{project}_{suffix}"
        if exact in collection_names:
            found[suffix] = exact
            continue
        candidates = sorted(
            n for n in collection_names if n.startswith(f"{project}_") and n.endswith(suffix)
        )
        if len(candidates) > 1:
            raise ValueError(
                f"Several AutoGraph {suffix} collections for project {project!r}: "
                f"{candidates}. Pass AutographCollections(...) explicitly."
            )
        found[suffix] = candidates[0] if candidates else None

    missing = [s for s in ("Chunks", "Documents", "Relations") if not found[s]]
    if missing:
        raise ValueError(
            f"AutoGraph collections not found for project {project!r}: missing {missing}. "
            "Has ingestion finished? Existing collections with this prefix: "
            f"{sorted(n for n in collection_names if n.startswith(project))}"
        )
    return AutographCollections(
        chunks=found["Chunks"],  # type: ignore[arg-type]
        documents=found["Documents"],  # type: ignore[arg-type]
        relations=found["Relations"],  # type: ignore[arg-type]
        entities=found["Entities"],
    )


def template_bind_params(template: str) -> set[str]:
    """Return the bind parameter names used in an AQL template (``@@x`` -> ``@x``)."""
    return {("@" + m.group(2)) if m.group(1) else m.group(2)
            for m in re.finditer(r"(?<![\w@])@(@?)(\w+)", template)}


def _tool(tool_id: str, description: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"_key": tool_id, "tool_id": tool_id, "tool_type": "custom_retriever",
            "description": description, "config": config}


def build_document_tools(ag: AutographCollections) -> list[dict[str, Any]]:
    """Tools over the AutoGraph-generated graph (unstructured documents)."""
    tools = [
        _tool(
            TECHDOCS_TOOL_ID,
            "Searches the operator's aircraft engine maintenance documentation — "
            "Airworthiness Directives (ADs), Service Bulletins (SBs) and Engine "
            "Maintenance Manual (AMM) task cards — for inspection requirements, "
            "compliance intervals, limits, required parts and tools, and procedures "
            "for an engine model (CFM56-7B27, CFM56-5B4, V2527-A5, PW4077D) and "
            "subsystem (fan, LPC, HPC, combustor, HPT, LPT). Use for 'what does "
            "the documentation require' questions. Does not know which specific "
            "engines in the fleet are degraded.",
            {
                "collection": ag.chunks,
                "search_modes": [
                    {"type": "lexical", "field": "content", "weight": 0.35},
                    {"type": "semantic", "field": "embedding", "weight": 0.65},
                ],
                "top_k": 8,
                "show_citations": True,
                "response_instructions": (
                    "For each relevant document give: document number, title, issuer, "
                    "applicable engine models and subsystems, compliance interval, and the "
                    "key mandatory or recommended actions and limits. Mandatory (AD) "
                    "requirements first. Say when a document supersedes another."
                ),
                "aql_config": {
                    "template": Q_TOOL_TECHDOCS.strip(),
                    "relations_collection": ag.relations,
                    "bind_params": {},
                },
            },
        )
    ]
    if ag.entities:
        tools.append(_tool(
            KG_TOOL_ID,
            "Explores the knowledge graph AutoGraph extracted from the maintenance "
            "documentation: entities such as directives, engine models, subsystems, "
            "parts, tools, sensors and failure modes, the passages that mention them "
            "and the entities they are related to. Use for 'how is X related to Y' or "
            "'what is connected to X' questions about maintenance concepts.",
            {
                "collection": ag.entities,
                "search_modes": [
                    {"type": "lexical", "field": "entity_name", "weight": 0.3},
                    {"type": "semantic", "field": "embedding", "weight": 0.7},
                ],
                "top_k": 15,
                "show_citations": True,
                "response_instructions": (
                    "Explain the relevant entities and how they are related, citing the "
                    "source passages."
                ),
                "aql_config": {
                    "template": Q_TOOL_KG_ENTITIES.strip(),
                    "relations_collection": ag.relations,
                    "bind_params": {"max_chunks": 3, "max_related": 8},
                },
            },
        ))
    return tools


def build_fleet_tools() -> list[dict[str, Any]]:
    """Tools over the structured AeroFleet fleet graph."""
    expand = Q_TOOL_FLEET_EXPAND.strip()
    return [
        _tool(
            FLEET_ENGINE_TOOL_ID,
            "Searches the AeroFleet structured fleet graph for engines of a SPECIFIC "
            "engine model (CFM56-7B27, CFM56-5B4, V2527-A5, PW4077D) or matching a "
            "fault description, using hybrid search over engine model and technician "
            "notes. Returns each engine's risk, predicted remaining useful life, "
            "aircraft and base, degrading subsystems, required parts with stock "
            "(blocking when zero), certified technicians at the base and recent work "
            "orders. Use when the question names a model or a fault. For overall fleet "
            "health use aerofleet_fleet_status_v5.",
            {
                "collection": "engines",
                "search_modes": [
                    {"type": "lexical", "field": "model", "weight": 0.2},
                    {"type": "semantic", "field": "notesEmbedding", "weight": 0.8},
                ],
                "top_k": 8,
                "show_citations": False,
                "response_instructions": (
                    "Summarise each engine: id, model, risk bucket, predicted RUL, aircraft "
                    "tail and base, degrading subsystems, blocking parts, technicians at the "
                    "same base, recent work orders."
                ),
                # Structured data is not in any AutoGraph partition; with automatic
                # partition selection the tool retrieves nothing (verified 2026-11-11).
                "auto_select_partitions": False,
                "aql_config": {
                    "template": expand,
                    "relations_collection": "installedOn",
                    "bind_params": {"degraded_only": False, "max_parts": 10,
                                    "max_techs": 5, "max_wos": 3},
                },
            },
        ),
        _tool(
            FLEET_STATUS_TOOL_ID,
            "Returns engines in CRITICAL or WARNING health state from the AeroFleet "
            "structured fleet graph, most urgent first. Use for questions about overall "
            "fleet health, which engines need attention, maintenance planning, blocking "
            "parts or resource prioritisation. Phrase the search with the words "
            "'critical' or 'warning'. Returns each engine's aircraft, degrading "
            "subsystems, blocking parts, certified technicians at the base and recent "
            "work orders.",
            {
                "collection": "engines",
                # Lexical match on the riskBucket value ("critical" / "warning");
                # the AQL also drops healthy engines via @degraded_only.
                "search_modes": [
                    {"type": "lexical", "field": "riskBucket", "weight": 1.0},
                ],
                "top_k": 10,
                "show_citations": False,
                "response_instructions": (
                    "List engines most critical first: id, model, risk bucket, predicted "
                    "RUL, aircraft and base, degrading subsystems, blocking parts (stock 0), "
                    "technicians at the same base. End with an overall fleet risk summary."
                ),
                # Structured data is not in any AutoGraph partition; with automatic
                # partition selection the tool retrieves nothing (verified 2026-11-11).
                "auto_select_partitions": False,
                "aql_config": {
                    "template": expand,
                    "relations_collection": "installedOn",
                    "bind_params": {"degraded_only": True, "max_parts": 5,
                                    "max_techs": 3, "max_wos": 2},
                },
            },
        ),
    ]


def build_tools(ag: AutographCollections | None) -> list[dict[str, Any]]:
    """All tools: fleet tools always, document tools when AutoGraph has run."""
    return (build_document_tools(ag) if ag else []) + build_fleet_tools()


def register_tools(db: Any, tools_collection: str, tools: list[dict[str, Any]]) -> list[str]:
    """Upsert ``tools`` into ``tools_collection`` (created if absent). Returns tool ids."""
    if not db.has_collection(tools_collection):
        db.create_collection(tools_collection)
    coll = db.collection(tools_collection)
    for tool in tools:
        if coll.has(tool["_key"]):
            coll.replace(tool)
        else:
            coll.insert(tool)
    return [t["tool_id"] for t in tools]
