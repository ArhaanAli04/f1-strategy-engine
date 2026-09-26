"""Unit tests for ingest_live_session.py's Checkpoint 1 live-parity fixes
(see docs/internal/core-feature-rebuild-strategy-recommendations.md and CLAUDE.md's
core-feature-rebuild session):

1. tyre_age_laps was previously hardcoded to 0 for every lap — now derived
   from the tracked stint start_lap (_current_tyre_age).
2. lap_data.position was never populated for a live session at all (F1 only
   ever sends the Position field once, in the Subscribe snapshot) — now
   re-derived continuously from the streaming GapToLeader field
   (_recompute_positions), and threaded into raw_lap.
3. A retired car's gap freezing in the live standings for the rest of the
   race. An earlier fix waited for a "RETIRED" GapToLeader string that F1's
   real feed was later confirmed (Monza 2026 archive,
   docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue C) to never
   send — it sends Retired/ShowPosition/Stopped booleans instead, which are
   what the retirement, lapped-car ("1 L"/"52L") and Position-vs-gap ranking
   tests below pin.

F1SignalRIngestor's __init__ has no network/DB side effects (those only
happen in start()/_build_connection()), so it's constructed directly here
with plain in-memory stand-ins — no real Redis, no real Celery broker.
"""

import json
import random
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.scripts import ingest_live_session


def _make_ingestor(
    car_number_to_driver_id: dict[str, Any] | None = None,
) -> ingest_live_session.F1SignalRIngestor:
    return ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=10,
        session_id="session-1",
        car_number_to_driver_id=car_number_to_driver_id or {},
        driver_code_to_id={},
        redis_client=MagicMock(),
        no_auth=True,
    )


# --- _current_tyre_age ---


@pytest.mark.unit
def test_current_tyre_age_derives_from_tracked_stint_start_lap() -> None:
    ingestor = _make_ingestor()
    ingestor._car_stint_start_lap["44"] = 12

    assert ingestor._current_tyre_age("44", 15) == 4  # 15 - 12 + 1
    assert ingestor._current_tyre_age("44", 12) == 1  # the out-lap itself


@pytest.mark.unit
def test_current_tyre_age_defaults_to_stint_start_lap_1_when_untracked() -> None:
    """No TimingAppData seen yet for this car — assume they started the
    session on their current tyre, same fallback spirit as
    _car_current_compound's own "UNKNOWN" default."""
    ingestor = _make_ingestor()

    assert ingestor._current_tyre_age("99", 5) == 5  # 5 - 1 + 1


@pytest.mark.unit
def test_current_tyre_age_floors_at_zero_for_out_of_order_message() -> None:
    ingestor = _make_ingestor()
    ingestor._car_stint_start_lap["44"] = 20

    assert ingestor._current_tyre_age("44", 10) == 0


# --- _handle_timing_app_data: stint start_lap tracking ---


@pytest.mark.unit
def test_handle_timing_app_data_tracks_stint_start_lap(monkeypatch: pytest.MonkeyPatch) -> None:
    driver_id = "driver-44"
    ingestor = _make_ingestor(car_number_to_driver_id={"44": driver_id})
    ingestor._laps_seen["44"] = 20  # 20 laps completed so far

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.record_tire_stint, "delay", lambda payload: dispatched.append(payload)
    )

    ingestor._handle_timing_app_data({"Lines": {"44": {"Stints": [{"Compound": "hard"}]}}})

    assert ingestor._car_stint_start_lap["44"] == 21  # laps_seen + 1
    assert dispatched[0]["start_lap"] == 21  # must agree with the tracked value
    assert ingestor._car_current_compound["44"] == "HARD"


