"""
Phase 4 tests — cover the graceful-failure paths that work without a live
txt2aql service. The "service reachable" acceptance criterion is verified
manually via scripts/eval_questions.py.
"""
import os

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.txt2aql import _MUTATING_RE, ask, health

client = TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Mutation guard (unit tests — no network needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("aql,should_match", [
    ("FOR e IN engines RETURN e", False),
    ("INSERT {name: 'x'} INTO engines", True),
    ("UPDATE e WITH {status: 'x'} IN engines", True),
    ("REPLACE e WITH {x: 1} IN engines", True),
    ("REMOVE e IN engines", True),
    ("UPSERT {_key: '1'} INSERT {} UPDATE {} IN engines", True),
    # edge case: word inside a string literal (still caught — conservative)
    ("FOR e IN engines FILTER e.name == 'insert' RETURN e", True),
])
def test_mutating_re(aql: str, should_match: bool) -> None:
    assert bool(_MUTATING_RE.search(aql)) == should_match


# ---------------------------------------------------------------------------
# ask() — graceful error when TXT2AQL_URL not set
# ---------------------------------------------------------------------------

def test_ask_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import asyncio
    import backend.txt2aql as _m
    _m._chain = None  # reset lazy cache so missing key is re-evaluated
    result = asyncio.run(ask("test question"))
    assert result["error"] == "service_not_configured"
    assert result["aql"] is None
    assert result["answer"]  # non-empty message


# ---------------------------------------------------------------------------
# health() — returns not_configured when API key unset
# ---------------------------------------------------------------------------

def test_health_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import asyncio
    import backend.txt2aql as _m
    _m._chain = None  # reset lazy cache
    status = asyncio.run(health())
    assert status == "not_configured"


# ---------------------------------------------------------------------------
# /api/ask endpoint — never returns 5xx regardless of service state
# ---------------------------------------------------------------------------

def test_ask_endpoint_never_crashes() -> None:
    """POST /api/ask must return 200 with a structured body even when txt2aql is down."""
    r = client.post("/api/ask", json={"question": "Which engines are critical?"})
    assert r.status_code == 200
    data = r.json()
    assert "answer" in data
    assert "aql" in data
    # error field is present but not a 5xx explosion
    assert data.get("error") in (None, "service_not_configured", "service_unavailable", "mutating_query_refused")


def test_ask_endpoint_requires_question_field() -> None:
    r = client.post("/api/ask", json={})
    assert r.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# /api/health — txt2aql field present and valid value
# ---------------------------------------------------------------------------

def test_health_endpoint_txt2aql_field() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    t2a = r.json()["txt2aql"]
    assert t2a in ("ok", "down", "not_configured")
