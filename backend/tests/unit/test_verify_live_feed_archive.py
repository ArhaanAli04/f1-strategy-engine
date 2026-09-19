"""Unit tests for verify_live_feed_archive.py's own measurement logic, plus a
faithfulness check on the committed real-F1-message excerpt it replays.

Most tests pin that the measuring stick itself is correct. The last test
replays the real excerpt through the ingestor and pins the Issue C fix on real
F1 messages (no ghost cars, no stale lapped gaps) — the check the older
DB-synthesized harness could not make, since it invented the retirement
signal instead of using F1's.
"""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.scripts import verify_live_feed_archive as harness

EXCERPT_PATH = Path(__file__).parent / "fixtures" / "monza_2026_r13_timing_excerpt.json"


# --- timestamps + diff merging ---


@pytest.mark.unit
def test_ts_seconds_accepts_string_timedelta_and_number() -> None:
    assert harness._ts_seconds("1:02:03.500") == 3723.5
    assert harness._ts_seconds(timedelta(minutes=2, seconds=1)) == 121.0
    assert harness._ts_seconds(8.731) == 8.731


@pytest.mark.unit
def test_merge_diff_merges_nested_dicts_and_replaces_scalars() -> None:
    base: dict[str, Any] = {"Lines": {"16": {"Position": "3", "InPit": True}}}
    harness._merge_diff(base, {"Lines": {"16": {"Position": "4"}, "1": {"Position": "1"}}})

    assert base == {"Lines": {"16": {"Position": "4", "InPit": True}, "1": {"Position": "1"}}}


@pytest.mark.unit
def test_merge_diff_applies_digit_keyed_dict_onto_existing_list_by_index() -> None:
    """How TimingAppData Stints diffs arrive: {"1": {...}} updates list slot 1."""
    base: dict[str, Any] = {"Stints": [{"Compound": "SOFT"}]}
    harness._merge_diff(base, {"Stints": {"1": {"Compound": "HARD"}}})

    assert base == {"Stints": [{"Compound": "SOFT"}, {"Compound": "HARD"}]}


@pytest.mark.unit
def test_merge_diff_does_not_alias_the_update_payload() -> None:
    update: dict[str, Any] = {"Lines": {"16": {"Position": "3"}}}
    base: dict[str, Any] = {}
    harness._merge_diff(base, update)
    base["Lines"]["16"]["Position"] = "9"

    assert update["Lines"]["16"]["Position"] == "3"


# --- lapped gap strings (real formats F1 sends) ---


@pytest.mark.unit
@pytest.mark.parametrize("value", ["1 L", "1L", "52L", "+1 LAP", "2 LAPS", " 3 L "])
def test_is_lapped_gap_string_accepts_real_lapped_formats(value: str) -> None:
    assert harness.is_lapped_gap_string(value) is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["+1.759", "+1:18.234", "", None, "LAP 14", "RETIRED"])
def test_is_lapped_gap_string_rejects_everything_else(value: str | None) -> None:
    """ "LAP 14" is the LEADER's own lap counter, not a lapped car."""
    assert harness.is_lapped_gap_string(value) is False


# --- TruthTracker ---


@pytest.mark.unit
def test_truth_tracker_follows_retirement_signals() -> None:
    tracker = harness.TruthTracker()
    tracker.apply({"16": {"Position": "3", "ShowPosition": True, "Retired": False}}, t=10.0)
    assert tracker.cars["16"].active is True
    assert tracker.cars["16"].position == 3

    tracker.apply({"16": {"Stopped": True}}, t=20.0)
    assert tracker.cars["16"].stopped is True
    # Stopped alone is NOT out of the race — LEC stopped and recovered twice.
    assert tracker.cars["16"].active is True

    tracker.apply({"16": {"Retired": True, "Stopped": True}}, t=30.0)
    assert tracker.cars["16"].active is False
    assert tracker.cars["16"].first_out_flag_s == 30.0