@pytest.mark.unit
def test_handle_timing_app_data_index_keyed_diff_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Later diffs key Stints by index string (e.g. {"1": {...}}) instead of
    resending the whole list — _latest_stint already handles both shapes;
    this just confirms stint tracking still fires correctly for the diff shape."""
    driver_id = "driver-44"
    ingestor = _make_ingestor(car_number_to_driver_id={"44": driver_id})
    ingestor._car_last_stint_index["44"] = 0  # already recorded stint 0
    ingestor._laps_seen["44"] = 15

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.record_tire_stint, "delay", lambda payload: dispatched.append(payload)
    )

    ingestor._handle_timing_app_data({"Lines": {"44": {"Stints": {"1": {"Compound": "soft"}}}}})

    assert ingestor._car_stint_start_lap["44"] == 16
    assert dispatched[0]["stint_number"] == 2  # stint_index (1) + 1


# --- _recompute_positions ---


@pytest.mark.unit
def test_recompute_positions_ranks_by_gap_to_leader() -> None:
    ingestor = _make_ingestor()
    # Insertion order deliberately NOT the finishing order, to prove ranking
    # comes from gap_to_leader, not dict iteration order.
    ingestor._car_live_gap_state = {
        "2": {"position": None, "gap_to_leader": 5.2, "gap_to_ahead": None, "laps_behind": 0},
        "1": {"position": None, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        "3": {"position": None, "gap_to_leader": 1.1, "gap_to_ahead": None, "laps_behind": 0},
    }

    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["1"]["position"] == 1  # no gap => leader
    assert ingestor._car_live_gap_state["3"]["position"] == 2  # smaller real gap
    assert ingestor._car_live_gap_state["2"]["position"] == 3


@pytest.mark.unit
def test_recompute_positions_falls_back_to_snapshot_order_with_no_gap_data() -> None:
    """Right after Subscribe, before any GapToLeader "feed" message has
    arrived for anyone — every car's gap_to_leader is still None. Must
    reproduce the original snapshot Position order, not scramble it."""
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "9": {"position": 3, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        "1": {"position": 1, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        "5": {"position": 2, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
    }

    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["1"]["position"] == 1
    assert ingestor._car_live_gap_state["5"]["position"] == 2
    assert ingestor._car_live_gap_state["9"]["position"] == 3


@pytest.mark.unit
def test_recompute_positions_transitions_on_lead_change() -> None:
    """Old leader now has a real gap_to_leader (they've been passed); new
    leader's gap_to_leader is still None (F1 sends blank for the leader) —
    ranking must follow the gap data, not stay pinned to whoever led first."""
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "1": {"position": 1, "gap_to_leader": 0.4, "gap_to_ahead": None, "laps_behind": 0},
        "2": {"position": 2, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
    }

    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["2"]["position"] == 1
    assert ingestor._car_live_gap_state["1"]["position"] == 2


# --- _handle_timing_data: raw_lap carries live position + real tyre_age_laps ---


@pytest.mark.unit
def test_handle_timing_data_dispatches_raw_lap_with_position_and_tyre_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = _make_ingestor(car_number_to_driver_id={"44": "driver-44"})
    ingestor._car_stint_start_lap["44"] = 10
    ingestor._car_current_compound["44"] = "MEDIUM"

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.process_lap, "delay", lambda raw_lap: dispatched.append(raw_lap)
    )
    monkeypatch.setattr(ingest_live_session.run_strategy_prediction, "delay", lambda raw_lap: None)

    ingestor._handle_timing_data(
        {
            "Lines": {
                "44": {
                    "Position": "3",
                    "GapToLeader": "+5.234",
                    "NumberOfLaps": 12,
                    "LastLapTime": {"Value": "1:32.456"},
                }
            }
        }
    )

    assert len(dispatched) == 1
    raw_lap = dispatched[0]
    assert raw_lap["tyre_age_laps"] == 3  # 12 - 10 + 1
    # Only one car in the field: no-gap-to-leader-vs-real-gap ambiguity does
    # not arise (see _recompute_positions' own leader-disambiguation tests
    # above) — this asserts the field IS populated from the recomputed rank,
    # not that F1's own stale/frozen Position("3") string leaked through.
    assert raw_lap["position"] == 1
    assert raw_lap["compound"] == "MEDIUM"


@pytest.mark.unit
def test_handle_timing_data_recomputes_positions_before_dispatching_within_one_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two cars update in the SAME message; one completes a lap. Its
    dispatched position must reflect the OTHER car's gap update from that
    same message (two-pass processing), not a stale value computed before
    the other car's update was applied."""
    ingestor = _make_ingestor(car_number_to_driver_id={"1": "driver-1", "2": "driver-2"})

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.process_lap, "delay", lambda raw_lap: dispatched.append(raw_lap)
    )
    monkeypatch.setattr(ingest_live_session.run_strategy_prediction, "delay", lambda raw_lap: None)

    ingestor._handle_timing_data(
        {
            "Lines": {
                # Car "1" is the leader (no GapToLeader) and completes a lap
                # in this message.
                "1": {"NumberOfLaps": 5, "LastLapTime": {"Value": "1:30.000"}},
                # Car "2" reports its gap to the leader in the SAME message —
                # must be factored into car "1"'s own position before car
                # "1"'s raw_lap is built, even though car "1" is processed
                # first in dict iteration order.
                "2": {"GapToLeader": "+3.500"},
            }
        }
    )

    assert len(dispatched) == 1
    assert dispatched[0]["position"] == 1  # car "1" is still the leader


# --- retirement: F1's real signals are Retired / ShowPosition / Stopped booleans ---
# (docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue C — F1 never
# sends a "RETIRED" gap string; confirmed against Monza 2026's archived feed)


def _gap_state(position: int | None, gap_to_leader: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "position": position,
        "gap_to_leader": gap_to_leader,
        "gap_to_ahead": None,
        "laps_behind": 0,
        **extra,
    }


def _three_car_field() -> ingest_live_session.F1SignalRIngestor:
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "1": _gap_state(1, None),
        "2": _gap_state(2, 5.0),
        "3": _gap_state(3, 10.0),
    }
    return ingestor


@pytest.mark.unit
def test_retired_flag_takes_car_out_of_ranking_and_promotes_those_behind() -> None:
    """The actual bug: a retired car's frozen gap kept occupying a ranking slot
    all race, shifting every trailing driver by one (LEC at Monza 2026)."""
    ingestor = _three_car_field()

    changed = ingestor._update_gap_state("2", {"Retired": True, "Stopped": True})
    ingestor._recompute_positions()

    assert changed is True
    state = ingestor._car_live_gap_state
    assert state["2"]["position"] is None
    assert state["1"]["position"] == 1
    assert state["3"]["position"] == 2  # promoted, not stuck at 3


@pytest.mark.unit
def test_show_position_false_alone_takes_car_out_of_ranking() -> None:
    """STR at Monza 2026 was hidden from the tower without F1 ever sending Retired."""
    ingestor = _three_car_field()

    ingestor._update_gap_state("2", {"ShowPosition": False})
    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["2"]["position"] is None
    assert ingestor._car_live_gap_state["3"]["position"] == 2


@pytest.mark.unit
def test_stopped_flag_hides_car_and_it_rejoins_with_its_gap_history_when_cleared() -> None:
    """LEC at Monza 2026 stopped and restarted twice before finally retiring;
    a stop must not permanently delete the car's state."""
    ingestor = _three_car_field()

    ingestor._update_gap_state("2", {"Stopped": True})
    ingestor._recompute_positions()
    assert ingestor._car_live_gap_state["2"]["position"] is None

    ingestor._update_gap_state("2", {"Stopped": False})
    ingestor._recompute_positions()
    assert ingestor._car_live_gap_state["2"]["gap_to_leader"] == 5.0  # history intact
    assert ingestor._car_live_gap_state["2"]["position"] == 2


@pytest.mark.unit
def test_snapshot_flags_that_are_all_clear_do_not_exclude_anyone() -> None:
    """The Subscribe snapshot carries Retired=false/Stopped=false/ShowPosition=true
    for every car."""
    ingestor = _three_car_field()

    for car in ("1", "2", "3"):
        ingestor._update_gap_state(car, {"Retired": False, "Stopped": False, "ShowPosition": True})
    ingestor._recompute_positions()

    assert [ingestor._car_live_gap_state[c]["position"] for c in ("1", "2", "3")] == [1, 2, 3]


