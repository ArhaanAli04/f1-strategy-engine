"""Unit tests for ingest_live_session.py's Checkpoint 1 live-parity fixes
(see docs/core-feature-rebuild-strategy-recommendations.md and CLAUDE.md's
core-feature-rebuild session):

1. tyre_age_laps was previously hardcoded to 0 for every lap — now derived
   from the tracked stint start_lap (_current_tyre_age).
2. lap_data.position was never populated for a live session at all (F1 only
   ever sends the Position field once, in the Subscribe snapshot) — now
   re-derived continuously from the streaming GapToLeader field
   (_recompute_positions), and threaded into raw_lap.
3. Checkpoint 7 (verification): a retired car's GapToLeader freezing
   _car_live_gap_state at its last real value — discovered via the
   recorded-feed harness (verify_live_feed_parity.py) against a real
   session with 3 retirements, not a pre-existing test gap — is now fixed
   by evicting the car on an explicit "RETIRED" GapToLeader marker.

F1SignalRIngestor's __init__ has no network/DB side effects (those only
happen in start()/_build_connection()), so it's constructed directly here
with plain in-memory stand-ins — no real Redis, no real Celery broker.
"""

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


# --- _update_gap_state: retirement eviction (Checkpoint 7) ---


@pytest.mark.unit
def test_update_gap_state_evicts_car_on_retired_marker() -> None:
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state["44"] = {
        "position": 5,
        "gap_to_leader": 12.3,
        "gap_to_ahead": 1.1,
        "laps_behind": 0,
    }

    changed = ingestor._update_gap_state("44", {"GapToLeader": "RETIRED"})

    assert changed is True
    assert "44" not in ingestor._car_live_gap_state


@pytest.mark.unit
def test_update_gap_state_retired_marker_is_case_and_whitespace_insensitive() -> None:
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state["44"] = {
        "position": 5,
        "gap_to_leader": 12.3,
        "gap_to_ahead": None,
        "laps_behind": 0,
    }

    ingestor._update_gap_state("44", {"GapToLeader": "  retired  "})

    assert "44" not in ingestor._car_live_gap_state


@pytest.mark.unit
def test_update_gap_state_retired_marker_on_unseen_car_is_a_no_op() -> None:
    """A car retiring before it was ever tracked (no prior TimingData) must
    not crash and must not create a phantom entry."""
    ingestor = _make_ingestor()

    changed = ingestor._update_gap_state("99", {"GapToLeader": "RETIRED"})

    assert changed is False
    assert "99" not in ingestor._car_live_gap_state


@pytest.mark.unit
def test_recompute_positions_excludes_retired_car() -> None:
    """The actual bug this fixes: without eviction, a retired car's stale
    gap_to_leader would keep occupying a ranking slot forever, shifting
    every trailing driver's position by one — confirmed live via
    verify_live_feed_parity.py against a real session with 3 retirements."""
    ingestor = _make_ingestor()
    ingestor._car_live_gap_state = {
        "1": {"position": 1, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        "2": {"position": 2, "gap_to_leader": 5.0, "gap_to_ahead": None, "laps_behind": 0},
        "3": {"position": 3, "gap_to_leader": 10.0, "gap_to_ahead": None, "laps_behind": 0},
    }

    # Car "2" retires — its stale 5.0s gap must no longer occupy rank 2.
    ingestor._update_gap_state("2", {"GapToLeader": "RETIRED"})
    ingestor._recompute_positions()

    assert "2" not in ingestor._car_live_gap_state
    assert ingestor._car_live_gap_state["1"]["position"] == 1
    assert ingestor._car_live_gap_state["3"]["position"] == 2  # promoted, not stuck at 3


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

    assert redis_client.setex.called
    key = redis_client.setex.call_args.args[0]
    assert key == "f1:2026:10:gaps"
