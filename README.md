# AeroFleet — Predictive Maintenance Demo

A sales demo for the **ArangoDB Contextual Data Platform**, modelling a fleet operator
managing 100 turbofan engines. Real NASA C-MAPSS telemetry drives a deterministic
health-index scorer. A synthetic operational graph connects each engine to its aircraft,
subsystems, parts, technicians, and work orders. Users interact through a React dashboard
and an AI-powered chat panel that translates natural language into AQL.

**Narrative: Predict → Understand → Ask**

---

## Use case

A fleet operator needs to know not just *which* engines are degrading, but *what breaks*
if one fails, *who* can fix it, and *whether the parts are available*. Traditional
monitoring answers the first question; a graph database answers all four.

| Screen | What it shows | Why it matters |
|---|---|---|
| Fleet Overview | Health status and predicted RUL for all 100 engines | Spot critical engines at a glance |
| Engine Detail | Sensor trends for the 14 drifting channels, full metadata | Understand *why* an engine is degrading |
| Impact Explorer | Affected aircraft, subsystems, blocking parts, qualified technicians | Plan maintenance before a grounding event |
| Chat (always-on) | Natural-language questions answered with live AQL | Explore the graph without writing a query |

---

## Architecture

```
NASA C-MAPSS FD001 dataset
        │
        ▼
pipeline/loader.py   ──► ArangoDB cloud (predictive-maintenance)
pipeline/synthetic_graph.py  ──► 8 edge collections, named graph fleetGraph
pipeline/scorer.py   ──► health index + predictedRUL written back to engines

backend/app.py (FastAPI)
  ├── GET  /api/fleet              fleet list with risk summary
  ├── GET  /api/engines/{id}       full engine document
  ├── GET  /api/engines/{id}/readings   sensor time-series
  ├── GET  /api/engines/{id}/impact     multi-hop graph traversal
  ├── GET  /api/health             service health check
  ├── GET  /api/suggestions        preset demo questions
  └── POST /api/ask                natural language → AQL → answer
        │
        ▼
LangChain ArangoGraphQAChain (GPT-4o)
  - introspects DB schema via ArangoGraph
  - generates and executes read-only AQL
  - returns natural-language answer + raw AQL for inspection

frontend/dist (served by FastAPI from /)
  ├── / (Fleet Overview)
  ├── /engines/:id (Engine Detail)
  └── /engines/:id/impact (Impact Explorer)
       └── ChatPanel (always visible, right-docked)
```

---

## Data sources

### NASA C-MAPSS FD001
20,631 rows of simulated turbofan telemetry from 100 engines. Each row is one
flight cycle with 21 sensor readings and 3 operational-setting channels. The
dataset records degradation from a healthy state through to failure, making it
ideal for remaining-useful-life (RUL) modelling.

Source: NASA Prognostics Data Repository — CMAPSS Jet Engine Simulated Data.

### Synthetic operational graph
Generated deterministically (seed=42) to wrap each engine in a realistic
operational context:

| Vertex collection | Count | Description |
|---|---|---|
| `engines` | 100 | Turbofan engines with health scores and predicted RUL |
| `aircraft` | 30 | Airframes; each engine is installed on one aircraft |
| `subsystems` | 600 | 6 subsystems per engine (fan, LPC, HPC, HPT, LPT, combustor) |
| `sensors` | 2,100 | 21 sensors per engine, mapped to subsystems |
| `parts` | ~500 | Spare parts with stock levels and lead times |
| `technicians` | 50 | Maintenance staff with subsystem certifications |
| `workOrders` | ~200 | Historical and open maintenance records |

| Edge collection | Meaning |
|---|---|
| `installedOn` | engine → aircraft |
| `partOf` | subsystem → engine |
| `monitors` | sensor → subsystem |
| `requiredBy` | part → subsystem |
| `certifiedFor` | technician → subsystem |
| `assignedTo` | workOrder → engine |
| `performedBy` | workOrder → technician |
| `uses` | workOrder → part |

