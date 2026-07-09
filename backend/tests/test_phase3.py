"""Endpoint tests for Phase 3 — run against the loaded + scored database."""
import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /api/fleet
# ---------------------------------------------------------------------------

def test_fleet_returns_kpi_and_engine_list() -> None:
    r = client.get("/api/fleet")
    assert r.status_code == 200
    data = r.json()
    assert "kpi" in data
    assert "engines" in data
    assert isinstance(data["engines"], list)
    assert len(data["engines"]) == 100


def test_fleet_engines_sorted_by_rul_ascending() -> None:
    r = client.get("/api/fleet")
    ruls = [e["predictedRUL"] for e in r.json()["engines"] if e["predictedRUL"] is not None]
    assert ruls == sorted(ruls)


def test_fleet_kpi_sums_to_100() -> None:
    kpi = client.get("/api/fleet").json()["kpi"]
    total = kpi.get("critical", 0) + kpi.get("warning", 0) + kpi.get("healthy", 0)
    assert total == 100


# ---------------------------------------------------------------------------
# GET /api/engines/{id}
# ---------------------------------------------------------------------------

def test_engine_detail_happy_path() -> None:
    r = client.get("/api/engines/48")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "48"
    assert data["riskBucket"] == "critical"
    assert data["aircraft"] is not None
    assert data["driverSubsystems"]


def test_engine_detail_404() -> None:
    r = client.get("/api/engines/9999")
    assert r.status_code == 404


def test_engine_detail_invalid_id_404() -> None:
    r = client.get("/api/engines/notanid")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/engines/{id}/readings
# ---------------------------------------------------------------------------

def test_readings_default_sensors() -> None:
    r = client.get("/api/engines/48/readings")
    assert r.status_code == 200
    data = r.json()
    assert data["engineId"] == "48"
    assert data["sensors"]  # non-empty
    assert data["readings"]
    # Cycles must be present in every row
    assert all("cycle" in row for row in data["readings"])


def test_readings_explicit_sensors() -> None:
    r = client.get("/api/engines/48/readings?sensors=s9,s14")
    assert r.status_code == 200
    data = r.json()
    assert set(data["sensors"]) == {"s9", "s14"}
    # Each row should have cycle, s9, s14
    row = data["readings"][0]
    assert "s9" in row and "s14" in row


def test_readings_downsampled_to_at_most_500() -> None:
    # FD001 max ~362 cycles — all engines stay under 500, but the cap must hold
    r = client.get("/api/engines/1/readings?sensors=s2")
    assert r.status_code == 200
    assert len(r.json()["readings"]) <= 500


def test_readings_cycles_ascending() -> None:
    r = client.get("/api/engines/48/readings?sensors=s9")
    cycles = [row["cycle"] for row in r.json()["readings"]]
    assert cycles == sorted(cycles)


def test_readings_404_unknown_engine() -> None:
    r = client.get("/api/engines/9999/readings")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/engines/{id}/impact
# ---------------------------------------------------------------------------

def test_impact_happy_path() -> None:
    r = client.get("/api/engines/48/impact")
    assert r.status_code == 200
    data = r.json()
    assert data["engine"]["id"] == "48"
    assert data["aircraft"] is not None
    assert data["degradingSubsystems"]
    assert data["parts"]
    assert data["technicians"] is not None  # may be empty if base has no certs


def test_impact_has_blocking_parts_for_critical_engine() -> None:
    """At least one critical engine must return at least one blocking part."""
    critical_ids = ["48", "9", "51"]
    blocking_found = False
    for eid in critical_ids:
        r = client.get(f"/api/engines/{eid}/impact")
        assert r.status_code == 200, f"Engine {eid} returned {r.status_code}"
        if r.json().get("blockingParts"):
            blocking_found = True
            break
    assert blocking_found, "No blocking parts found for any critical engine"


def test_impact_404() -> None:
    r = client.get("/api/engines/9999/impact")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

def test_health_db_is_ok() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["db"] == "ok"
    assert data["version"] is not None


# ---------------------------------------------------------------------------
# GET /api/suggestions
# ---------------------------------------------------------------------------

def test_suggestions_non_empty() -> None:
    r = client.get("/api/suggestions")
    assert r.status_code == 200
    suggestions = r.json()
    assert isinstance(suggestions, list)
    assert len(suggestions) >= 4


# ---------------------------------------------------------------------------
# POST /api/ask
# ---------------------------------------------------------------------------

def test_ask_returns_graceful_response_when_not_configured() -> None:
    r = client.post("/api/ask", json={"question": "Which engines are critical?"})
    assert r.status_code == 200
    data = r.json()
    # Either a real answer or a graceful error — never a 500
    assert "answer" in data
    assert data.get("error") in (None, "service_not_configured", "service_unavailable")