@pytest.mark.unit
def test_truth_tracker_treats_show_position_false_alone_as_out() -> None:
    """STR never received Retired=true — only ShowPosition=false."""
    tracker = harness.TruthTracker()
    tracker.apply({"18": {"Position": "20", "ShowPosition": True}}, t=1.0)
    tracker.apply({"18": {"ShowPosition": False}}, t=2.0)

    assert tracker.cars["18"].retired is False
    assert tracker.cars["18"].active is False


@pytest.mark.unit
def test_truth_tracker_ignores_bool_sentinel_entries_and_counts_position_updates() -> None:
    tracker = harness.TruthTracker()
    tracker.apply({"16": True, "1": {"Position": "1"}}, t=1.0)
    tracker.apply({"1": {"Position": "2"}}, t=2.0)

    assert "16" not in tracker.cars
    assert tracker.cars["1"].position == 2
    assert tracker.cars["1"].position_updates == 2


@pytest.mark.unit
def test_truth_tracker_keeps_last_non_empty_gap_string() -> None:
    tracker = harness.TruthTracker()
    tracker.apply({"77": {"GapToLeader": "+82.238"}}, t=1.0)
    tracker.apply({"77": {"GapToLeader": ""}}, t=2.0)
    tracker.apply({"77": {"GapToLeader": {"Value": "1 L"}}}, t=3.0)

    assert tracker.cars["77"].last_gap_string == "1 L"


# --- audit_tower ---


def _entry(code: str, position: int) -> dict[str, Any]:
    return {"driver_id": code, "position": position}


@pytest.mark.unit
def test_audit_tower_flags_ghost_car_and_measures_the_shift_it_causes() -> None:
    """A retired car published at P2 pushes the truly-P2 car to published P3."""
    truth = {
        "1": harness.CarTruth(position=1, show_position=True),
        "16": harness.CarTruth(position=3, show_position=True, retired=True, first_out_flag_s=5.0),
        "63": harness.CarTruth(position=2, show_position=True),
    }
    report = harness.ReplayReport()

    harness.audit_tower(
        report,
        [_entry("VER", 1), _entry("LEC", 2), _entry("RUS", 3)],
        truth,
        {"VER": "1", "LEC": "16", "RUS": "63"},
        {},
        t=50.0,
    )

    assert report.tower_samples == 3
    assert report.active_samples == 2
    assert report.ghost_retired_samples == {"LEC": 1}
    assert report.ghost_last_seen_s == {"LEC": 50.0}
    assert report.ghost_first_out_flag_s == {"LEC": 5.0}
    assert report.rank_mismatch_samples == 1  # RUS published P3, F1 says P2
    assert report.rank_offset_hist == {0: 1, 1: 1}
    # With the ghost dropped, the remaining order is already correct.
    assert report.dense_rank_mismatch_samples == 0


@pytest.mark.unit
def test_audit_tower_flags_stale_numeric_gap_only_for_lapped_active_cars() -> None:
    truth = {
        "77": harness.CarTruth(position=5, show_position=True, last_gap_string="1 L"),
        "11": harness.CarTruth(position=6, show_position=True, last_gap_string="+9.1"),
    }
    report = harness.ReplayReport()

    harness.audit_tower(
        report,
        [_entry("BOT", 5), _entry("PER", 6)],
        truth,
        {"BOT": "77", "PER": "11"},
        {"77": {"gap_to_leader": 82.238}, "11": {"gap_to_leader": 9.1}},
        t=1.0,
    )

    assert report.stale_lapped_samples == {"BOT": 1}


@pytest.mark.unit
def test_audit_tower_skips_entries_with_no_truth() -> None:
    report = harness.ReplayReport()

    harness.audit_tower(report, [_entry("XXX", 1)], {}, {"XXX": "99"}, {}, t=1.0)

    assert report.tower_samples == 0


# --- audit_lap_completion ---


