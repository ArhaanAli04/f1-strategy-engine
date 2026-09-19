"""Unit tests for the raw live-feed recorder (V5) and the harness's --recording mode.

The recorder must (a) write exactly what the ingestor received, in a form the
harness can replay, (b) record only the TimingData family, and (c) never raise
into the feed callback. The round-trip test — ingestor -> recorder -> file ->
harness replay — is the one that proves the recording is actually usable.
"""

import gzip
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import redis

from backend.scripts import ingest_live_session
from backend.scripts import verify_live_feed_archive as harness
from backend.scripts._raw_feed_recorder import RECORDED_TOPICS, RawFeedRecorder

EXCERPT_PATH = Path(__file__).parent / "fixtures" / "monza_2026_r13_timing_excerpt.json"


def _read(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _recorder(tmp_path: Path, clock_values: list[float] | None = None) -> RawFeedRecorder:
    ticks = iter(clock_values or [float(i) for i in range(1, 1000)])
    return RawFeedRecorder(tmp_path / "rec.jsonl.gz", clock=lambda: next(ticks))


# --- the recorder itself ---


@pytest.mark.unit
def test_recorder_writes_one_json_line_per_message_with_time_topic_and_data(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path, [100.0, 100.5])

    recorder.record("TimingData", {"Lines": {"1": {"Position": "1"}}})
    recorder.record("TrackStatus", {"Status": "2"})
    recorder.close()

    assert _read(recorder.path) == [
        {"t": 100.0, "topic": "TimingData", "data": {"Lines": {"1": {"Position": "1"}}}},
        {"t": 100.5, "topic": "TrackStatus", "data": {"Status": "2"}},
    ]


@pytest.mark.unit
def test_recorder_ignores_the_f1tv_only_telemetry_topics_and_unknown_topics(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)

    recorder.record("CarData.z", "base64blob")
    recorder.record("Position.z", "base64blob")
    recorder.record("Heartbeat", {})
    recorder.record("TimingData", {"Lines": {}})
    recorder.close()

    assert [r["topic"] for r in _read(recorder.path)] == ["TimingData"]
    assert "CarData.z" not in RECORDED_TOPICS
    assert "Position.z" not in RECORDED_TOPICS


@pytest.mark.unit
def test_recorder_snapshot_keeps_only_recorded_topics(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)

    recorder.record_snapshot(
        {"TimingData": {"Lines": {}}, "CarData.z": "blob", "DriverList": {"1": {"Tla": "NOR"}}}
    )
    recorder.close()

    (record,) = _read(recorder.path)
    assert record["topic"] == "Subscribe"
    assert set(record["data"]) == {"TimingData", "DriverList"}


@pytest.mark.unit
def test_recorder_records_connection_events(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)

    recorder.record_event("opened")
    recorder.record_event("closed")
    recorder.close()

    assert [(r["topic"], r["data"]) for r in _read(recorder.path)] == [
        ("_event", "opened"),
        ("_event", "closed"),
    ]


@pytest.mark.unit
def test_recorder_skips_an_unserialisable_message_and_keeps_recording(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)

    recorder.record("TimingData", {"bad": object()})
    recorder.record("TimingData", {"ok": 1})
    recorder.close()

    assert [r["data"] for r in _read(recorder.path)] == [{"ok": 1}]


@pytest.mark.unit
def test_recorder_stops_quietly_after_a_disk_error_instead_of_raising(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    failing = MagicMock()
    failing.write.side_effect = OSError("disk full")
    recorder._file = failing

    recorder.record("TimingData", {"a": 1})  # must not raise
    recorder.record("TimingData", {"b": 2})  # and later writes are no-ops

    failing.write.assert_called_once()
    assert recorder._file is None


@pytest.mark.unit
def test_writes_after_close_are_ignored(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.close()

    recorder.record("TimingData", {"late": True})  # no error

    assert _read(recorder.path) == []


@pytest.mark.unit
def test_try_create_names_the_file_by_session_and_makes_the_directory(tmp_path: Path) -> None:
    recorder = RawFeedRecorder.try_create(tmp_path / "nested" / "recordings", 2026, 14, "R")

    assert recorder is not None
    assert recorder.path.parent == tmp_path / "nested" / "recordings"
    assert recorder.path.name.startswith("2026_R14_R_")
    assert recorder.path.name.endswith(".jsonl.gz")
    recorder.close()


@pytest.mark.unit
def test_try_create_returns_none_when_the_directory_cannot_be_created(tmp_path: Path) -> None:
    blocker = tmp_path / "a_file"
    blocker.write_text("not a directory")

    assert RawFeedRecorder.try_create(blocker / "recordings", 2026, 14, "R") is None


# --- the ingestor's recorder hooks ---


def _ingestor(recorder: RawFeedRecorder | None) -> ingest_live_session.F1SignalRIngestor:
    return ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=14,
        session_id="s",
        car_number_to_driver_id={},
        driver_code_to_id={},
        redis_client=MagicMock(),
        no_auth=True,
        recorder=recorder,
    )


@pytest.mark.unit
def test_ingestor_records_feed_messages_snapshots_and_connection_events(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    ingestor = _ingestor(recorder)

    ingestor._on_open()
    ingestor._on_subscribe_result(
        MagicMock(result={"TimingData": {"Lines": {}}, "CarData.z": "blob"})
    )
    ingestor._on_feed(["TimingData", {"Lines": {"1": {"GapToLeader": "+1.0"}}}])
    ingestor._on_feed(["CarData.z", "blob"])
    ingestor._on_close()
    recorder.close()

    assert [(r["topic"]) for r in _read(recorder.path)] == [
        "_event",
        "Subscribe",
        "TimingData",
        "_event",
    ]


@pytest.mark.unit
def test_ingestor_records_a_message_its_handler_then_fails_on(tmp_path: Path) -> None:
    """Recorded BEFORE handling, so a message the handler chokes on is still in the record."""
    recorder = _recorder(tmp_path)
    ingestor = _ingestor(recorder)

    ingestor._on_feed(["TimingData", "not a dict"])  # the handler raises internally; swallowed
    recorder.close()

    assert [r["data"] for r in _read(recorder.path)] == ["not a dict"]


@pytest.mark.unit
def test_ingestor_without_a_recorder_behaves_as_before() -> None:
    ingestor = _ingestor(None)

    ingestor._on_open()
    ingestor._on_feed(["TimingData", {"Lines": {}}])
    ingestor._on_close()

    assert ingestor.stats_snapshot()["recording"] is None


# --- always-on counters ---


@pytest.mark.unit
def test_stats_count_what_the_live_feed_did() -> None:
    ingestor = _ingestor(None)

    ingestor._on_open()
    ingestor._on_subscribe_result(MagicMock(result={"TimingData": {"Lines": {}}}))
    ingestor._handle_timing_data({"Lines": {"1": {"GapToLeader": "+1.0"}}})
    ingestor._handle_timing_data({"Lines": {"1": {"Retired": True}}})

    stats = ingestor.stats_snapshot()
    assert stats["timing_messages"] == 2
    assert stats["connections_opened"] == 1
    assert stats["subscribe_snapshots"] == 1
    assert stats["cars_flagged_out"] == 1
    assert stats["rankings_by_gaps"] == 2
    assert stats.get("rankings_by_f1_position", 0) == 0
    assert stats["position_first_message_seq"] is None


@pytest.mark.unit
def test_stats_record_when_position_first_streamed_and_switch_the_ranking_source() -> None:
    ingestor = _ingestor(None)

    ingestor._handle_timing_data({"Lines": {"1": {"GapToLeader": "+1.0"}}})
    ingestor._handle_timing_data({"Lines": {"1": {"Position": "1"}, "2": {"Position": "2"}}})
    ingestor._handle_timing_data({"Lines": {"1": {"Position": "1"}}})

    stats = ingestor.stats_snapshot()
    assert stats["position_first_message_seq"] == 2  # the second TimingData message
    assert stats["rankings_by_gaps"] == 1
    assert stats["rankings_by_f1_position"] == 2


@pytest.mark.unit
def test_position_first_streamed_is_logged_exactly_once(caplog: pytest.LogCaptureFixture) -> None:
    ingestor = _ingestor(None)

    with caplog.at_level("INFO", logger=ingest_live_session.__name__):
        ingestor._handle_timing_data({"Lines": {"1": {"Position": "1"}}})
        ingestor._handle_timing_data({"Lines": {"1": {"Position": "2"}}})

    streaming = [r for r in caplog.records if "Position field is streaming" in r.getMessage()]
    assert len(streaming) == 1


@pytest.mark.unit
def test_publish_stats_writes_json_to_redis_and_throttles() -> None:
    redis_client = MagicMock()
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=14,
        session_id="s",
        car_number_to_driver_id={},
        driver_code_to_id={},
        redis_client=redis_client,
        no_auth=True,
    )

    ingestor.publish_stats(force=True)
    ingestor.publish_stats()  # inside the throttle window: no second write
    stats_writes = [
        c for c in redis_client.setex.call_args_list if c.args[0].endswith(":ingest_stats")
    ]

    assert len(stats_writes) == 1
    key, ttl, payload = stats_writes[0].args
    assert key == "f1:2026:14:ingest_stats"
    assert ttl == ingest_live_session._STATS_KEY_TTL_SECONDS
    assert "timing_messages" in json.loads(payload) or json.loads(payload)["recording"] is None


@pytest.mark.unit
def test_publish_stats_survives_a_redis_error() -> None:
    redis_client = MagicMock()
    redis_client.setex.side_effect = redis.RedisError("down")
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=14,
        session_id="s",
        car_number_to_driver_id={},
        driver_code_to_id={},
        redis_client=redis_client,
        no_auth=True,
    )

    ingestor.publish_stats(force=True)  # must not raise


@pytest.mark.unit
def test_session_summary_warns_when_position_never_streamed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger=ingest_live_session.__name__):
        ingest_live_session._log_session_summary(
            {"timing_messages": 500, "position_first_message_seq": None}
        )
        ingest_live_session._log_session_summary(
            {"timing_messages": 500, "position_first_message_seq": 12}
        )

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "never streamed" in warnings[0].getMessage()


