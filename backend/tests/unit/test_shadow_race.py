"""Unit tests for the shadow-race tool's pure logic (V3): pacing, the cleanup safety
predicate, every check, and the manifest. The DB/Redis/Celery-driven parts are exercised
by running the tool against the real stack, not here."""

import gzip
import json
from pathlib import Path

import pytest

from backend.scripts import shadow_race as sr
from backend.scripts._raw_feed_recorder import load_recording_counts

# --- pacing ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("previous", "ts", "speed", "max_gap", "expected"),
    [
        (10.0, 10.4, 1.0, 5.0, 0.4),  # real-time gap honoured
        (10.0, 10.4, 2.0, 5.0, 0.2),  # twice as fast halves it
        (10.0, 1747.0, 1.0, 5.0, 5.0),  # a red-flag-sized gap is capped
        (10.0, 1747.0, 2.0, 5.0, 2.5),  # ...before the speed-up
        (10.0, 9.0, 1.0, 5.0, 0.0),  # out-of-order timestamps never wait negative time
    ],
)
def test_pace_delay(
    previous: float, ts: float, speed: float, max_gap: float, expected: float
) -> None:
    assert sr.pace_delay(previous, ts, speed, max_gap) == pytest.approx(expected)


@pytest.mark.unit
def test_max_completed_lap_takes_the_highest_count_in_a_message() -> None:
    content = {"Lines": {"1": {"NumberOfLaps": 11}, "2": {"NumberOfLaps": 12}, "3": True, "4": {}}}

    assert sr.max_completed_lap(content) == 12
    assert sr.max_completed_lap({"Lines": {}}) == 0
    assert sr.max_completed_lap({}) == 0


# --- parsing and small helpers ---


@pytest.mark.unit
def test_parse_alert_message() -> None:
    assert sr.parse_alert_message("Undercut threat: VER on RUS (55%)") == ("VER", "RUS", 55)
    assert sr.parse_alert_message("Something else entirely") is None


@pytest.mark.unit
def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]

    assert sr.percentile(values, 0.95) == 95.0
    assert sr.percentile([7.0], 0.95) == 7.0
    assert sr.percentile([3.0, 1.0, 2.0], 0.5) == 2.0


# --- the cleanup safety predicate: the ONLY thing standing between cleanup and real data ---


@pytest.mark.unit
@pytest.mark.parametrize(
    ("season", "event_name", "expected"),
    [
        (2098, "SHADOW RACE 1 (V3 replay of the Monza feed)", True),
        (2098, "SHADOW RACE", True),
        (2026, "SHADOW RACE 1 (V3 replay of the Monza feed)", False),  # a real season
        (2098, "Italian Grand Prix", False),  # right season, wrong name
        (2098, None, False),
        (2026, "Italian Grand Prix", False),
        (2025, None, False),
    ],
)
def test_is_shadow_race_only_matches_season_2098_shadow_races(
    season: int, event_name: str | None, expected: bool
) -> None:
    assert sr.is_shadow_race(season, event_name) is expected


# --- checks ---


@pytest.mark.unit
def test_check_total_laps() -> None:
    assert sr.check_total_laps(53, 53).status == "PASS"
    assert sr.check_total_laps(None, 53).status == "FAIL"
    assert sr.check_total_laps(40, 53).status == "FAIL"


@pytest.mark.unit
def test_check_no_ghosts_fails_on_any_ghost_stale_gap_or_swallowed_error() -> None:
    assert sr.check_no_ghosts([], [], 0).status == "PASS"
    assert sr.check_no_ghosts(["LEC"], [], 0).status == "FAIL"
    assert sr.check_no_ghosts([], ["BOT"], 0).status == "FAIL"
    assert sr.check_no_ghosts([], [], 2).status == "FAIL"


@pytest.mark.unit
def test_check_laps_persisted_passes_when_every_lap_arrived_with_f1s_position() -> None:
    expected: dict[str, int | None] = {"VER:1": 2, "VER:2": 2, "NOR:1": 1}

    persisted, position = sr.check_laps_persisted(expected, dict(expected))

    assert persisted.status == "PASS"
    assert position.status == "PASS"


@pytest.mark.unit
def test_check_laps_persisted_fails_when_a_lap_never_reached_the_database() -> None:
    expected: dict[str, int | None] = {"VER:1": 2, "VER:2": 2}

    persisted, _ = sr.check_laps_persisted(expected, {"VER:1": 2})

    assert persisted.status == "FAIL"
    assert "VER:2" in persisted.detail