@pytest.mark.unit
def test_audit_lap_completion_scores_position_and_permutation_validity() -> None:
    truth = {
        "1": harness.CarTruth(position=1, show_position=True),
        "63": harness.CarTruth(position=2, show_position=True),
    }
    report = harness.ReplayReport()

    harness.audit_lap_completion(report, {"position": 1}, "1", truth, {"1": "VER"})
    harness.audit_lap_completion(report, {"position": 1}, "63", truth, {"63": "RUS"})

    assert report.laps_dispatched == 2
    assert report.position_checked == 2
    assert report.position_matched == 1
    assert report.position_mismatch_by_car == {"RUS": 1}
    assert report.f1_permutation_checked == 2
    assert report.f1_permutation_valid == 2


@pytest.mark.unit
def test_audit_lap_completion_flags_broken_f1_position_table() -> None:
    truth = {
        "1": harness.CarTruth(position=1, show_position=True),
        "63": harness.CarTruth(position=3, show_position=True),  # P2 missing
    }
    report = harness.ReplayReport()

    harness.audit_lap_completion(report, {"position": 1}, "1", truth, {"1": "VER"})

    assert report.f1_permutation_checked == 1
    assert report.f1_permutation_valid == 0


# --- RecordingRedis ---


@pytest.mark.unit
def test_recording_redis_records_gaps_and_ignores_other_keys_but_rejects_other_methods() -> None:
    redis_fake = harness.RecordingRedis()

    redis_fake.setex("f1:0:0:gaps", 30, '{"gaps": [{"driver_id": "VER"}]}')
    redis_fake.setex("f1:0:0:weather:latest", 60, "{}")

    assert redis_fake.gaps_entries == [{"driver_id": "VER"}]
    with pytest.raises(AttributeError):
        redis_fake.get("anything")  # type: ignore[attr-defined]


# --- build_excerpt ---


def _timing(ts: float, lines: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    return ts, {"Lines": lines}


@pytest.mark.unit
def test_build_excerpt_keeps_snapshot_and_windows_thins_gap_only_and_drops_sectors() -> None:
    gap_only = [_timing(100.0 + i, {"16": {"GapToLeader": f"+{i}.0"}}) for i in range(1, 9)]
    archive: harness.Archive = {
        "TimingData": [
            _timing(5.0, {"16": {"Position": "3", "Sectors": {"0": {}}}, "44": {"Position": "1"}}),
            *gap_only,
            _timing(500.0, {"16": {"Retired": True, "Speeds": {"FL": {}}}}),
            _timing(900.0, {"16": {"ShowPosition": False}}),
        ],
        "TimingAppData": [],
        "DriverList": [(5.0, {"16": {"Tla": "LEC", "Line": 3}, "44": {"Tla": "HAM"}})],
        "TrackStatus": [(1.0, {"Status": "1"})],
    }

    excerpt = harness.build_excerpt(
        archive, cars=["16"], windows=[(100.0, 110.0), (500.0, 501.0)], gap_stride=4, connect_at=9.0
    )
    timing = excerpt["topics"]["TimingData"]

    # Snapshot message kept, car 44 and Sectors dropped.
    assert timing[0] == [5.0, {"Lines": {"16": {"Position": "3"}}}]
    # 8 gap-only messages in the window, thinned to 1 in 4.
    assert [ts for ts, _ in timing if 100.0 <= ts <= 110.0] == [104.0, 108.0]
    # Event message inside a window survives with Speeds stripped; outside is gone.
    assert [500.0, {"Lines": {"16": {"Retired": True}}}] in timing
    assert not any(ts == 900.0 for ts, _ in timing)
    assert excerpt["topics"]["DriverList"] == [[5.0, {"16": {"Tla": "LEC"}}]]
    assert excerpt["topics"]["TrackStatus"] == [[1.0, {"Status": "1"}]]


# --- the committed real-F1 excerpt stays faithful ---


@pytest.mark.unit
def test_excerpt_carries_the_real_retirement_and_lapped_signals_f1_actually_sends() -> None:
    """The fixture exists so tests of the ingestor's fix run on real messages.
    If it is ever regenerated, it must still contain the moments that matter."""
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)
    lines = [entry for _, msg in archive["TimingData"] for entry in [msg["Lines"]]]

    def _values(car: str, key: str) -> list[Any]:
        return [line[car][key] for line in lines if car in line and key in line[car]]

    assert connect_at == 9.0
    assert True in _values("16", "Retired")  # LEC
    assert False in _values("16", "ShowPosition")
    stopped = _values("16", "Stopped")  # stopped, recovered, stopped, recovered, retired
    assert True in stopped
    assert False in stopped
    assert False in _values("18", "ShowPosition")  # STR: hidden without ever "Retired"
    assert True not in _values("18", "Retired")
    bot_gaps = [harness._string_field(v) for v in _values("77", "GapToLeader")]
    assert any(harness.is_lapped_gap_string(g) for g in bot_gaps)
    # The string the ingestor's eviction waits for never appears in F1's real feed.
    every_gap = [
        harness._string_field(v) for car in ("16", "18", "14") for v in _values(car, "GapToLeader")
    ]
    assert "RETIRED" not in every_gap
    assert {e["Tla"] for e in archive["DriverList"][0][1].values()} >= {"LEC", "STR", "ALO"}


