"""Poisson-process safety car probability model, fit per circuit from FastF1 TrackStatus.

Rate (lambda) is estimated per circuit as event_count / lap_exposure — the
closed-form MLE for a homogeneous Poisson process — then adjusted by lap-1,
wet-track, and street-circuit multipliers before computing
P(>=1 SC/VSC event in the next N laps) = 1 - exp(-lambda * N).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# FastF1 TrackStatus codes: 1=AllClear, 2=Yellow, 4=SCDeployed, 5=Red,
# 6=VSCDeployed, 7=VSCEnding. Multiple simultaneous codes are concatenated
# into one string (e.g. "24"), so membership is checked per-character.
SC_STATUS_CODES = frozenset({"4"})
VSC_STATUS_CODES = frozenset({"6", "7"})
WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})

# No street-circuit flag exists in the Circuit model; this mirrors the
# FastF1-location-name lookup pattern already used in _ingest_common.py.
STREET_CIRCUITS = frozenset(
    {
        "Circuit de Monaco",
        "Baku City Circuit",
        "Marina Bay Street Circuit",
        "Jeddah Corniche Circuit",
        "Las Vegas Strip Circuit",
    }
)

LAP1_MULTIPLIER = 2.5
WET_MULTIPLIER = 3.0
STREET_MULTIPLIER = 1.8
PREDICTION_HORIZONS = (1, 2, 3, 5, 10)

# Same versioning contract as tire_deg_model.TRAINING_SCHEMA_VERSION /
# pit_predictor.TRAINING_SCHEMA_VERSION — bump when a change to what build_lap_flags
# is FED (not this module's own logic) makes holdout_mae non-comparable across
# versions. train_models.serialize_evaluate_and_upload's promotion guard (CP2,
# docs/tire-deg-model-quality-and-rival-pit-behavior.md) force-promotes a candidate
# over an incumbent recorded at a different version, regardless of MAE — the same
# way it already force-promotes over a feature-count or feature-names mismatch.
# This model has no feature vector at all (fitted_feature_count returns None for
# it — see train_models.py), so neither of those checks can ever fire for it;
# version is the ONLY mechanism that can flag a training-input change here.
#
# 1 (implicit, never recorded under this name): train_models.train_all/
#   retrain_incremental.retrain fit this model on is_valid-filtered laps only
#   (the same filter tire_deg_model correctly wants, for a genuinely different
#   reason). That filter is wrong for THIS model: a lap run under an active
#   SC/VSC period has an anomalous, non-representative lap time, so FastF1 marks
#   almost all of them is_valid=False — filtering them out removes virtually the
#   entire positive-class signal build_lap_flags depends on. Confirmed directly
#   against the real 2018-2025 local corpus: 3832 laps carry track_status "4"
#   (SC) before the is_valid filter, 0 after. The result was a production
#   circuit_rates dict of all zeros and default_rate=0.0 — a model that always
#   predicts P(SC)=0, with a holdout_mae of exactly 0.0 (every actual was also
#   0 once filtered) that no honestly-trained model could ever beat under the
#   old MAE-only guard.
# 2 (2026-09-11): fit on ALL laps regardless of is_valid (same unfiltered input
#   pit_predictor already uses, for the same reason — detecting an SC/VSC period
#   is orthogonal to whether a lap's time is race-pace-representative).
TRAINING_SCHEMA_VERSION = 2

# Circuits with fewer than this many dry, non-lap-1 laps in the training
# window fall back to default_rate rather than an unstable per-circuit estimate.
MIN_LAPS_FOR_CIRCUIT_ESTIMATE = 200


@dataclass(frozen=True)
class SafetyCarModel:
    """Per-circuit Poisson SC/VSC rate model.

    circuit_rates: circuit_name -> base lambda (events per lap, dry track, non-lap-1).
    default_rate: fallback lambda for circuits with too little history.
    """

    circuit_rates: dict[str, float]
    default_rate: float

    def rate(self, circuit_name: str, lap_number: int, wet_track: bool) -> float:
        """Adjusted Poisson rate (lambda) for one lap.

        Args:
            circuit_name: Circuit name as stored on the Circuit model.
            lap_number: 1-indexed lap number.
            wet_track: Whether the lap was run on intermediate/wet tyres.
        Returns:
            Adjusted lambda (expected SC/VSC onset events per lap).
        """
        base = self.circuit_rates.get(circuit_name, self.default_rate)
        if lap_number == 1:
            base *= LAP1_MULTIPLIER
        if wet_track:
            base *= WET_MULTIPLIER
        if circuit_name in STREET_CIRCUITS:
            base *= STREET_MULTIPLIER
        return base

    def probability_within(
        self, circuit_name: str, lap_number: int, wet_track: bool, n_laps: int
    ) -> float:
        """P(>=1 SC/VSC event in the next n_laps), under a homogeneous Poisson assumption.

        Args:
            circuit_name: Circuit name.
            lap_number: Current 1-indexed lap number.
            wet_track: Whether the current lap is wet.
            n_laps: Horizon length in laps.
        Returns:
            Probability in [0, 1].
        """
        lam = self.rate(circuit_name, lap_number, wet_track)
        return float(1.0 - np.exp(-lam * n_laps))

    def probability_by_horizon(
        self, circuit_name: str, lap_number: int, wet_track: bool
    ) -> dict[int, float]:
        """P(>=1 SC/VSC event) for each horizon in PREDICTION_HORIZONS.

        Args:
            circuit_name: Circuit name.
            lap_number: Current 1-indexed lap number.
            wet_track: Whether the current lap is wet.
        Returns:
            Mapping of horizon (laps) to probability.
        """
        return {
            n: self.probability_within(circuit_name, lap_number, wet_track, n)
            for n in PREDICTION_HORIZONS
        }


def _is_sc_or_vsc(track_status: str | None) -> bool:
    if not track_status:
        return False
    codes = set(track_status)
    return bool(codes & (SC_STATUS_CODES | VSC_STATUS_CODES))


def build_lap_flags(laps: pd.DataFrame) -> pd.DataFrame:
    """Add is_sc_event (onset) and wet_track columns to a raw laps frame.

    Args:
        laps: One row per lap; must include session_id, lap_number, track_status,
            compound, circuit_name.
    Returns:
        Copy of laps sorted by (session_id, lap_number) with is_sc_event (True only
        on the first lap of a new SC/VSC period) and wet_track columns added.
    """
    df = laps.sort_values(["session_id", "lap_number"]).reset_index(drop=True).copy()
    active = df["track_status"].apply(_is_sc_or_vsc)
    prev_active = active.groupby(df["session_id"]).shift(1, fill_value=False)
    df["is_sc_event"] = active & ~prev_active
    df["wet_track"] = df["compound"].isin(WET_COMPOUNDS)
    return df


def train_safety_car_model(df: pd.DataFrame) -> SafetyCarModel:
    """Fit per-circuit Poisson SC/VSC rates from historical lap flags.

    Args:
        df: Output of build_lap_flags(), with a circuit_name column present.
            Lap 1 and wet laps are excluded from the base-rate estimate so the
            LAP1_MULTIPLIER/WET_MULTIPLIER adjustments aren't double-counted.
    Returns:
        Fitted SafetyCarModel.
    """
    baseline = df[(df["lap_number"] != 1) & (~df["wet_track"])]

    circuit_rates: dict[str, float] = {}
    for circuit_name, group in baseline.groupby("circuit_name"):
        if len(group) < MIN_LAPS_FOR_CIRCUIT_ESTIMATE:
            continue
        circuit_rates[str(circuit_name)] = float(group["is_sc_event"].sum() / len(group))

    default_rate = float(baseline["is_sc_event"].sum() / len(baseline)) if len(baseline) else 0.0

    logger.info(
        "safety_car_model: fit %d circuit rate(s), default_rate=%.5f (n=%d dry non-lap-1 laps)",
        len(circuit_rates),
        default_rate,
        len(baseline),
    )
    return SafetyCarModel(circuit_rates=circuit_rates, default_rate=default_rate)


def evaluate_holdout(model: SafetyCarModel, df: pd.DataFrame) -> float:
    """MAE between predicted P(SC/VSC in next lap) and the actual onset indicator.

    Args:
        model: Fitted SafetyCarModel.
        df: Output of build_lap_flags() for the holdout season.
    Returns:
        Mean absolute error, comparable across model versions for promotion decisions.
    """
    preds = np.array(
        [
            model.probability_within(row.circuit_name, int(row.lap_number), bool(row.wet_track), 1)
            for row in df.itertuples()
        ]
    )
    actual = df["is_sc_event"].to_numpy(dtype=float)
    return float(np.mean(np.abs(preds - actual)))
