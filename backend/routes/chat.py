from fastapi import APIRouter
from pydantic import BaseModel

import backend.txt2aql as txt2aql_client

router = APIRouter()

SUGGESTIONS: list[str] = [
    "Which engines have less than 40 cycles of remaining useful life?",
    "How many engines at each base are in critical or warning status?",
    "Which technicians at LHR are certified for HPC maintenance?",
    "What spare parts are out of stock and needed for critical engines?",
    "Show work orders performed on engine 48 in the last two years",
    "Which aircraft has the most engines in critical condition?",
    "List all HPC subsystems with a degrading engine",
    "How many engines has each technician worked on?",
]


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str | None
    aql: str | None
    raw: dict | None = None
    error: str | None = None


@router.get("/suggestions", response_model=list[str])
async def suggestions() -> list[str]:
    return SUGGESTIONS


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest) -> AskResponse:
    result = await txt2aql_client.ask(body.question)
    return AskResponse(**result)
