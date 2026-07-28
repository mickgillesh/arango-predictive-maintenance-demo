import logging
import os
import pathlib
import secrets
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

load_dotenv(".env.local", override=True)

from backend.routes import chat, engines, fleet, health, planning
from backend.routes.auth import router as auth_router

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
    """Run scoring at startup if any engine lacks scoringMethod.

    If SCORER_URL is set the request is delegated to the scorer sidecar service
    (container mode). Otherwise the scorer runs inline (local dev mode).
    """
    try:
        from backend.db import get_db
        db = get_db()
        needs = next(iter(db.aql.execute(_NEEDS_SCORE_AQL)))
        if not needs:
            return
        scorer_url = os.environ.get("SCORER_URL", "").strip()
        if scorer_url:
            import httpx
            log.info("Delegating scoring to scorer service at %s", scorer_url)
            resp = httpx.post(f"{scorer_url}/score/fleet", timeout=120.0)
            resp.raise_for_status()
            log.info("Scorer service response: %s", resp.json())
        else:
            log.info("Engines not yet scored — running score_and_writeback() inline")
            from pipeline.scorer_runner import score_and_writeback
            dist = score_and_writeback()
            log.info("Scoring complete: %s", dict(dist))
    except Exception as exc:
        log.warning("Startup scoring skipped: %s", exc)


class _AuthGuard(BaseHTTPMiddleware):
    """Reject unauthenticated requests; skip /auth/* paths."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path.startswith("/auth/"):
            return await call_next(request)
        if not request.session.get("user"):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/auth/login")
        return await call_next(request)


_session_secret = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
if not os.environ.get("SESSION_SECRET"):
    log.warning("SESSION_SECRET not set — using a per-process ephemeral key (sessions will not survive restarts)")

app = FastAPI(title="AeroFleet Demo", lifespan=lifespan)

# SessionMiddleware must wrap AuthGuard so request.session is populated first.
# In Starlette, the last middleware added is the outermost (runs first).
app.add_middleware(_AuthGuard)
app.add_middleware(SessionMiddleware, secret_key=_session_secret, https_only=False)

app.include_router(auth_router)
app.include_router(fleet.router, prefix="/api")
app.include_router(engines.router, prefix="/api/engines")
app.include_router(chat.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(planning.router, prefix="/api/plan")

# Serve built frontend — only mount if dist exists
_dist = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
