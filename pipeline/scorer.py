"""
Deterministic health-index scorer for the AeroFleet predictive maintenance demo.

Replaces a trained ML model with a transparent, reproducible heuristic that
runs on real NASA C-MAPSS FD001 telemetry:

  1. For each engine, establish an early-life baseline (mean/std of each
     degradation-sensitive sensor over the first BASELINE_CYCLES cycles).
  2. Compute the absolute z-score drift of each sensor from that baseline,
     smoothed with an exponentially weighted moving average.
  3. Blend the per-sensor drifts into a single health index in [0, 1]
     (0 = as-new, 1 = fully degraded).
  4. Map the health index to a pseudo-RUL, a risk bucket, and the top
     "driver" sensors (which downstream code maps to subsystems).

The public interface is score_engine(); keep it stable so a real trained
model can replace this module later without touching the API or frontend.

Demo control: FORCED_CRITICAL lets you pin specific engine IDs to the
critical bucket for rehearsed demos, regardless of computed score.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# FD001 sensors that actually drift with degradation. The remaining channels
# (s1, s5, s6, s10, s16, s18, s19) are constant or near-constant in FD001
# and are excluded.
DRIFT_SENSORS = [
    "s2", "s3", "s4", "s7", "s8", "s9", "s11",
    "s12", "s13", "s14", "s15", "s17", "s20", "s21",
]

BASELINE_CYCLES = 30    # cycles used to establish the healthy baseline
EWMA_SPAN = 15          # smoothing span for the drift signal, in cycles
DRIFT_SATURATION = 12.5 # z-score drift treated as "fully degraded"
                        # tuned so FD001 fleet → ~3 critical, ~10 warning, ~87 healthy
MAX_RUL = 125           # pseudo-RUL ceiling, matching the usual C-MAPSS cap
TOP_DRIVERS = 3         # number of driver sensors to report

RISK_BUCKETS = [        # (health index threshold, bucket name)
    (0.75, "critical"),
    (0.62, "warning"),  # raised from default 0.45 to tighten warning band
    (0.00, "healthy"),
]

# Engine IDs to force into the critical bucket for rehearsed demos.
# Leave empty to let the data speak for itself.
FORCED_CRITICAL: set[int] = set()


@dataclass
class EngineScore:
    engine_id: int
    health_index: float          # 0 = as-new, 1 = fully degraded
    pseudo_rul: int              # cycles, capped at MAX_RUL
    risk: str                    # healthy | warning | critical
    drivers: list[str] = field(default_factory=list)  # top drifting sensors
    forced: bool = False         # True if pinned via FORCED_CRITICAL

    def to_document(self) -> dict:
        """Fields to merge onto the Engine vertex in ArangoDB."""
        return {
            "healthIndex": round(self.health_index, 4),
            "predictedRUL": self.pseudo_rul,
            "riskScore": round(self.health_index, 4),
            "riskBucket": self.risk,
            "driverSensors": self.drivers,
            "scoringMethod": "health-index-v1" + ("-forced" if self.forced else ""),
        }


def _sensor_drift(readings: pd.DataFrame) -> pd.DataFrame:
    """Per-cycle absolute z-score drift of each sensor from its baseline.

    `readings` is one engine's telemetry, ordered by cycle, with columns
    DRIFT_SENSORS. Returns a DataFrame of the same shape.
    """
    baseline = readings.head(BASELINE_CYCLES)
    mu = baseline.mean()
    sigma = baseline.std().replace(0, np.nan)  # guard flat channels
    z = ((readings - mu) / sigma).abs().fillna(0.0)
    return z.ewm(span=EWMA_SPAN).mean()


def score_engine(readings: pd.DataFrame, engine_id: int) -> EngineScore:
    """Score one engine from its full telemetry history.

    `readings` must be sorted by cycle and contain the DRIFT_SENSORS columns.
    """
    drift = _sensor_drift(readings[DRIFT_SENSORS])
    latest = drift.iloc[-1]

    # Health index: mean drift across sensors, saturated and clipped to [0, 1].
    health_index = float(np.clip(latest.mean() / DRIFT_SATURATION, 0.0, 1.0))

    forced = engine_id in FORCED_CRITICAL
    if forced:
        health_index = max(health_index, 0.90)

    pseudo_rul = int(round(MAX_RUL * (1.0 - health_index)))
    risk = next(name for threshold, name in RISK_BUCKETS
                if health_index >= threshold)
    drivers = latest.nlargest(TOP_DRIVERS).index.tolist()

    return EngineScore(engine_id, health_index, pseudo_rul, risk,
                       drivers, forced)


def score_fleet(telemetry: pd.DataFrame) -> list[EngineScore]:
    """Score every engine in a C-MAPSS telemetry frame.

    `telemetry` needs columns: engine_id, cycle, and the DRIFT_SENSORS.
    """
    scores = []
    for engine_id, group in telemetry.sort_values("cycle").groupby("engine_id"):
        scores.append(score_engine(group, int(engine_id)))
    return scores
