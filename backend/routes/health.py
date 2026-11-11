from fastapi import APIRouter
from pydantic import BaseModel

from backend.aql import Q_HEALTH_CHECK
from backend.db import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str | None = None


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_status = "ok"
    version = None
    try:
        result = next(iter(get_db().aql.execute(Q_HEALTH_CHECK)))
        version = result.get("version")
    except Exception:
        db_status = "down"

    overall = "ok" if db_status == "ok" else "error"
    return HealthResponse(status=overall, db=db_status, version=version)
