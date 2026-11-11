# Enablement Session Runbook — AutoGraph + AutoRAG on Arango

**Goal:** the customer leaves knowing how to deploy AutoGraph and AutoRAG with the AI Suite
SDK, how a generated knowledge graph and an existing structured graph are queried
together, and how the generated graph is kept current with incremental updates.

**Format:** about 90 minutes, driven by
[`notebooks/aerofleet_autograph_autorag.ipynb`](notebooks/aerofleet_autograph_autorag.ipynb)
on the platform notebook server of a fresh cluster. Answer key:
[`corpus/QUESTIONS.md`](corpus/QUESTIONS.md).

---

## 1. Before the session (do a full dry run the day before)

### Checklist

- [ ] Fresh cluster with the AI Suite enabled; you can log in to the web UI.
- [ ] Notebook server kernel is **Python 3.12+**: `import sys; sys.version`. If it is
      3.11, create a 3.12 kernel (below).
- [ ] Notebook server can reach `github.com` (repo + SDK). The C-MAPSS telemetry is
      committed in `data/train_FD001.txt`, so NASA access is not needed.
- [ ] A database user with permission to create databases (for `aerofleet_enablement`).
- [ ] A user for `FPS_RECOVERY_USERNAME` with read/write on the database. The SDK does
      not create it. Using the same user as `ARANGO_USER` is fine for a demo.
- [ ] OpenAI API key with access to `gpt-4o` and `text-embedding-3-small`.
- [ ] This branch pushed to GitHub, so the notebook can clone it.
- [ ] Dry run of the whole notebook with a throwaway project. Note the timings for §5
      and §8, which are the slow steps, and fill in the table below.

### Python 3.12 kernel (if the server's default is 3.11)

The SDK uses Python 3.12 syntax (PEP 695 generics and `type` aliases), so it cannot be
installed on 3.11. In a terminal on the notebook server:

```bash
python -m pip install --user -q uv
python -m uv venv --seed --python 3.12 ~/.venvs/aerofleet312   # --seed adds pip (needed by §1)
python -m uv pip install --python ~/.venvs/aerofleet312/bin/python ipykernel
~/.venvs/aerofleet312/bin/python -m ipykernel install --user \
    --name aerofleet312 --display-name "Python 3.12 (AeroFleet)"
```

Reload the browser tab, choose **Kernel → Change Kernel → Python 3.12 (AeroFleet)**, and run
the notebook from §0. §1 installs into this kernel's environment (`sys.executable -m pip`).

### Verify on the cluster — record the outcomes

These are platform behaviours the notebook checks rather than assumes. Write down the
result of the dry run so there are no surprises live.

| # | Question | Where checked | Result |
|---|---|---|---|
| 1 | Python version; GitHub reachable | §0, §1 | |
| 2 | AutoGraph collection names for an SDK project (one set, or one per partition?) | §5 "What AutoGraph created" | |
| 3 | Which Tools collection the retriever reads (`{project}_Tools` or `Tools`) | §7 first check | **`Tools`** (shared by all projects in the database), verified 2026-11-11 |
| 4 | Custom (`query_type` 4) and Deep Search (`4` + `use_llm_planner`) work as expected | §7 | Work. **The retriever binds `@nodes`, `@@relations_collection` and `@top_k` into every expansion**; a template must declare all three or the AQL fails and the tool silently answers from the plain search hits (`failed_tools` stays empty). Fixed in all four tools, verified 2026-11-11 |
| 5 | Fleet tools return engines **without** `partition_id` (or need `auto_select_partitions: false`) | §7 partition check | **No `partition_id` needed, but `auto_select_partitions: false` is required**: with the default, the fleet tools retrieve nothing (verified 2026-11-11). Now set in the fleet tools' own config; the metadata confirms it (`partition_routing.mode: tool_config_disabled`) |
| 6 | `ingest(incremental=True)` accepts a new file in the already-built category | §8 (falls back to a new category) | |
| 7 | `FPS_RECOVERY_USERNAME` accepted | §5 deploy | |

