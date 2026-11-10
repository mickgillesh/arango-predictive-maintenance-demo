"""
AeroFleet Scorer Service — runs as a standalone container alongside ArangoDB.

Exposes HTTP endpoints so the main backend (or a cron schedule) can trigger
re-scoring without running the model in-process. In the demo this represents a
'prediction model deployed next to the database': the readings collection holds
a snapshot of the NASA C-MAPSS sensor data, which stands in for live telemetry.

In production this container would be replaced by a trained ML model that
implements the same /health and /score/* interface.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv(".env.local", override=True)

log = logging.getLogger(__name__)

_READING_COLS = (
    ["engineId", "cycle", "op1", "op2", "op3"]
    + [f"s{i}" for i in range(1, 22)]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "Scorer service ready — ARANGO_URL=%s DB=%s",
        os.environ.get("ARANGO_URL", "(unset)"),
        os.environ.get("ARANGO_DB", "(unset)"),
    )
    yield


app = FastAPI(title="AeroFleet Scorer Service", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "aerofleet-scorer"}


@app.post("/score/fleet")
async def score_fleet_endpoint() -> dict:
    """Read the sensor snapshot from the readings collection, score all engines,
    and write healthIndex / predictedRUL / riskBucket back to each engine vertex.

    This endpoint mimics what a live telemetry ingestion event would trigger.
    The readings collection holds the NASA C-MAPSS FD001 sensor snapshot.
    """
    try:
        from pipeline.scorer_runner import score_and_writeback

        dist = await asyncio.to_thread(score_and_writeback)
        return {
            "scored": sum(dist.values()),
            "distribution": dict(dist),
        }
    except Exception as exc:
        log.exception("score_fleet failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/score/engine/{engine_id}")
async def score_engine_endpoint(engine_id: int) -> dict:
    """Score a single engine from its existing readings snapshot and write back.

    Useful for targeted rescoring after new telemetry arrives for one engine.
    """
    try:
        from pipeline.scorer import score_engine
        from pipeline.scorer_runner import _driver_subsystems
        from backend.db import get_db

        def _run() -> dict:
            db = get_db()
            cursor = db.aql.execute(
                "FOR r IN readings FILTER r.engineId == @eid RETURN KEEP(r, @cols)",
                bind_vars={"eid": engine_id, "cols": _READING_COLS},
            )
            rows = list(cursor)
            if not rows:
                raise ValueError(f"No readings found for engine {engine_id}")

            df = (
                pd.DataFrame(rows)
                .rename(columns={"engineId": "engine_id"})
                .sort_values("cycle")
            )
            score = score_engine(df, engine_id)
            doc = score.to_document()
            doc["driverSubsystems"] = _driver_subsystems(score.drivers)
            db.collection("engines").update({"_key": str(engine_id), **doc})
            return {"engine_id": engine_id, **doc}

        return await asyncio.to_thread(_run)

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("score_engine(%s) failed: %s", engine_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