@pytest.mark.unit
def test_check_laps_persisted_fails_below_the_position_match_threshold() -> None:
    expected: dict[str, int | None] = {f"D{i}:1": i for i in range(1, 101)}
    actual = dict(expected)
    for i in range(1, 4):  # 3 of 100 wrong: 97% < 99%
        actual[f"D{i}:1"] = 99

    _, position = sr.check_laps_persisted(expected, actual)

    assert position.status == "FAIL"
    assert "97.0%" in position.detail


@pytest.mark.unit
def test_check_laps_persisted_skips_laps_with_no_expected_position_and_fails_on_no_data() -> None:
    _, position = sr.check_laps_persisted({"VER:1": None}, {"VER:1": 5})
    assert position.status == "FAIL"  # nothing comparable is not a pass

    persisted, _ = sr.check_laps_persisted({}, {})
    assert persisted.status == "FAIL"


@pytest.mark.unit
def test_check_predictions_reports_coverage_and_lag() -> None:
    dispatched = {"VER:1": 100.0, "VER:2": 200.0, "NOR:1": 100.0}
    predicted = {"VER:1": 102.0, "VER:2": 204.0, "NOR:1": 110.0}

    coverage, lag = sr.check_predictions(dispatched, predicted)

    assert coverage.status == "PASS"
    assert lag.status == "INFO"
    assert "median 4.0" in lag.detail
    assert "max 10.0" in lag.detail


@pytest.mark.unit
def test_check_predictions_fails_when_laps_have_no_prediction() -> None:
    dispatched = {f"D:{i}": 1.0 for i in range(10)}

    coverage = sr.check_predictions(dispatched, {"D:0": 2.0})[0]

    assert coverage.status == "FAIL"


@pytest.mark.unit
def test_check_pipeline_stats_needs_live_gaps_neighbours_and_mostly_live_alert_pairing() -> None:
    good = {
        "gap_source_live": 900,
        "gap_source_summed": 60,
        "neighbors_source_live": 1000,
        "neighbors_source_db": 5,
        "alert_order_source_live": 950,
        "alert_order_source_db": 20,
    }
    assert sr.check_pipeline_stats(good).status == "PASS"

    assert sr.check_pipeline_stats({**good, "gap_source_live": 0}).status == "FAIL"
    assert sr.check_pipeline_stats({**good, "neighbors_source_live": 0}).status == "FAIL"
    mostly_db = {**good, "alert_order_source_live": 100, "alert_order_source_db": 900}
    assert sr.check_pipeline_stats(mostly_db).status == "FAIL"
    assert sr.check_pipeline_stats({}).status == "FAIL"


def _alert(t: float = 1000.0, lap: int | None = 20, age: int | None = 10) -> sr.AlertRecord:
    return sr.AlertRecord(t, "VER", "RUS", lap, age)


@pytest.mark.unit
def test_check_alerts_passes_for_a_normal_alert() -> None:
    (check,) = sr.check_alerts([_alert()], {}, 53)

    assert check.status == "PASS"


@pytest.mark.unit
def test_check_alerts_flags_an_alert_for_a_car_already_flagged_out() -> None:
    assert sr.check_alerts([_alert(t=1010.0)], {"RUS": 1000.0}, 53)[0].status == "FAIL"
    assert sr.check_alerts([_alert(t=1010.0)], {"VER": 1000.0}, 53)[0].status == "FAIL"


@pytest.mark.unit
def test_check_alerts_allows_the_worker_lag_grace_around_a_flag() -> None:
    """An alert committed within the grace period after the flag is not a violation."""
    assert sr.check_alerts([_alert(t=1003.0)], {"RUS": 1000.0}, 53)[0].status == "PASS"
    assert sr.check_alerts([_alert(t=990.0)], {"RUS": 1000.0}, 53)[0].status == "PASS"


@pytest.mark.unit
def test_check_alerts_flags_fresh_tyres_and_the_final_laps() -> None:
    assert sr.check_alerts([_alert(age=3)], {}, 53)[0].status == "FAIL"
    assert sr.check_alerts([_alert(age=4)], {}, 53)[0].status == "PASS"
    assert sr.check_alerts([_alert(lap=39)], {}, 53)[0].status == "FAIL"  # 14 laps left
    assert sr.check_alerts([_alert(lap=38)], {}, 53)[0].status == "PASS"  # 15 left
    assert sr.check_alerts([_alert(lap=52)], {}, None)[0].status == "PASS"  # distance unknown


