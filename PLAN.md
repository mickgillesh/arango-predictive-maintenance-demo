# AeroFleet — Predictive Maintenance Demo on the Arango Contextual Data Platform

Build plan for Claude Code. Work through phases in order; each phase has a goal,
detailed spec, and acceptance criteria. Do not start a phase until the previous
phase's acceptance criteria pass. Show evidence (command output, test results,
screenshots) when claiming a phase is complete.

## Project summary

A sales demo modelling a fleet operator with ~100 turbofan engines. Real NASA
C-MAPSS FD001 telemetry drives a deterministic health-index scorer (no ML
training). A synthetic operational graph (aircraft, subsystems, parts,
technicians, work orders) surrounds each engine. Users interact via a React
dashboard and a chat panel backed by the Arango AI Suite's Natural Language to
AQL (txt2aql) service. Everything deploys to the company's cloud Arango
Contextual Data Platform instance: the database is platform-native, and the
app ships as a single container via the platform's Bring Your Own
Code/Container (BYOC) flow.

Narrative: predict → understand (graph impact) → ask (natural language).

## Repository layout

```
aerofleet-demo/
├── CLAUDE.md                  # project conventions (see appendix)
├── PLAN.md                    # this file
├── data/                      # raw C-MAPSS files (gitignored)
├── pipeline/
│   ├── download_data.py       # fetch C-MAPSS FD001
│   ├── synthetic_graph.py     # generate operational graph entities
│   ├── loader.py              # create collections, load all data
│   └── scorer.py              # health-index scorer (provided, see appendix)
├── backend/
│   ├── app.py                 # FastAPI app factory + static file serving
│   ├── db.py                  # python-arango connection helper
│   ├── routes/                # one module per endpoint group
│   ├── txt2aql.py             # proxy to platform txt2aql REST API
│   └── tests/
├── frontend/                  # React + Vite
│   └── src/
│       ├── screens/           # FleetOverview, EngineDetail, ImpactExplorer
│       └── components/        # ChatPanel, RulGauge, SensorTrend, ...
├── Dockerfile                 # single image: backend + built frontend
├── docker-compose.yml         # local dev only (app container + optional local db)
├── .env.example               # all required env vars, no real values
└── Makefile                   # make load, make dev, make test, make image
```

## Configuration and secrets

All runtime config via environment variables. Never hardcode or commit
credentials. Required vars (document each in `.env.example`):

- `ARANGO_URL` — platform database endpoint
- `ARANGO_DB` — dedicated demo database name (e.g. `aerofleet`)
- `ARANGO_USER` / `ARANGO_PASSWORD` — service account (loader needs
  read-write; the app should use a read-mostly account where possible)
- `TXT2AQL_URL` — txt2aql service REST endpoint on the platform
- `TXT2AQL_AUTH` — auth token/header for the txt2aql service if required
- `FORCED_CRITICAL` — optional comma-separated engine IDs pinned to critical

The human owns: platform access, service accounts, txt2aql availability, and
the BYOC/Container Manager deployment step. Claude Code builds and verifies
everything locally against these env vars; assume they exist, fail loudly and
clearly if they do not.

---

## Phase 0 — Skeleton and connectivity

**Goal:** repo scaffold, tooling, and proven connectivity to the target database.

Spec:
- Initialise repo with the layout above, Python 3.11+ (uv or pip-tools),
  ruff + pytest configured, Vite + React + TypeScript in `frontend/`.
- `backend/db.py`: connection helper reading env vars, with a
  `check_connection()` returning server version and database name.
- `make check` runs a connectivity script printing ArangoDB version, database
  name, and current collection count.

Acceptance criteria:
- `make check` succeeds against the cloud instance (or a local ArangoDB
  container as fallback while cloud credentials are pending).
- `pytest` and `npm run build` both pass on the empty skeleton.

## Phase 1 — Data pipeline

**Goal:** all collections created and loaded; graph traversals return sensible results.

### 1a. C-MAPSS ingest (`pipeline/download_data.py`, part of `loader.py`)
- FD001 train set: 100 engines, ~20k rows. Columns: unit, cycle, 3 op
  settings, sensors s1–s21 (whitespace-separated, no header).
- Load into document collection `readings`: one doc per row with
  `engineId`, `cycle`, `op1..op3`, `s1..s21`. Persistent index on
  `(engineId, cycle)`.
- If the NASA source URL is unreachable, stop and ask the human to place the
  file in `data/` manually (Kaggle mirrors exist). Do not fabricate data.

### 1b. Synthetic operational graph (`pipeline/synthetic_graph.py`)
Deterministic generation — seed all randomness (`random.seed(42)`, Faker seed)
so every rebuild produces identical data. Entities:

- `engines` (100): `_key` = engine id; fields: model, entryIntoService.
  Scoring fields added in Phase 2.
