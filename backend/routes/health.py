from fastapi import APIRouter
from pydantic import BaseModel

from backend.aql import Q_HEALTH_CHECK
from backend.db import get_db
import backend.txt2aql as txt2aql_client

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    db: str
    txt2aql: str
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

    t2a_status = await txt2aql_client.health()

    overall = "ok" if db_status == "ok" else "error"
    return HealthResponse(status=overall, db=db_status, txt2aql=t2a_status, version=version)