# --- the harness's --recording mode ---


def _record_excerpt_through_the_ingestor(tmp_path: Path) -> Path:
    """Feed the committed real-message excerpt through a recording ingestor, the way a
    live connection would: snapshot via Subscribe, everything after as feed diffs."""
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)
    recorder = RawFeedRecorder(tmp_path / "live.jsonl.gz")
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=2026,
        round_number=13,
        session_id="s",
        car_number_to_driver_id={},
        driver_code_to_id={},
        redis_client=MagicMock(),
        no_auth=True,
        recorder=recorder,
    )
    ingestor._on_open()
    ingestor._on_subscribe_result(
        MagicMock(
            result={
                topic: harness._snapshot(archive, topic, connect_at)
                for topic in ("DriverList", "TimingAppData", "TimingData")
            }
        )
    )
    messages = sorted(
        (
            (ts, topic, content)
            for topic, entries in archive.items()
            for ts, content in entries
            if ts > connect_at
        ),
        key=lambda message: message[0],
    )
    for _, topic, content in messages:
        ingestor._on_feed([topic, content])
    recorder.close()
    return recorder.path


@pytest.mark.unit
def test_recording_round_trip_summarises_and_replays_through_the_ingestor(tmp_path: Path) -> None:
    path = _record_excerpt_through_the_ingestor(tmp_path)

    records, truncated = harness.load_recording(path)
    summary = harness.summarize_recording(records, truncated)
    archive, connect_at = harness.recording_to_archive(records)
    report = harness.replay(archive, connect_at)

    assert not truncated
    assert summary.subscribe_snapshots == 1
    assert summary.messages["TimingData"] > 0
    assert summary.position_updates > 0  # the real excerpt carries Position updates
    assert summary.first_position_update_s is not None
    assert report.messages_replayed > 0
    assert report.laps_dispatched > 0
    assert report.handler_errors == 0


