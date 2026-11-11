"""
Generate technician notes for every engine and embed them with OpenAI.

Notes are generated deterministically from each engine's scored health state
(so `make score` must run before this). Embeddings are stored alongside the
note text on each engine vertex, enabling hybrid RAG demo patterns via the
ArangoDB query editor.

Entry point:
  make embed   — CLI via __main__

Idempotent: if all 100 engines already carry a `notesEmbedding` field the
script exits immediately without making any OpenAI API calls.
"""

import os
import random
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from backend.aql import Q_EMBED_COUNT_EMBEDDED, Q_EMBED_ENGINES_FOR_NOTES  # noqa: E402
from backend.db import get_db  # noqa: E402

# Must match the embedding model configured on the AutoRAG retriever, otherwise
# the retriever's query vectors and these note vectors live in different spaces.
_EMBED_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
# The vectors must also have the same LENGTH as the retriever's query vectors:
# the AI Suite retriever embedded queries at 512 dimensions (dry run 2026-11-11:
# "a vector must be of dimension 1536, but is 512"). text-embedding-3-* models
# shorten natively via the `dimensions` parameter. The notebook (§7) reads the
# real value from AutoGraph's chunk embeddings and re-embeds if it differs.
_DEFAULT_DIMENSIONS = 512
_ENGINE_COUNT = 100
# Name the Custom Retriever looks for (idx_vector_{collection}_{field}), so
# auto_create_indexes finds this index instead of building a second one.
_VECTOR_INDEX_NAME = "idx_vector_engines_notesEmbedding"

# ---------------------------------------------------------------------------
# Note templates — one bucket per risk level
# ---------------------------------------------------------------------------

_CRITICAL_TEMPLATES = [
    (
        "{model} in-service since {eis}. Cycle estimate: {cycle}. "
        "Significant {subsystem} degradation confirmed; sensor drift exceeds 2σ on {sensors}. "
        "Compressor wash completed — performance recovery insufficient. "
        "Borescope inspection mandatory before next revenue flight. "
        "Shop visit authorised; coordinate with MCC for AOG cover."
    ),
    (
        "{model} — critical health index. {subsystem} outlet temperature trending "
        "18 °C above baseline; vibration signature indicates stage-2 blade erosion. "
        "On-condition monitoring elevated to daily. "
        "{subsystem2} coolant bleed flow reduced by 12 %. "
        "Spare parts requisition raised — blade set on backorder."
    ),
    (
        "Unscheduled removal candidate — {model}. "
        "{subsystem} and {subsystem2} showing correlated anomaly pattern on {sensors}. "
        "HSI findings: HPT shroud segment deterioration, nozzle guide vane cracking observed. "
        "Engine grounded pending engineering disposition."
    ),
]

_WARNING_TEMPLATES = [
    (
        "{model} in-service since {eis}. Cycle estimate: {cycle}. "
        "Early-stage {subsystem} wear detected; {sensors} drifting within 1–1.5σ band. "
        "Performance still within certified limits. "
        "Scheduled borescope at next line check. "
        "Monitor fuel-flow ratio for further deterioration."
    ),
    (
        "{model} — warning threshold crossed on {subsystem}. "
        "EWMA trend indicates continued degradation at current rate. "
        "Compressor wash scheduled for next base maintenance window. "
        "Parts usage review initiated for {subsystem} consumables. "
        "Predictive RUL estimate: {cycle_next} cycles remaining."
    ),
    (
        "Trend alert — {model}. {subsystem} outlet pressure margin reducing; "
        "{sensors} showing upward drift pattern. "
        "No immediate airworthiness concern. "
        "Recommended: bring forward next C-check by 150 cycles. "
        "Re-evaluate after engine water wash."
    ),
]

_HEALTHY_TEMPLATES = [
    (
        "{model} in-service since {eis}. Cycle estimate: {cycle}. "
        "Routine line inspection completed. All sensor readings nominal; "
        "{subsystem} performance within baseline. "
        "No anomalies detected. Next scheduled check at {cycle_next} cycles."
    ),
    (
        "{model} — C-check completed satisfactorily. "
        "Borescope showed no erosion on fan or HPC blades. "
        "Oil consumption within limits; {subsystem} performing nominally. "
        "Engine returned to service."
    ),
    (
        "Routine on-wing inspection — {model}. "
        "FOD check clear. All fluid levels nominal. "
        "{subsystem} performance restoration wash not required. "
        "Engine health trend stable. No maintenance action raised."
    ),
]


def _generate_note(engine: dict[str, Any], rng: random.Random) -> str:
    """Return a deterministic maintenance note derived from a scored engine doc."""
    risk = engine.get("riskBucket", "healthy")
    model = engine.get("model", "CFM56-7B27")
    eis = engine.get("entryIntoService", "2015-01-01")
    driver_subs: list[str] = engine.get("driverSubsystems") or ["HPC"]
    subsystem = driver_subs[0]
    subsystem2 = driver_subs[1] if len(driver_subs) > 1 else "LPT"
    driver_sensors: list[str] = engine.get("driverSensors") or ["s3"]
    sensors = ", ".join(driver_sensors[:2])

    health: float = engine.get("healthIndex", 0.5)
    cycle = int(50 + health * 75)
    cycle_next = max(30, 200 - cycle)

    if risk == "critical":
        template = rng.choice(_CRITICAL_TEMPLATES)
    elif risk == "warning":
        template = rng.choice(_WARNING_TEMPLATES)
    else:
        template = rng.choice(_HEALTHY_TEMPLATES)

    return template.format(
        model=model,
        eis=eis,
        subsystem=subsystem,
        subsystem2=subsystem2,
        sensors=sensors,
        cycle=cycle,
        cycle_next=cycle_next,
    )


