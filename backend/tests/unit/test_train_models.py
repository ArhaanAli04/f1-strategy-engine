"""Unit tests for scripts/train_models.py's schema-aware promotion guard (item 9,
extended by CP2 — docs/tire-deg-model-quality-and-rival-pit-behavior.md).

Uses an in-memory fake S3 client (no real boto3/S3 calls) and real fitted sklearn
Pipelines / a real SafetyCarModel so fitted_feature_count's introspection is
exercised against genuine objects, not mocks. train_models.MODEL_DIR is redirected
to each test's own tmp_path so nothing is ever written to the real (gitignored)
models/ directory.

Covers the decision table serialize_evaluate_and_upload/_resolve_incumbent_schema/
_feature_names_mismatch/_training_schema_mismatch implement: no-incumbent
promotion, schema-match MAE comparison (both directions), the exact tire_deg_wet.pkl
8-vs-6-feature legacy mismatch (force-promoted despite a worse holdout_mae), sidecar
backfill eliminating a repeat .pkl download, an unloadable incumbent treated as
incompatible, the feature_names/fitted-count caller-bug guard, a no-feature-vector
model type (safety_car_model) staying on the original MAE-only path, and CP2's two
new independent incompatibility axes (feature_names differing at an identical
count — CP1's fuel_adjusted_time -> fuel_load_penalty rename; training_schema_version
differing with an identical feature vector entirely — pit_predictor's label
redefinition, which the other two checks cannot see at all).
"""

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest
from botocore.exceptions import ClientError
from lightgbm import LGBMClassifier
from sklearn.pipeline import Pipeline

from backend.scripts import train_models
from backend.services.ml.pit_predictor import FEATURE_COLUMNS as PIT_FEATURE_COLUMNS
from backend.services.ml.safety_car_model import SafetyCarModel
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS, _build_pipeline

BUCKET = "test-bucket"
VERSION_TAG = "20260902-000000"


class _FakeBody:
    """Stands in for botocore's StreamingBody — only .read() is used."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    """In-memory stand-in for a boto3 S3 client.

    Implements exactly the four methods serialize_evaluate_and_upload /
    _resolve_incumbent_schema / download_metrics call: upload_file, download_file,
    put_object, get_object. Objects are stored in a single flat dict keyed by S3
    key (bucket is accepted but not partitioned on — these tests only ever use one).
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_download_keys: set[str] = set()
        self.download_file_calls = 0

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_file_calls += 1
        if key in self.fail_download_keys:
            raise RuntimeError(f"simulated download failure for {key}")
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        Path(filename).write_bytes(self.objects[key])

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803 — mirrors boto3's real kwarg casing
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 — mirrors boto3's real kwarg casing
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _FakeBody(self.objects[Key])}


