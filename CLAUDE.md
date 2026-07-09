# AeroFleet demo — project conventions
- Read PLAN.md before starting work; work one phase at a time, in order.
- Never commit secrets; all config via env vars mirrored in .env.example.
- All AQL is bind-parameterised and lives in backend/aql.py.
- Data generation is deterministic (fixed seeds). If output changes across
  runs, that is a bug.
- Loader safety: destructive operations only against databases whose name
  contains "aerofleet".
- Verify before claiming done: run `make test` (pytest + frontend build) and
  paste the output. Show evidence, not assertions.
- Python: ruff-clean, type hints on public functions. Frontend: TypeScript
  strict.
- When platform-specific behaviour (BYOC, txt2aql) is uncertain, stop and
  ask rather than inventing API details.