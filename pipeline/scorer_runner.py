"""
Score every engine in the fleet and write results back to ArangoDB.

Reads telemetry from the `readings` collection (populated by make load),
runs the deterministic health-index scorer, resolves driver sensors to
subsystem names, and merges all fields onto each engine vertex.

Entry points:
  make score               — CLI via __main__
  backend/app.py lifespan  — auto-run when engines lack scoringMethod
"""
from collections import Counter

import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from backend.db import get_db  # noqa: E402
from pipeline.scorer import score_fleet  # noqa: E402
from pipeline.synthetic_graph import SENSOR_TO_SUBSYSTEM  # noqa: E402

_READING_COLS = (
    ["engineId", "cycle", "op1", "op2", "op3"]
    + [f"s{i}" for i in range(1, 22)]
)


def _driver_subsystems(driver_sensors: list[str]) -> list[str]:
    """Map driver sensor names → unique subsystem names (order-preserved)."""
    seen: list[str] = []
    for s in driver_sensors:
        sub = SENSOR_TO_SUBSYSTEM.get(s)
        if sub and sub not in seen:
            seen.append(sub)
    return seen


def score_and_writeback() -> Counter:
    """Fetch readings, score the fleet, write results back to engines collection.

    Returns a Counter of riskBucket → engine count.
    """
    db = get_db()

    # Fetch all readings in one query
    cursor = db.aql.execute(
        "FOR r IN readings RETURN KEEP(r, @cols)",
        bind_vars={"cols": _READING_COLS},
        batch_size=5000,
    )
    df = pd.DataFrame(list(cursor))
    # scorer.py expects engine_id (snake_case)
    df = df.rename(columns={"engineId": "engine_id"})

    scores = score_fleet(df)

    updates = []
    for s in scores:
        doc = s.to_document()
        doc["driverSubsystems"] = _driver_subsystems(s.drivers)
        doc["_key"] = str(s.engine_id)
        updates.append(doc)

    db.collection("engines").import_bulk(updates, on_duplicate="update")
    return Counter(s.risk for s in scores)


def main() -> None:
    import time
    t0 = time.monotonic()
    dist = score_and_writeback()
    print(f"Scored 100 engines in {time.monotonic() - t0:.1f}s")
    print(f"  critical : {dist['critical']:>3}")
    print(f"  warning  : {dist['warning']:>3}")
    print(f"  healthy  : {dist['healthy']:>3}")


if __name__ == "__main__":
    main()