@pytest.mark.unit
def test_recording_compared_with_its_own_source_archive_matches_message_for_message(
    tmp_path: Path,
) -> None:
    path = _record_excerpt_through_the_ingestor(tmp_path)
    archive, connect_at = harness.load_excerpt(EXCERPT_PATH)
    summary = harness.summarize_recording(*harness.load_recording(path))

    counts, position_updates = harness.archive_feed_counts(archive, connect_at)

    assert summary.messages["TimingData"] == counts["TimingData"]
    assert summary.position_updates == position_updates


@pytest.mark.unit
def test_summary_says_no_when_no_position_value_ever_streamed() -> None:
    records = [
        {
            "t": 10.0,
            "topic": "Subscribe",
            "data": {"TimingData": {"Lines": {"1": {"Position": "1"}}}},
        },
        {"t": 11.0, "topic": "TimingData", "data": {"Lines": {"1": {"GapToLeader": "+1.0"}}}},
    ]

    summary = harness.summarize_recording(records)
    text = harness.format_recording_report(summary, None, 0.0)

    assert summary.position_updates == 0  # the snapshot's Position does not count
    assert "NO — no TimingData feed message carried a Position value" in text


@pytest.mark.unit
def test_report_compares_recording_with_archive_per_topic() -> None:
    records = [
        {"t": 0.0, "topic": "Subscribe", "data": {"TimingData": {"Lines": {}}}},
        {"t": 1.0, "topic": "TimingData", "data": {"Lines": {"1": {"Position": "1"}}}},
    ]
    archive: harness.Archive = {
        "TimingData": [
            (5.0, {"Lines": {"1": {"Position": "1"}}}),
            (6.0, {"Lines": {"1": {"Position": "2"}}}),
        ],
        "TimingAppData": [],
        "TrackStatus": [],
        "DriverList": [],
    }

    text = harness.format_recording_report(harness.summarize_recording(records), archive, 1.0)

    assert "YES — 1 Position update(s)" in text
    assert "TimingData" in text
    assert "50%" in text  # 1 recorded of 2 in the archive


@pytest.mark.unit
def test_load_recording_keeps_what_is_readable_from_a_truncated_file(tmp_path: Path) -> None:
    path = tmp_path / "cut.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for i in range(50):
            handle.write(json.dumps({"t": float(i), "topic": "TimingData", "data": {}}) + "\n")
    path.write_bytes(path.read_bytes()[:-20])  # chop the gzip trailer/tail

    records, truncated = harness.load_recording(path)

    assert truncated
    assert len(records) > 0


@pytest.mark.unit
def test_empty_recording_is_handled() -> None:
    summary = harness.summarize_recording([])
    archive, connect_at = harness.recording_to_archive([])

    assert summary.messages == {}
    assert all(not messages for messages in archive.values())
    assert connect_at == harness._RECORDING_CONNECT_AT_SECONDS