The named graph `fleetGraph` spans all 8 edge collections and enables
multi-hop traversals in a single AQL query.

---

## Health scoring

The health index scorer (`pipeline/scorer.py`) computes a 0–1 score from the
14 drifting sensor channels using exponential drift saturation:

- **DRIFT_SATURATION = 12.5 σ** — a sensor reading this far from baseline is
  fully degraded.
- **Warning threshold = 0.62** — engines below this are flagged.
- **Critical threshold** — derived from the RUL distribution; engines with
  very low predicted RUL are marked critical.

Typical distribution across 100 engines: ~3 critical / ~10 warning / ~87 healthy.

Scoring runs automatically on API startup if any engine lacks a health score.

---

## AI query assistant

The chat panel is backed by LangChain's `ArangoGraphQAChain`:

1. `ArangoGraph` introspects the live database — collection schemas, property
   descriptions, enum values, and edge directions — providing the LLM with a
   faithful schema context without any manual prompt engineering.
2. GPT-4o generates an AQL query, which the chain executes against the database.
3. The answer is returned in natural language; the raw AQL is shown in a
   collapsible panel so users can inspect exactly what ran.

**Safety:** `force_read_only_query=True` prevents any mutation query from
executing. A belt-and-suspenders regex blocks INSERT/UPDATE/REPLACE/REMOVE/UPSERT
even if the chain check is somehow bypassed.

Example questions the assistant handles:

- "Which engines have less than 40 cycles of remaining useful life?"
- "What parts are needed to repair engine 42 and are they in stock?"
- "Which technicians are certified to work on critical HPC subsystems?"
- "How many engines on aircraft with more than 3 open work orders are in warning?"

---

## Setup

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+ with npm
- ArangoDB cloud instance (3.12+) with a `predictive-maintenance` database
- OpenAI API key (for the chat assistant)

### 1. Clone and install

```bash
git clone <repo>
cd aerofleet-demo
uv sync                  # Python deps
cd frontend && npm install && cd ..
```

### 2. Configure environment

```bash
cp .env.example .env.local
# Edit .env.local with your values:
#   ARANGO_URL, ARANGO_DB, ARANGO_USER, ARANGO_PASSWORD, OPENAI_API_KEY
```

### 3. Load data

```bash
make load    # downloads C-MAPSS, generates synthetic graph, loads to ArangoDB
make score   # computes health scores and writes predictedRUL back to engines
```

### 4. Run

```bash
make dev     # starts FastAPI (port 8000) + Vite dev server (port 5173) concurrently
```

Open `http://localhost:5173` for the dashboard with live API proxy.

For a production-style single-process serve:

```bash
cd frontend && npm run build && cd ..
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# App is served at http://localhost:8000
```

### 5. Test

```bash
make test    # pytest (36 tests) + frontend TypeScript build check
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ARANGO_URL` | Yes | ArangoDB instance URL |
| `ARANGO_DB` | Yes | Database name (must contain `predictive-maintenance`) |
| `ARANGO_USER` | Yes | Database user |
| `ARANGO_PASSWORD` | Yes | Database password |
| `OPENAI_API_KEY` | Yes* | OpenAI key for the AI chat assistant |
| `OPENAI_MODEL` | No | LLM model name (default: `gpt-4o`) |
| `FORCED_CRITICAL` | No | Comma-separated engine IDs pinned to critical risk |

*Without `OPENAI_API_KEY` the app still runs; the chat panel shows a
"not configured" status and suggestions are disabled.

---

## Project conventions

- All AQL queries are bind-parameterised and live in `backend/aql.py`. No
  string interpolation in queries.
- Data generation is deterministic (fixed seeds). Output must not change
  across runs.
- Loader safety: destructive operations are guarded to databases whose name
  contains `predictive-maintenance`.
- No secrets in the repository. All config via environment variables mirrored
  in `.env.example`.
- Python: ruff-clean, type hints on all public functions.
- Frontend: TypeScript strict mode.
