"""
Idempotent loader for AeroFleet.

`make load` → drops and recreates all collections, then bulk-inserts
C-MAPSS readings and the synthetic operational graph.

Safety guard: refuses to run unless ARANGO_DB contains "predictive-maintenance"
(prevents accidental wipe of an unrelated database).
"""
import os
import sys
import time

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from backend.db import get_db  # noqa: E402
from pipeline.download_data import ensure_fd001  # noqa: E402
from pipeline.synthetic_graph import generate_all  # noqa: E402

_SAFE_DB_PATTERN = "predictive-maintenance"

VERTEX_COLLECTIONS = [
    "engines", "aircraft", "subsystems", "sensors",
    "parts", "technicians", "workOrders",
    "readings",  # telemetry — excluded from the named graph
]
EDGE_COLLECTIONS = [
    "installedOn", "partOf", "monitors", "requiredBy",
    "certifiedFor", "maintains", "performedBy", "consumed",
]
GRAPH_NAME = "fleetGraph"

# readings is telemetry only — not part of the operational named graph
_GRAPH_EDGE_DEFS = [
    {
        "edge_collection": "installedOn",
        "from_vertex_collections": ["engines"],
        "to_vertex_collections": ["aircraft"],
    },
    {
        "edge_collection": "partOf",
        "from_vertex_collections": ["subsystems"],
        "to_vertex_collections": ["engines"],
    },
    {
        "edge_collection": "monitors",
        "from_vertex_collections": ["sensors"],
        "to_vertex_collections": ["subsystems"],
    },
    {
        "edge_collection": "requiredBy",
        "from_vertex_collections": ["parts"],
        "to_vertex_collections": ["subsystems"],
    },
    {
        "edge_collection": "certifiedFor",
        "from_vertex_collections": ["technicians"],
        "to_vertex_collections": ["subsystems"],
    },
    {
        "edge_collection": "maintains",
        "from_vertex_collections": ["workOrders"],
        "to_vertex_collections": ["engines"],
    },
    {
        "edge_collection": "performedBy",
        "from_vertex_collections": ["workOrders"],
        "to_vertex_collections": ["technicians"],
    },
    {
        "edge_collection": "consumed",
        "from_vertex_collections": ["workOrders"],
        "to_vertex_collections": ["parts"],
    },
]


def _assert_safe_db() -> None:
    db_name = os.environ.get("ARANGO_DB", "")
    if _SAFE_DB_PATTERN not in db_name:
        sys.exit(
            f"ERROR: ARANGO_DB='{db_name}' does not contain '{_SAFE_DB_PATTERN}'. "
            "Refusing to run destructive loader against an unrecognised database."
        )


def _reset_collections(db) -> None:
    """Drop everything and create fresh collections + named graph.

    ArangoDB Cloud may disallow graph deletion (HTTP 403 / ERR 1004).
    When that happens we fall back to truncating every collection so the
    loader can safely re-insert — the graph definition is preserved as-is.
    """
    graph_existed = db.has_graph(GRAPH_NAME)
    deleted_graph = False
    if graph_existed:
        try:
            db.delete_graph(GRAPH_NAME, drop_collections=False)
            deleted_graph = True
        except Exception:
            # ArangoDB Cloud may block graph/collection management calls (HTTP 403).
            # Fall back to AQL REMOVE per-collection; skip any that are read-only
            # (structural edges like partOf/monitors/requiredBy are deterministic —
            # same seed produces identical data, so stale copies are correct copies).
            print("  (graph deletion restricted; clearing collections via AQL)")
            skipped: list[str] = []
            for name in EDGE_COLLECTIONS + VERTEX_COLLECTIONS:
                if db.has_collection(name):
                    try:
                        db.aql.execute(f"FOR doc IN {name} REMOVE doc IN {name}")
                    except Exception:
                        skipped.append(name)
            if skipped:
                print(f"  (skipped read-only collections: {', '.join(skipped)})")
            return

    for name in EDGE_COLLECTIONS + VERTEX_COLLECTIONS:
        if db.has_collection(name):
            db.delete_collection(name)

    for name in VERTEX_COLLECTIONS:
        db.create_collection(name)
    for name in EDGE_COLLECTIONS:
        db.create_collection(name, edge=True)

    db.create_graph(GRAPH_NAME, edge_definitions=_GRAPH_EDGE_DEFS)