Note on query types (from the SDK's `retrievers.proto`): `GLOBAL = 1`, `LOCAL = 2`,
`UNIFIED = 3`, `CUSTOM = 4`. Deep Search is **CUSTOM + `use_llm_planner: true`**, not
`query_type` 3. Earlier scripts in this repo had this wrong.

---

## 2. Agenda

| Time | Notebook | Topic |
|---|---|---|
| 0:00 | — | The problem: maintenance knowledge is split between operational data (which engine, which part, who) and documents (what the regulator and the OEM require). |
| 0:10 | §0-§2 | The structured graph: load, score, one multi-hop traversal. |
| 0:20 | §3-§4 | AI Suite projects, secret profiles, uploading a corpus by category. |
| 0:25 | §5 | AutoGraph: corpus build → strategies (partitions) → orchestrate. Explore the generated graph. *Start the build, then talk through the corpus while it runs.* |
| 0:45 | §6 | AutoRAG: LOCAL / GLOBAL / UNIFIED on document questions. |
| 0:55 | §7 | Custom Retriever tools across both graphs; Deep Search. |
| 1:10 | §8 | Incremental update: a new emergency AD arrives. Before / after. |
| 1:25 | — | Q&A, how to apply this to their own data. |

---

## 3. Talking points

### §2 Structured graph
- Real NASA C-MAPSS telemetry; synthetic but deterministic operational data (seed 42).
- The scorer turns sensor drift into a health index, a predicted remaining useful life and
  **driver subsystems**. Those subsystems are what joins the fleet to the documents.
- `Q_ENGINE_IMPACT` (backend/aql.py) is one AQL query that walks four edge types: the
  aircraft, degrading subsystems, required parts with stock, and certified technicians at
  the base.

### §4 Corpus
- 36 documents of three kinds: ADs (mandatory, from regulators), SBs (recommended, from
  OEMs) and AMM task cards (procedures).
- The documents name models, subsystems, sensors, part names, tools and other documents,
  which gives AutoGraph shared entities to connect.
- Distractor documents for engines the operator doesn't fly: retrieval must not answer from them.

### §5 AutoGraph
- **Category** is the second upload scope label. One category is one corpus here.
- **Corpus build** links similar documents and clusters them.
- **Strategize** chooses FullGraphRAG or VectorRAG per cluster. Each cluster becomes a
  **partition** (`rag_partition_id`). Partitions are created by AutoGraph; you don't
  assign them.
- **Orchestrate** imports partitions; only stale partitions are re-imported later.
- Show the relation types (`PART_OF`, `MENTIONED_IN`, `RELATED_TO`) and the graph in the
  Graph Visualizer.

### §6 AutoRAG
- LOCAL for "what does X require", GLOBAL for themes, UNIFIED for mixed questions.
- **Show the metadata under each answer** — it is how the retriever explains itself:
  - LOCAL runs an **LLM planner** by default: it splits the question into sub-queries
    (`step 1 … asked: …`) and gathers graph context for each. That is also why LOCAL is
    the slowest mode (16-40 s in the dry run).
  - UNIFIED lists the cited chunks; GLOBAL lists scored **support points** from the
    community summaries (map-reduce) and which communities it read.
  - `citations: [n] …` maps each `[n]` in the answer to the chunk (and document) it came from.
- **A6 is the bridge to §7.** In the dry run (2026-11-11) LOCAL answered as if the
  Trent 700 were in "your fleet": the documents describe engines but not which ones the
  operator flies. That fact lives in the structured graph, which only the §7 custom tools
  can reach. Use the wrong answer, don't hide it.

### §7 Custom Retriever, the key idea
- A tool is a **document**: a collection to search, a search mode (lexical, semantic or
  hybrid with weights), an AQL expansion, and a natural-language description.
- Tools can target **any collection**, including the structured fleet graph. That is how
  existing operational data joins the RAG answer without being re-ingested.
- Blocking parts are computed before the parts list is truncated, so a zero-stock part is
  never hidden.
- **Deep Search** lets an LLM planner pick tools by description and combine results.
- **Semantic search over your own data needs matching vectors**: the same embedding model
  *and the same length*. The AI Suite retriever embeds queries at 512 dimensions, so the
  engine notes are embedded at 512 (not the model's native 1536) with a matching vector
  index. §7 reads the length from AutoGraph's chunk embeddings and re-embeds if needed.
- **The template contract (learned the hard way in the dry run).** The retriever binds
  `@nodes`, `@@relations_collection` and `@top_k` into every expansion. ArangoDB rejects a
  bind parameter the query does not declare, so a template that leaves one out fails, and
  the retriever then **silently falls back to the plain search hits**: no error, nothing in
  `failed_tools`, just a thinner answer. Our first fleet tool answered from the raw engine
  documents (no aircraft, no blocking parts) for exactly this reason. Show the §7 expansion
  check: it runs every tool the way the retriever does, so a broken template shows up as an
  error, and `test_retriever_tools.py` enforces the contract offline.

### §7 The `partition_id` question
- **Before:** an earlier deployment copied the AutoGraph partition id onto every engine
  and rebuilt the engine search index, so the retriever's automatic partition selection
  would not drop structured results. It broke every time the fleet was reloaded.
- **Now:** removed, as the technical team advised, and verified on the cluster: no data
  changes are needed. What *is* needed is `auto_select_partitions: false` on requests that
  use tools over non-AutoGraph collections. With the default, partition auto-selection only
  considers AutoGraph partitions and the fleet tools retrieve nothing. The fleet tools now
  set `auto_select_partitions: false` in their **own config** (a documented per-tool field),
  so no request flag is needed and Deep Search can still mix them with the document tools,
  which leave it unset. The §7 check confirms this from the answer's metadata.
- **Worth feeding back to the AI Suite team:** expansion failures are silent (no entry in
  `failed_tools`), and the docs' airport example declares neither `@@relations_collection`
  nor `@top_k`, so it would hit the same fallback.
- **Open issue: intermittent retrieval (dry run 2026-11-11).** Once the expansion worked,
  identical fleet-status queries sometimes returned nothing (no `graph_metadata`), in
  about 4 of 17 runs with the full template. Dropping the top-level `content` field from the
  expansion rows gave 3/3, but that sample is small and a simpler template also failed 0/3,
  so the root cause is not confirmed. After the field was removed, every repeated §7 check
  succeeded on attempt 1 with the full expansion (aircraft, bases, blocking parts matching
  `QUESTIONS.md` B1). The §7 check still retries up to 3 times and prints the
  attempt number. **Track the attempt numbers across dry runs**, and check the retriever
  service logs for a failing query before raising it with the AI Suite team.

### §8 Incremental update
- Scenario: EASA issues an emergency AD (`AD-EASA-2026-0214`) that supersedes
  AD-EASA-2024-0312. It halves the interval to 50 cycles, widens the scope to stages 1-5,
  requires inspection within 10 cycles for engines with an HPC trend alert, and requires
  replacing the whole **HPC blade set stage 1-4** on any reject.
- `ingest(..., incremental=True)` keeps the existing corpus: `documents_added == 1`,
  everything else unchanged, and only affected partitions are re-imported.
- Before: the answer cites the 2024 AD with a 100-cycle interval. After: it cites the 2026
  AD and the new limits.
- Deep Search after the update joins the new AD to the fleet. CFM56-7B27 engines with HPC
  as a driver subsystem (engine 9 is critical) need inspection within 10 cycles, and the
  HPC blade set stage 1-4 has **zero stock**.

---

## 4. Failure drills

| Symptom | Likely cause | Action |
|---|---|---|
| §0 asserts "needs Python 3.12+" | Server default kernel is 3.11 | Create the 3.12 kernel (section 1) and switch to it |
| §1: "No module named pip" | Kernel venv created without `--seed` | `python -m uv pip install --python ~/.venvs/aerofleet312/bin/python pip`; restart kernel |
| §1 SDK install fails | No GitHub access | `pip download` the SDK on a laptop and upload the wheel |
| §2 "Could not download C-MAPSS FD001" | `data/train_FD001.txt` missing from the clone | `git checkout -- data/train_FD001.txt` (or upload it to `data/`); re-run §2 |
| Fleet-engine tool: `a vector must be of dimension 1536, but is 512` | Note embeddings longer than the retriever's query vectors | §7's first cell re-embeds the notes at AutoGraph's chunk-embedding length (512 on the platform) and rebuilds the index; re-run it |
| §2 `CERTIFICATE_VERIFY_FAILED: self-signed certificate` | Cluster CA not trusted (§0 uses `ARANGO_DEPLOYMENT_CA` by default) | Check `echo $ARANGO_DEPLOYMENT_CA` in a terminal; otherwise set `ARANGO_TLS_VERIFY=false` in §0 and re-run from §0 |
| `ensure_database()` permission error | User cannot create databases | Create `aerofleet_enablement` in the web UI; re-run |
| AutoGraph deploy rejected | `FPS_RECOVERY_USERNAME` missing or without rights | Create the user / grant rw; re-run deploy |
| §6/§8 `PlatformUnreachableError: … did not answer within the request timeout` | A retriever query took longer than the SDK's 60 s default | §3 raises it to `REQUEST_TIMEOUT` (300 s); raise further and re-run the question. The first query after a deploy is the slowest |
| `NameError` (e.g. `answers`, `rag`, `db`) after logging back in | Kernel restarted; variables lost, cluster state intact | Run §0 and §1, set `RESUME = True` in the Resume cell and run it, then re-run the section you were on. §6 reuses the deployed retriever |
| §5 build very slow | LLM rate limits | Talk through the corpus and `QUESTIONS.md`; continue when done |
| Tool queries return errors | Tools registered somewhere the retriever does not read | Tools must be in the `Tools` collection (the default); check `TOOLS_COLLECTION` is unset |
| Fleet tools return nothing | Partition selection, or the intermittent retrieval issue (see §7 talking points) | The §7 check retries automatically; for later cells just re-run. If it never succeeds, stop and show the dry-run output |
| `CategoryAlreadyBuiltError` in §8 | Service won't add to a built category | The cell falls back to a new category automatically |
| Deep Search answer misses a fact | LLM planner variance | Run the single tools (B1, techdocs) to show the underlying data |

---

## 5. Honest framing

- Telemetry is real (NASA C-MAPSS); aircraft, parts, technicians and documents are
  synthetic but realistic. Document numbers follow real AD/SB/AMM conventions and are not
  real directives.
- The AI Suite SDK is pre-alpha (pinned to a commit in the notebook). Custom tools and Deep
  Search are called over REST because the SDK doesn't expose them yet.
- LLM answers vary between runs. The answer key lists the facts that must be present, not
  exact wording.
