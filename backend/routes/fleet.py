from fastapi import APIRouter
from pydantic import BaseModel

from backend.aql import Q_FLEET_LIST
from backend.db import get_db

router = APIRouter()


class EngineRow(BaseModel):
    id: str
    tailNumber: str | None
    base: str | None
    predictedRUL: int | None
    riskBucket: str | None


class KPI(BaseModel):
    critical: int = 0
    warning: int = 0
    healthy: int = 0


class FleetResponse(BaseModel):
    kpi: KPI
    engines: list[EngineRow]


@router.get("/fleet", response_model=FleetResponse)
async def fleet() -> FleetResponse:
    db = get_db()
    result = next(iter(db.aql.execute(Q_FLEET_LIST)))
    return FleetResponse(
        kpi=KPI(**result["kpi"]),
        engines=[EngineRow(**e) for e in result["engines"]],
    )