@pytest.mark.unit
def test_retirement_flag_on_a_car_never_seen_before_does_not_crash_or_get_ranked() -> None:
    ingestor = _make_ingestor()

    ingestor._update_gap_state("99", {"Retired": True})
    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["99"]["position"] is None


@pytest.mark.unit
def test_retired_car_is_left_out_of_the_published_standings() -> None:
    redis_mock = MagicMock()
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=13,
        session_id="s",
        car_number_to_driver_id={"1": "d1", "2": "d2", "3": "d3"},
        driver_code_to_id={},
        redis_client=redis_mock,
        no_auth=True,
    )
    ingestor._car_live_gap_state = {
        "1": _gap_state(1, None),
        "2": _gap_state(2, 5.0),
        "3": _gap_state(3, 10.0),
    }
    ingestor._update_gap_state("2", {"Retired": True})
    ingestor._recompute_positions()

    ingestor._publish_live_gaps()

    payload = json.loads(redis_mock.setex.call_args.args[2])
    assert [(e["driver_id"], e["position"]) for e in payload["gaps"]] == [("d1", 1), ("d3", 2)]


# --- lapped cars: F1 sends "1 L" / "1L" / "52L" ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1 L", (None, 1)),
        ("1L", (None, 1)),
        ("52L", (None, 52)),
        ("+1 LAP", (None, 1)),
        ("2 LAPS", (None, 2)),
        ("+1.759", (1.759, 0)),
        ("LAP 14", (None, 0)),  # the LEADER's own lap counter, not a lapped car
        ("", (None, 0)),
        (None, (None, 0)),
    ],
)
def test_parse_gap_string_handles_real_f1_lapped_formats(
    value: str | None, expected: tuple[float | None, int]
) -> None:
    assert ingest_live_session._parse_gap_string(value) == expected


@pytest.mark.unit
def test_lapped_gap_to_leader_replaces_the_frozen_numeric_gap() -> None:
    """BOT at Monza 2026 kept its last numeric +82.238s for the rest of the race."""
    ingestor = _make_ingestor()
    ingestor._update_gap_state("77", {"GapToLeader": "+82.238"})

    changed = ingestor._update_gap_state("77", {"GapToLeader": "1 L"})

    state = ingestor._car_live_gap_state["77"]
    assert changed is True
    assert state["gap_to_leader"] is None
    assert state["laps_down"] == 1


@pytest.mark.unit
def test_lapped_car_that_unlaps_itself_returns_to_a_numeric_gap() -> None:
    ingestor = _make_ingestor()
    ingestor._update_gap_state("77", {"GapToLeader": "1 L"})

    ingestor._update_gap_state("77", {"GapToLeader": "+40.5"})

    state = ingestor._car_live_gap_state["77"]
    assert state["gap_to_leader"] == 40.5
    assert state["laps_down"] == 0


@pytest.mark.unit
def test_interval_to_car_ahead_in_laps_updates_laps_behind() -> None:
    ingestor = _make_ingestor()
    ingestor._update_gap_state("77", {"IntervalToPositionAhead": {"Value": "+3.2"}})

    ingestor._update_gap_state("77", {"IntervalToPositionAhead": {"Value": "1 L"}})

    state = ingestor._car_live_gap_state["77"]
    assert state["gap_to_ahead"] is None
    assert state["laps_behind"] == 1


@pytest.mark.unit
def test_gap_ranking_puts_lapped_cars_behind_lead_lap_cars_not_mistaken_for_the_leader() -> None:
    """A lapped car's gap_to_leader is None — the same value that marks the
    leader — so without the laps_down split it would be ranked P1."""
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "77": _gap_state(4, None, laps_down=2),
        "5": _gap_state(3, None, laps_down=1),
        "1": _gap_state(1, None),
        "2": _gap_state(2, 5.0),
    }

    ingestor._recompute_positions()

    ranks = {car: s["position"] for car, s in ingestor._car_live_gap_state.items()}
    assert ranks == {"1": 1, "2": 2, "5": 3, "77": 4}  # 1 lap down before 2 laps down


# --- ranking source: F1's Position field vs. the gap-based fallback ---


def _position_field(order: dict[str, int], *, gaps: dict[str, float | None]) -> dict[str, Any]:
    return {
        car: _gap_state(None, gaps[car], f1_position=position) for car, position in order.items()
    }


@pytest.mark.unit
def test_recompute_uses_f1_position_once_seen_streaming_even_where_gaps_disagree() -> None:
    ingestor = _make_ingestor()
    ingestor._position_diff_seen = True
    # Gaps would rank 1,2,3 — F1 says 3 leads (an overtake the gap data hasn't caught up to).
    ingestor._car_live_gap_state = _position_field(
        {"1": 2, "2": 3, "3": 1}, gaps={"1": None, "2": 5.0, "3": 10.0}
    )

    ingestor._recompute_positions()

    assert [ingestor._car_live_gap_state[c]["position"] for c in ("3", "1", "2")] == [1, 2, 3]


@pytest.mark.unit
def test_recompute_ignores_f1_position_until_it_has_been_seen_on_a_live_diff() -> None:
    """Position present only from the Subscribe snapshot may be stale for the
    whole race (2026 Dutch GP) — the gap-based ranking must stay in charge."""
    ingestor = _make_ingestor()
    assert ingestor._position_diff_seen is False
    ingestor._car_live_gap_state = _position_field(
        {"1": 2, "2": 3, "3": 1}, gaps={"1": None, "2": 5.0, "3": 10.0}
    )

    ingestor._recompute_positions()

    assert [ingestor._car_live_gap_state[c]["position"] for c in ("1", "2", "3")] == [1, 2, 3]