@pytest.mark.unit
def test_replaying_the_excerpt_runs_clean_through_the_real_ingestor() -> None:
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)

    report = harness.replay(archive, connect_at)

    assert report.messages_replayed > 0
    assert report.laps_dispatched > 0
    assert report.handler_errors == 0
    assert report.position_updates["LEC"] > 0


@pytest.mark.unit
@pytest.mark.parametrize("strip_position_diffs", [False, True], ids=["f1-position", "gap-fallback"])
def test_real_monza_excerpt_leaves_no_ghost_cars_or_stale_lapped_gaps(
    strip_position_diffs: bool,
) -> None:
    """Issue C on real messages: LEC (Stopped -> Retired -> ShowPosition=false),
    STR (ShowPosition=false, never Retired) and lapped BOT/PER ("1 L", "52L")
    must not linger in the published standings — whether F1's Position field
    is streaming or only the gap-based ranking is available."""
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)

    report = harness.replay(archive, connect_at, strip_position_diffs=strip_position_diffs)

    assert report.handler_errors == 0
    assert report.ghost_retired_samples == {}
    assert report.ghost_hidden_samples == {}
    assert report.stale_lapped_samples == {}


@pytest.mark.unit
def test_replay_reports_each_published_standings_snapshot_to_on_publish() -> None:
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)
    snapshots: list[list[dict[str, Any]]] = []

    harness.replay(archive, connect_at, on_publish=snapshots.append)

    assert snapshots
    first = snapshots[0][0]
    assert {"driver_id", "lap_number", "position"} <= set(first)
    # Each call gets its own list, so a caller may keep a reference to it.
    assert len({id(s) for s in snapshots}) == len(snapshots)


# --- multi-race batch mode (V1) ---


@pytest.mark.unit
def test_detect_connect_at_is_the_first_message_by_which_the_field_has_appeared() -> None:
    archive: harness.Archive = {
        "DriverList": [(1.0, {str(n): {"Tla": f"D{n}"} for n in range(1, 11)})],
        "TimingData": [
            _timing(2.0, {"1": {}, "2": {}, "3": {}}),
            _timing(5.5, {str(n): {} for n in range(4, 10)}),  # 9 of 10 seen: >= 90%
            _timing(9.0, {"10": {}}),
        ],
    }

    assert harness.detect_connect_at(archive) == 5.5


@pytest.mark.unit
def test_detect_connect_at_falls_back_to_the_default_without_a_driver_list_or_full_field() -> None:
    assert harness.detect_connect_at({}) == harness._DEFAULT_CONNECT_AT_SECONDS
    partial: harness.Archive = {
        "DriverList": [(1.0, {str(n): {"Tla": "X"} for n in range(1, 11)})],
        "TimingData": [_timing(2.0, {"1": {}})],
    }
    assert harness.detect_connect_at(partial) == harness._DEFAULT_CONNECT_AT_SECONDS