- `aircraft` (50): tail number, base airport (pick 5 bases, e.g. LHR, JFK,
  SIN, DXB, FRA). Each aircraft gets exactly 2 engines via `installedOn`.
- `subsystems` (6 per engine = 600): fan, LPC, HPC, combustor, HPT, LPT.
  Edge `partOf` → engine.
- `sensors` (21 per engine): map each C-MAPSS sensor to a subsystem using
  the C-MAPSS documentation's station meanings (e.g. T24/s2 → LPC outlet,
  T30/s3 → HPC outlet, T50/s4 → LPT outlet, P30/s7 → HPC, Nf/s8 → fan,
  Nc/s9 → core/HPC, phi/s12 → combustor; put remaining channels on the most
  plausible subsystem and record the mapping in a MAPPING.md). Edge
  `monitors` → subsystem.
- `parts` (~40 catalogue items): name, stockLevel (0–20, make 3–4 items
  zero-stock), leadTimeDays (2–30). Edge `requiredBy` → subsystem type
  (model as edges to each matching subsystem, or a type-level part node —
  choose one, document it).
- `technicians` (25): name, homeBase (one of the 5 bases), certifications
  (subset of subsystem types). Edge `certifiedFor` → subsystem type.
- `workOrders` (~200 historical): date, description, status=closed. Edges:
  `maintains` → engine, `performedBy` → technician, `consumed` → part.

Create named graph `fleetGraph` over all vertex/edge collections.

### 1c. Loader (`pipeline/loader.py`)
- Idempotent: `make load` drops and recreates the demo database contents
  (guard: refuse to run unless `ARANGO_DB` == a name containing `aerofleet`).
- Batch inserts (python-arango `import_bulk`), not per-document writes.

Acceptance criteria:
- `make load` completes in under ~2 minutes against the cloud instance.
- A pytest module runs 3 smoke AQL queries and asserts non-empty results:
  (1) engines per aircraft == 2 for all aircraft; (2) a 2-hop traversal from
  any engine reaches its subsystems and sensors; (3) at least one part has
  stockLevel == 0.

## Phase 2 — Scoring

**Goal:** every engine document carries health/RUL/risk fields derived from real telemetry.

Spec:
- Use the provided `pipeline/scorer.py` (health-index scorer) as-is; wire a
  `score_and_writeback()` that pulls readings per engine, runs `score_fleet`,
  and merges `to_document()` output onto each engine vertex.
- Run automatically at app startup if engines lack `scoringMethod`; also
  expose as `make score`.
- Tune `DRIFT_SATURATION` so the fleet lands at roughly 3–5 critical and
  10–15 warning engines; record the chosen value and resulting distribution
  in the PR description.
- Map each engine's `driverSensors` to subsystem names via the `monitors`
  edges and store `driverSubsystems` on the engine too (this powers the
  impact story).

Acceptance criteria:
- After `make score`, an AQL count by riskBucket shows the target
  distribution; every engine has predictedRUL, riskBucket, driverSensors,
  driverSubsystems.
- Scoring is deterministic: two consecutive runs produce identical fields.

## Phase 3 — Backend API

**Goal:** all REST endpoints the frontend needs, tested.

FastAPI app serving JSON under `/api` and the built frontend as static files
at `/`. Endpoints (define pydantic response models for each):

- `GET /api/fleet` — KPI counts by riskBucket + engine list (id, aircraft
  tail, base, predictedRUL, riskBucket) sorted by RUL ascending.
- `GET /api/engines/{id}` — engine fields + aircraft + top driver
  subsystems.