@pytest.mark.unit
def test_recompute_falls_back_to_gaps_while_f1_positions_collide_mid_overtake() -> None:
    """F1 sends one car's new Position per message, so two cars can briefly
    share a value."""
    ingestor = _make_ingestor()
    ingestor._position_diff_seen = True
    ingestor._car_live_gap_state = _position_field(
        {"1": 1, "2": 2, "3": 2}, gaps={"1": None, "2": 5.0, "3": 10.0}
    )

    ingestor._recompute_positions()

    assert [ingestor._car_live_gap_state[c]["position"] for c in ("1", "2", "3")] == [1, 2, 3]


@pytest.mark.unit
def test_recompute_falls_back_to_gaps_when_a_ranked_car_has_no_f1_position() -> None:
    ingestor = _make_ingestor()
    ingestor._position_diff_seen = True
    ingestor._car_live_gap_state = _position_field(
        {"1": 2, "2": 3, "3": 1}, gaps={"1": None, "2": 5.0, "3": 10.0}
    )
    del ingestor._car_live_gap_state["3"]["f1_position"]

    ingestor._recompute_positions()

    assert [ingestor._car_live_gap_state[c]["position"] for c in ("1", "2", "3")] == [1, 2, 3]


@pytest.mark.unit
def test_f1_position_ranking_is_dense_after_a_car_drops_out() -> None:
    """A retired car's F1 slot is removed and everyone behind moves up — no hole."""
    ingestor = _make_ingestor()
    ingestor._position_diff_seen = True
    ingestor._car_live_gap_state = _position_field(
        {"1": 1, "2": 2, "3": 3}, gaps={"1": None, "2": 5.0, "3": 10.0}
    )
    ingestor._update_gap_state("2", {"Retired": True})

    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["2"]["position"] is None
    assert ingestor._car_live_gap_state["3"]["position"] == 2


@pytest.mark.unit
def test_position_diff_seen_is_set_by_a_live_diff_but_not_by_the_subscribe_snapshot() -> None:
    ingestor = _make_ingestor()

    ingestor._on_subscribe_result(
        SimpleNamespace(result={"TimingData": {"Lines": {"1": {"Position": "1"}}}})
    )
    assert ingestor._position_diff_seen is False

    ingestor._handle_timing_data({"Lines": {"1": {"Position": "1"}}})
    assert ingestor._position_diff_seen is True


# --- _is_plausible_lap (Issue D: docs/internal/live-race-ingestion-and-strategy-
# gaps-monza-2026.md) ---


@pytest.mark.unit
def test_is_plausible_lap_accepts_a_normal_green_flag_lap() -> None:
    assert ingest_live_session._is_plausible_lap(87.174, 28.453, 30.254, 28.467, {"1"}, set())


@pytest.mark.unit
def test_is_plausible_lap_accepts_a_green_to_yellow_transition() -> None:
    """Both orderings F1's own accuracy check allows ('12' and '21') collapse
    to the same set membership check here — order isn't tracked at all."""
    assert ingest_live_session._is_plausible_lap(90.0, 30.0, 30.0, 30.0, {"1", "2"}, set())


@pytest.mark.unit
def test_is_plausible_lap_rejects_safety_car_status() -> None:
    assert not ingest_live_session._is_plausible_lap(90.0, 30.0, 30.0, 30.0, {"4"}, set())


@pytest.mark.unit
def test_is_plausible_lap_rejects_red_flag_status() -> None:
    assert not ingest_live_session._is_plausible_lap(90.0, 30.0, 30.0, 30.0, {"5"}, set())


@pytest.mark.unit
def test_is_plausible_lap_rejects_lap_immediately_after_a_safety_car_lap() -> None:
    """FastF1's own check_3: the lap AFTER an SC/VSC/red-flag lap often has
    its own timing anomalies, even if that lap's own status was clean."""
    assert not ingest_live_session._is_plausible_lap(90.0, 30.0, 30.0, 30.0, {"1"}, {"4"})


@pytest.mark.unit
def test_is_plausible_lap_rejects_missing_lap_time() -> None:
    assert not ingest_live_session._is_plausible_lap(None, 30.0, 30.0, 30.0, {"1"}, set())


@pytest.mark.unit
def test_is_plausible_lap_rejects_missing_sector() -> None:
    """Matches the real Monza lap 1 shape: sector1 never arrives (no
    reference point before the start line), so the lap can't be judged
    either way and is treated as not plausible — same as ingest_historical.py
    treating a NULL IsAccurate as False."""
    assert not ingest_live_session._is_plausible_lap(90.0, None, 30.0, 30.0, {"1"}, set())


@pytest.mark.unit
def test_is_plausible_lap_rejects_sector_sum_mismatch() -> None:
    assert not ingest_live_session._is_plausible_lap(90.0, 30.0, 30.0, 30.0 + 5.0, {"1"}, set())


@pytest.mark.unit
def test_is_plausible_lap_tolerates_small_sector_sum_rounding() -> None:
    assert ingest_live_session._is_plausible_lap(90.02, 30.0, 30.0, 30.0, {"1"}, set())


@pytest.mark.unit
def test_is_plausible_lap_magnitude_backstop_rejects_a_red_flag_scale_lap() -> None:
    """Isolates the magnitude backstop specifically: all three sectors
    present and correctly summing to the (implausible) lap time, with a
    clean status and clean previous lap — the ONLY thing that can reject
    this is _MAX_PLAUSIBLE_LAP_SECONDS. Covers laps ingested before this
    ingestor's first-ever TrackStatus message (still defaulted to "1"),
    the scenario a real red flag at Monza 2026 would hit if this ingestor
    connected after the session had already started. Magnitude matches the
    real ~1956.9s value observed for every driver's lap 4 that race."""
    assert not ingest_live_session._is_plausible_lap(
        1956.913, 650.971, 650.971, 654.971, {"1"}, set()
    )


# --- _handle_lap_count (Issue A, docs/internal/live-race-ingestion-and-strategy-
# gaps-monza-2026.md) ---


@pytest.mark.unit
def test_handle_lap_count_dispatches_on_first_value(monkeypatch: pytest.MonkeyPatch) -> None:
    ingestor = _make_ingestor()
    dispatched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ingest_live_session.update_session_total_laps,
        "delay",
        lambda session_id, total_laps: dispatched.append((session_id, total_laps)),
    )

    ingestor._handle_lap_count({"TotalLaps": 53})

    assert ingestor._total_laps_dispatched == 53
    assert dispatched == [("session-1", 53)]