def _apply_collection_schemas(db) -> None:
    """Attach JSON Schema metadata to each vertex collection.

    level='none' stores the schema for service introspection only — no validation
    is enforced, so there is zero impact on inserts or existing documents.
    The named graph edge definitions (fleetGraph) already encode the edge ontology;
    this function adds property-level semantics for each vertex collection.
    """
    schemas: dict[str, dict] = {
        "engines": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Turbofan engine with real-time health scoring derived from "
                    "NASA C-MAPSS sensor telemetry. One document per engine (100 total). "
                    "Connected to aircraft via installedOn (OUTBOUND from engine)."
                ),
                "type": "object",
                "properties": {
                    "engineId":         {"type": "integer",
                                         "description": "Unique engine number 1-100"},
                    "model":            {"type": "string",
                                         "description": "Engine model e.g. CFM56-7B27, V2527-A5"},
                    "healthIndex":      {"type": "number",
                                         "description": "Degradation score: 0.0=new, 1.0=end-of-life"},
                    "predictedRUL":     {"type": "integer",
                                         "description": "Predicted remaining useful life in flight cycles; LOWER = more urgent"},
                    "riskScore":        {"type": "number",
                                         "description": "Composite risk score 0-1"},
                    "riskBucket":       {"type": "string",
                                         "enum": ["critical", "warning", "healthy"],
                                         "description": "Risk tier: critical=lowest RUL, warning=mid, healthy=above threshold"},
                    "driverSensors":    {"type": "array", "items": {"type": "string"},
                                         "description": "Sensor IDs (s1-s21) with highest degradation drift"},
                    "driverSubsystems": {"type": "array", "items": {"type": "string"},
                                         "description": "Subsystem names (fan/LPC/HPC/combustor/HPT/LPT) mapped from driverSensors"},
                    "entryIntoService": {"type": "string",
                                         "description": "ISO date string when engine entered service"},
                    "scoringMethod":    {"type": "string",
                                         "description": "Scoring algorithm identifier"},
                },
            },
        },
        "readings": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "NASA C-MAPSS FD001 sensor telemetry — one row per engine per flight "
                    "cycle. Filter by engineId and/or cycle range. s1-s21 are sensor "
                    "channels; s1/s5/s6/s10/s16/s18/s19 are near-constant and less "
                    "informative for degradation analysis."
                ),
                "type": "object",
                "properties": {
                    "engineId": {"type": "integer",
                                  "description": "Engine identifier — matches engines._key cast to integer"},
                    "cycle":    {"type": "integer",
                                  "description": "Flight cycle number, 1-based, ascending until engine failure"},
                    "s2":  {"type": "number", "description": "T24 — LPC outlet temperature (deg R)"},
                    "s3":  {"type": "number", "description": "T30 — HPC outlet temperature (deg R)"},
                    "s4":  {"type": "number", "description": "T50 — LPT outlet temperature (deg R)"},
                    "s7":  {"type": "number", "description": "P30 — HPC outlet pressure (psia)"},
                    "s11": {"type": "number", "description": "Ps30 — static pressure at HPC outlet (psia)"},
                    "s12": {"type": "number", "description": "phi — fuel-flow to Ps30 ratio (combustor load)"},
                    "s20": {"type": "number", "description": "W31 — HPT coolant bleed flow"},
                    "s21": {"type": "number", "description": "W32 — LPT coolant bleed flow"},
                },
            },
        },
        "aircraft": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Aircraft carrying turbofan engines. Engines point TO aircraft "
                    "via the installedOn edge (traverse OUTBOUND from an engine vertex)."
                ),
                "type": "object",
                "properties": {
                    "tailNumber":    {"type": "string",
                                      "description": "Aircraft registration e.g. G-ABCD, N12345"},
                    "model":         {"type": "string",
                                      "description": "Aircraft type e.g. Boeing 737-800, Airbus A320"},
                    "base":          {"type": "string",
                                      "description": "Home airport IATA code: LHR=London Heathrow, JFK=New York JFK, SIN=Singapore Changi, DXB=Dubai, FRA=Frankfurt"},
                    "flightsPerDay": {"type": "integer",
                                      "description": "Average revenue flights per day for this airframe (1-3)"},
                },
            },
        },
        "subsystems": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Engine subsystem instances — exactly 6 per engine (one per type). "
                    "Subsystems point TO engines via partOf. "
                    "Parts point TO subsystems via requiredBy. "
                    "Technicians point TO subsystems they are certified for via certifiedFor."
                ),
                "type": "object",
                "properties": {
                    "name":     {"type": "string",
                                  "enum": ["fan", "LPC", "HPC", "combustor", "HPT", "LPT"],
                                  "description": "Subsystem type: fan=fan section, LPC=low-pressure compressor, HPC=high-pressure compressor, combustor=combustion chamber, HPT=high-pressure turbine, LPT=low-pressure turbine"},
                    "engineId": {"type": "integer",
                                  "description": "Parent engine identifier"},
                },
            },
        },
        "parts": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Spare parts catalogue (~40 entries). Parts point TO subsystems via "
                    "requiredBy — each part links to every subsystem instance of its type. "
                    "stockLevel==0 means out of stock and blocks maintenance."
                ),
                "type": "object",
                "properties": {
                    "partNumber":    {"type": "string",
                                      "description": "Manufacturer part number"},
                    "name":          {"type": "string",
                                      "description": "Human-readable part name"},
                    "subsystemType": {"type": "string",
                                      "enum": ["fan", "LPC", "HPC", "combustor", "HPT", "LPT"],
                                      "description": "Subsystem type this part services"},
                    "stockLevel":    {"type": "integer",
                                      "description": "Units in stock; 0=out of stock (blocks maintenance)"},
                    "leadTimeDays":  {"type": "integer",
                                      "description": "Procurement lead time in calendar days"},
                },
            },
        },
        "technicians": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Maintenance technicians. certifiedFor edges are scoped to the "
                    "technician's homeBase only — not fleet-wide. To find available "
                    "technicians for an engine, match tech.homeBase == aircraft.base."
                ),
                "type": "object",
                "properties": {
                    "name":           {"type": "string",
                                       "description": "Technician full name"},
                    "homeBase":       {"type": "string",
                                       "description": "Home airport IATA code: LHR/JFK/SIN/DXB/FRA"},
                    "certifications": {"type": "array", "items": {"type": "string"},
                                       "description": "Subsystem types this technician is licensed to maintain"},
                },
            },
        },
        "workOrders": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": (
                    "Maintenance work orders. Linked to engines via maintains, "
                    "to technicians via performedBy, to consumed parts via consumed."
                ),
                "type": "object",
                "properties": {
                    "description":        {"type": "string",
                                           "description": "Work order task description"},
                    "status":             {"type": "string",
                                           "enum": ["closed", "open", "pending-parts"],
                                           "description": "Work order status: closed=historical completed order, open=ready to action, pending-parts=waiting on procurement work order"},
                    "generatedByPlanner": {"type": "boolean",
                                           "description": "True for AI-generated planner work orders; field absent on all historical closed orders"},
                    "type":               {"type": "string",
                                           "enum": ["maintenance", "procurement"],
                                           "description": "maintenance=hands-on engine work; procurement=ordering out-of-stock parts"},
                    "engineId":           {"type": "string",
                                           "description": "Target engine _key"},
                    "technicianId":       {"type": "string",
                                           "description": "Assigned technician _key"},
                    "deadline":           {"type": "string",
                                           "description": "ISO date by which the work order must be completed"},
                    "riskBucket":         {"type": "string",
                                           "description": "Risk tier inherited from the engine at plan time"},
                    "createdAt":          {"type": "string",
                                           "description": "ISO datetime when this work order was created by the planner"},
                },
            },
        },
        "airports": {
            "level": "none",
            "rule": {
                "$schema": "http://json-schema.org/draft-07/schema",
                "description": "Reference table for the 5 hub airports used as technician and aircraft bases.",
                "type": "object",
                "properties": {
                    "iata":    {"type": "string",
                                "description": "IATA airport code: LHR/JFK/SIN/DXB/FRA"},
                    "city":    {"type": "string", "description": "City name"},
                    "country": {"type": "string", "description": "Country name"},
                },
            },
        },
    }

    applied = 0
    for coll_name, schema in schemas.items():
        if db.has_collection(coll_name):
            try:
                db.collection(coll_name).configure(schema=schema)
                applied += 1
            except Exception:
                pass  # cloud may restrict collection config; schema is metadata-only anyway
    print(f"  Applied JSON Schema metadata to {applied} collections")


