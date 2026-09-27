"""Unit tests for scripts/validate_replay_precompute.py's check functions.

The checks are pure functions of plain rows, so each test builds a tiny race
by hand: three cars (A leads B leads C) over laps 20-22 of a 52-lap race.
"""

import uuid

import pytest

from backend.scripts import validate_replay_precompute as v

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _laps(tyre_age: int = 10) -> list[v.LapRow]:
    return [
        v.LapRow(driver, lap, position, tyre_age)
        for lap in (20, 21, 22)
        for driver, position in ((A, 1), (B, 2), (C, 3))
    ]


def _prediction(
    driver: uuid.UUID,
    lap: int,
    pit_probability: float = 0.1,
    undercut: float = 0.2,
    optimal: int = 30,
    recommended: int | None = 31,
    window_end: int | None = 33,
) -> v.PredictionRow:
    return v.PredictionRow(driver, lap, pit_probability, undercut, optimal, recommended, window_end)


def _full_predictions() -> list[v.PredictionRow]:
    return [_prediction(d, lap) for lap in (20, 21, 22) for d in (A, B, C)]


# --- prediction coverage ---


@pytest.mark.unit
def test_coverage_passes_with_one_prediction_per_driver_lap() -> None:
    assert v.check_prediction_coverage(_laps(), _full_predictions(), 20, 22) == []


@pytest.mark.unit
def test_coverage_reports_missing_duplicate_and_stray_predictions() -> None:
    predictions = [p for p in _full_predictions() if (p.driver_id, p.lap_number) != (C, 22)]
    predictions += [_prediction(A, 20), _prediction(B, 40)]

    problems = v.check_prediction_coverage(_laps(), predictions, 20, 22)

    assert len(problems) == 3
    assert any("no prediction" in p and "lap 22" in p for p in problems)
    assert any("2 predictions" in p for p in problems)
    assert any("not a lap in the window" in p for p in problems)


@pytest.mark.unit
def test_coverage_ignores_the_lap_before_the_window() -> None:
    laps = _laps() + [v.LapRow(A, 19, 1, 9)]  # loaded for the snapshot check only
    assert v.check_prediction_coverage(laps, _full_predictions(), 20, 22) == []


# --- pit laps within the race ---


@pytest.mark.unit
def test_pit_laps_past_the_finish_are_reported_per_field() -> None:
    predictions = [
        _prediction(A, 50, optimal=91),
        _prediction(B, 50, recommended=53),
        _prediction(C, 50, window_end=54),
        _prediction(A, 51, optimal=52, recommended=None, window_end=None),
    ]

    problems = v.check_pit_laps_within_race(predictions, 52)

    assert len(problems) == 3
    assert {p.split(": ")[1].split(" ")[0] for p in problems} == {
        "optimal_pit_lap",
        "recommended_pit_lap",
        "window_end",
    }


@pytest.mark.unit
def test_pit_laps_cannot_be_checked_without_a_race_distance() -> None:
    assert v.check_pit_laps_within_race(_full_predictions(), None) != []


# --- gap snapshot order ---


@pytest.mark.unit
def test_snapshot_order_matching_lap_data_passes() -> None:
    assert v.check_snapshot_order({20: [A, B, C], 21: [A, B, C]}, _laps()) == []


@pytest.mark.unit
def test_snapshot_order_differing_from_lap_data_is_reported() -> None:
    problems = v.check_snapshot_order({21: [B, A, C]}, _laps())
    assert problems == ["lap 21: snapshot order differs from lap_data positions"]


@pytest.mark.unit
def test_snapshot_order_compares_only_cars_in_both() -> None:
    """A snapshot leaves out a car with no FastF1 time on that lap."""
    assert v.check_snapshot_order({22: [A, C]}, _laps()) == []


# --- alert rules ---


def _alert(
    driver: uuid.UUID, rival: uuid.UUID | None, lap: int = 21, score: float | None = 0.8
) -> v.AlertRow:
    return v.AlertRow(lap, driver, rival, score, "Undercut threat")


@pytest.mark.unit
def test_an_alert_obeying_every_rule_passes() -> None:
    assert v.check_alert_rules([_alert(B, A)], _laps(), 52) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("alert", "laps", "total_laps", "expected"),
    [
        (_alert(B, A, score=0.5), _laps(), 52, "not above"),
        (_alert(B, A), _laps(tyre_age=3), 52, "tyres only 3 laps old"),
        (_alert(B, A, lap=21), _laps(), 30, "only 9 laps left"),
        (_alert(C, A), _laps(), 52, "not the car directly ahead"),
        (_alert(B, A, lap=40), _laps(), 52, "no lap_data row"),
    ],
)
def test_alert_rule_breaks_are_reported(
    alert: v.AlertRow, laps: list[v.LapRow], total_laps: int, expected: str
) -> None:
    problems = v.check_alert_rules([alert], laps, total_laps)
    assert any(expected in problem for problem in problems), problems


# --- reported metrics ---


@pytest.mark.unit
def test_pit_probability_around_a_stop_averages_the_laps_before_and_after() -> None:
    predictions = [
        _prediction(A, lap, pit_probability=prob)
        for lap, prob in ((17, 0.6), (18, 0.8), (19, 0.9), (20, 0.95), (21, 0.1), (22, 0.05))
    ]

    (stop,) = v.pit_probability_around_stops(predictions, [v.PitStop(A, 20)])

    assert stop.before == pytest.approx((0.6 + 0.8 + 0.9) / 3)
    assert stop.after == pytest.approx((0.1 + 0.05) / 2)


@pytest.mark.unit
def test_pit_probability_is_none_when_the_window_has_no_laps_on_that_side() -> None:
    (stop,) = v.pit_probability_around_stops([_prediction(A, 22)], [v.PitStop(A, 22)])
    assert stop.before is None
    assert stop.after is None


@pytest.mark.unit
def test_undercut_spread_counts_varying_drivers_and_saturated_scores() -> None:
    predictions = [
        _prediction(A, 20, undercut=0.0),
        _prediction(A, 21, undercut=0.0),
        _prediction(B, 20, undercut=0.3),
        _prediction(B, 21, undercut=1.0),
    ]

    spread = v.undercut_spread(predictions)

    assert spread == v.UndercutSpread(
        drivers=2, drivers_varying=1, distinct_values=3, saturated_share=0.75
    )


@pytest.mark.unit
def test_pit_stops_are_stint_ends_followed_by_another_stint_inside_the_window() -> None:
    stints = [
        (A, 1, 18),  # pits end of lap 18, before the window
        (A, 2, 21),  # pits end of lap 21
        (A, 3, 52),  # last stint: no pit
        (B, 1, 20),  # pits end of lap 20
        (B, 2, None),
        (C, 1, 52),  # never pits
    ]

    stops = v.pit_stops_in_window(stints, 20, 22)

    assert stops == [v.PitStop(B, 20), v.PitStop(A, 21)]