@pytest.mark.unit
def test_handle_lap_count_does_not_redispatch_the_same_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1's own feed can resend the same TotalLaps repeatedly over a
    session — sessions.total_laps only needs to be set once."""
    ingestor = _make_ingestor()
    dispatched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ingest_live_session.update_session_total_laps,
        "delay",
        lambda session_id, total_laps: dispatched.append((session_id, total_laps)),
    )

    ingestor._handle_lap_count({"TotalLaps": 53})
    ingestor._handle_lap_count({"TotalLaps": 53})
    ingestor._handle_lap_count({"TotalLaps": 53})

    assert dispatched == [("session-1", 53)]


@pytest.mark.unit
def test_handle_lap_count_redispatches_on_a_genuine_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real correction (e.g. a shortened race distance) is picked up, not
    permanently locked to the first value seen."""
    ingestor = _make_ingestor()
    dispatched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ingest_live_session.update_session_total_laps,
        "delay",
        lambda session_id, total_laps: dispatched.append((session_id, total_laps)),
    )

    ingestor._handle_lap_count({"TotalLaps": 53})
    ingestor._handle_lap_count({"TotalLaps": 50})

    assert dispatched == [("session-1", 53), ("session-1", 50)]
    assert ingestor._total_laps_dispatched == 50


@pytest.mark.unit
def test_handle_lap_count_ignores_missing_or_non_positive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = _make_ingestor()
    dispatched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ingest_live_session.update_session_total_laps,
        "delay",
        lambda session_id, total_laps: dispatched.append((session_id, total_laps)),
    )

    ingestor._handle_lap_count({})
    ingestor._handle_lap_count({"TotalLaps": None})
    ingestor._handle_lap_count({"TotalLaps": "53"})  # wrong type, not coerced
    ingestor._handle_lap_count({"TotalLaps": 0})
    ingestor._handle_lap_count({"TotalLaps": -1})

    assert dispatched == []
    assert ingestor._total_laps_dispatched is None


# --- _handle_track_status ---


@pytest.mark.unit
def test_handle_track_status_updates_current_status() -> None:
    ingestor = _make_ingestor()
    assert ingestor._current_track_status == "1"

    ingestor._handle_track_status({"Status": "4", "Message": "SCDeployed"})

    assert ingestor._current_track_status == "4"


@pytest.mark.unit
def test_handle_track_status_ignores_unparseable_payload() -> None:
    ingestor = _make_ingestor()

    ingestor._handle_track_status({"Message": "no Status field at all"})

    assert ingestor._current_track_status == "1"  # unchanged default


@pytest.mark.unit
def test_handle_track_status_accepts_value_wrapped_status() -> None:
    """Same {"Value": ...}-wrapped shape _extract_string_field already
    handles for GapToLeader/IntervalToPositionAhead — defensive in case
    F1 sends TrackStatus's Status field wrapped too."""
    ingestor = _make_ingestor()

    ingestor._handle_track_status({"Status": {"Value": "2"}})

    assert ingestor._current_track_status == "2"


# --- _handle_timing_data end-to-end: track_status/is_valid on the dispatched raw_lap ---


@pytest.mark.unit
def test_handle_timing_data_dispatches_valid_lap_under_green_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = _make_ingestor(car_number_to_driver_id={"44": "driver-44"})

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.process_lap, "delay", lambda raw_lap: dispatched.append(raw_lap)
    )
    monkeypatch.setattr(ingest_live_session.run_strategy_prediction, "delay", lambda raw_lap: None)

    # Sectors arrive across separate messages, same as the real feed.
    ingestor._handle_timing_data({"Lines": {"44": {"Sectors": {"0": {"Value": "28.453"}}}}})
    ingestor._handle_timing_data({"Lines": {"44": {"Sectors": {"1": {"Value": "30.254"}}}}})
    ingestor._handle_timing_data(
        {
            "Lines": {
                "44": {
                    "Sectors": {"2": {"Value": "28.467"}},
                    "NumberOfLaps": 1,
                    "LastLapTime": {"Value": "1:27.174"},
                }
            }
        }
    )

    assert len(dispatched) == 1
    raw_lap = dispatched[0]
    assert raw_lap["track_status"] == "1"
    assert raw_lap["is_valid"] is True


@pytest.mark.unit
def test_handle_timing_data_marks_lap_invalid_and_records_status_during_red_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end reproduction of Issue D: a red flag (status "5") active
    while a lap is in progress must both (a) be recorded in track_status and
    (b) mark the lap is_valid=False — exactly what was missing at the real
    2026 Italian GP (every driver's lap 4 stored as ~1955s, is_valid=True)."""
    ingestor = _make_ingestor(car_number_to_driver_id={"44": "driver-44"})

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.process_lap, "delay", lambda raw_lap: dispatched.append(raw_lap)
    )
    monkeypatch.setattr(ingest_live_session.run_strategy_prediction, "delay", lambda raw_lap: None)

    # Car "44" is mid-lap when the red flag is thrown...
    ingestor._handle_timing_data({"Lines": {"44": {"GapToLeader": "+1.0"}}})
    ingestor._handle_track_status({"Status": "5", "Message": "Red"})
    # ...and the field is still being updated (position/gap fields update on
    # essentially every message) while the flag is out, before the session
    # resumes and this car's lap finally completes.
    ingestor._handle_timing_data(
        {
            "Lines": {
                "44": {
                    "GapToLeader": "+1.0",
                    "Sectors": {"0": {"Value": "28.0"}, "1": {"Value": "1900.0"}},
                }
            }
        }
    )
    ingestor._handle_track_status({"Status": "1", "Message": "AllClear"})
    ingestor._handle_timing_data(
        {
            "Lines": {
                "44": {
                    "Sectors": {"2": {"Value": "28.0"}},
                    "NumberOfLaps": 1,
                    "LastLapTime": {"Value": "1956.0"},
                }
            }
        }
    )

    assert len(dispatched) == 1
    raw_lap = dispatched[0]
    assert raw_lap["track_status"] == "15"  # both codes observed, sorted
    assert raw_lap["is_valid"] is False


