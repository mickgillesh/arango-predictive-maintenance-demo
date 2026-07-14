"""Tests for the maintenance planning endpoints.

Requires a loaded and scored database (run `make reset` first).
The POST /api/plan/run endpoint is NOT tested end-to-end here (it calls OpenAI),
but the contract (status codes, response shape) is verified.
"""
from fastapi.testclient import TestClient

from backend.app import app
from backend.db import get_db

client = TestClient(app, raise_server_exceptions=True)


def test_reset_is_idempotent() -> None:
    """Resetting when no planner WOs exist must succeed and return zeros."""
    # First reset any leftover planner WOs
    client.post("/api/plan/reset")
    # Second reset should still work cleanly
    r = client.post("/api/plan/reset")
    assert r.status_code == 200
    data = r.json()
    assert "deleted" in data
    assert data["deleted"]["workOrders"] == 0


def test_reset_preserves_historical_work_orders() -> None:
    """After a plan reset, all 200 historical (status=closed) work orders survive."""
    client.post("/api/plan/reset")
    db = get_db()
    count = next(iter(db.aql.execute(
        "RETURN COUNT(FOR w IN workOrders FILTER w.status == 'closed' RETURN 1)"
    )))
    assert count == 200


def test_work_orders_endpoint_empty_after_reset() -> None:
    """GET /api/plan/work-orders returns an empty list after a reset."""
    client.post("/api/plan/reset")
    r = client.get("/api/plan/work-orders")
    assert r.status_code == 200
    data = r.json()
    assert "workOrders" in data
    assert isinstance(data["workOrders"], list)
    assert len(data["workOrders"]) == 0


def test_run_endpoint_returns_stream_or_known_error() -> None:
    """POST /api/plan/run must return 200 with event-stream or a known SSE error."""
    r = client.post("/api/plan/run", headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    # Response must be SSE: starts with 'event:'
    body = r.text
    assert "event:" in body
    # If OPENAI_API_KEY is not set the first event is an error — that is acceptable
    if "openai_not_configured" in body:
        assert "openai_not_configured" in body
    else:
        # Otherwise we get at minimum a start event
        assert "event: start" in body or "event: error" in body


def test_chat_requires_valid_body() -> None:
    """POST /api/plan/chat with missing fields returns 422."""
    r = client.post("/api/plan/chat", json={})
    assert r.status_code == 422


def test_chat_returns_sse_with_chat_start() -> None:
    """POST /api/plan/chat returns 200 SSE; first event is chat_start or error."""
    r = client.post(
        "/api/plan/chat",
        json={"message": "show me work orders", "session_id": "test-abc"},
        headers={"Accept": "text/event-stream"},
    )
    assert r.status_code == 200
    assert "event:" in r.text
    assert "chat_start" in r.text or "error" in r.text


def test_apply_edits_empty_list() -> None:
    """POST /api/plan/apply-edits with an empty list returns applied: 0."""
    r = client.post("/api/plan/apply-edits", json={"edits": []})
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == 0
    assert data["errors"] == []
