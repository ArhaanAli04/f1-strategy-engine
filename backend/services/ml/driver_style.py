"""K-Means driver fingerprinting from lap- and stint-level features.

The original spec called for braking_consistency and throttle_application_smoothness
computed from 100ms telemetry. That raw telemetry was never ingested — Day 5 skipped
speed/throttle/brake to avoid tens of millions of rows (see CLAUDE.md's Deferred
Telemetry Features note). This module uses four lap/stint-level proxies instead:

- sector_time_variance: mean of std(sector1/2/3 seconds) per driver-season — a proxy
  for corner-execution consistency.
- tyre_management_index: each stint's avg_deg_per_lap, z-scored against the
  population mean/std for that compound+season. This is a population-relative proxy,
  not a literal comparison against the tire_deg model's prediction — reproducing that
  model's training-time categorical encoding here would be a fragile coupling (see
  race_simulator.py's module docstring for the same encoding concern).
- lap_time_consistency: std of valid lap times per driver-season, a proxy for
  braking consistency.
- stint_length_tendency: mean stint length per driver-season, a proxy for
  throttle/tyre management style.

PCA(n_components=4) on these 4 features is a decorrelation/whitening step before
K-Means (the features aren't independent — e.g. tyre_management_index and
lap_time_consistency both partly reflect race pace), not dimensionality reduction.

Cluster -> archetype label assignment is a documented heuristic over cluster
centroids (K-Means itself has no notion of "aggressive" vs "conservative") — see
_label_clusters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import umap
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = [
    "sector_time_variance",
    "tyre_management_index",
    "lap_time_consistency",
    "stint_length_tendency",
]

N_CLUSTERS = 5


@dataclass(frozen=True)
class DriverStyleResult:
    scaler: StandardScaler
    pca: PCA
    kmeans: KMeans
    cluster_labels: dict[int, str]
    assignments: pd.DataFrame


def compute_sector_time_variance(laps: pd.DataFrame) -> pd.DataFrame:
    """Mean of std(sector1/2/3 seconds) per driver per season.

    Args:
        laps: lap_data rows; must include driver_id, season, sector1_seconds,
            sector2_seconds, sector3_seconds.
    Returns:
        DataFrame with driver_id, season, sector_time_variance.
    """
    grouped = laps.groupby(["driver_id", "season"])[
        ["sector1_seconds", "sector2_seconds", "sector3_seconds"]
    ].std()
    return grouped.mean(axis=1).rename("sector_time_variance").reset_index()


def compute_lap_time_consistency(laps: pd.DataFrame) -> pd.DataFrame:
    """Std of valid lap times per driver per season.

    Args:
        laps: lap_data rows; must include driver_id, season, lap_time_seconds, is_valid.
    Returns:
        DataFrame with driver_id, season, lap_time_consistency.
    """
    valid = laps[laps["is_valid"]]
    return (
        valid.groupby(["driver_id", "season"])["lap_time_seconds"]
        .std()
        .rename("lap_time_consistency")
        .reset_index()
    )


def compute_stint_length_tendency(stints: pd.DataFrame, laps: pd.DataFrame) -> pd.DataFrame:
    """Mean stint length (laps) per driver per season.

    Args:
        stints: tire_stints rows; must include session_id, driver_id, season,
            start_lap, end_lap (nullable — the final/ongoing stint of a session).
        laps: lap_data rows; must include session_id, lap_number, used only to
            resolve each session's last lap for stints with a null end_lap.
    Returns:
        DataFrame with driver_id, season, stint_length_tendency.
    """
    laps_in_session = laps.groupby("session_id")["lap_number"].max()
    df = stints.copy()
    df["end_lap"] = df["end_lap"].fillna(df["session_id"].map(laps_in_session))
    df["stint_length"] = df["end_lap"] - df["start_lap"] + 1
    return (
        df.groupby(["driver_id", "season"])["stint_length"]
        .mean()
        .rename("stint_length_tendency")
        .reset_index()
    )


def compute_tyre_management_index(stints: pd.DataFrame) -> pd.DataFrame:
    """Each stint's avg_deg_per_lap, z-scored against the population for its compound+season.

    Positive values mean a driver degrades tyres faster than their season/compound
    peers on average (more aggressive); negative means slower (more conservative).

    Args:
        stints: tire_stints rows; must include driver_id, season, compound,
            avg_deg_per_lap (nullable — stints too short to fit a degradation slope
            are dropped).
    Returns:
        DataFrame with driver_id, season, tyre_management_index.
    """
    df = stints.dropna(subset=["avg_deg_per_lap"]).copy()
    group_mean = df.groupby(["season", "compound"])["avg_deg_per_lap"].transform("mean")
    group_std = df.groupby(["season", "compound"])["avg_deg_per_lap"].transform("std")
    df["z"] = (df["avg_deg_per_lap"] - group_mean) / group_std.replace(0, np.nan)
    return (
        df.groupby(["driver_id", "season"])["z"]
        .mean()
        .rename("tyre_management_index")
        .reset_index()
    )


def build_driver_style_features(laps: pd.DataFrame, stints: pd.DataFrame) -> pd.DataFrame:
    """Assemble one row per (driver_id, season) with all four style features.

    Args:
        laps: lap_data rows, each with a season column attached (e.g. via a join to
            races.season, the same pattern scripts/train_models.py._fetch_laps uses).
        stints: tire_stints rows, with the same season column attached.
    Returns:
        DataFrame with driver_id, season, and FEATURE_COLUMNS. Driver-seasons missing
        any one of the four features (e.g. too few stints/laps) are dropped rather
        than imputed, since imputing a style feature would misrepresent that driver.
        Empty (not a crash) if laps or stints is empty — a 0-row DataFrame built from
        an empty DB query result has object dtype on every column (no data to infer
        from), and object-dtype boolean indexing (compute_lap_time_consistency's
        laps[laps["is_valid"]]) silently drops all columns in pandas, which would
        otherwise surface as an unrelated KeyError in the groupby calls below instead
        of the empty-result contract callers (driver_service._fit_population) rely on.
    """
    if laps.empty or stints.empty:
        return pd.DataFrame(columns=["driver_id", "season", *FEATURE_COLUMNS])

    sector_var = compute_sector_time_variance(laps)
    lap_consistency = compute_lap_time_consistency(laps)
    stint_length = compute_stint_length_tendency(stints, laps)
    tyre_mgmt = compute_tyre_management_index(stints)

    features = sector_var
    for other in (tyre_mgmt, lap_consistency, stint_length):
        features = features.merge(other, on=["driver_id", "season"], how="inner")

    return features.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)


def _label_clusters(centroids: npt.NDArray[np.float64]) -> dict[int, str]:
    """Assign a semantic archetype label to each K-Means cluster centroid.

    K-Means has no notion of "aggressive" vs "conservative" — this greedily assigns
    the cluster that best matches each archetype's defining feature, in a fixed
    priority order, leaving the one unmatched cluster labelled "balanced". This is a
    documented heuristic, not a statistically validated taxonomy.

    Args:
        centroids: (n_clusters, 4) centroid coordinates in the STANDARDIZED
            FEATURE_COLUMNS order (sector_time_variance, tyre_management_index,
            lap_time_consistency, stint_length_tendency) — not PCA space, so each
            axis stays individually interpretable.
    Returns:
        Mapping of cluster index to archetype label.
    """
    sector_var_idx, tyre_mgmt_idx, lap_consistency_idx = 0, 1, 2
    remaining = list(range(centroids.shape[0]))
    labels: dict[int, str] = {}

    def pick(score_fn: Callable[[int], float], label: str) -> None:
        best = max(remaining, key=score_fn)
        labels[best] = label
        remaining.remove(best)

    pick(lambda c: -centroids[c, sector_var_idx], "technical")
    pick(
        lambda c: centroids[c, lap_consistency_idx] + centroids[c, sector_var_idx],
        "inconsistent",
    )
    pick(lambda c: centroids[c, tyre_mgmt_idx], "aggressive")
    pick(lambda c: -centroids[c, tyre_mgmt_idx], "conservative")

    for c in remaining:
        labels[c] = "balanced"
    return labels


def fit_driver_style_clusters(
    features: pd.DataFrame, n_clusters: int = N_CLUSTERS, random_state: int = 42
) -> DriverStyleResult:
    """Fit the full driver-style pipeline: scale -> PCA(4) -> KMeans(k) -> UMAP(2D).

    Args:
        features: Output of build_driver_style_features (driver_id, season, FEATURE_COLUMNS).
        n_clusters: Number of K-Means clusters (5, per the archetype taxonomy). Capped
            at len(features) if there are fewer driver-seasons than clusters.
        random_state: Seed for PCA/KMeans/UMAP reproducibility.
    Returns:
        DriverStyleResult with the fitted transformers and one assignment row per
        driver-season (cluster id, archetype label, 2D UMAP coordinates).
    """
    k = min(n_clusters, len(features))
    raw = features[FEATURE_COLUMNS].to_numpy(dtype=float)

    scaler = StandardScaler()
    scaled = scaler.fit_transform(raw)

    pca = PCA(n_components=4, random_state=random_state)
    pca_features = pca.fit_transform(scaled)

    kmeans = KMeans(n_clusters=k, random_state=random_state, n_init=10)
    cluster_ids = kmeans.fit_predict(pca_features)

    centroids = np.array([scaled[cluster_ids == c].mean(axis=0) for c in range(k)])
    cluster_labels = _label_clusters(centroids)

    reducer = umap.UMAP(n_components=2, random_state=random_state)
    embedding = reducer.fit_transform(scaled)

    assignments = features[["driver_id", "season", *FEATURE_COLUMNS]].copy()
    assignments["cluster"] = cluster_ids
    assignments["archetype"] = [cluster_labels[c] for c in cluster_ids]
    assignments["umap_x"] = embedding[:, 0]
    assignments["umap_y"] = embedding[:, 1]

    return DriverStyleResult(
        scaler=scaler,
        pca=pca,
        kmeans=kmeans,
        cluster_labels=cluster_labels,
        assignments=assignments,
    )