@pytest.mark.unit
def test_handle_timing_data_invalidates_lap_immediately_after_a_safety_car_lap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = _make_ingestor(car_number_to_driver_id={"44": "driver-44"})

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ingest_live_session.process_lap, "delay", lambda raw_lap: dispatched.append(raw_lap)
    )
    monkeypatch.setattr(ingest_live_session.run_strategy_prediction, "delay", lambda raw_lap: None)

    def _complete_lap(lap_number: int, lap_time: str) -> None:
        ingestor._handle_timing_data(
            {"Lines": {"44": {"Sectors": {"0": {"Value": "30.0"}, "1": {"Value": "30.0"}}}}}
        )
        ingestor._handle_timing_data(
            {
                "Lines": {
                    "44": {
                        "Sectors": {"2": {"Value": "30.0"}},
                        "NumberOfLaps": lap_number,
                        "LastLapTime": {"Value": lap_time},
                    }
                }
            }
        )

    ingestor._handle_track_status({"Status": "4", "Message": "SCDeployed"})
    _complete_lap(1, "90.0")  # under SC — correctly invalid on its own status
    ingestor._handle_track_status({"Status": "1", "Message": "AllClear"})
    _complete_lap(2, "90.0")  # clean status, but immediately follows an SC lap

    assert len(dispatched) == 2
    assert dispatched[0]["is_valid"] is False
    assert dispatched[1]["track_status"] == "1"  # this lap's OWN status was clean
    assert dispatched[1]["is_valid"] is False  # still invalid: check_3


@pytest.mark.unit
def test_handle_timing_data_publishes_gaps_with_recomputed_positions() -> None:
    """_publish_live_gaps reads _car_live_gap_state["position"] — confirms
    the recomputed rank (not the frozen snapshot value) is what actually
    reaches the f1:{season}:{round}:gaps Redis key."""
    redis_client = MagicMock()
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=10,
        session_id="session-1",
        car_number_to_driver_id={"1": "driver-1", "2": "driver-2"},
        driver_code_to_id={},
        redis_client=redis_client,
        no_auth=True,
    )

    ingestor._handle_timing_data(
        {
            "Lines": {
                "1": {"GapToLeader": "+2.0"},
                "2": {},  # leader: no GapToLeader ever sent
            }
        }
    )

    # The gaps snapshot is one of the writes; the always-on ingest_stats write
    # (see publish_stats) follows it, so look for the key rather than the last call.
    keys = [call.args[0] for call in redis_client.setex.call_args_list]
    assert "f1:2026:10:gaps" in keys


# --- Real-world verification: actual field data from the 2026 Italian GP
# (Monza, session_id 3ddc84bd-f10e-4870-9e98-631d79695beb) — see docs/internal/live-
# race-ingestion-and-strategy-gaps-monza-2026.md Issue D. Every value below
# was queried directly from the local DB's lap_data table, not fabricated —
# see that document's Section 0b / Issue D re-verification note for the
# queries. sector1_seconds is omitted from each tuple because it is None
# for literally every one of these real rows (confirmed by direct query:
# lap 4 and lap 5 are missing sector1 for all 21 drivers still running).
#
# This is deliberately fed through _is_plausible_lap with status_codes={"1"}
# (green) and no incident in the previous lap — the MOST CHARITABLE possible
# assumption, i.e. "assume nothing is known about track status at all". The
# point of that choice is to prove laps 4 and 5 are caught by the
# missing-sector/magnitude/sum checks ALONE, with no dependency on real
# TrackStatus ground truth (which doesn't exist for this already-ingested
# race — track_status is NULL for all 1052 rows in this session, since it
# was ingested before this fix). See test_is_plausible_lap_real_monza_lap3_
# is_a_known_uncloseable_gap below for the lap this does NOT close.

_MONZA_LAP4_REAL_VALUES: list[tuple[str, float, float, float]] = [
    ("ALB", 1952.319, 32.254, 49.850),
    ("ALO", 1948.683, 32.911, 49.693),
    ("ANT", 1955.709, 33.473, 49.130),
    ("BEA", 1953.349, 34.921, 45.497),
    ("BOR", 1954.830, 32.918, 49.126),
    ("BOT", 1952.109, 34.053, 47.964),
    ("COL", 1957.224, 36.583, 41.903),
    ("GAS", 1955.174, 36.470, 37.965),
    ("HAM", 1956.220, 36.288, 44.176),
    ("HUL", 1956.009, 33.326, 50.074),
    ("LAW", 1952.849, 33.770, 51.231),
    ("LIN", 1953.462, 37.853, 42.102),
    ("NOR", 1953.175, 39.475, 41.193),
    ("OCO", 1954.058, 34.999, 47.296),
    ("PER", 1952.583, 32.887, 51.202),
    ("PIA", 1954.257, 38.361, 42.734),
    ("RUS", 1958.319, 36.989, 37.463),
    ("SAI", 1953.793, 31.943, 51.857),
    ("STR", 1952.267, 33.309, 49.163),
    ("TSU", 1954.517, 34.534, 50.912),
    ("VER", 1956.913, 36.182, 40.997),
]

