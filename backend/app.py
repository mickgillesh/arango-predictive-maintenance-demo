import logging
import pathlib
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv(".env.local", override=True)

from backend.routes import chat, engines, fleet, health, planning

log = logging.getLogger(__name__)

_NEEDS_SCORE_AQL = """
RETURN COUNT(
  FOR e IN engines FILTER e.scoringMethod == null RETURN 1
) > 0
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    _maybe_score()
    yield


def _maybe_score() -> None:
    """Run scoring at startup if any engine lacks scoringMethod."""
    try:
        from backend.db import get_db
        db = get_db()
        needs = next(iter(db.aql.execute(_NEEDS_SCORE_AQL)))
        if needs:
            log.info("Engines not yet scored — running score_and_writeback()")
            from pipeline.scorer_runner import score_and_writeback
            dist = score_and_writeback()
            log.info("Scoring complete: %s", dict(dist))
    except Exception as exc:
        log.warning("Startup scoring skipped: %s", exc)


app = FastAPI(title="AeroFleet Demo", lifespan=lifespan)

app.include_router(fleet.router, prefix="/api")
app.include_router(engines.router, prefix="/api/engines")
app.include_router(chat.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(planning.router, prefix="/api/plan")

# Serve built frontend — only mount if dist exists
_dist = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