@pytest.fixture(autouse=True)
def _isolated_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect train_models.MODEL_DIR to a per-test tmp dir for every test in this file."""
    monkeypatch.setattr(train_models, "MODEL_DIR", tmp_path)


def _fit_pipeline(n_features: int, seed: int) -> Pipeline:
    rng = np.random.default_rng(seed)
    n_samples = 30
    features = rng.random((n_samples, n_features))
    target = rng.normal(0.0, 0.3, n_samples)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


def _seed_legacy_production(
    fake_client: _FakeS3Client,
    tmp_path: Path,
    filename: str,
    model_obj: Any,
    metrics: dict[str, Any],
) -> None:
    """Populate the fake client's 'production' tag directly, bypassing
    serialize_evaluate_and_upload — simulates a model+sidecar that predates this
    schema check (no n_features/schema_source keys), matching every real S3
    sidecar as of today.
    """
    seed_path = tmp_path / f"_seed_{filename}"
    joblib.dump(model_obj, seed_path)
    fake_client.objects[f"production/{filename}"] = seed_path.read_bytes()
    fake_client.objects[f"production/{filename}.metrics.json"] = json.dumps(metrics).encode("utf-8")


def _load_from_fake_client(fake_client: _FakeS3Client, tmp_path: Path, key: str) -> Any:
    """Round-trip a fake client's stored bytes back through joblib.load.

    Trusted in-test fixture data (produced by this same test process's own
    joblib.dump calls above), not external/untrusted input.
    """
    out = tmp_path / "_roundtrip.pkl"
    out.write_bytes(fake_client.objects[key])
    return joblib.load(out)  # noqa: S301


def _seed_declared_production(
    fake_client: _FakeS3Client,
    tmp_path: Path,
    filename: str,
    model_obj: Any,
    metrics: dict[str, Any],
    n_features: int,
) -> None:
    """Populate the fake client's 'production' tag with a schema-aware sidecar
    (n_features/schema_source already recorded) — simulates a model already
    promoted once under this fix, so _resolve_incumbent_schema should never need
    to download the .pkl.
    """
    declared_metrics = dict(metrics)
    declared_metrics["n_features"] = n_features
    declared_metrics["feature_names"] = list(FEATURE_COLUMNS)
    declared_metrics["schema_source"] = "declared"
    _seed_legacy_production(fake_client, tmp_path, filename, model_obj, declared_metrics)


# --- fitted_feature_count: direct coverage of the new public function ---
# (Also exercised indirectly by every test below; these three cover its three
# real shapes explicitly, matching CLAUDE.md's per-function unit test convention.)


@pytest.mark.unit
def test_fitted_feature_count_reads_tire_deg_pipeline() -> None:
    pipeline = _fit_pipeline(len(FEATURE_COLUMNS), seed=1)
    assert train_models.fitted_feature_count(pipeline) == len(FEATURE_COLUMNS)


@pytest.mark.unit
def test_fitted_feature_count_reads_bare_lgbm_classifier() -> None:
    rng = np.random.default_rng(2)
    features = rng.random((20, 8))
    target = rng.integers(0, 2, 20)
    clf = LGBMClassifier(n_estimators=5, verbosity=-1)
    clf.fit(features, target)
    assert train_models.fitted_feature_count(clf) == 8


@pytest.mark.unit
def test_fitted_feature_count_none_for_no_feature_vector_model() -> None:
    sc_model = SafetyCarModel(circuit_rates={"Monza": 0.01}, default_rate=0.02)
    assert train_models.fitted_feature_count(sc_model) is None


# --- serialize_evaluate_and_upload: the promotion decision table ---


@pytest.mark.unit
def test_promotes_when_no_existing_production_model() -> None:
    fake_client = _FakeS3Client()
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=10)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_soft.pkl",
        candidate,
        {"holdout_mae": 0.5, "n_samples": 100},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="no_production_model")
    assert "production/tire_deg_soft.pkl" in fake_client.objects


@pytest.mark.unit
def test_promotes_when_schema_matches_and_mae_improves(tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=11)
    _seed_declared_production(
        fake_client,
        tmp_path,
        "tire_deg_soft.pkl",
        incumbent,
        {"holdout_mae": 0.6, "n_samples": 90},
        n_features=len(FEATURE_COLUMNS),
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=12)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_soft.pkl",
        candidate,
        {"holdout_mae": 0.4, "n_samples": 110},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="holdout_mae_improved")
    # Schema was already recorded — no .pkl download needed.
    assert fake_client.download_file_calls == 0


@pytest.mark.unit
def test_does_not_promote_when_schema_matches_and_mae_not_improved(tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=13)
    _seed_declared_production(
        fake_client,
        tmp_path,
        "tire_deg_soft.pkl",
        incumbent,
        {"holdout_mae": 0.4, "n_samples": 90},
        n_features=len(FEATURE_COLUMNS),
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=14)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_soft.pkl",
        candidate,
        {"holdout_mae": 0.5, "n_samples": 110},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert outcome == train_models.PromotionOutcome(
        promoted=False, reason="holdout_mae_not_improved"
    )
    assert fake_client.download_file_calls == 0
    # The version-tagged candidate is still uploaded even when not promoted.
    assert f"{VERSION_TAG}/tire_deg_soft.pkl" in fake_client.objects
    assert "production/tire_deg_soft.pkl.metrics.json" in fake_client.objects


@pytest.mark.unit
def test_force_promotes_on_legacy_wet_schema_mismatch_despite_worse_mae(tmp_path: Path) -> None:
    """The exact real-world repro: an 8-feature legacy tire_deg_wet.pkl incumbent
    (predates this fix, no n_features in its sidecar) with a strong holdout_mae,
    versus a schema-correct 6-feature candidate with a WORSE holdout_mae. Must
    force-promote — a schema-incompatible incumbent cannot even serve inference.
    """
    fake_client = _FakeS3Client()
    stale_wet = _fit_pipeline(8, seed=20)
    _seed_legacy_production(
        fake_client,
        tmp_path,
        "tire_deg_wet.pkl",
        stale_wet,
        {"holdout_mae": 5.7906, "cv_mae": 5.5, "n_samples": 900},
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=21)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_wet.pkl",
        candidate,
        {"holdout_mae": 6.2, "cv_mae": 6.0, "n_samples": 300, "promotion_basis": "cv_only"},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="schema_mismatch")
    promoted_model = _load_from_fake_client(fake_client, tmp_path, "production/tire_deg_wet.pkl")
    assert train_models.fitted_feature_count(promoted_model) == len(FEATURE_COLUMNS)


@pytest.mark.unit
def test_backfills_legacy_sidecar_and_skips_second_download(tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    stale_wet = _fit_pipeline(8, seed=22)
    _seed_legacy_production(
        fake_client,
        tmp_path,
        "tire_deg_wet.pkl",
        stale_wet,
        {"holdout_mae": 5.7906, "n_samples": 900},
    )
    first_candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=23)

    first_outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_wet.pkl",
        first_candidate,
        {"holdout_mae": 6.5, "n_samples": 300},
        feature_names=list(FEATURE_COLUMNS),
    )
    assert first_outcome.reason == "schema_mismatch"
    assert fake_client.download_file_calls == 1

    # A run that doesn't win on MAE and doesn't get promoted (production stays
    # the same incumbent's now-backfilled sidecar) — a second candidate must not
    # trigger a second .pkl download, since the sidecar now carries n_features.
    second_candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=24)
    second_outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        "20260902-010000",
        "tire_deg_wet.pkl",
        second_candidate,
        {"holdout_mae": 6.5, "n_samples": 300},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert fake_client.download_file_calls == 1  # unchanged — no second download
    # The now-promoted incumbent (from the first call) is schema-correct, so the
    # second call falls through to a normal MAE comparison, not another mismatch.
    assert second_outcome.reason in ("holdout_mae_improved", "holdout_mae_not_improved")


@pytest.mark.unit
def test_force_promotes_when_incumbent_pkl_unloadable(tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    # Legacy sidecar (no n_features) but the .pkl itself can't be downloaded.
    fake_client.objects["production/tire_deg_wet.pkl.metrics.json"] = json.dumps(
        {"holdout_mae": 5.7906, "n_samples": 900}
    ).encode("utf-8")
    fake_client.fail_download_keys.add("production/tire_deg_wet.pkl")
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=30)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_wet.pkl",
        candidate,
        {"holdout_mae": 6.5, "n_samples": 300},
        feature_names=list(FEATURE_COLUMNS),
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="schema_mismatch")


@pytest.mark.unit
def test_feature_names_length_mismatch_raises_and_uploads_nothing() -> None:
    fake_client = _FakeS3Client()
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=40)

    with pytest.raises(ValueError, match="feature_names has"):
        train_models.serialize_evaluate_and_upload(
            fake_client,
            BUCKET,
            VERSION_TAG,
            "tire_deg_soft.pkl",
            candidate,
            {"holdout_mae": 0.5, "n_samples": 100},
            feature_names=list(FEATURE_COLUMNS) + ["extra_bogus_column"],
        )

    assert fake_client.objects == {}


@pytest.mark.unit
def test_schema_check_not_applicable_for_model_with_no_feature_vector(tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    fake_client.objects["production/safety_car_model.pkl.metrics.json"] = json.dumps(
        {"holdout_mae": 0.6, "n_circuits": 5}
    ).encode("utf-8")
    candidate = SafetyCarModel(circuit_rates={"Monza": 0.01, "Spa": 0.02}, default_rate=0.015)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "safety_car_model.pkl",
        candidate,
        {"holdout_mae": 0.5, "n_circuits": 6},
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="holdout_mae_improved")
    # No feature-vector concept for this model type — the schema path (and any
    # .pkl download) must never even be attempted, even though an incumbent exists.
    assert fake_client.download_file_calls == 0


@pytest.mark.unit
def test_promoted_sidecar_preserves_original_metrics_and_adds_schema_fields() -> None:
    fake_client = _FakeS3Client()
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=50)

    train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_soft.pkl",
        candidate,
        {"holdout_mae": 0.5, "cv_mae": 0.45, "n_samples": 100, "promotion_basis": "holdout"},
        feature_names=list(FEATURE_COLUMNS),
    )

    written = json.loads(fake_client.objects["production/tire_deg_soft.pkl.metrics.json"])
    assert written["holdout_mae"] == 0.5
    assert written["cv_mae"] == 0.45
    assert written["n_samples"] == 100
    assert written["promotion_basis"] == "holdout"
    assert written["n_features"] == len(FEATURE_COLUMNS)
    assert written["feature_names"] == list(FEATURE_COLUMNS)
    assert written["schema_source"] == "declared"


# --- CP2: training_schema_version / feature_names incompatibility axes ---
# (docs/tire-deg-model-quality-and-rival-pit-behavior.md — the promotion-guard gap
# that would have refused every CP1-corrected tire_deg model and every already-
# written-but-never-promoted pit_predictor label fix.)


@pytest.mark.unit
def test_force_promotes_on_training_schema_version_mismatch_despite_worse_mae(
    tmp_path: Path,
) -> None:
    """The real CP1 scenario, isolated to ONLY the version axis: identical feature
    COUNT and feature NAMES (so neither of the other two checks can fire), a
    legacy incumbent with no training_schema_version recorded at all (every real
    sidecar as of today), and a WORSE candidate holdout_mae — must still
    force-promote, since holdout_mae isn't comparable across a target-definition
    change no matter how the two numbers compare.
    """
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=60)
    _seed_declared_production(
        fake_client,
        tmp_path,
        "tire_deg_medium.pkl",
        incumbent,
        {"holdout_mae": 0.497, "n_samples": 41933},  # the real pre-CP1 incumbent MAE
        n_features=len(FEATURE_COLUMNS),
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=61)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_medium.pkl",
        candidate,
        {"holdout_mae": 0.543, "n_samples": 41933},  # the real honest CP1 candidate MAE
        feature_names=list(FEATURE_COLUMNS),
        training_schema_version=2,
    )

    assert outcome == train_models.PromotionOutcome(
        promoted=True, reason="training_schema_version_mismatch"
    )
    written = json.loads(fake_client.objects["production/tire_deg_medium.pkl.metrics.json"])
    assert written["training_schema_version"] == 2


@pytest.mark.unit
def test_force_promotes_on_pit_predictor_label_version_mismatch_with_unchanged_features(
    tmp_path: Path,
) -> None:
    """pit_predictor's real scenario: FEATURE_COLUMNS is byte-for-byte identical
    between the old out-lap-only label and the new K=3-window label (the label
    change lives entirely in TARGET_COLUMN's values) — so this is the test that
    proves the feature-names check alone could NEVER have caught this class of
    drift, only an explicit version can.
    """
    fake_client = _FakeS3Client()
    incumbent = LGBMClassifier(n_estimators=5, verbosity=-1)
    rng = np.random.default_rng(70)
    incumbent.fit(rng.random((30, len(PIT_FEATURE_COLUMNS))), rng.integers(0, 2, 30))
    _seed_legacy_production(
        fake_client,
        tmp_path,
        "pit_predictor.pkl",
        incumbent,
        {
            # the real old-label incumbent's actual recorded sidecar values,
            # already schema-aware (item 9) — feature_names identical to what the
            # candidate below will declare, isolating this test to the version
            # axis alone. No training_schema_version recorded, matching every
            # real sidecar as of today.
            "holdout_mae": 0.0327,
            "cv_auc": 0.9920,
            "positive_rate": 0.0276,
            "n_samples": 137286,
            "n_features": len(PIT_FEATURE_COLUMNS),
            "feature_names": list(PIT_FEATURE_COLUMNS),
            "schema_source": "declared",
        },
    )

    candidate = LGBMClassifier(n_estimators=5, verbosity=-1)
    candidate.fit(rng.random((30, len(PIT_FEATURE_COLUMNS))), rng.integers(0, 2, 30))

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "pit_predictor.pkl",
        candidate,
        # the new label's higher positive rate makes its honest holdout_mae look
        # numerically worse than the same-lap detector's artificially low one
        {"holdout_mae": 0.087, "cv_auc": 0.7742, "positive_rate": 0.0874, "n_samples": 137286},
        feature_names=list(PIT_FEATURE_COLUMNS),
        training_schema_version=2,
    )

    assert outcome == train_models.PromotionOutcome(
        promoted=True, reason="training_schema_version_mismatch"
    )


@pytest.mark.unit
def test_force_promotes_on_feature_names_mismatch_with_matching_count_and_no_version(
    tmp_path: Path,
) -> None:
    """CP1's actual tire_deg rename in isolation: same feature COUNT, no
    training_schema_version declared on either side (so that axis can't fire),
    just a feature_names difference at the same position — the class of drift
    a shape-based count check can never detect.
    """
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=62)
    old_names = [*FEATURE_COLUMNS[:3], "fuel_adjusted_time", *FEATURE_COLUMNS[4:]]
    _seed_declared_production(
        fake_client,
        tmp_path,
        "tire_deg_hard.pkl",
        incumbent,
        {"holdout_mae": 0.3, "n_samples": 100},
        n_features=len(FEATURE_COLUMNS),
    )
    incumbent_metrics = json.loads(fake_client.objects["production/tire_deg_hard.pkl.metrics.json"])
    incumbent_metrics["feature_names"] = old_names
    fake_client.objects["production/tire_deg_hard.pkl.metrics.json"] = json.dumps(
        incumbent_metrics
    ).encode("utf-8")
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=63)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_hard.pkl",
        candidate,
        {"holdout_mae": 0.9},  # worse — must be irrelevant
        feature_names=list(
            FEATURE_COLUMNS
        ),  # carries "fuel_load_penalty", not "fuel_adjusted_time"
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="feature_names_mismatch")


@pytest.mark.unit
def test_promotes_normally_when_schema_names_and_version_all_match(tmp_path: Path) -> None:
    """The non-regression case: once a candidate has already been promoted once
    under CP2 (sidecar carries matching feature_names + training_schema_version),
    a later run with a WORSE holdout_mae must fall through to a normal comparison
    and correctly NOT promote — CP2 must not force-promote every run forever.
    """
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=64)
    _seed_declared_production(
        fake_client,
        tmp_path,
        "tire_deg_soft.pkl",
        incumbent,
        {"holdout_mae": 0.5, "n_samples": 100, "training_schema_version": 2},
        n_features=len(FEATURE_COLUMNS),
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=65)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_soft.pkl",
        candidate,
        {"holdout_mae": 0.6, "n_samples": 100},
        feature_names=list(FEATURE_COLUMNS),
        training_schema_version=2,
    )

    assert outcome == train_models.PromotionOutcome(
        promoted=False, reason="holdout_mae_not_improved"
    )


@pytest.mark.unit
def test_schema_mismatch_takes_priority_when_all_three_axes_disagree(tmp_path: Path) -> None:
    """When feature count, feature names, AND training_schema_version all differ at
    once, the reported reason must be the highest-severity one (schema_mismatch —
    a hard crash risk) rather than an arbitrary or last-checked one, so operators
    reading the log/summary see the most actionable cause first.
    """
    fake_client = _FakeS3Client()
    stale = _fit_pipeline(8, seed=66)  # wrong count too
    _seed_legacy_production(
        fake_client,
        tmp_path,
        "tire_deg_wet.pkl",
        stale,
        {
            "holdout_mae": 5.0,
            "n_samples": 300,
            "feature_names": ["a"],
            "training_schema_version": 1,
        },
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=67)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_wet.pkl",
        candidate,
        {"holdout_mae": 6.0},
        feature_names=list(FEATURE_COLUMNS),
        training_schema_version=2,
    )

    assert outcome == train_models.PromotionOutcome(promoted=True, reason="schema_mismatch")


@pytest.mark.unit
def test_version_mismatch_takes_priority_over_names_mismatch_at_matching_count(
    tmp_path: Path,
) -> None:
    """The exact shape of every real tire_deg production sidecar as of 2026-09-11
    (MEDIUM/HARD/INTER/WET): 6 features recorded, feature_names still carrying
    fuel_adjusted_time, no training_schema_version. Against a v2 candidate both the
    names and version axes fire at once with no count mismatch — the reported
    reason must be the deliberate version signal, not the name-based safety net.
    """
    fake_client = _FakeS3Client()
    incumbent = _fit_pipeline(len(FEATURE_COLUMNS), seed=68)
    old_names = [*FEATURE_COLUMNS[:3], "fuel_adjusted_time", *FEATURE_COLUMNS[4:]]
    _seed_legacy_production(
        fake_client,
        tmp_path,
        "tire_deg_hard.pkl",
        incumbent,
        {
            "holdout_mae": 0.5167500066896806,
            "n_samples": 46963,
            "n_features": len(FEATURE_COLUMNS),
            "feature_names": old_names,
            "schema_source": "declared",
        },
    )
    candidate = _fit_pipeline(len(FEATURE_COLUMNS), seed=69)

    outcome = train_models.serialize_evaluate_and_upload(
        fake_client,
        BUCKET,
        VERSION_TAG,
        "tire_deg_hard.pkl",
        candidate,
        {"holdout_mae": 0.9},
        feature_names=list(FEATURE_COLUMNS),
        training_schema_version=2,
    )

    assert outcome == train_models.PromotionOutcome(
        promoted=True, reason="training_schema_version_mismatch"
    )
    assert fake_client.download_file_calls == 0


# --- _feature_names_mismatch / _training_schema_mismatch: direct unit coverage ---


@pytest.mark.unit
def test_feature_names_mismatch_returns_false_when_candidate_declares_none() -> None:
    assert train_models._feature_names_mismatch(None, {"feature_names": ["x"]}) is False


@pytest.mark.unit
def test_feature_names_mismatch_true_for_legacy_incumbent_with_no_names_recorded() -> None:
    assert train_models._feature_names_mismatch(["a", "b"], {"holdout_mae": 0.5}) is True


@pytest.mark.unit
def test_feature_names_mismatch_false_when_identical() -> None:
    assert train_models._feature_names_mismatch(["a", "b"], {"feature_names": ["a", "b"]}) is False


@pytest.mark.unit
def test_training_schema_mismatch_returns_false_when_candidate_declares_none() -> None:
    assert train_models._training_schema_mismatch(None, {"training_schema_version": 1}) is False


@pytest.mark.unit
def test_training_schema_mismatch_true_for_legacy_incumbent_with_no_version_recorded() -> None:
    assert train_models._training_schema_mismatch(2, {"holdout_mae": 0.5}) is True


@pytest.mark.unit
def test_training_schema_mismatch_false_when_identical() -> None:
    assert train_models._training_schema_mismatch(2, {"training_schema_version": 2}) is False
