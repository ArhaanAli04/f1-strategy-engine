"""Unit tests for evaluate_undercut_live_gaps.py's pure logic (target resolution,
alert replay, summary statistics). The DB/S3/archive-driven parts are exercised by
running the script against the real Monza session, not here."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.scripts import evaluate_undercut_live_gaps as evaluation

T0 = datetime(2026, 9, 6, 13, 0, 0, tzinfo=UTC)


def _lap(position: int | None, seconds: int, lap_time: float | None = 90.0) -> Any:
    return evaluation.LapRow(
        position=position,
        compound="MEDIUM",
        tyre_age_laps=5,
        lap_time_seconds=lap_time,
        created_at=T0 + timedelta(seconds=seconds),
    )


def _race(
    laps: dict[tuple[str, int], Any],
    predictions: list[Any] | None = None,
    subscribed: set[str] | None = None,
) -> Any:
    codes = sorted({code for code, _ in laps})
    ids = {code: uuid.uuid4() for code in codes}
    lap_numbers: dict[str, list[int]] = {}
    for code, lap in laps:
        lap_numbers.setdefault(code, []).append(lap)
    cumulative: dict[str, dict[int, float]] = {}
    for code, numbers in lap_numbers.items():
        numbers.sort()
        running = 0.0
        cumulative[code] = {}
        for lap in numbers:
            running += laps[(code, lap)].lap_time_seconds or 0.0
            cumulative[code][lap] = running
    return evaluation.RaceData(
        code_by_id={v: k for k, v in ids.items()},
        id_by_code=ids,
        laps=laps,
        lap_numbers=lap_numbers,
        predictions=predictions or [],
        circuit_id=uuid.uuid4(),
        circuit_name="Monza",
        total_laps=max(lap for _, lap in laps),
        subscribed=subscribed or set(),
        real_alerts=[],
        cumulative_time=cumulative,
    )


# --- RaceData lookups ---


@pytest.mark.unit
def test_latest_lap_at_or_before_and_summed_time_use_the_last_recorded_lap() -> None:
    data = _race({("VER", 1): _lap(1, 0, None), ("VER", 2): _lap(1, 90), ("VER", 4): _lap(1, 270)})

    assert data.latest_lap_at_or_before("VER", 3) == 2
    assert data.latest_lap_at_or_before("VER", 4) == 4
    assert data.latest_lap_at_or_before("VER", 0) is None
    # Lap 1 has no recorded time (the live-ingestion gap the summed gaps inherit).
    assert data.summed_time("VER", 2) == 90.0
    assert data.summed_time("VER", 4) == 180.0
    assert data.summed_time("VER", 0) == 0.0


# --- target resolution ---


@pytest.mark.unit
def test_old_target_reads_each_drivers_latest_row_and_keeps_a_retiree_in_the_order() -> None:
    """The bug being measured: LEC's lap-1 row (position 2) never leaves the field."""
    data = _race(
        {
            ("LEC", 1): _lap(2, 0),
            ("RUS", 5): _lap(1, 0),
            ("VER", 5): _lap(3, 0),
        }
    )

    assert evaluation.old_target(data, "VER", 5) == "LEC"
    assert evaluation.old_target(data, "RUS", 5) is None  # leader


@pytest.mark.unit
def test_old_target_is_bounded_by_the_requested_lap() -> None:
    data = _race({("RUS", 5): _lap(1, 0), ("RUS", 9): _lap(3, 0), ("VER", 5): _lap(2, 0)})

    assert evaluation.old_target(data, "VER", 5) == "RUS"  # RUS's lap-9 row is not yet visible


@pytest.mark.unit
def test_live_target_is_the_entry_one_position_ahead() -> None:
    tower = [
        {"driver_id": "VER", "position": 3},
        {"driver_id": "RUS", "position": 1},
        {"driver_id": "NOR", "position": 2},
    ]

    assert evaluation.live_target(tower, "VER") == "NOR"
    assert evaluation.live_target(tower, "RUS") is None
    assert evaluation.live_target(tower, "XXX") is None


# --- alert replay (alert_service.evaluate_threats' rules) ---


def _alert_setup(scores: list[float], gap_seconds: int) -> tuple[Any, list[Any]]:
    """VER trails RUS; one prediction for VER every `gap_seconds`, with the given scores."""
    laps = {("RUS", 1): _lap(1, 0), ("VER", 1): _lap(2, 0)}
    predictions = [
        evaluation.Prediction("VER", 1, T0 + timedelta(seconds=10 + i * gap_seconds), score)
        for i, score in enumerate(scores)
    ]
    return _race(laps, predictions, subscribed={"VER"}), predictions


def _replay(data: Any) -> list[Any]:
    return evaluation.emulate_alerts(
        data, lambda p: p.stored_score, lambda code, lap: data.laps[(code, lap)].position
    )


@pytest.mark.unit
def test_emulate_alerts_fires_only_above_the_threshold() -> None:
    data, _ = _alert_setup([0.5, 0.51], gap_seconds=100)

    alerts = _replay(data)

    assert [(a[1], a[2]) for a in alerts] == [("VER", "RUS")]
    assert alerts[0][3] == pytest.approx(0.51)


