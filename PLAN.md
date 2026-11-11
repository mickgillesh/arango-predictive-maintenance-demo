# AeroFleet — AutoGraph + AutoRAG Enablement Plan

Branch `ai-suite-enablement`. A stripped-down AeroFleet demo for a customer enablement
session: deploy AutoGraph and AutoRAG with the AI Suite SDK and query a structured graph
and an AutoGraph-generated graph together. The original sales-demo build plan (phases 0-7:
pipeline, scoring, API, txt2aql, frontend, BYOC) is on `main`.

Work through the phases in order; show evidence (`make test` output) for each.

## Phase A — Strip the branch ✅
Removed the planner, txt2aql chat, Google OAuth, the broken retriever proxy, Docker/BYOC
packaging and the old demo scripts. Kept the pipeline, the fleet/engine/impact API and
screens. Fixed: loader guard aligned to "aerofleet"; `embed_runner` engine order made
deterministic (`SORT`); embedding model configurable via `EMBEDDING_MODEL`; loader AQL
bind-parameterised.

**Accept:** `make test` green; no references to removed features.

## Phase B — Maintenance corpus ✅
`corpus/initial/` has 36 hand-written docs (10 ADs, 10 SBs, 16 AMM task cards).
`corpus/incremental/` has one held-back emergency AD (AD-EASA-2026-0214). Linkage to the
fleet is by engine model and subsystem only. `pipeline/corpus.py` builds a deterministic
`MANIFEST.json`. `corpus/QUESTIONS.md` is the answer key.

**Accept:** `test_corpus.py` passes:
- the manifest is current
- headers are present
- models and subsystems are known
- lengths are 750+ words for fleet docs and 450+ for distractors
- related documents resolve
- the held-back doc is never mentioned by an initial doc
- every fleet model and subsystem is covered

## Phase C — Custom retriever review ✅
- Tool AQL moved to `backend/aql.py`; tool definitions in `pipeline/retriever_tools.py`
  (pure, typed, tested). The two fleet tools share one expansion template.
- `partition_id` tagging, index rebuild and view re-link removed.
- AutoGraph collection names discovered, not hard-coded. Tools collection configurable.
- Planner-dependent work-order filter replaced by historical work orders via `maintains`.
- Blocking parts computed before `LIMIT` so they are never hidden.
- Deep Search corrected to CUSTOM + `use_llm_planner` (it is not `query_type` 3).

**Accept:** `test_retriever_tools.py` passes:
- offline structure and bind-parameter coverage
- every template parses on the database
- the fleet expansion returns context for the critical engines

## Phase D — Notebook ✅
`notebooks/aerofleet_autograph_autorag.ipynb`, sections §0-§9 (config, setup, structured
graph, project, upload, AutoGraph, AutoRAG, custom tools + Deep Search + partition check,
incremental update, cleanup). The SDK is pinned to a commit.

**Accept:** `test_notebook.py` passes (valid nbformat, no outputs, cells compile, no
secrets, SDK pinned) and ruff is clean on the notebook.

## Phase E — Docs ✅
ENABLEMENT.md (runbook), README.md, CLAUDE.md, .env.example, Makefile.

## Phase F — Live verification on the fresh cluster ⏳
Run the notebook top to bottom. Fill in the "Verify on the cluster" table in
ENABLEMENT.md.

**Accept:**
- every `QUESTIONS.md` answer contains its required facts
- the §7 partition check passes
- §8 reports `documents_added == 1` and the post-update answers cite AD-EASA-2026-0214
