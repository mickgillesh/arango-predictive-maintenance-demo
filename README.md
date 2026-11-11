# AeroFleet — AutoGraph + AutoRAG Enablement

> **Branch `ai-suite-enablement`.** This is a stripped-down AeroFleet demo for a hands-on
> enablement session on the **Arango AI Suite**. The full sales demo (maintenance planner,
> natural-language chat, container deployment) lives on `main`.

The session shows the shape of a solution that answers questions over **two graphs at once**:

| Graph | Source | Built by |
|---|---|---|
| **Structured fleet graph** (`fleetGraph`) | NASA C-MAPSS telemetry + deterministic synthetic operations data | `pipeline/` (load → score → embed) |
| **Generated knowledge graph** | 37 engine maintenance documents in `corpus/` | **AutoGraph** |

**AutoRAG** answers document questions; **Custom Retriever tools** reach into both graphs;
**Deep Search** plans across all tools. A held-back emergency directive demonstrates an
**incremental AutoGraph update**.

Start here: **[ENABLEMENT.md](ENABLEMENT.md)** (session runbook) and
**[notebooks/aerofleet_autograph_autorag.ipynb](notebooks/aerofleet_autograph_autorag.ipynb)**.

---

## Repository layout

```
corpus/
  initial/                 36 Markdown docs: 10 ADs, 10 SBs, 16 AMM task cards
  incremental/             AD-EASA-2026-0214 — held back for the incremental-update demo
  MANIFEST.json            generated: doc ids, models, subsystems, cross-refs, word counts, hashes
  QUESTIONS.md             demo questions + answer key
notebooks/
  aerofleet_autograph_autorag.ipynb   end-to-end session notebook (runs on the platform)
  nb_helpers.py            REST helper for custom tools / Deep Search, display helpers
pipeline/
  download_data.py         NASA C-MAPSS FD001 download
  synthetic_graph.py       deterministic operational graph (seed 42)
  loader.py                creates collections + fleetGraph (destructive, "aerofleet" DBs only)
  scorer.py, scorer_runner.py   health index → predicted RUL, risk bucket, driver subsystems
  embed_runner.py          technician notes + embeddings on engines (for a custom tool)
  corpus.py                corpus parsing + MANIFEST.json build/check
  retriever_tools.py       Custom Retriever tool definitions (2 per graph)
backend/                   FastAPI: fleet, engine, impact, health endpoints; all AQL in aql.py
frontend/                  React: Fleet Overview, Engine Detail, Impact Explorer
scripts/                   check_connection.py, setup_retriever.py, strip_notebook.py
```

---

## The maintenance corpus

Hand-written, realistic documents for the four engine models in the fleet
(CFM56-7B27, CFM56-5B4, V2527-A5, PW4077D) plus a few for engines the operator does *not*
fly (Trent 700, GE90-115B, CF6-80C2) as retrieval distractors.

| Type | Count | Content |
|---|---|---|
| Airworthiness Directives (ADs) | 10 (+1 held back) | Applicability, unsafe condition, compliance times, mandatory actions, limits, parts/tools, cost, background |
| Service Bulletins (SBs) | 10 | Recommended actions, limits, parts, planning information, background |
| AMM task cards | 16 | Set-up, procedure, limits tables, fault isolation from trend data, service experience |

Documents link to the fleet graph only through **engine model** and **subsystem**
(fan, LPC, HPC, combustor, HPT, LPT), and reference each other through a
`**Related Documents:**` header, which gives AutoGraph shared entities to connect.
The fleet-wide trend-monitoring card (`AMM-GEN-77-00-00-ECTM-001`) maps the same
C-MAPSS sensors the scorer uses (T24, T30, T50, Ps30, phi, W31, W32, …) to subsystems.

After editing any document, run `uv run python -m pipeline.corpus --write` to refresh
`MANIFEST.json`. `make test` fails if the manifest is stale.

---

## Structured fleet graph

