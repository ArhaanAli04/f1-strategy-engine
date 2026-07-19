"""Unit tests for services/ml/driver_style.py.

fit_driver_style_clusters runs a full PCA->KMeans->UMAP pipeline — UMAP fitting is
comparatively slow even on tiny synthetic data, so tests that call it are marked
@pytest.mark.slow, same convention test_race_simulator.py established Day 14 for
its numba JIT warm-up cost (see that file's module docstring).

Synthetic fixture: 8 drivers x 12 laps each (above KMeans k=5's minimum of 5
samples, per the Day 15 spec note), each driver given a distinct per-driver mean
offset plus noise so sector/lap-time/tyre-degradation features actually vary
across the population — a constant-feature population would collapse cluster
std to zero and get z-score/PCA steps producing NaNs.
"""

import uuid
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backend.services.ml.driver_style import (
    N_CLUSTERS,
    build_driver_style_features,
    fit_driver_style_clusters,
)

_ARCHETYPES = {"aggressive", "conservative", "technical", "balanced", "inconsistent"}
_SEASON = 2026
_N_DRIVERS = 8
_N_LAPS = 12


def _synthetic_laps_and_stints(n_drivers: int = _N_DRIVERS, n_laps: int = _N_LAPS) -> Any:
    rng = np.random.default_rng(7)
    driver_ids = [uuid.uuid4() for _ in range(n_drivers)]
    lap_rows = []
    stint_rows = []
    for i, driver_id in enumerate(driver_ids):
        session_id = uuid.uuid4()
        noise_scale = 0.05 + i * 0.02
        base_s1, base_s2, base_s3 = 28.0 + i * 0.3, 34.0 + i * 0.2, 27.0 + i * 0.25
        for lap_number in range(1, n_laps + 1):
            s1 = base_s1 + rng.normal(0, noise_scale)
            s2 = base_s2 + rng.normal(0, noise_scale)
            s3 = base_s3 + rng.normal(0, noise_scale)
            lap_rows.append(
                {
                    "driver_id": driver_id,
                    "session_id": session_id,
                    "lap_number": lap_number,
                    "sector1_seconds": s1,
                    "sector2_seconds": s2,
                    "sector3_seconds": s3,
                    "lap_time_seconds": s1 + s2 + s3,
                    "is_valid": True,
                    "season": _SEASON,
                }
            )
        stint_rows.append(
            {
                "driver_id": driver_id,
                "session_id": session_id,
                "compound": "MEDIUM",
                "avg_deg_per_lap": 0.05 + i * 0.03 + rng.normal(0, 0.005),
                "start_lap": 1,
                "end_lap": n_laps,
                "season": _SEASON,
            }
        )
    return pd.DataFrame(lap_rows), pd.DataFrame(stint_rows)


@pytest.mark.unit
@pytest.mark.slow
def test_compute_fingerprint_returns_cluster_label() -> None:
    laps, stints = _synthetic_laps_and_stints()
    features = build_driver_style_features(laps, stints)
    assert len(features) == _N_DRIVERS

    result = fit_driver_style_clusters(features, n_clusters=N_CLUSTERS)

    assert set(result.assignments["archetype"]).issubset(_ARCHETYPES)
    assert len(result.assignments) == _N_DRIVERS


@pytest.mark.unit
@pytest.mark.slow
def test_umap_coordinates_are_2d() -> None:
    laps, stints = _synthetic_laps_and_stints()
    features = build_driver_style_features(laps, stints)

    result = fit_driver_style_clusters(features, n_clusters=N_CLUSTERS)

    embedding = result.assignments[["umap_x", "umap_y"]].to_numpy()
    assert embedding.shape == (_N_DRIVERS, 2)
    assert np.isfinite(embedding).all()


@pytest.mark.unit
def test_missing_season_data_raises_error() -> None:
    # A bare columns=[...] 0-row frame (object dtype on every column — exactly
    # what pd.DataFrame(db_rows, columns=[...]) produces in driver_service.
    # _fit_population when a season has zero laps/stints ingested) used to crash
    # downstream in compute_lap_time_consistency: object-dtype boolean indexing
    # (laps[laps["is_valid"]]) silently drops every column in pandas, then the
    # groupby raised an unrelated KeyError instead of this empty-result contract.
    # build_driver_style_features now guards on laps.empty/stints.empty before
    # reaching any dtype-sensitive step — this is a regression test for that fix.
    empty_laps = pd.DataFrame(
        columns=[
            "driver_id",
            "session_id",
            "lap_number",
            "sector1_seconds",
            "sector2_seconds",
            "sector3_seconds",
            "lap_time_seconds",
            "is_valid",
            "season",
        ]
    )
    empty_stints = pd.DataFrame(
        columns=[
            "driver_id",
            "session_id",
            "compound",
            "avg_deg_per_lap",
            "start_lap",
            "end_lap",
            "season",
        ]
    )

    features = build_driver_style_features(empty_laps, empty_stints)

    assert features.empty
