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
TOOLS_COLL     = "Tools"

# Main AeroFleet collections (created by pipeline/loader.py)
FLEET_GRAPH_COLLECTIONS = ("engines", "installedOn", "partOf", "requiredBy",
                            "certifiedFor", "workOrders")

FLEET_TOOL_ID        = "aerofleet_fleet_graph_v1"
FLEET_STATUS_TOOL_ID = "aerofleet_fleet_status_v4"


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
            "  LET content = CONCAT(\n"
            "    \"Engine \", eng._key, \" — \", eng.model,\n"
            "    \" | Risk: \", eng.riskBucket,\n"
            "    \" | Health: \", TO_STRING(ROUND(eng.healthIndex * 100)), \"%\",\n"
            "    \" | RUL: \", TO_STRING(eng.predictedRUL), \" cycles\",\n"
            "    \" | Aircraft: \", ac.tailNumber, \" (\", ac.base, \")\",\n"
            "    \" | Degrading subsystems: \", CONCAT_SEPARATOR(\", \", eng.driverSubsystems),\n"
            "    \" | Notes: \", eng.technicianNotes\n"
            "  )\n"
            "  RETURN {\n"
            "    content:             content,\n"
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
                "Searches the AeroFleet structured fleet graph for a SPECIFIC engine model or "
                "engine ID. Use this tool when the query names a model (e.g. CFM56-7B27, "
                "V2527-A5, PW4077D) or references a known engine ID. Performs semantic search "
                "on technician maintenance notes to surface the most relevant engines for that "
                "model or fault type, then expands the graph to return degrading subsystems, "
                "blocking parts, certified local technicians, and open work orders. "
                "For general fleet health status or all engines needing maintenance, use "
                "aerofleet_fleet_status_v4 instead."
            ),
            "config": {
                "collection": "engines",
                "search_modes": [
                    {"type": "lexical",  "field": "model",          "weight": 0.2},
                    {"type": "semantic", "field": "notesEmbedding", "weight": 0.8},
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

        # ── Tool 3: Fleet Status (maintenance-vocabulary search → graph expansion) ──
        print("\n[3/3] Fleet-status tool (maintenance-vocabulary search + graph expansion):")
        # The search stage finds engines whose technicianNotes contain health/maintenance
        # vocabulary (degradation, anomaly, threshold, wash, borescope, etc.).
        # The AQL receives @nodes from that search and expands each matched engine
        # through the full graph — same traversal as fleet_graph_v1.
        # The LLM response_instructions direct the model to surface degraded engines first.
        fleet_status_aql = (
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
            "  LET content = CONCAT(\n"
            "    \"Engine \", eng._key, \" — \", eng.model,\n"
            "    \" | Risk: \", eng.riskBucket,\n"
            "    \" | Health: \", TO_STRING(ROUND(eng.healthIndex * 100)), \"%\",\n"
            "    \" | RUL: \", TO_STRING(eng.predictedRUL), \" cycles\",\n"
            "    \" | Aircraft: \", ac.tailNumber, \" (\", ac.base, \")\",\n"
            "    \" | Degrading subsystems: \", CONCAT_SEPARATOR(\", \", eng.driverSubsystems),\n"
            "    \" | Notes: \", eng.technicianNotes\n"
            "  )\n"
            "  RETURN {\n"
            "    content:             content,\n"
            "    engineId:            eng._key,\n"
            "    model:               eng.model,\n"
            "    riskBucket:          eng.riskBucket,\n"
            "    predictedRUL:        eng.predictedRUL,\n"
            "    healthIndex:         eng.healthIndex,\n"
            "    driverSubsystems:    eng.driverSubsystems,\n"
            "    technicianNotes:     eng.technicianNotes,\n"
            "    aircraft:            { tailNumber: ac.tailNumber, base: ac.base },\n"
            "    degradingSubsystems: (FOR s IN degradingSubs RETURN s.name),\n"
            "    blockingParts:       (FOR p IN parts FILTER p.blocking == true RETURN p),\n"
            "    parts:               parts,\n"
            "    technicians:         techs,\n"
            "    openWorkOrders:      open_wos\n"
            "  }"
        )

        fleet_status_tool = {
            "_key":      FLEET_STATUS_TOOL_ID,
            "tool_id":   FLEET_STATUS_TOOL_ID,
            "tool_type": "custom_retriever",
            "description": (
                "Returns engines in critical or warning health state from the AeroFleet fleet. "
                "Use this tool for questions about overall fleet health, which engines need "
                "attention, maintenance planning, AOG risk, or resource prioritisation. "
                "Always phrase queries to include the words 'critical' or 'warning' so the "
                "search stage can find the right risk buckets. "
                "Returns each matched engine with its aircraft, degrading subsystems, blocking "
                "parts, certified local technicians, and open work orders."
            ),
            "config": {
                "collection": "engines",
                # Lexical search on technicianNotes ensures the search stage finds
                # nodes (health/maintenance vocabulary appears in almost all notes),
                # which allows the AQL stage to run. The AQL itself ignores @nodes
                # and queries by riskBucket directly.
                "search_modes": [
                    {"type": "lexical", "field": "riskBucket", "weight": 1.0},
                ],
                "top_k": 5,
                "show_citations": False,
                "response_instructions": (
                    "Return a prioritised list of engines requiring maintenance, most critical "
                    "first. For each engine include: engine ID, model, risk bucket, predicted "
                    "RUL, aircraft tail number and base, degrading subsystems, technician notes "
                    "summary, any parts with zero stock (blocking), available certified "
                    "technicians at the same base, and open work orders. "
                    "Summarise the overall fleet risk at the end."
                ),
                "aql_config": {
                    "template":             fleet_status_aql,
                    "relations_collection": "installedOn",
                    "bind_params": {
                        "max_parts": 3,
                        "max_techs": 2,
                        "max_wos":   2,
                    },
                },
            },
        }
        _upsert(coll, fleet_status_tool)

    # ── Partition-tag engine documents FIRST (must precede index build) ──────────
    # The retriever service always applies partition filtering (auto_select_partitions=true).
    # It routes to the only registered partition: the technicians_documentation one.
    # Engine documents need a matching partition_id field so the search-stage filter
    # passes them through. Documents must be tagged BEFORE the index is built so that
    # partition_id is present in every document at index time.
    partition_id: str | None = None
    if not skip_fleet:
        print("[*] Discovering partition_id from technicians_documentation_Chunks…")
        if CHUNKS_COLL in existing:
            cursor = db.aql.execute(
                f"FOR c IN {CHUNKS_COLL} FILTER HAS(c, 'partition_id') LIMIT 1 RETURN c.partition_id"
            )
            partition_id = next(iter(cursor), None)

        if partition_id:
            print(f"    partition_id = {partition_id!r}")
            engine_keys = list(db.aql.execute("FOR e IN engines RETURN e._key"))
            updates = [{"_key": k, "partition_id": partition_id} for k in engine_keys]
            db.collection("engines").import_bulk(updates, on_duplicate="update")
            print(f"    Tagged {len(updates)} engine documents with partition_id.")
        else:
            print("    WARNING: could not discover partition_id — fleet search may return empty.")
            print("    Ensure technicians_documentation Autograph ingestion has completed.")

    # ── Rebuild engines inverted index AFTER documents are tagged ─────────────
    # The retriever filters within the SEARCH block using partition_id (identity
    # analyzer), mirroring the Autograph Chunks index schema. Build this index
    # after tagging so partition_id is present in all documents at index time.
    if not skip_fleet:
        print("[*] Rebuilding cr_engines_riskBucket index with partition_id field…")
        eng_col = db.collection("engines")
        existing_indexes = eng_col.indexes()
        old = next((i for i in existing_indexes if i.get("name") == "cr_engines_riskBucket"), None)
        if old:
            eng_col.delete_index(old["id"])
            print("    Deleted old cr_engines_riskBucket index.")
        eng_col.add_index({
            "type": "inverted",
            "name": "cr_engines_riskBucket",
            "analyzer": "text_en",
            "features": ["frequency", "position", "norm"],
            "fields": [
                {"name": "riskBucket"},
                {
                    "name": "partition_id",
                    "analyzer": "identity",
                    "features": ["frequency", "norm"],
                },
            ],
        })
        print("    Created cr_engines_riskBucket with riskBucket + partition_id.")

        # Deleting an index auto-removes it from any search-alias view that referenced it.
        # Re-add the new index to cr_engines_view explicitly.
        view_name = "cr_engines_view"
        import httpx as _httpx
        _auth = _httpx.post(
            arango_url.rstrip("/") + "/_open/auth",
            json={"username": arango_user, "password": arango_pass},
            timeout=10,
        )
        _jwt = _auth.json().get("jwt", "")
        _resp = _httpx.patch(
            arango_url.rstrip("/") + f"/_db/{arango_db}/_api/view/{view_name}/properties",
            headers={"Authorization": f"bearer {_jwt}"},
            json={"indexes": [{"collection": "engines", "index": "cr_engines_riskBucket"}]},
            timeout=10,
        )
        if _resp.status_code in (200, 201):
            print(f"    Re-linked cr_engines_riskBucket → {view_name}.")
        else:
            print(f"    WARNING: view re-link returned {_resp.status_code}: {_resp.text[:200]}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("Tools registered in:", TOOLS_COLL)
    if not skip_techdocs:
        print(f"  {techdocs_tool_id:40s}  — AD/SB unstructured docs (Autograph hybrid search)")
    if not skip_fleet:
        print(f"  {FLEET_TOOL_ID:40s}  — Specific engine/model search (semantic on notes + graph traversal)")
        print(f"  {FLEET_STATUS_TOOL_ID:40s}  — Degraded engines (riskBucket search + graph expansion)")
    print()
    print("Next steps:")
    print("  1. Set in .env.local:")
    print("       RETRIEVER_URL=https://<endpoint>:8529/graphrag/retriever/<service_id>/v1")
    if not skip_techdocs:
        print(f"       RETRIEVER_TOOL_ID={techdocs_tool_id}")
    print("  (Auth is automatic — ARANGO_USER / ARANGO_PASSWORD are reused to fetch a JWT)")
    print()
    print("  2. Fleet status query (maintenance-vocabulary search → graph expansion):")
    print('       {"query": "which engines need maintenance", "query_type": 4,')
    print(f'        "custom_tools": ["{FLEET_STATUS_TOOL_ID}"]}}')
    print()
    print("  3. Specific engine/model query (requires inverted + vector index — see README):")
    print('       {"query": "CFM56-7B27 HPC degradation", "query_type": 4,')
    print(f'        "custom_tools": ["{FLEET_TOOL_ID}"], "auto_create_indexes": true}}')
    print()
    print("  4. Deep Search combining all tools (query_type 3):")
    print('       {"query": "critical engines — what parts, technicians, and ADs apply?",')
    print('        "query_type": 3, "auto_create_indexes": true}')
    print("     (The LLM selects tools automatically based on their descriptions.)")


if __name__ == "__main__":
    main()
