# AeroFleet enablement branch — project conventions
- Branch `ai-suite-enablement`: AutoGraph + AutoRAG enablement. Never merge into main.
- Read PLAN.md before starting work; work one phase at a time, in order.
- Never commit secrets; all config via env vars mirrored in .env.example.
- All AQL is bind-parameterised and lives in backend/aql.py. Exception: the
  enablement notebook may show short, read-only, bind-parameterised AQL inline
  for teaching.
- Data generation is deterministic (fixed seeds). If output changes across
  runs, that is a bug. The corpus is static Markdown; after editing it run
  `uv run python -m pipeline.corpus --write` (tests fail on a stale manifest).
- corpus/initial must never mention documents in corpus/incremental.
- Loader safety: destructive operations only against databases whose name
  contains "aerofleet".
- Verify before claiming done: run `make test` (pytest + frontend build) and
  paste the output. Show evidence, not assertions.
- Python: ruff-clean (notebooks included), type hints on public functions.
  Frontend: TypeScript strict. Commit notebooks without outputs (`make nb-strip`).
- When AI Suite behaviour (SDK, AutoGraph, Retriever, custom tools, partitions)
  is uncertain, stop and ask, or verify on the cluster, rather than inventing
  API details. The SDK source (arangoml/arango-ai-sdk) is the reference.