@pytest.mark.unit
def test_check_ingest_stats() -> None:
    good = {"position_first_message_seq": 12, "rankings_by_f1_position": 900, "rankings_by_gaps": 3}
    assert sr.check_ingest_stats(good).status == "PASS"
    assert sr.check_ingest_stats({**good, "position_first_message_seq": None}).status == "FAIL"
    assert sr.check_ingest_stats({**good, "rankings_by_f1_position": 0}).status == "FAIL"
    assert sr.check_ingest_stats(None).status == "FAIL"


@pytest.mark.unit
def test_check_recording_compares_topic_counts() -> None:
    fed = {"TimingData": 100, "LapCount": 5, "Subscribe": 1}

    assert sr.check_recording(fed, dict(fed)).status == "PASS"
    assert sr.check_recording(fed, {**fed, "TimingData": 99}).status == "FAIL"
    assert sr.check_recording(fed, None).status == "FAIL"


@pytest.mark.unit
def test_check_real_data_untouched_ignores_keys_that_merely_expired() -> None:
    before = {"lap_data": 1052, "predictions": 1052, "alerts": 33, "redis_keys": ["a", "b"]}

    assert sr.check_real_data_untouched(before, {**before, "redis_keys": ["a"]}).status == "PASS"
    assert sr.check_real_data_untouched(before, {**before, "lap_data": 1053}).status == "FAIL"
    assert sr.check_real_data_untouched(before, {**before, "alerts": 34}).status == "FAIL"
    assert (
        sr.check_real_data_untouched(before, {**before, "redis_keys": ["a", "b", "c"]}).status
        == "FAIL"
    )  # a NEW real key appeared


@pytest.mark.unit
def test_check_worker_logs() -> None:
    clean = "\n".join(
        ["[2026] INFO/MainProcess Task process_lap succeeded", "[2026] WARNING/MainProcess slow"]
    )
    assert sr.check_worker_logs(clean).status == "PASS"
    assert sr.check_worker_logs(None).status == "SKIP"
    broken = "\n".join(
        [clean, "[2026] ERROR/MainProcess Task failed", "[2026] CRITICAL/MainProcess boom"]
    )
    result = sr.check_worker_logs(broken)
    assert result.status == "FAIL"
    assert "2 line(s)" in result.detail


@pytest.mark.unit
def test_check_worker_logs_ignores_a_warning_level_traceback() -> None:
    """The periodic Ergast schedule check logs a failed request at WARNING with a traceback
    and falls back to its cache. That is background noise, not a pipeline failure — a real
    task failure is logged at ERROR before its traceback."""
    noise = "\n".join(
        [
            "[2026] WARNING/MainProcess Request for URL https://api.jolpi.ca/x failed; using cache",
            "Traceback (most recent call last):",
            "  File connectionpool.py, line 534, in _make_request",
            "ConnectionResetError: [Errno 104] Connection reset by peer",
        ]
    )

    assert sr.check_worker_logs(noise).status == "PASS"


@pytest.mark.unit
def test_format_checks_summarises_failures() -> None:
    text = sr.format_checks([sr.Check("a", "PASS", "ok"), sr.Check("b", "FAIL", "bad")])

    assert "PASS  a" in text
    assert "FAIL  b" in text
    assert "2 checks: 1 failed" in text
    assert "none failed" in sr.format_checks([sr.Check("a", "PASS", "ok")])


# --- manifest ---


@pytest.mark.unit
def test_manifest_round_trips_through_json(tmp_path: Path) -> None:
    manifest = sr.Manifest(
        run_id="20260919T000000Z",
        season=2098,
        round_number=3,
        race_id="r",
        session_id="s",
        source_season=2026,
        source_round=13,
        real_session_id="x",
        speed=2.0,
        max_gap=5.0,
        until_lap=12,
    )
    manifest.dispatched["VER:1"] = {"t": 5.0, "expected_position": 2}
    manifest.out_wall["LEC"] = 9.5
    path = tmp_path / "nested" / "m.json"

    manifest.save(path)

    assert sr.Manifest.load(path) == manifest


# --- recording counts (used by the recording check) ---


@pytest.mark.unit
def test_load_recording_counts_counts_topics_and_skips_connection_events(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl.gz"
    records = [
        {"t": 1.0, "topic": "_event", "data": "opened"},
        {"t": 1.1, "topic": "Subscribe", "data": {}},
        {"t": 2.0, "topic": "TimingData", "data": {}},
        {"t": 3.0, "topic": "TimingData", "data": {}},
        {"t": 4.0, "topic": "LapCount", "data": {}},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(r) for r in records) + "\n")

    assert load_recording_counts(path) == {"Subscribe": 1, "TimingData": 2, "LapCount": 1}