def _load_readings(db, fd001_path) -> int:
    cols = ["engineId", "cycle", "op1", "op2", "op3"] + [f"s{i}" for i in range(1, 22)]
    df = pd.read_csv(fd001_path, sep=r"\s+", header=None, names=cols)

    docs = [
        {
            "engineId": int(row.engineId),
            "cycle": int(row.cycle),
            "op1": float(row.op1),
            "op2": float(row.op2),
            "op3": float(row.op3),
            **{f"s{n}": float(getattr(row, f"s{n}")) for n in range(1, 22)},
        }
        for row in df.itertuples(index=False, name="Row")
    ]

    coll = db.collection("readings")
    try:
        coll.import_bulk(docs, on_duplicate="replace")
        try:
            coll.add_index({"type": "persistent", "fields": ["engineId", "cycle"], "unique": False})
        except Exception:
            pass  # index may already exist
    except Exception:
        print("  (readings write restricted — telemetry already loaded, skipping)")
        return 0
    return len(docs)


def _load_graph(db, data: dict[str, list[dict]]) -> None:
    order = [
        "engines", "aircraft", "subsystems", "sensors",
        "parts", "technicians", "workOrders",
        "installedOn", "partOf", "monitors", "requiredBy",
        "certifiedFor", "maintains", "performedBy", "consumed",
    ]
    for key in order:
        docs = data.get(key, [])
        if docs:
            try:
                db.collection(key).import_bulk(docs, on_duplicate="replace")
                print(f"  {key:20s}: {len(docs):>6} items")
            except Exception as exc:
                print(f"  {key:20s}: SKIPPED (read-only: {exc})")
        else:
            print(f"  {key:20s}:      0 items")


