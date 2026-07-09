import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.aql import Q_ENGINE_BY_ID, Q_ENGINE_IMPACT, Q_ENGINE_READINGS
from backend.db import get_db

router = APIRouter()

_VALID_SENSORS = {f"s{i}" for i in range(1, 22)}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class AircraftInfo(BaseModel):
    tailNumber: str | None
    base: str | None


class EngineDetail(BaseModel):
    id: str
    engineId: int | None = None
    model: str | None = None
    entryIntoService: str | None = None
    healthIndex: float | None = None
    predictedRUL: int | None = None
    riskScore: float | None = None
    riskBucket: str | None = None
    driverSensors: list[str] = []
    driverSubsystems: list[str] = []
    scoringMethod: str | None = None
    aircraft: AircraftInfo | None = None


class ReadingsResponse(BaseModel):
    engineId: str
    sensors: list[str]
    readings: list[dict]


class PartInfo(BaseModel):
    id: str
    name: str
    subsystemType: str
    stockLevel: int
    leadTimeDays: int
    blocking: bool


class TechInfo(BaseModel):
    id: str
    name: str
    homeBase: str
    certifications: list[str]


class ImpactEngine(BaseModel):
    id: str
    riskBucket: str | None = None
    predictedRUL: int | None = None
    driverSubsystems: list[str] = []


class ImpactResponse(BaseModel):
    engine: ImpactEngine
    aircraft: AircraftInfo | None = None
    degradingSubsystems: list[str] = []
    parts: list[PartInfo] = []
    technicians: list[TechInfo] = []
    blockingParts: list[PartInfo] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{engine_id}", response_model=EngineDetail)
async def engine_detail(engine_id: str) -> EngineDetail:
    db = get_db()
    result = list(db.aql.execute(Q_ENGINE_BY_ID, bind_vars={"engineId": engine_id}))
    if not result:
        raise HTTPException(status_code=404, detail=f"Engine '{engine_id}' not found")
    row = result[0]
    row["id"] = row.pop("_key")
    return EngineDetail(**row)


@router.get("/{engine_id}/readings", response_model=ReadingsResponse)
async def engine_readings(engine_id: str, sensors: str | None = None) -> ReadingsResponse:
    db = get_db()

    try:
        engine_id_int = int(engine_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Engine '{engine_id}' not found")

    # Resolve sensor list (default: engine's driverSensors)
    if sensors:
        sensor_list = [s.strip() for s in sensors.split(",") if s.strip() in _VALID_SENSORS]
        if not sensor_list:
            raise HTTPException(status_code=422, detail="No valid sensor names provided")
    else:
        eng = list(db.aql.execute(Q_ENGINE_BY_ID, bind_vars={"engineId": engine_id}))
        if not eng:
            raise HTTPException(status_code=404, detail=f"Engine '{engine_id}' not found")
        sensor_list = eng[0].get("driverSensors") or ["s9"]

    rows = list(
        db.aql.execute(
            Q_ENGINE_READINGS,
            bind_vars={"engineId": engine_id_int, "sensors": sensor_list},
        )
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No readings for engine '{engine_id}'")

    # Downsample to ≤500 points
    if len(rows) > 500:
        stride = math.ceil(len(rows) / 500)
        rows = rows[::stride]

    return ReadingsResponse(engineId=engine_id, sensors=sensor_list, readings=rows)


@router.get("/{engine_id}/impact", response_model=ImpactResponse)
async def engine_impact(engine_id: str) -> ImpactResponse:
    db = get_db()
    result = list(db.aql.execute(Q_ENGINE_IMPACT, bind_vars={"engineId": engine_id}))
    if not result or result[0].get("engine") is None:
        raise HTTPException(status_code=404, detail=f"Engine '{engine_id}' not found")
    return ImpactResponse(**result[0])
