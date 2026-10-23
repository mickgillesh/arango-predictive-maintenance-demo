"""
Insert both AeroFleet retriever tool documents into ArangoDB.

This only needs to be run ONCE (and again whenever you want to update the tools).

Two tools are created:

  aerofleet_techdocs_v1     — searches unstructured Airworthiness Directives and
                               Service Bulletins ingested via Autograph (Autograph
                               project name: technicians_documentation).

  aerofleet_fleet_graph_v1  — searches the structured AeroFleet fleet graph
                               (engines, aircraft, subsystems, parts, technicians,
                               work orders). Demonstrates Custom Retriever on
                               pre-existing, non-GraphRAG structured data.

Both tools live in technicians_documentation_Tools so they are accessible from the same
retriever service endpoint and can be used together in Deep Search (query_type=3).

Usage:
    uv run python scripts/setup_retriever.py

Required env vars (same as the main app):
    ARANGO_URL, ARANGO_DB, ARANGO_USER, ARANGO_PASSWORD

Optional env vars:
    RETRIEVER_TOOL_ID  — _key for the tech-docs tool (default: aerofleet_techdocs_v1)
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from arango import ArangoClient  # noqa: E402


# Autograph collections (created by Autograph project "technicians_documentation")
CHUNKS_COLL    = "technicians_documentation_Chunks"
DOCS_COLL      = "technicians_documentation_Documents"
RELATIONS_COLL = "technicians_documentation_Relations"
TOOLS_COLL     = "technicians_documentation_Tools"

# Main AeroFleet collections (created by pipeline/loader.py)
FLEET_GRAPH_COLLECTIONS = ("engines", "installedOn", "partOf", "requiredBy",
                            "certifiedFor", "workOrders")

FLEET_TOOL_ID = "aerofleet_fleet_graph_v1"


def _upsert(coll, doc: dict) -> None:
    key = doc["_key"]
    if coll.has(key):
        coll.replace(doc)
        print(f"  Updated  '{key}' in '{coll.name}'.")
    else:
        coll.insert(doc)
        print(f"  Inserted '{key}' into '{coll.name}'.")


def main() -> None:
    arango_url  = os.environ["ARANGO_URL"]
    arango_db   = os.environ["ARANGO_DB"]
    arango_user = os.environ["ARANGO_USER"]
    arango_pass = os.environ["ARANGO_PASSWORD"]
    techdocs_tool_id = os.environ.get("RETRIEVER_TOOL_ID", "aerofleet_techdocs_v1").strip()

    client = ArangoClient(hosts=arango_url)
    db = client.db(arango_db, username=arango_user, password=arango_pass)

    existing = {c["name"] for c in db.collections()}

    # ── Prerequisite checks ───────────────────────────────────────────────────
    missing_autograph = [c for c in (CHUNKS_COLL, DOCS_COLL, RELATIONS_COLL)
                         if c not in existing]
    if missing_autograph:
        print("WARNING: Autograph collections not found (tech-docs tool will be skipped):")
        for c in missing_autograph:
            print(f"  - {c}")
        print("  Ensure Autograph ingestion completed with project name 'technicians_documentation'.")
        skip_techdocs = True
    else:
        skip_techdocs = False

    missing_fleet = [c for c in FLEET_GRAPH_COLLECTIONS if c not in existing]
    if missing_fleet:
        print("WARNING: Fleet graph collections not found (fleet-graph tool will be skipped):")
        for c in missing_fleet:
            print(f"  - {c}")
        print("  Run `make load` to populate the fleet graph first.")
        skip_fleet = True
    else:
        skip_fleet = False

    if skip_techdocs and skip_fleet:
        print("\nERROR: No tools can be created — both prerequisite checks failed.")
        sys.exit(1)

    # ── Ensure Tools collection exists ────────────────────────────────────────
    if TOOLS_COLL not in existing:
        print(f"Creating tools collection: {TOOLS_COLL}")
        db.create_collection(TOOLS_COLL)

    coll = db.collection(TOOLS_COLL)

    # ── Tool 1: Tech Docs (unstructured Autograph docs) ───────────────────────
    if not skip_techdocs:
        print("\n[1/2] Tech-docs tool (Airworthiness Directives + Service Bulletins):")
        # The AQL template receives @nodes (list of chunk document IDs found by
        # hybrid search). For each chunk, traverse the PART_OF edge in
        # technicians_documentation_Relations to get the parent source document, which
        # holds the human-readable title and filename.
        techdocs_aql = (
            "LET nodes = @nodes\n"
            "FOR nodeId IN nodes\n"
            "  LET chunk = DOCUMENT(nodeId)\n"
            "  FILTER chunk != null\n"
            "  LET source_doc = FIRST(\n"
            "    FOR e IN @@relations_collection\n"
            "      FILTER e._from == chunk._id AND e.type == \"PART_OF\"\n"
            "      LET doc = DOCUMENT(e._to)\n"
            "      FILTER doc != null\n"
            "      RETURN doc\n"
            "  )\n"
            "  RETURN {\n"
            "    content:  chunk.content,\n"
            "    title:    source_doc.title,\n"
            "    filename: source_doc.filename,\n"
            "    doc_meta: source_doc\n"
            "  }"
        )

        techdocs_tool = {
            "_key":      techdocs_tool_id,
            "tool_id":   techdocs_tool_id,
            "tool_type": "custom_retriever",
            "description": (
                "Searches AeroFleet technical documentation — Airworthiness Directives (ADs) "
                "and Service Bulletins (SBs) — for maintenance requirements, inspection intervals, "
                "compliance obligations, and recommended actions related to turbofan engine subsystems "
                "including fan, LPC, HPC, combustor, HPT, and LPT. Use this tool when a technician "
                "needs to know what regulatory or manufacturer requirements apply to a specific engine "
                "model and subsystem maintenance task."
            ),
            "config": {
                "collection": CHUNKS_COLL,
                "search_modes": [
                    {"type": "lexical",  "field": "content",   "weight": 0.35},
                    {"type": "semantic", "field": "embedding",  "weight": 0.65},
                ],
                "top_k": 8,
                "show_citations": True,
                "response_instructions": (
                    "Return a structured list of relevant documents. For each document include: "
                    "document number, title, issuer, compliance interval, and key mandatory or "
                    "recommended actions. Prioritise mandatory (AD) requirements over recommendations (SB)."
                ),
                "aql_config": {
                    "template":             techdocs_aql,
                    "relations_collection": RELATIONS_COLL,
                    "bind_params":          {},
                },
            },
        }
        _upsert(coll, techdocs_tool)

    # ── Tool 2: Fleet Graph (structured AeroFleet operational data) ───────────
    if not skip_fleet:
        print("\n[2/2] Fleet-graph tool (structured engine / maintenance graph):")
        # Lexical search on engines.model (BM25 on tokenised model string).
        # The AQL template receives @nodes (list of matching engine document IDs)
        # and does a multi-hop graph traversal to return a rich operational
        # snapshot: aircraft, degrading subsystems, required parts (with blocking
        # flag), certified local technicians, and open work orders.
        #
        # Edge direction recap (from Q_ENGINE_IMPACT in aql.py):
        #   installedOn : engine  → aircraft   (OUTBOUND)
        #   partOf      : sub     → engine     (INBOUND  from engine)
        #   requiredBy  : part    → subsystem  (INBOUND  from subsystem)
        #   certifiedFor: tech    → subsystem  (INBOUND  from subsystem)
        fleet_aql = (
            "LET nodes = @nodes\n"
            "FOR engineId IN nodes\n"
            "  LET eng = DOCUMENT(engineId)\n"
            "  FILTER eng != null\n"
            "  LET ac = FIRST(FOR a IN 1..1 OUTBOUND eng installedOn RETURN a)\n"
            "  LET degradingSubs = (\n"
            "    FOR sub IN 1..1 INBOUND eng partOf\n"
            "      FILTER sub.name IN eng.driverSubsystems\n"
            "      RETURN sub\n"
            "  )\n"
            "  LET parts = (\n"
            "    FOR sub IN degradingSubs\n"
            "      FOR part IN 1..1 INBOUND sub requiredBy\n"
            "        LIMIT @max_parts\n"
            "        RETURN DISTINCT {\n"
            "          name:          part.name,\n"
            "          subsystemType: part.subsystemType,\n"
            "          stockLevel:    part.stockLevel,\n"
            "          leadTimeDays:  part.leadTimeDays,\n"
            "          blocking:      part.stockLevel == 0\n"
            "        }\n"
            "  )\n"
            "  LET techs = (\n"
            "    FOR sub IN degradingSubs\n"
            "      FOR tech IN 1..1 INBOUND sub certifiedFor\n"
            "        FILTER tech.homeBase == ac.base\n"
            "        LIMIT @max_techs\n"
            "        RETURN DISTINCT {\n"
            "          name:           tech.name,\n"
            "          homeBase:       tech.homeBase,\n"
            "          certifications: tech.certifications\n"
            "        }\n"
            "  )\n"
            "  LET open_wos = (\n"
            "    FOR wo IN workOrders\n"
            "      FILTER wo.engineId == eng._key\n"
            "        AND wo.generatedByPlanner == true\n"
            "        AND wo.status != \"complete\"\n"
            "      LIMIT @max_wos\n"
            "      RETURN { key: wo._key, type: wo.type, status: wo.status, deadline: wo.deadline }\n"
            "  )\n"
            "  RETURN {\n"
            "    engineId:            eng._key,\n"
            "    model:               eng.model,\n"
            "    riskBucket:          eng.riskBucket,\n"
            "    predictedRUL:        eng.predictedRUL,\n"
            "    healthIndex:         eng.healthIndex,\n"
            "    driverSubsystems:    eng.driverSubsystems,\n"
            "    aircraft:            { tailNumber: ac.tailNumber, base: ac.base },\n"
            "    degradingSubsystems: (FOR s IN degradingSubs RETURN s.name),\n"
            "    blockingParts:       (FOR p IN parts FILTER p.blocking == true RETURN p),\n"
            "    parts:               parts,\n"
            "    technicians:         techs,\n"
            "    openWorkOrders:      open_wos\n"
            "  }"
        )

        fleet_tool = {
            "_key":      FLEET_TOOL_ID,
            "tool_id":   FLEET_TOOL_ID,
            "tool_type": "custom_retriever",
            "description": (
                "Searches the AeroFleet structured fleet graph for engine health, maintenance "
                "status, parts availability, and technician assignments. Use this tool to find "
                "engines by model (e.g. CFM56-7B27, GE90-115B, Trent 700) or risk status, "
                "and retrieve their degrading subsystems, blocking parts, certified local "
                "technicians, and open work orders. Best used for operational queries about "
                "fleet readiness, resource availability, and scheduling context."
            ),
            "config": {
                "collection": "engines",
                "search_modes": [
                    {"type": "lexical", "field": "model", "weight": 1.0},
                ],
                "top_k": 5,
                "show_citations": False,
                "response_instructions": (
                    "Return a structured summary for each engine found. Include: engine ID, "
                    "model, risk status, predicted RUL, aircraft tail number and base, "
                    "degrading subsystems, any blocking parts (stock=0), available certified "
                    "technicians at the same base, and open work orders with their deadlines."
                ),
                "aql_config": {
                    "template":             fleet_aql,
                    "relations_collection": "installedOn",
                    "bind_params": {
                        "max_parts": 10,
                        "max_techs": 5,
                        "max_wos":   5,
                    },
                },
            },
        }
        _upsert(coll, fleet_tool)

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("Tools registered in:", TOOLS_COLL)
    if not skip_techdocs:
        print(f"  {techdocs_tool_id:40s}  — AD/SB unstructured docs (Autograph hybrid search)")
    if not skip_fleet:
        print(f"  {FLEET_TOOL_ID:40s}  — Fleet graph structured data (lexical + graph traversal)")
    print()
    print("Next steps:")
    print("  1. Set in .env.local:")
    print("       RETRIEVER_URL=https://<endpoint>:8529/graphrag/retriever/<service_id>/v1")
    if not skip_techdocs:
        print(f"       RETRIEVER_TOOL_ID={techdocs_tool_id}")
    print("  (Auth is automatic — ARANGO_USER / ARANGO_PASSWORD are reused to fetch a JWT)")
    print()
    print("  2. Demo query for the fleet-graph tool (curl or Postman):")
    print('       {"query": "CFM56-7B HPC degradation maintenance", "query_type": 4,')
    print(f'        "custom_tools": ["{FLEET_TOOL_ID}"], "auto_create_indexes": true}}')
    print()
    print("  3. Demo query combining BOTH tools via Deep Search (query_type 3):")
    print('       {"query": "CFM56 HPC — what parts, technicians, and ADs apply?",')
    print('        "query_type": 3, "auto_create_indexes": true}')
    print("     (The LLM selects tools automatically based on their descriptions.)")


if __name__ == "__main__":
    main()