def _apply_ontology(db) -> None:
    """Populate ontologyNodes and ontologyEdges for the chat planning agent.

    These collections are NOT part of fleetGraph — they are metadata only.
    Truncated and re-inserted on every load for idempotency.
    """
    for name in ["ontologyNodes", "ontologyEdges"]:
        if not db.has_collection(name):
            try:
                db.create_collection(name)
            except Exception:
                pass
        else:
            try:
                db.aql.execute(f"FOR doc IN {name} REMOVE doc IN {name}")
            except Exception:
                pass  # will be overwritten by import_bulk with on_duplicate="replace"

    nodes = [
        {
            "_key": "engine",
            "label": "Engine",
            "collection": "engines",
            "allowedOps": ["create", "read", "update", "delete"],
            "editableFields": ["model", "riskBucket", "predictedRUL"],
            "cascadeOnDelete": [
                "installedOn edge from this engine to its aircraft",
                "all planner work orders targeting this engine and their edges",
            ],
            "constraints": [
                "Adding an engine: call propose_create_entity then propose_create_relationship"
                " to link it to an aircraft via installedOn",
            ],
        },
        {
            "_key": "aircraft",
            "label": "Aircraft",
            "collection": "aircraft",
            "allowedOps": ["create", "read", "update", "delete"],
            "editableFields": ["tailNumber", "base", "flightsPerDay"],
            "cascadeOnDelete": [
                "all engines installed on this aircraft (full engine cascade per engine)",
                "installedOn edges to this aircraft",
            ],
            "constraints": [
                "Changing base changes which technicians are eligible — existing planner WO"
                " assignments may become invalid",
            ],
        },
        {
            "_key": "technician",
            "label": "Technician",
            "collection": "technicians",
            "allowedOps": ["create", "read", "update", "delete"],
            "editableFields": ["name", "homeBase"],
            "cascadeOnDelete": [
                "certifiedFor edges from this technician to subsystems",
                "performedBy edges on planner work orders assigned to this technician"
                " (WOs are kept but unassigned — not deleted)",
            ],
            "constraints": [
                "Changing homeBase may invalidate existing WO assignments:"
                " technicians can only work at their homeBase airport",
            ],
        },
        {
            "_key": "part",
            "label": "Part",
            "collection": "parts",
            "allowedOps": ["create", "read", "update", "delete"],
            "editableFields": ["name", "stockLevel", "leadTimeDays"],
            "cascadeOnDelete": [
                "requiredBy edges from this part to subsystems",
                "consumed edges where this part is consumed by a work order",
            ],
            "constraints": [
                "stockLevel must be >= 0",
                "leadTimeDays must be >= 0",
            ],
        },
        {
            "_key": "workOrder",
            "label": "Work Order",
            "collection": "workOrders",
            "allowedOps": ["create", "read", "update", "delete"],
            "editableFields": ["status", "deadline", "description"],
            "cascadeOnDelete": [
                "maintains edge from this work order to its engine",
                "performedBy edge from this work order to its technician",
                "consumed edges from this work order to parts",
            ],
            "constraints": [
                "Only generatedByPlanner==true documents may be updated or deleted via this agent",
                "Historical work orders (status='closed') must not be modified",
            ],
        },
        {
            "_key": "subsystem",
            "label": "Subsystem",
            "collection": "subsystems",
            "allowedOps": ["read", "update"],
            "editableFields": ["name"],
            "cascadeOnDelete": [],
            "constraints": [
                "Subsystems are structural components of engines — deletion not supported in this demo",
            ],
        },
    ]

    edges = [
        {
            "_key": "installedOn",
            "label": "Engine installed on aircraft",
            "edgeCollection": "installedOn",
            "fromType": "engine",
            "toType": "aircraft",
            "allowedOps": ["create", "delete"],
            "constraints": ["An engine should be installed on at most one aircraft at a time"],
        },
        {
            "_key": "performedBy",
            "label": "Work order assigned to technician",
            "edgeCollection": "performedBy",
            "fromType": "workOrder",
            "toType": "technician",
            "allowedOps": ["create", "delete"],
            "constraints": ["Technician homeBase must match the engine's aircraft base"],
        },
        {
            "_key": "certifiedFor",
            "label": "Technician certified for subsystem type",
            "edgeCollection": "certifiedFor",
            "fromType": "technician",
            "toType": "subsystem",
            "allowedOps": ["create", "delete"],
            "constraints": ["The subsystem must exist in the subsystems collection"],
        },
        {
            "_key": "maintains",
            "label": "Work order targets engine",
            "edgeCollection": "maintains",
            "fromType": "workOrder",
            "toType": "engine",
            "allowedOps": ["create", "delete"],
            "constraints": [],
        },
        {
            "_key": "consumed",
            "label": "Work order consumes part",
            "edgeCollection": "consumed",
            "fromType": "workOrder",
            "toType": "part",
            "allowedOps": ["create", "delete"],
            "constraints": [],
        },
        {
            "_key": "partOf",
            "label": "Subsystem is part of engine",
            "edgeCollection": "partOf",
            "fromType": "subsystem",
            "toType": "engine",
            "allowedOps": ["read"],
            "constraints": ["Read-only — structural edge, not modifiable via agent"],
        },
        {
            "_key": "requiredBy",
            "label": "Part required by subsystem",
            "edgeCollection": "requiredBy",
            "fromType": "part",
            "toType": "subsystem",
            "allowedOps": ["read"],
            "constraints": ["Read-only — structural edge, not modifiable via agent"],
        },
        {
            "_key": "monitors",
            "label": "Sensor monitors subsystem",
            "edgeCollection": "monitors",
            "fromType": "sensor",
            "toType": "subsystem",
            "allowedOps": ["read"],
            "constraints": ["Read-only — structural edge, not modifiable via agent"],
        },
    ]

    for coll_name, docs in [("ontologyNodes", nodes), ("ontologyEdges", edges)]:
        try:
            db.collection(coll_name).import_bulk(docs, on_duplicate="replace")
        except Exception:
            # Cloud may restrict writes to previously-created metadata collections.
            # If they already exist with correct data, this is safe to skip.
            print(f"  (ontology {coll_name} write restricted — existing data retained)")


def main() -> None:
    _assert_safe_db()
    t0 = time.monotonic()
    db = get_db()

    print("Resetting collections and graph …")
    _reset_collections(db)

    print("Applying collection schemas …")
    _apply_collection_schemas(db)

    print("Applying ontology …")
    _apply_ontology(db)

    print("Ensuring C-MAPSS FD001 data …")
    fd001_path = ensure_fd001()

    print("Loading readings …")
    n = _load_readings(db, fd001_path)
    print(f"  readings             : {n:>6} documents")

    print("Generating synthetic graph …")
    data = generate_all()

    print("Loading synthetic graph …")
    _load_graph(db, data)

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