# ---------------------------------------------------------------------------
# Vector index helpers
# ---------------------------------------------------------------------------

def embedding_dimensions() -> int:
    """Target length of the note embeddings: ``EMBEDDING_DIMENSIONS`` or 512."""
    return int(os.environ.get("EMBEDDING_DIMENSIONS") or _DEFAULT_DIMENSIONS)


def _drop_mismatched_vector_indexes(db: Any, dimension: int) -> bool:
    """Drop vector indexes on engines.notesEmbedding whose dimension differs.

    Must run BEFORE writing vectors of a new length: ArangoDB rejects a write
    that does not fit an existing vector index (ERR 17 "type error"), and a
    stale index makes every vector query fail. Returns True if a matching index
    remains.
    """
    col = db.collection("engines")
    matching = False
    for idx in col.indexes():
        if idx.get("type") != "vector" or idx.get("fields") != ["notesEmbedding"]:
            continue
        if (idx.get("params") or {}).get("dimension") == dimension:
            matching = True
            continue
        col.delete_index(idx["id"].split("/")[-1])
        print(f"  Dropped vector index {idx.get('name')} "
              f"({(idx.get('params') or {}).get('dimension')}-dim).")
    return matching


def _ensure_vector_index(db: Any, dimension: int) -> None:
    """Create the cosine vector index on engines.notesEmbedding unless a matching
    one exists (mismatched ones are dropped first)."""
    col = db.collection("engines")
    if _drop_mismatched_vector_indexes(db, dimension):
        print(f"  Vector index already present ({dimension}-dim).")
        return
    col.add_index({
        "type": "vector",
        "name": _VECTOR_INDEX_NAME,
        "fields": ["notesEmbedding"],
        "params": {
            "metric": "cosine",
            "dimension": dimension,
            "nLists": 2,
        },
    })
    print(f"  Vector index {_VECTOR_INDEX_NAME} created (cosine, {dimension}-dim).")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def embed_and_writeback(dimensions: int | None = None) -> int:
    """Generate notes + embeddings and write back to engine vertices.

    ``dimensions`` defaults to :func:`embedding_dimensions`. Engines whose
    embedding already has that length are left alone, so re-running is cheap,
    and a dimension change re-embeds everything and rebuilds the vector index.
    Returns the number of documents updated (0 if already complete).
    """
    from openai import OpenAI

    db = get_db()
    dimensions = dimensions or embedding_dimensions()

    # Idempotency: count engines that already have an embedding of this length
    cursor = db.aql.execute(Q_EMBED_COUNT_EMBEDDED, bind_vars={"dimension": dimensions})
    already = next(iter(cursor), 0)
    if already >= _ENGINE_COUNT:
        print(f"All {_ENGINE_COUNT} engines already embedded ({dimensions}-dim) — nothing to do.")
        _ensure_vector_index(db, dimension=dimensions)
        return 0

    print(f"  {already}/{_ENGINE_COUNT} engines embedded — generating the rest.")

    # Fetch scored engine docs (need riskBucket, driverSubsystems etc.).
    # SORT is required for determinism: the seeded rng below is consumed in
    # this order, so an unordered scan would assign different notes per run.
    cursor = db.aql.execute(Q_EMBED_ENGINES_FOR_NOTES)
    engines = list(cursor)
    if not engines:
        raise RuntimeError(
            "No engine documents found — run `make load && make score` first."
        )

    # Generate notes deterministically (same base seed as synthetic_graph.py)
    rng = random.Random(42)
    notes = [_generate_note(e, rng) for e in engines]

    # One batch API call for all 100 notes
    client = OpenAI()
    print(f"  Calling OpenAI {_EMBED_MODEL} ({dimensions}-dim) for {len(notes)} notes…")
    t0 = time.monotonic()
    response = client.embeddings.create(model=_EMBED_MODEL, input=notes, dimensions=dimensions)
    print(f"  Embeddings received in {time.monotonic() - t0:.1f}s.")

    ordered = sorted(response.data, key=lambda d: d.index)
    updates = [
        {
            "_key": engines[i]["_key"],
            "technicianNotes": notes[i],
            "notesEmbedding": ordered[i].embedding,
        }
        for i in range(len(engines))
    ]

    _drop_mismatched_vector_indexes(db, dimensions)  # before writing new-length vectors
    db.collection("engines").import_bulk(updates, on_duplicate="update")
    print(f"  Updated {len(updates)} engine documents.")

    _ensure_vector_index(db, dimension=len(ordered[0].embedding))
    return len(updates)


def main() -> None:
    t0 = time.monotonic()
    print("=== AeroFleet embed_runner ===")
    n = embed_and_writeback()
    if n > 0:
        print(f"Done in {time.monotonic() - t0:.1f}s.")


if __name__ == "__main__":
    main()