- `GET /api/engines/{id}/readings?sensors=s2,s4` — cycle series for the
  requested sensors (default: the engine's driverSensors), downsampled to
  ≤500 points per sensor.
- `GET /api/engines/{id}/impact` — one AQL traversal returning: aircraft,
  degrading subsystems (from driverSubsystems), required parts with stock
  and lead time, certified technicians at the aircraft's base, and open
  questions (e.g. parts with stockLevel 0 flagged `blocking: true`).
- `POST /api/ask` — body `{question}`; proxies to txt2aql (Phase 4);
  returns `{answer, aql, raw}`.
- `GET /api/suggestions` — the curated question chips (hardcoded list).
- `GET /api/health` — app + db + txt2aql reachability.

Rules:
- All AQL lives in one module (`backend/aql.py`) as named, bind-parameterised
  queries — no string interpolation of user input, ever.
- The impact traversal must be a single AQL query (it is a demo talking
  point); include it verbatim in a docstring with an explanation.

Acceptance criteria:
- pytest suite covers every endpoint against the loaded database (happy path
  + 404s + a downsampling check). All green.
- `GET /api/engines/{worst}/impact` returns at least one blocking part for
  at least one critical engine (adjust synthetic stock levels if not).

## Phase 4 — txt2aql integration

**Goal:** `/api/ask` answers questions through the platform's Natural Language to AQL service.

Spec:
- `backend/txt2aql.py`: thin client for the txt2aql REST API using
  `TXT2AQL_URL`/`TXT2AQL_AUTH`. Request both the natural-language answer and
  the generated AQL; pass through raw JSON when available.
- Timeouts (10s) and a graceful failure message the chat panel can render.
- A `scripts/eval_questions.py` harness: runs the curated suggestion
  questions through the live service and prints question, generated AQL, and
  answer for human review. This is the tool for iterating on question
  phrasing against the real schema.
- The human configures the txt2aql service itself (LLM provider, deployment)
  on the platform; if it is unreachable, the endpoint must return a clear
  "service not configured" error rather than crashing.

Acceptance criteria:
- With the service reachable, all curated questions return a non-error
  answer and syntactically valid AQL (harness output pasted as evidence).
- With the service unreachable, `/api/ask` returns the graceful error and
  `/api/health` reports txt2aql=down.

## Phase 5 — Frontend

**Goal:** three screens + chat panel, wired to the API, demo-quality polish.

Consult the frontend-design skill/conventions if available. Stack: React +
TypeScript + Vite; charts with Recharts; graph view with Cytoscape.js (fall
back to a structured card layout if the graph view exceeds the timebox —
the built-in platform Graph Visualizer can cover live graph exploration).

- **Fleet overview (`/`)**: KPI cards (critical/warning/healthy counts),
  risk-ranked table (click → engine detail), small RUL histogram.
- **Engine detail (`/engines/:id`)**: RUL gauge, riskBucket badge, sensor
  trend charts for driverSensors (real degradation should be visible),
  driver subsystem callout, button → impact view.
- **Impact explorer (`/engines/:id/impact`)**: renders the impact payload as
  an interactive graph (engine centre; aircraft, subsystems, parts,
  technicians around it). Blocking parts highlighted. Side panel lists the
  actionable summary ("2 parts needed, 1 out of stock (12-day lead), 3
  certified technicians at LHR").
- **Chat panel**: docked right on all screens. Suggestion chips from
  `/api/suggestions`, free-text input, answers rendered with a collapsible
  "Show AQL" section. Loading and error states.

Acceptance criteria:
- `npm run build` clean; app usable at desktop 1440px and projector-safe
  (large fonts, high contrast).
- Manual walkthrough of the 5-beat demo script succeeds end-to-end
  (screenshot evidence per beat).

## Phase 6 — Container and BYOC packaging

**Goal:** one image that runs the whole demo; ready for the platform's BYOC flow.

Spec:
- Multi-stage Dockerfile: stage 1 builds the frontend; stage 2 is a slim
  Python image with backend + `frontend/dist` + pipeline; entrypoint runs
  migrations-check → conditional scoring → uvicorn.
- Image must run as non-root, listen on a configurable `PORT`, and log to
  stdout — standard requirements for platform-hosted containers.
- `make image` builds and `make smoke` runs the container locally against
  the cloud database and curls `/api/health`.
- Produce `DEPLOY.md`: exact steps for the human to upload/deploy via the
  platform's Container Manager (BYOC), including required env vars. Where
  platform-specific steps are uncertain, say so explicitly and link the
  relevant docs section rather than inventing steps.

Acceptance criteria:
- `make smoke` passes; image < 500MB; container cold-starts (including
  conditional scoring against already-scored data) in < 15s.

## Phase 7 — Demo hardening

**Goal:** the demo is rehearsable and repeatable.

- Freeze the curated questions after Phase 4 evaluation; store in one place.
- `make reset` = load + score from scratch (single command full rebuild).
- Write `DEMO_SCRIPT.md`: the 5 beats with exact clicks, the engine IDs to
  use, the questions to ask, expected answers, and talking points (including
  the honest framing: real NASA telemetry + illustrative synthetic org data;
  scorer is a deterministic health index behind an ML-ready interface).
- Failure drills: what to do if txt2aql is down (chips → canned screenshots),
  if the graph view misbehaves (platform Graph Visualizer fallback).

Acceptance criteria: a colleague can run `make reset`, open the app, and
deliver the demo from DEMO_SCRIPT.md without help.

---

## Out of scope (do not build)

Live/streaming data, authentication/multi-user, real ML training, mobile
layouts, CI/CD pipelines, and any write operations from the chat panel
(txt2aql usage in this demo is read-only by convention; never surface a
generated query that mutates data — if one is returned, refuse to render it
and show the graceful error).

## Appendix A — CLAUDE.md starter

```markdown
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
```

## Appendix B — provided scorer

`pipeline/scorer.py` is already written (health-index scorer,
`health_index_scorer.py` from the planning conversation). Copy it in
unchanged; treat its public interface (`score_engine`, `score_fleet`,
`EngineScore.to_document`) as a stable contract.
