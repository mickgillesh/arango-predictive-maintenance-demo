"""Smoke tests for Phase 2 — run after `make score`."""
from backend.db import get_db
from pipeline.scorer_runner import score_and_writeback


def test_all_engines_have_scoring_fields() -> None:
    """Every engine must have the four scoring fields after make score."""
    db = get_db()
    result = list(
        db.aql.execute(
            """
            RETURN {
              total:          COUNT(engines),
              scored:         COUNT(FOR e IN engines FILTER e.scoringMethod  != null RETURN 1),
              withRUL:        COUNT(FOR e IN engines FILTER e.predictedRUL   != null RETURN 1),
              withDriverSubs: COUNT(FOR e IN engines FILTER e.driverSubsystems != null RETURN 1)
            }
            """
        )
    )
    r = result[0]
    assert r["total"] == 100
    assert r["scored"] == 100, f"Only {r['scored']}/100 engines scored"
    assert r["withRUL"] == 100
    assert r["withDriverSubs"] == 100


def test_risk_distribution() -> None:
    """Fleet distribution must hit the demo target: 3-5 critical, 8-17 warning."""
    db = get_db()
    rows = list(
        db.aql.execute(
            """
            FOR e IN engines
              COLLECT bucket = e.riskBucket WITH COUNT INTO cnt
              RETURN {bucket, cnt}
            """
        )
    )
    dist = {r["bucket"]: r["cnt"] for r in rows}
    assert 3 <= dist.get("critical", 0) <= 5, f"critical={dist.get('critical')}"
    assert 8 <= dist.get("warning", 0) <= 17, f"warning={dist.get('warning')}"
    assert dist.get("healthy", 0) >= 75, f"healthy={dist.get('healthy')}"


def test_scoring_is_deterministic() -> None:
    """Two consecutive score runs must produce identical predictedRUL values."""
    db = get_db()

    def snapshot() -> list:
        return list(
            db.aql.execute(
                "FOR e IN engines SORT e._key RETURN {k: e._key, rul: e.predictedRUL}"
            )
        )

    first = snapshot()
    score_and_writeback()
    second = snapshot()
    assert first == second, "Scoring is not deterministic"
