"""
Register the AeroFleet Custom Retriever tools from a laptop.

The enablement notebook (notebooks/aerofleet_autograph_autorag.ipynb, §7) does
the same thing on the platform; this script is for re-registering tools after
editing them without re-running the notebook.

Usage:
    uv run python scripts/setup_retriever.py            # or: make tools

Env vars:
    ARANGO_URL, ARANGO_DB, ARANGO_USER, ARANGO_PASSWORD   database connection
    PROJECT_NAME        AutoGraph project whose collections the doc tools search
                        (the full, suffixed name the notebook printed)
    TOOLS_COLLECTION    optional override (default "{PROJECT_NAME}_Tools")
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from backend.db import get_db  # noqa: E402
from pipeline.retriever_tools import (  # noqa: E402
    build_tools,
    default_tools_collection,
    discover_autograph_collections,
    register_tools,
)


def main() -> int:
    project = os.environ.get("PROJECT_NAME", "").strip()
    if not project:
        print("ERROR: set PROJECT_NAME to the AutoGraph project name.")
        return 1

    db = get_db()
    names = [c["name"] for c in db.collections()]

    try:
        ag = discover_autograph_collections(names, project)
        print(f"AutoGraph collections: {ag}")
    except ValueError as exc:
        print(f"WARNING: {exc}\n  Document tools skipped; registering fleet tools only.")
        ag = None

    if "engines" not in names:
        print("ERROR: 'engines' collection not found — run `make reset` first.")
        return 1

    tools_collection = default_tools_collection(project)
    ids = register_tools(db, tools_collection, build_tools(ag))
    print(f"Registered in {tools_collection}:")
    for tool_id in ids:
        print(f"  {tool_id}")
    print(
        "\nQuery with POST <retriever service_url>/v1/graphrag-query, e.g.\n"
        '  {"query": "which engines are critical", "query_type": 4,\n'
        f'   "custom_tools": ["{ids[-1]}"], "auto_create_indexes": true}}'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
