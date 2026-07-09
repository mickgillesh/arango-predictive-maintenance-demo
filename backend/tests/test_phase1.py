"""Smoke tests for Phase 1 — run after `make load`."""
import pytest

from backend.db import get_db


def test_engines_per_aircraft_is_two() -> None:
    """Every aircraft must have exactly 2 engines installed."""
    db = get_db()
    result = list(
        db.aql.execute(
            """
            LET violations = (
              FOR a IN aircraft
                LET cnt = LENGTH(FOR e IN 1..1 INBOUND a installedOn RETURN 1)
                FILTER cnt != 2
                RETURN {aircraft: a._key, count: cnt}
            )
            RETURN {ok: LENGTH(violations) == 0, violations: violations}
            """
        )
    )
    assert result[0]["ok"] is True, f"Aircraft with wrong engine count: {result[0]['violations']}"


def test_two_hop_traversal_reaches_subsystems_and_sensors() -> None:
    """A 2-hop traversal from an engine yields 6 subsystems and 21 sensors."""
    db = get_db()
    result = list(
        db.aql.execute(
            """
            FOR e IN engines LIMIT 1
              LET subs = (FOR sub IN 1..1 INBOUND e partOf RETURN sub)
              LET sens = (
                FOR sub IN subs
                  FOR sen IN 1..1 INBOUND sub monitors
                  RETURN sen
              )
              RETURN {subsystemCount: LENGTH(subs), sensorCount: LENGTH(sens)}
            """
        )
    )
    assert result, "No engines found — run `make load` first"
    row = result[0]
    assert row["subsystemCount"] == 6, f"Expected 6 subsystems, got {row['subsystemCount']}"
    assert row["sensorCount"] == 21, f"Expected 21 sensors, got {row['sensorCount']}"


def test_at_least_one_part_zero_stock() -> None:
    """At least one catalogue part must have stockLevel == 0."""
    db = get_db()
    result = list(
        db.aql.execute(
            "RETURN COUNT(FOR p IN parts FILTER p.stockLevel == 0 RETURN 1)"
        )
    )
    assert result[0] >= 1, "No zero-stock parts found — check synthetic_graph.py ZERO_STOCK_PARTS"
