"""Raw live-feed recorder: what F1's socket actually delivered, kept for replay.

The live ingestor parses each message and throws it away, so after a race there
is no way to tell whether the socket sent a field (e.g. the race-order
`Position` inside TimingData), dropped messages, or delivered them late. The
verification done against F1's archived feed shows what F1 recorded server-side,
which is not necessarily what this connection received. With RECORD_RAW_FEED
on, this writes one gzip'd JSONL file per session that
`verify_live_feed_archive --recording` can replay and compare with the archive.
See docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md section 7c (V5).

One JSON object per line: {"t": <receive time, epoch seconds>, "topic": ...,
"data": ...}. Besides feed messages there are two extra topics:
- "Subscribe": the snapshot F1 returns as the RESULT of the Subscribe call (the
  only place the full initial state arrives), restricted to recorded topics;
- "_event": "opened" / "closed", so connection drops show up in the record.

Only the TimingData family is recorded. CarData.z / Position.z (telemetry
gauges, circuit-map dots) need an F1TV subscription and carry nothing in
no_auth mode, so they are excluded — they are large and unrelated to the
ranking, gap and alert logic this exists to verify.

Recording must never break ingestion: every write error is caught and logged,
and an OSError (disk full, permissions) stops recording for the rest of the
session instead of raising into the feed callback.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

logger = logging.getLogger(__name__)

RECORDED_TOPICS = frozenset(
    {
        "TimingData",
        "TimingAppData",
        "TrackStatus",
        "DriverList",
        "LapCount",
        "WeatherData",
        "SessionInfo",
    }
)
SNAPSHOT_TOPIC = "Subscribe"
EVENT_TOPIC = "_event"

# gzip buffers internally; flushing every so often bounds what a crash can lose
# without paying for a sync flush on every one of the ~300 messages a minute.
_FLUSH_EVERY_RECORDS = 100


class RawFeedRecorder:
    """Appends feed messages to one compressed JSONL file. Thread-safe."""

    def __init__(
        self,
        path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._file: IO[str] | None = gzip.open(path, "at", encoding="utf-8")
        self.records_written = 0

    @classmethod
    def try_create(
        cls,
        directory: Path,
        season: int,
        round_number: int,
        session_type: str,
    ) -> RawFeedRecorder | None:
        """Open a new recording file, or None (logged) if the directory is unusable."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{season}_R{round_number:02d}_{session_type}_{stamp}.jsonl.gz"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return cls(path)
        except OSError:
            logger.exception("Raw feed recording requested but %s is not writable", directory)
            return None

    def record(self, topic: str, data: Any) -> None:
        """Record one feed message; topics outside RECORDED_TOPICS are ignored."""
        if topic in RECORDED_TOPICS:
            self._write(topic, data)

    def record_snapshot(self, result: dict[str, Any]) -> None:
        """Record the Subscribe result, keeping only the recorded topics."""
        self._write(SNAPSHOT_TOPIC, {k: v for k, v in result.items() if k in RECORDED_TOPICS})

    def record_event(self, name: str) -> None:
        self._write(EVENT_TOPIC, name)

    def close(self) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.close()
            except OSError:
                logger.exception("Error closing raw feed recording %s", self.path)
            self._file = None

    def _write(self, topic: str, data: Any) -> None:
        try:
            line = json.dumps(
                {"t": round(self._clock(), 3), "topic": topic, "data": data},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            logger.warning("Skipping a %s message that is not JSON-serialisable", topic)
            return
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(line + "\n")
                self.records_written += 1
                if self.records_written % _FLUSH_EVERY_RECORDS == 0:
                    self._file.flush()
            except OSError:
                logger.exception(
                    "Raw feed recording to %s failed; recording stopped for this session",
                    self.path,
                )
                failed, self._file = self._file, None
                try:
                    failed.close()
                except OSError:
                    logger.debug("Could not close the failed recording %s", self.path)


def load_recording_counts(path: Path) -> dict[str, int]:
    """Records per topic in a recording file ("Subscribe" for snapshots; events excluded).

    A file cut off mid-write yields the counts of everything readable before the cut.
    """
    counts: dict[str, int] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    topic = json.loads(line)["topic"]
                except (json.JSONDecodeError, KeyError):
                    continue
                if topic != EVENT_TOPIC:
                    counts[topic] = counts.get(topic, 0) + 1
    except EOFError:
        logger.warning("Recording %s is truncated; counted what was readable", path)
    return counts