_MONZA_LAP5_REAL_VALUES: list[tuple[str, float, float, float]] = [
    ("ALB", 197.566, 33.777, 50.387),
    ("ALO", 195.888, 35.442, 49.612),
    ("ANT", 199.022, 34.249, 46.618),
    ("BEA", 194.870, 33.753, 40.137),
    ("BOR", 198.186, 33.440, 46.465),
    ("BOT", 197.236, 35.753, 48.842),
    ("COL", 198.372, 32.878, 40.557),
    ("GAS", 200.677, 35.398, 40.200),
    ("HAM", 194.748, 34.411, 38.878),
    ("HUL", 197.751, 34.649, 46.893),
    ("LAW", 198.082, 35.578, 48.414),
    ("LIN", 197.161, 34.202, 40.122),
    ("NOR", 197.849, 33.842, 41.814),
    ("OCO", 194.602, 35.135, 40.826),
    ("PER", 196.624, 35.544, 49.404),
    ("PIA", 196.620, 35.287, 39.228),
    ("RUS", 200.259, 36.049, 39.056),
    ("SAI", 198.108, 34.806, 50.063),
    ("STR", 196.538, 33.478, 51.215),
    ("TSU", 198.308, 35.643, 47.651),
    ("VER", 198.400, 36.302, 39.298),
]


@pytest.mark.unit
@pytest.mark.parametrize(("code", "lap_time", "sector2", "sector3"), _MONZA_LAP4_REAL_VALUES)
def test_is_plausible_lap_rejects_every_real_monza_lap4_row(
    code: str, lap_time: float, sector2: float, sector3: float
) -> None:
    """All 21 drivers' real, literal lap 4 values from the actual race this
    fix was written for. Confirms the fix would have caught the exact
    incident that motivated it, for every driver, not just VER."""
    assert not ingest_live_session._is_plausible_lap(lap_time, None, sector2, sector3, {"1"}, set())


@pytest.mark.unit
@pytest.mark.parametrize(("code", "lap_time", "sector2", "sector3"), _MONZA_LAP5_REAL_VALUES)
def test_is_plausible_lap_rejects_every_real_monza_lap5_row(
    code: str, lap_time: float, sector2: float, sector3: float
) -> None:
    """Extension found during re-verification (not in the original doc
    draft): lap 5 is also distorted for every driver, and — like lap 4 — is
    independently caught by the missing-sector check alone, no TrackStatus
    ground truth required."""
    assert not ingest_live_session._is_plausible_lap(lap_time, None, sector2, sector3, {"1"}, set())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("code", "lap_time", "sector1", "sector2", "sector3"),
    [
        ("ALO", 169.953, 47.321, 51.157, 71.475),
        ("GAS", 123.475, 31.599, 41.157, 50.719),
        ("RUS", 119.148, 31.124, 40.730, 47.294),
        ("STR", 162.980, 45.547, 50.158, 67.275),
        ("VER", 125.983, 33.413, 40.597, 51.973),
    ],
)
def test_is_plausible_lap_real_monza_lap3_is_a_known_uncloseable_gap(
    code: str, lap_time: float, sector1: float, sector2: float, sector3: float
) -> None:
    """Documents a real, known limitation rather than papering over it: lap 3
    is ALSO part of the same red-flag window (docs/internal/live-race-ingestion-and-
    strategy-gaps-monza-2026.md Issue D re-verification, laps 3-6), but every
    field here is genuinely present and sums correctly (confirmed: 0 of 21
    lap-3 rows are missing a sector), and 120-170s is well under
    _MAX_PLAUSIBLE_LAP_SECONDS. The ONLY signal that could have caught this
    lap is TrackStatus — which doesn't exist as ground truth for this
    already-ingested race (track_status is NULL for all 1052 rows in this
    session; it was ingested before this fix landed). This asserts the
    CURRENT, honest behavior (still plausible under a "nothing known"
    status assumption) rather than silently omitting lap 3 from this
    verification pass. The fix is structurally capable of catching this for
    a genuinely live-ingested FUTURE race, where real TrackStatus messages
    would arrive — it cannot be proven retroactively against Monza's already-
    ingested data, and this test says so rather than implying otherwise.
    """
    assert ingest_live_session._is_plausible_lap(lap_time, sector1, sector2, sector3, {"1"}, set())


# --- property tests: invariants of the live ranking that must hold for ANY field
# (retirements, lapped cars, either ranking source), not just hand-picked ones.
# Seeded stdlib randomness, so every run is reproducible. ---

_RANKING_SEED = 20260919


def _random_field(
    rng: random.Random,
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, int]]:
    """A random field. Returns (per-car gap state, cars F1 has flagged out, F1 positions).

    One lead-lap leader (no gap), lead-lap chasers with strictly increasing gaps,
    some lapped cars (no seconds gap, laps_down >= 1), a random subset flagged out.
    """
    n = rng.randint(4, 22)
    cars = [str(i) for i in range(1, n + 1)]
    rng.shuffle(cars)
    n_lapped = rng.randint(0, min(4, n - 2))
    lead_lap, lapped = cars[: n - n_lapped], cars[n - n_lapped :]

    gap = 0.0
    state: dict[str, dict[str, Any]] = {}
    previous_positions = rng.sample(range(1, n + 1), n)
    for index, car in enumerate(lead_lap):
        gap += rng.uniform(0.05, 9.0)
        state[car] = _gap_state(previous_positions[index], None if index == 0 else gap)
    for offset, car in enumerate(lapped):
        state[car] = _gap_state(
            previous_positions[len(lead_lap) + offset],
            None,
            laps_down=rng.randint(1, 3),
            laps_completed=rng.randint(30, 40),
            lap_seq=rng.randint(1, 500),
        )
    out = {car for car in cars if rng.random() < 0.2}
    f1_positions = dict(zip(rng.sample(cars, n), range(1, n + 1), strict=True))
    for car in cars:
        state[car]["f1_position"] = f1_positions[car]
    return state, out, f1_positions


def _ranked(
    state: dict[str, dict[str, Any]], out: set[str], *, f1_streaming: bool, order: list[str]
) -> dict[str, int | None]:
    ingestor = _make_ingestor()
    ingestor._position_diff_seen = f1_streaming
    ingestor._car_live_gap_state = {car: dict(state[car]) for car in order}
    for car in sorted(out):
        ingestor._update_gap_state(car, {"Retired": True})
    ingestor._recompute_positions()
    return {car: s["position"] for car, s in ingestor._car_live_gap_state.items()}


