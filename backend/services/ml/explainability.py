"""SHAP explainability for the tire_deg and pit_predictor tree models.

tire_deg models are sklearn Pipelines (StandardScaler -> XGBRegressor); pit_predictor
is a raw LGBMClassifier with no preprocessing step. shap.TreeExplainer needs the raw
tree estimator, not a Pipeline, so this module unwraps the final pipeline step and
applies any preceding steps manually before handing features to SHAP.

Verified against shap>=0.45's Explanation API (see scratch probe): for both an
XGBRegressor and a binary LGBMClassifier, `explainer(X).values` is already
(n_rows, n_features) — no extra per-class dimension to select. Some model/version
combinations do return a 3D (n_rows, n_features, n_classes) array for binary
classifiers, so that shape is handled defensively (last class index = positive class).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import shap
from sklearn.pipeline import Pipeline

DEFAULT_TOP_K = 5

# Human-readable labels for the feature names used across the strategy models.
FEATURE_LABELS: dict[str, str] = {
    "lap_number": "Lap number",
    "compound_encoded": "Tyre compound",
    "tyre_age_laps": "Tyre age",
    "fuel_adjusted_time": "Fuel-adjusted pace",
    "circuit_id_encoded": "Circuit",
    "driver_id_encoded": "Driver",
    "current_tyre_age": "Tyre age",
    "predicted_life_remaining": "Predicted tyre life remaining",
    "gap_to_car_ahead": "Gap to car ahead",
    "gap_to_car_behind": "Gap to car behind",
    "safety_car_probability": "Safety car probability",
    "laps_to_race_end": "Laps to race end",
    "position": "Track position",
    "fuel_load_est": "Estimated fuel load",
}


@dataclass(frozen=True)
class FeatureContribution:
    feature_name: str
    value: float
    contribution: float
    direction: str


def _unwrap_tree_model(model: Any) -> tuple[Any, Any]:
    """Split a fitted model into (raw tree estimator, preprocessing step or None).

    Args:
        model: A fitted sklearn Pipeline (tire_deg_model) or a raw tree estimator
            (pit_predictor's LGBMClassifier has no preprocessing step).
    Returns:
        (tree_estimator, preprocessor). preprocessor is a fitted Pipeline of every
        step before the final one, or None if model isn't a Pipeline.
    """
    if isinstance(model, Pipeline):
        tree_estimator = model.steps[-1][1]
        preprocessor = Pipeline(model.steps[:-1]) if len(model.steps) > 1 else None
        return tree_estimator, preprocessor
    return model, None


def _positive_class_values(raw_values: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Reduce a SHAP Explanation.values array to (n_rows, n_features).

    Args:
        raw_values: Either already (n_rows, n_features), or (n_rows, n_features,
            n_classes) for a binary/multiclass classifier on some shap/model
            version combinations.
    Returns:
        (n_rows, n_features) array — the positive/last class's contributions if a
        class dimension was present.
    """
    if raw_values.ndim == 3:
        return raw_values[:, :, -1]
    return raw_values


def explain_prediction(
    model: Any,
    feature_names: list[str],
    features: npt.NDArray[np.float64],
    top_k: int = DEFAULT_TOP_K,
) -> list[list[FeatureContribution]]:
    """Top-k SHAP feature contributions for each row in features.

    Args:
        model: Fitted tire_deg pipeline or pit_predictor classifier.
        feature_names: Column names matching features' column order (e.g.
            tire_deg_model.FEATURE_COLUMNS or pit_predictor.FEATURE_COLUMNS).
        features: (n_rows, n_features) raw (unscaled) feature values.
        top_k: Number of highest-magnitude contributions to return per row.
    Returns:
        One list of FeatureContribution per input row, sorted by |contribution|
        descending, longest DEFAULT_TOP_K entries.
    """
    tree_estimator, preprocessor = _unwrap_tree_model(model)
    transformed = preprocessor.transform(features) if preprocessor is not None else features

    explainer = shap.TreeExplainer(tree_estimator)
    explanation = explainer(transformed)
    shap_values = _positive_class_values(np.asarray(explanation.values))

    results: list[list[FeatureContribution]] = []
    for row_idx in range(transformed.shape[0]):
        row_shap = shap_values[row_idx]
        row_raw = features[row_idx]
        order = np.argsort(-np.abs(row_shap))[:top_k]
        results.append(
            [
                FeatureContribution(
                    feature_name=feature_names[i],
                    value=float(row_raw[i]),
                    contribution=float(row_shap[i]),
                    direction="+" if row_shap[i] >= 0 else "-",
                )
                for i in order
            ]
        )
    return results


def format_contribution(contribution: FeatureContribution, unit: str = "probability") -> str:
    """Human-readable rendering of one contribution, e.g. "Tyre age +0.80 probability".

    Args:
        contribution: A single FeatureContribution.
        unit: Unit label appended after the signed value (e.g. "probability", "s").
    Returns:
        Formatted string.
    """
    label = FEATURE_LABELS.get(contribution.feature_name, contribution.feature_name)
    sign = "+" if contribution.contribution >= 0 else ""
    return f"{label} {sign}{contribution.contribution:.2f} {unit}"