@pytest.mark.unit
def test_detect_connect_at_matches_the_committed_real_excerpts_recorded_boundary() -> None:
    """On the real Monza messages the detection lands where the fixture was cut (8.731s
    for the full archive; the excerpt keeps only 8 cars, so its own coverage differs)."""
    archive, recorded = harness.load_excerpt(EXCERPT_PATH)

    assert recorded == 9.0
    assert harness.detect_connect_at(archive) <= recorded


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("5", [5]),
        ("1-4", [1, 2, 3, 4]),
        ("1-3,7,9-10", [1, 2, 3, 7, 9, 10]),
        (" 2 , 4-5 ", [2, 4, 5]),
    ],
)
def test_parse_rounds(spec: str, expected: list[int]) -> None:
    assert harness.parse_rounds(spec) == expected


@pytest.mark.unit
def test_retired_and_hidden_cars_separates_the_two_kinds_of_exit() -> None:
    archive: harness.Archive = {
        "TimingData": [
            _timing(1.0, {"16": {"Retired": False}, "18": {"ShowPosition": True}}),
            _timing(2.0, {"16": {"Retired": True}, "18": {"Stopped": True}}),
            _timing(3.0, {"16": {"ShowPosition": False}, "18": {"ShowPosition": False}}),
        ]
    }

    retired, hidden_only = harness.retired_and_hidden_cars(archive)

    assert retired == {"16"}
    assert hidden_only == {"18"}  # STR-style: hidden without ever being Retired


@pytest.mark.unit
def test_track_status_flags_names_safety_car_vsc_and_red_flag() -> None:
    def _status(*codes: str) -> harness.Archive:
        return {"TrackStatus": [(float(i), {"Status": c}) for i, c in enumerate(codes)]}

    assert harness.track_status_flags(_status("1", "2", "1")) == "-"
    assert harness.track_status_flags(_status("1", "4", "1", "5")) == "RED,SC"
    assert harness.track_status_flags(_status("6", "7")) == "VSC"
    assert harness.track_status_flags({}) == "-"


def _report(matched: int, checked: int, ghosts: int = 0) -> Any:
    report = harness.ReplayReport()
    report.laps_dispatched = checked
    report.position_matched, report.position_checked = matched, checked
    for i in range(ghosts):
        report.ghost_retired_samples[f"G{i}"] += 5
    return report


@pytest.mark.unit
def test_summarize_race_combines_the_two_replays_and_the_archives_own_facts() -> None:
    archive: harness.Archive = {
        "TimingData": [_timing(1.0, {"16": {"Retired": True}, "18": {"ShowPosition": False}})],
        "TrackStatus": [(1.0, {"Status": "5"})],
    }
    streaming = _report(100, 100)
    fallback = _report(94, 100, ghosts=1)

    summary = harness.summarize_race(13, "Italian Grand Prix", archive, streaming, fallback)

    assert (summary.retired, summary.hidden_only, summary.flags) == (1, 1, "RED")
    assert summary.match_streaming == (100, 100)
    assert summary.match_fallback == (94, 100)
    assert (summary.ghosts_streaming, summary.ghosts_fallback) == (0, 1)


@pytest.mark.unit
def test_format_batch_shows_skipped_races_and_totals_only_over_those_that_ran() -> None:
    ran = harness.RaceSummary(
        13,
        "Italian Grand Prix",
        laps=1052,
        retired=2,
        flags="RED",
        match_streaming=(1052, 1052),
        match_fallback=(990, 1052),
    )
    skipped = harness.RaceSummary(14, "Spanish Grand Prix", skipped="no archived feed")

    text = harness.format_batch([ran, skipped])

    assert "Italian Grand Prix" in text
    assert "skipped: no archived feed" in text
    assert "1 race(s), 1052 lap completions" in text
    assert "100.0% with F1 Position" in text
    assert "94.1% gap-only" in text


@pytest.mark.unit
def test_format_batch_with_nothing_run_has_no_totals_line() -> None:
    text = harness.format_batch([harness.RaceSummary(1, "?", skipped="ValueError: bad round")])

    assert "skipped: ValueError: bad round" in text
    assert "race(s)" not in text
