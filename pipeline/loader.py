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
    """Drop everything and create fresh collections + named graph."""
    if db.has_graph(GRAPH_NAME):
        db.delete_graph(GRAPH_NAME, drop_collections=False)

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
                    "tailNumber": {"type": "string",
                                   "description": "Aircraft registration e.g. G-ABCD, N12345"},
                    "model":      {"type": "string",
                                   "description": "Aircraft type e.g. Boeing 737-800, Airbus A320"},
                    "base":       {"type": "string",
                                   "description": "Home airport IATA code: LHR=London Heathrow, JFK=New York JFK, SIN=Singapore Changi, DXB=Dubai, FRA=Frankfurt"},
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
                    "description": {"type": "string",
                                    "description": "Work order task description"},
                    "status":      {"type": "string",
                                    "enum": ["open", "in_progress", "completed"],
                                    "description": "Current work order status"},
                    "priority":    {"type": "string",
                                    "enum": ["high", "medium", "low"],
                                    "description": "Maintenance priority level"},
                    "engineId":    {"type": "integer",
                                    "description": "Target engine identifier"},
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

    for coll_name, schema in schemas.items():
        if db.has_collection(coll_name):
            db.collection(coll_name).configure(schema=schema)

    applied = sum(1 for n in schemas if db.has_collection(n))
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
    coll.import_bulk(docs, on_duplicate="replace")
    coll.add_index({"type": "persistent", "fields": ["engineId", "cycle"], "unique": False})
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
            db.collection(key).import_bulk(docs, on_duplicate="replace")
        print(f"  {key:20s}: {len(docs):>6} items")


def main() -> None:
    _assert_safe_db()
    t0 = time.monotonic()
    db = get_db()

    print("Resetting collections and graph …")
    _reset_collections(db)

    print("Applying collection schemas …")
    _apply_collection_schemas(db)

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
