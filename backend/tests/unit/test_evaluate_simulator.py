"""Unit tests for evaluate_simulator's pure helpers (the harness itself needs the models)."""

import pytest

from backend.scripts import evaluate_simulator as es


def _laps(*rows: tuple[int, str, int]) -> list[es.LapRow]:
    return [es.LapRow(lap, compound, age, None) for lap, compound, age in rows]


@pytest.mark.unit
def test_real_stops_are_in_laps_after_the_start_lap() -> None:
    laps = _laps(
        (10, "MEDIUM", 10),
        (11, "MEDIUM", 11),  # in-lap of a stop onto HARD
        (12, "HARD", 1),
        (13, "HARD", 2),  # in-lap of a stop onto HARD again (same compound)
        (14, "HARD", 1),
    )
    assert es.real_stops(laps, after_lap=10) == [(11, "HARD"), (13, "HARD")]
    # A stop whose in-lap is the start lap itself is already under way.
    assert es.real_stops(laps, after_lap=11) == [(13, "HARD")]


@pytest.mark.unit
def test_real_stops_skip_a_gap_in_the_laps() -> None:
    laps = _laps((10, "MEDIUM", 10), (12, "HARD", 1))
    assert es.real_stops(laps, after_lap=0) == []


@pytest.mark.unit
def test_first_stop_lap_is_where_half_the_runs_have_stopped() -> None:
    assert es.first_stop_lap([(20, 0.2), (21, 0.3), (22, 0.3)]) == 21
    assert es.first_stop_lap([(20, 0.2), (21, 0.1)]) is None
    assert es.first_stop_lap([]) is None


@pytest.mark.unit
def test_ranks_order_by_value_then_key() -> None:
    assert es.ranks({"b": 3.0, "a": 3.0, "c": 1.5}) == {"c": 1, "a": 2, "b": 3}