| Vertex collection | Count | Description |
|---|---|---|
| `engines` | 100 | Health index, predicted RUL, risk bucket, driver subsystems, technician notes + embedding |
| `aircraft` | 50 | Tail number and base (LHR, JFK, SIN, DXB, FRA); 2 engines each |
| `subsystems` | 600 | fan, LPC, HPC, combustor, HPT, LPT per engine |
| `sensors` | 2,100 | 21 C-MAPSS channels per engine, mapped to subsystems (`pipeline/MAPPING.md`) |
| `parts` | 39 | Spare parts with stock level and lead time; zero stock = blocking |
| `technicians` | 10 | Home base + subsystem certifications |
| `workOrders` | 200 | Historical closed work orders |
| `readings` | 20,631 | Raw telemetry (not in the named graph) |

| Edge collection | Direction |
|---|---|
| `installedOn` | engine → aircraft |
| `partOf` | subsystem → engine |
| `monitors` | sensor → subsystem |
| `requiredBy` | part → subsystem |
| `certifiedFor` | technician → subsystem |
| `maintains` | workOrder → engine |
| `performedBy` | workOrder → technician |
| `consumed` | workOrder → part |

Health scoring (`pipeline/scorer.py`) turns sensor drift into a 0–1 health index and a
predicted RUL; the sensors with the largest drift become the engine's **driver
subsystems**. With default settings the fleet has 3 critical engines (48, 9, 51).

---

## Custom Retriever tools

Defined in `pipeline/retriever_tools.py`, AQL in `backend/aql.py`.

| Tool | Graph | Search | Expansion |
|---|---|---|---|
| `aerofleet_techdocs_v2` | generated | hybrid on chunks | chunk → source document |
| `aerofleet_kg_entities_v1` | generated | hybrid on entities | mentions + related entities |
| `aerofleet_fleet_engine_v2` | structured | hybrid on model + technician notes | aircraft, degrading subsystems, parts (blocking first), technicians at base, recent work orders |
| `aerofleet_fleet_status_v5` | structured | lexical on risk bucket | same expansion, critical/warning only |

Earlier versions copied the AutoGraph `partition_id` onto every engine document so the
retriever's partition selection would not filter structured results out. That workaround
has been removed; notebook §7 verifies the result on the cluster and falls back to
`auto_select_partitions: false` if needed.

---

## Running it

**On the platform (the session):** follow [ENABLEMENT.md](ENABLEMENT.md). The notebook
clones this branch, loads the structured graph, and does everything else.

**On a laptop (preparation, the web app):**

```bash
uv sync --extra dev
cd frontend && npm install && cd ..
cp .env.example .env.local     # fill in ARANGO_*, OPENAI_API_KEY
make check                     # connectivity
make reset                     # load + score + embed (destructive; DB name must contain "aerofleet")
make dev                       # API on :8000, UI on :5173
make test                      # pytest + frontend build
make tools                     # re-register retriever tools (needs PROJECT_NAME)
```

| Variable | Used by | Purpose |
|---|---|---|
| `ARANGO_URL`, `ARANGO_DB`, `ARANGO_USER`, `ARANGO_PASSWORD` | everything | Database connection |
| `ARANGO_TLS_VERIFY` | everything | `true` (default), `false` for a self-signed cluster certificate, or a CA bundle path |
| `OPENAI_API_KEY` | embed, notebook | Note embeddings; AI Suite secret profiles |
| `CHAT_MODEL`, `EMBEDDING_MODEL` | notebook, embed | Must match between `make embed` and AutoRAG |
| `PROJECT_NAME` | notebook, `make tools` | AutoGraph project (auto-generated per run if unset) |
| `FPS_RECOVERY_USERNAME` | notebook | AutoGraph File Parser recovery user |
| `TOOLS_COLLECTION` | notebook, `make tools` | Override the Tools collection (default `{project}_Tools`) |
| `FORCED_CRITICAL` | scorer | Pin engine ids to critical for rehearsals |

See [CLAUDE.md](CLAUDE.md) for project conventions.