@pytest.mark.unit
def test_emulate_alerts_dedups_within_60s_and_refires_after() -> None:
    data, _ = _alert_setup([0.9, 0.9, 0.9], gap_seconds=40)  # t = 10, 50, 90

    alerts = _replay(data)

    # t=10 fires; t=50 is inside the 60s claim; t=90 is past it (claim ended t=70).
    assert [round((a[0] - T0).total_seconds()) for a in alerts] == [10, 90]


@pytest.mark.unit
def test_emulate_alerts_only_alerts_for_subscribed_trailing_drivers() -> None:
    data, _ = _alert_setup([0.9], gap_seconds=100)
    data.subscribed = {"RUS"}  # RUS is ahead, not trailing

    assert _replay(data) == []


@pytest.mark.unit
def test_emulate_alerts_skips_drivers_with_no_position() -> None:
    laps = {("RUS", 1): _lap(1, 0), ("VER", 1): _lap(None, 0)}
    predictions = [evaluation.Prediction("VER", 1, T0 + timedelta(seconds=10), 0.9)]
    data = _race(laps, predictions, subscribed={"VER"})

    assert _replay(data) == []


# --- summaries ---


@pytest.mark.unit
def test_distribution_counts_saturation_and_alert_eligibility() -> None:
    counts = evaluation.distribution([1.0, 0.9995, 0.0, 0.0005, 0.4, 0.6])

    assert counts["n"] == 6
    assert counts[">=0.999"] == 2
    assert counts["<=0.001"] == 2
    assert counts["in between"] == 2
    assert counts[">0.5 (alert-eligible)"] == 3  # 1.0, 0.9995, 0.6


@pytest.mark.unit
def test_flip_stats_counts_only_consecutive_lap_swings() -> None:
    def _row(lap: int, score: float) -> Any:
        return evaluation.RowResult(evaluation.Prediction("VER", lap, T0, 0.0), old_score=score)

    rows = [_row(1, 0.0), _row(2, 1.0), _row(3, 0.95), _row(5, 0.0)]  # lap 5 has no lap 4

    flips, mean_delta = evaluation.flip_stats(rows, lambda r: r.old_score)

    assert flips == 1  # 0.0 -> 1.0; 1.0 -> 0.95 is small; lap 5 has no predecessor
    assert mean_delta == pytest.approx((1.0 + 0.05) / 2)


@pytest.mark.unit
def test_retiree_last_row_flags_only_cars_that_stopped_well_short_of_the_distance() -> None:
    laps: dict[tuple[str, int], Any] = {("LEC", 1): _lap(3, 5)}
    laps.update({("VER", n): _lap(1, n) for n in range(1, 54)})
    data = _race(laps)

    assert evaluation.retiree_last_row(data) == {"LEC": T0 + timedelta(seconds=5)}


@pytest.mark.unit
def test_emulate_alerts_live_order_replaces_the_stored_order() -> None:
    """A retiree stuck in the stored order (LEC, P2) takes the alert; the live order omits it."""
    laps = {
        ("LEC", 1): _lap(2, 0),
        ("RUS", 1): _lap(1, 0),
        ("VER", 1): _lap(3, 0),
    }
    predictions = [evaluation.Prediction("VER", 1, T0 + timedelta(seconds=10), 0.9)]
    data = _race(laps, predictions, subscribed={"VER"})
    position_of = lambda code, lap: data.laps[(code, lap)].position  # noqa: E731

    stored = evaluation.emulate_alerts(data, lambda p: p.stored_score, position_of)
    live = evaluation.emulate_alerts(
        data, lambda p: p.stored_score, position_of, live_order_at=lambda c, lap: ["RUS", "VER"]
    )

    assert [(a[1], a[2]) for a in stored] == [("VER", "LEC")]
    assert [(a[1], a[2]) for a in live] == [("VER", "RUS")]


@pytest.mark.unit
def test_emulate_alerts_race_state_gates_suppress_fresh_tyres_and_the_final_laps() -> None:
    def _laps(lap: int, tyre_age: int) -> dict[tuple[str, int], Any]:
        lead = evaluation.LapRow(1, "MEDIUM", 20, 90.0, T0)
        trail = evaluation.LapRow(2, "MEDIUM", tyre_age, 90.0, T0)
        far = evaluation.LapRow(3, "MEDIUM", 5, 90.0, T0)  # sets total_laps to 53
        return {("RUS", lap): lead, ("VER", lap): trail, ("NOR", 53): far}

    def _alerts(lap: int, tyre_age: int, gates: bool) -> int:
        predictions = [evaluation.Prediction("VER", lap, T0 + timedelta(seconds=10), 0.9)]
        data = _race(_laps(lap, tyre_age), predictions, subscribed={"VER"})
        return len(
            evaluation.emulate_alerts(
                data,
                lambda p: p.stored_score,
                lambda code, lap_: data.laps[(code, lap_)].position,
                apply_race_state_gates=gates,
            )
        )

    assert _alerts(20, 10, gates=True) == 1
    assert _alerts(20, 2, gates=True) == 0  # tyres 2 laps old
    assert _alerts(45, 10, gates=True) == 0  # 8 laps left
    assert _alerts(20, 2, gates=False) == 1  # the same situation without the gates