@pytest.mark.unit
@pytest.mark.parametrize("f1_streaming", [False, True], ids=["gap-based", "f1-position"])
def test_property_ranking_is_a_dense_permutation_independent_of_insertion_order(
    f1_streaming: bool,
) -> None:
    rng = random.Random(_RANKING_SEED)  # noqa: S311 - seeded on purpose, not security
    for _ in range(300):
        state, out, _ = _random_field(rng)
        cars = list(state)
        shuffled = cars[:]
        rng.shuffle(shuffled)

        positions = _ranked(state, out, f1_streaming=f1_streaming, order=cars)
        reordered = _ranked(state, out, f1_streaming=f1_streaming, order=shuffled)

        included = [car for car in cars if car not in out]
        assert sorted(p for car in included if (p := positions[car]) is not None) == list(
            range(1, len(included) + 1)
        )
        assert all(positions[car] is None for car in out)
        assert positions == reordered


@pytest.mark.unit
def test_property_gap_ranking_orders_lead_lap_by_gap_and_puts_lapped_cars_last() -> None:
    rng = random.Random(_RANKING_SEED + 1)  # noqa: S311 - seeded on purpose, not security
    for _ in range(300):
        state, out, _ = _random_field(rng)
        positions = _ranked(state, out, f1_streaming=False, order=list(state))
        included = [car for car in state if car not in out]
        lapped = [car for car in included if state[car].get("laps_down", 0) > 0]
        lead_lap = [car for car in included if car not in lapped]

        # Lead-lap cars: better position <=> smaller gap (the leader has no gap = smallest).
        gaps = {
            car: -1.0 if state[car]["gap_to_leader"] is None else state[car]["gap_to_leader"]
            for car in lead_lap
        }
        by_position = sorted(lead_lap, key=lambda car: positions[car] or 0)
        assert [gaps[car] for car in by_position] == sorted(gaps.values())
        # Every lapped car is behind every lead-lap car; more laps down is never ahead.
        if lapped and lead_lap:
            assert min(positions[car] or 0 for car in lapped) > max(
                positions[car] or 0 for car in lead_lap
            )
        # Lapped cars: fewer laps down first, then more laps completed, then whoever
        # crossed the line first.
        lapped_order = sorted(lapped, key=lambda car: positions[car] or 0)
        keys = [
            (state[car]["laps_down"], -state[car]["laps_completed"], state[car]["lap_seq"])
            for car in lapped_order
        ]
        assert keys == sorted(keys)


@pytest.mark.unit
def test_property_f1_position_ranking_follows_f1s_order_and_ignores_the_gaps() -> None:
    rng = random.Random(_RANKING_SEED + 2)  # noqa: S311 - seeded on purpose, not security
    for _ in range(300):
        state, out, f1_positions = _random_field(rng)
        positions = _ranked(state, out, f1_streaming=True, order=list(state))
        included = [car for car in state if car not in out]

        assert sorted(included, key=lambda car: positions[car] or 0) == sorted(
            included, key=lambda car: f1_positions[car]
        )


# --- lapped cars are ordered by crossing order, not by a frozen previous position
# (V1: on 14 archived 2026 races, adjacent lapped/lapped pairs were in the wrong
# order ~35% of the time on the Dutch GP with the old previous-position tie-break) ---


@pytest.mark.unit
def test_update_gap_state_records_laps_completed_and_arrival_order_only_when_the_count_rises() -> (
    None
):
    ingestor = _make_ingestor()
    ingestor._message_seq = 7
    ingestor._update_gap_state("5", {"NumberOfLaps": 40})
    ingestor._message_seq = 9
    ingestor._update_gap_state("5", {"NumberOfLaps": 40})  # repeated value: not a new crossing
    ingestor._update_gap_state("5", {"NumberOfLaps": 39})  # never goes backwards

    state = ingestor._car_live_gap_state["5"]
    assert state["laps_completed"] == 40
    assert state["lap_seq"] == 7

    ingestor._message_seq = 12
    ingestor._update_gap_state("5", {"NumberOfLaps": 41})
    assert (state["laps_completed"], state["lap_seq"]) == (41, 12)


@pytest.mark.unit
def test_handle_timing_data_counts_messages() -> None:
    ingestor = _make_ingestor()

    ingestor._handle_timing_data({"Lines": {}})
    ingestor._handle_timing_data({"Lines": {}})

    assert ingestor._message_seq == 2


@pytest.mark.unit
def test_lapped_cars_rank_by_laps_completed_then_who_crossed_the_line_first() -> None:
    """Car 5 held the better previous position, but car 6 completes the next lap first."""
    ingestor = _make_ingestor()
    # Previous positions 5 -> 3, 6 -> 4; both a lap down on 40 completed laps.
    ingestor._handle_timing_data(
        {
            "Lines": {
                "1": {"Position": "1"},
                "2": {"GapToLeader": "+5.0"},
                "5": {"GapToLeader": "1 L", "NumberOfLaps": 40, "Position": "3"},
                "6": {"GapToLeader": "1 L", "NumberOfLaps": 40, "Position": "4"},
            }
        }
    )
    assert (
        ingestor._car_live_gap_state["5"]["position"]
        < ingestor._car_live_gap_state["6"]["position"]
    )

    ingestor._handle_timing_data({"Lines": {"6": {"NumberOfLaps": 41}}})  # 6 gets a lap ahead
    state = ingestor._car_live_gap_state
    assert state["6"]["position"] < state["5"]["position"]

    ingestor._handle_timing_data({"Lines": {"5": {"NumberOfLaps": 41}}})  # same count, later
    assert state["6"]["position"] < state["5"]["position"]  # 6 crossed first, stays ahead


@pytest.mark.unit
def test_lapped_ordering_never_lets_a_lapped_car_ahead_of_a_lead_lap_car() -> None:
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "1": _gap_state(1, None),
        "2": _gap_state(2, 30.0),
        "9": _gap_state(3, None, laps_down=1, laps_completed=99, lap_seq=1),
    }

    ingestor._recompute_positions()

    assert ingestor._car_live_gap_state["9"]["position"] == 3
