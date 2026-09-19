"""Archive-driven replay harness for the live ingestion path.

verify_live_feed_parity.py replays a race from OUR OWN database, re-synthesizing
the F1 messages it thinks the live feed would have sent. That is exactly why it
missed docs/live-race-ingestion-and-strategy-gaps-monza-2026.md's Issue C: it
INVENTED a `GapToLeader: "RETIRED"` marker that F1's real feed never sends, so
the ingestor's retirement eviction passed a test that only ever exercised the
harness's own assumption.

This harness instead replays F1's REAL recorded messages — the per-session
`TimingData.jsonStream` / `TimingAppData.jsonStream` / `TrackStatus.jsonStream`
/ `DriverList.jsonStream` archives F1 publishes (the same files FastF1 reads) —
in timestamp order through the UNMODIFIED production F1SignalRIngestor, then
audits what the ingestor published against F1's own per-car state derived from
that same message stream. No database, Redis, or Celery broker is touched.

What it measures (see ReplayReport):
- lap-completion position accuracy: raw_lap["position"] vs F1's own Position
  field for that car at that moment;
- "ghost" cars: a car F1 has marked Retired / ShowPosition=false that the
  ingestor is still publishing into the live standings;
- stale lapped gaps: a car F1 reports as lapped ("1 L", "52L") whose last
  numeric gap the ingestor is still holding;
- whether F1's own Position field, on its own, forms a valid 1..N ranking at
  each lap completion (the evidence for whether it could replace the
  ingestor's gap-based re-ranking);
- handler errors swallowed by F1SignalRIngestor._on_feed.

Known limit: this replays F1's archived stream, which is what F1 recorded
server-side. It cannot prove the live SignalR socket delivered every one of
those messages (the Dutch GP notes in ingest_live_session.py record that
Position arrived only in the Subscribe snapshot live) — a genuine live race is
still the only full end-to-end check.

Run via:
    python -m backend.scripts.verify_live_feed_archive --season 2026 --round 13
    python -m backend.scripts.verify_live_feed_archive --excerpt PATH
    python -m backend.scripts.verify_live_feed_archive --season 2026 --round 13 \
        --write-excerpt backend/tests/unit/fixtures/monza_2026_r13_timing_excerpt.json
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import fastf1
from fastf1 import _api

from backend.core.config import get_ml_settings
from backend.scripts import ingest_live_session

logger = logging.getLogger(__name__)

# topic name as F1's feed (and ingest_live_session._on_feed) knows it -> the
# FastF1 archive page that holds its recorded stream.
_TOPIC_PAGES: dict[str, str] = {
    "TimingData": "timing_data",
    "TimingAppData": "timing_app_data",
    "TrackStatus": "track_status",
    "DriverList": "driver_list",
}

# Replay order for messages sharing one timestamp: session-wide state first,
# so a status change is already in force when the TimingData it accompanies runs.
_TOPIC_ORDER: dict[str, int] = {
    "DriverList": 0,
    "TrackStatus": 1,
    "TimingAppData": 2,
    "TimingData": 3,
}

# Stream time of the earliest full-state TimingData message in the Monza 2026 R
# archive (00:00:08.731). Everything at or before this is treated as the
# Subscribe snapshot F1 hands a client at connect time; everything after
# arrives as a "feed" diff, exactly like the production ingestor sees it.
_DEFAULT_CONNECT_AT_SECONDS = 9.0

# Broader than ingest_live_session._LAPS_BEHIND_PATTERN on purpose: that
# pattern needs the literal word LAP(S), but F1 actually sends "1 L", "1L",
# "52L". This is the harness's independent statement of what F1 sends, so it
# must not share the ingestor's (possibly wrong) assumption.
_LAPPED_GAP_PATTERN = re.compile(r"^\+?\s*\d+\s*L(?:AP)?S?$", re.IGNORECASE)

Archive = dict[str, list[tuple[float, dict[str, Any]]]]


# --- timestamps + diff merging ---


def _ts_seconds(value: Any) -> float:
    """Stream timestamp ("H:MM:SS.mmm" string, timedelta, or number) -> seconds."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, int | float):
        return float(value)
    hours, minutes, seconds = str(value).split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _fmt_ts(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def _merge_diff(base: dict[str, Any], update: dict[str, Any]) -> None:
    """Fold one F1 diff message into an accumulated state dict, in place.

    F1's stream is a diff protocol: nested dicts merge, and a dict keyed by
    digit strings applied onto an existing list updates that list by index
    (how TimingAppData's Stints diffs arrive). Anything else replaces.
    """
    for key, value in update.items():
        existing = base.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            _merge_diff(existing, value)
        elif (
            isinstance(value, dict)
            and isinstance(existing, list)
            and all(k.isdigit() for k in value)
        ):
            for index_str, item in value.items():
                index = int(index_str)
                while len(existing) <= index:
                    existing.append({})
                if isinstance(item, dict) and isinstance(existing[index], dict):
                    _merge_diff(existing[index], item)
                else:
                    existing[index] = copy.deepcopy(item)
        else:
            base[key] = copy.deepcopy(value)


def _string_field(raw: Any) -> str | None:
    """A GapToLeader/Interval value, whether F1 sent it bare or as {"Value": ...}."""
    if isinstance(raw, dict):
        raw = raw.get("Value")
    return raw if isinstance(raw, str) else None


def is_lapped_gap_string(value: str | None) -> bool:
    """Whether F1's own gap string says this car is a lap (or more) down."""
    if not value:
        return False
    return _LAPPED_GAP_PATTERN.match(value.strip()) is not None


# --- F1's own per-car state, derived from the same message stream ---


@dataclass
class CarTruth:
    """What F1's stream itself says about one car right now."""

    position: int | None = None
    show_position: bool | None = None
    retired: bool = False
    stopped: bool = False
    last_gap_string: str | None = None
    position_updates: int = 0
    first_out_flag_s: float | None = None

    @property
    def active(self) -> bool:
        """Still a competitor F1 is ranking: not retired, not hidden from the tower."""
        return not self.retired and self.show_position is not False


class TruthTracker:
    """Applies TimingData diffs to a per-car CarTruth table."""

    def __init__(self) -> None:
        self.cars: dict[str, CarTruth] = {}

    def apply(self, lines: dict[str, Any], t: float) -> None:
        for car_number, entry in lines.items():
            if not isinstance(entry, dict):
                continue
            car = self.cars.setdefault(car_number, CarTruth())
            position = entry.get("Position")
            if isinstance(position, str) and position.strip().isdigit():
                car.position = int(position.strip())
                car.position_updates += 1
            if isinstance(entry.get("ShowPosition"), bool):
                car.show_position = entry["ShowPosition"]
            if isinstance(entry.get("Retired"), bool):
                car.retired = entry["Retired"]
            if isinstance(entry.get("Stopped"), bool):
                car.stopped = entry["Stopped"]
            gap = _string_field(entry.get("GapToLeader"))
            if gap is not None and gap.strip():
                car.last_gap_string = gap.strip()
            if not car.active and car.first_out_flag_s is None:
                car.first_out_flag_s = t


# --- the audit ---


@dataclass
class ReplayReport:
    messages_replayed: int = 0
    handler_errors: int = 0
    laps_dispatched: int = 0
    position_checked: int = 0
    position_matched: int = 0
    position_mismatch_by_car: Counter[str] = field(default_factory=Counter)
    f1_permutation_checked: int = 0
    f1_permutation_valid: int = 0
    tower_samples: int = 0
    active_samples: int = 0
    rank_mismatch_samples: int = 0
    # Same check after dropping ghost cars from the published ranking and
    # re-ranking the rest densely — separates "a ghost shifted everyone" from
    # any other cause of a wrong order.
    dense_rank_mismatch_samples: int = 0
    # published position minus F1's Position, for active cars (0 = correct).
    rank_offset_hist: Counter[int] = field(default_factory=Counter)
    ghost_retired_samples: Counter[str] = field(default_factory=Counter)
    ghost_hidden_samples: Counter[str] = field(default_factory=Counter)
    ghost_first_out_flag_s: dict[str, float] = field(default_factory=dict)
    ghost_last_seen_s: dict[str, float] = field(default_factory=dict)
    stale_lapped_samples: Counter[str] = field(default_factory=Counter)
    position_updates: Counter[str] = field(default_factory=Counter)


def audit_tower(
    report: ReplayReport,
    entries: list[dict[str, Any]],
    truth: dict[str, CarTruth],
    car_by_code: dict[str, str],
    gap_state: dict[str, dict[str, Any]],
    t: float,
) -> None:
    """Score one published standings snapshot against F1's own per-car state.

    Args:
        report: Accumulator, mutated in place.
        entries: The `gaps` list the ingestor last published (SessionGapsResponse
            entries; each carries driver_id — the driver code in this harness —
            and the ingestor's derived position).
        truth: F1's own state per car number.
        car_by_code: driver code -> car number.
        gap_state: The ingestor's internal per-car gap state (car number -> dict).
        t: Stream time of the message just processed, in seconds.
    """
    active_ranked: list[tuple[int, CarTruth]] = []
    for entry in entries:
        code = str(entry["driver_id"])
        car_number = car_by_code.get(code)
        car = truth.get(car_number) if car_number is not None else None
        if car is None:
            continue
        report.tower_samples += 1

        if car.retired:
            report.ghost_retired_samples[code] += 1
        if car.show_position is False:
            report.ghost_hidden_samples[code] += 1
        if not car.active:
            report.ghost_last_seen_s[code] = t
            if car.first_out_flag_s is not None:
                report.ghost_first_out_flag_s.setdefault(code, car.first_out_flag_s)
            continue

        report.active_samples += 1
        active_ranked.append((entry["position"], car))
        if car.position is not None:
            report.rank_offset_hist[entry["position"] - car.position] += 1
            if entry["position"] != car.position:
                report.rank_mismatch_samples += 1
        state_gap = gap_state.get(car_number or "", {}).get("gap_to_leader")
        if is_lapped_gap_string(car.last_gap_string) and state_gap is not None:
            report.stale_lapped_samples[code] += 1

    active_ranked.sort(key=lambda pair: pair[0])
    for dense_rank, (_published, car) in enumerate(active_ranked, start=1):
        if car.position is not None and dense_rank != car.position:
            report.dense_rank_mismatch_samples += 1


def audit_lap_completion(
    report: ReplayReport,
    raw_lap: dict[str, Any],
    car_number: str | None,
    truth: dict[str, CarTruth],
    code_by_car: dict[str, str],
) -> None:
    """Score one dispatched raw_lap's position, and F1's own Position table validity."""
    report.laps_dispatched += 1
    car = truth.get(car_number) if car_number is not None else None
    if car is not None and car.active and car.position is not None:
        report.position_checked += 1
        if raw_lap["position"] == car.position:
            report.position_matched += 1
        elif car_number is not None:
            report.position_mismatch_by_car[code_by_car.get(car_number, car_number)] += 1

    ranked = sorted(c.position for c in truth.values() if c.active and c.position is not None)
    if ranked:
        report.f1_permutation_checked += 1
        if ranked == list(range(1, len(ranked) + 1)):
            report.f1_permutation_valid += 1


# --- fake Redis: records the standings the ingestor publishes ---


class RecordingRedis:
    """Only the one write the ingestor makes that this harness cares about.

    Any other Redis method raises AttributeError — if the ingestor grows a new
    Redis dependency this fails loudly instead of silently no-oping.
    """

    def __init__(self, on_publish: Callable[[list[dict[str, Any]]], None] | None = None) -> None:
        self.gaps_entries: list[dict[str, Any]] = []
        self._on_publish = on_publish

    def setex(self, key: str, ttl: int, value: str) -> None:
        if key.endswith(":gaps"):
            self.gaps_entries = json.loads(value)["gaps"]
            if self._on_publish is not None:
                self._on_publish(self.gaps_entries)


class _ErrorCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


# --- replay ---


def _snapshot(archive: Archive, topic: str, connect_at: float) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for ts, content in archive.get(topic, []):
        if ts <= connect_at:
            _merge_diff(state, content)
    return state


def _without_position_fields(content: dict[str, Any]) -> dict[str, Any]:
    """A TimingData diff with Position/Line removed from every car's entry."""
    lines = {
        car: (
            {k: v for k, v in entry.items() if k not in ("Position", "Line")}
            if isinstance(entry, dict)
            else entry
        )
        for car, entry in content.get("Lines", {}).items()
    }
    return {**content, "Lines": lines}


def replay(
    archive: Archive,
    connect_at: float = _DEFAULT_CONNECT_AT_SECONDS,
    strip_position_diffs: bool = False,
    on_publish: Callable[[list[dict[str, Any]]], None] | None = None,
) -> ReplayReport:
    """Drive the real F1SignalRIngestor with an archive's messages and audit it.

    Args:
        archive: topic -> [(stream_seconds, content), ...], each list time-sorted.
        connect_at: Messages at or before this stream time become the Subscribe
            snapshot (delivered through _on_subscribe_result, as in production);
            later ones arrive as "feed" diffs through _on_feed.
        strip_position_diffs: Remove Position/Line from every replayed diff
            (the snapshot keeps them) before the ingestor sees it, while the
            audit's own ground truth still uses the real values. Simulates a
            live connection that streams gaps but not Position — what the
            2026 Dutch GP run observed — to exercise the gap-based fallback.
        on_publish: Called with the `gaps` entries list each time the ingestor
            publishes standings (each call gets a fresh list, safe to keep).
    Returns:
        The audit report.
    """
    report = ReplayReport()
    driver_list = _snapshot(archive, "DriverList", connect_at)
    codes = {
        car: str(entry["Tla"])
        for car, entry in driver_list.items()
        if isinstance(entry, dict) and entry.get("Tla")
    }
    car_by_code = {code: car for car, code in codes.items()}
    redis_fake = RecordingRedis(on_publish)
    truth = TruthTracker()

    ingestor = ingest_live_session.F1SignalRIngestor(
        season=0,
        round_number=0,
        session_id="archive-replay",
        car_number_to_driver_id={},
        driver_code_to_id={code: code for code in codes.values()},
        redis_client=redis_fake,  # type: ignore[arg-type]
        no_auth=True,
    )

    def _capture_lap(raw_lap: dict[str, Any]) -> None:
        car_number = car_by_code.get(str(raw_lap["driver_id"]))
        audit_lap_completion(report, raw_lap, car_number, truth.cars, codes)

    def _ignore(*_args: Any, **_kwargs: Any) -> None:
        return None

    error_counter = _ErrorCounter()
    ingestor_logger = logging.getLogger(ingest_live_session.__name__)
    previous_level = ingestor_logger.level
    ingestor_logger.setLevel(logging.ERROR)
    ingestor_logger.addHandler(error_counter)
    try:
        with (
            patch.object(ingest_live_session.process_lap, "delay", _capture_lap),
            patch.object(ingest_live_session.run_strategy_prediction, "delay", _ignore),
            patch.object(ingest_live_session.record_tire_stint, "delay", _ignore),
            patch.object(ingest_live_session.update_session_total_laps, "delay", _ignore),
        ):
            timing_snapshot = _snapshot(archive, "TimingData", connect_at)
            truth.apply(timing_snapshot.get("Lines", {}), connect_at)
            ingestor._on_subscribe_result(
                SimpleNamespace(
                    result={
                        "DriverList": driver_list,
                        "TimingAppData": _snapshot(archive, "TimingAppData", connect_at),
                        "TimingData": timing_snapshot,
                    }
                )
            )

            messages = sorted(
                (
                    (ts, _TOPIC_ORDER[topic], topic, content)
                    for topic in _TOPIC_PAGES
                    for ts, content in archive.get(topic, [])
                    if ts > connect_at
                ),
                key=lambda m: (m[0], m[1]),
            )
            for ts, _order, topic, content in messages:
                if topic == "TimingData":
                    truth.apply(content.get("Lines", {}), ts)
                    if strip_position_diffs:
                        content = _without_position_fields(content)
                ingestor._on_feed([topic, content])
                report.messages_replayed += 1
                if topic == "TimingData":
                    audit_tower(
                        report,
                        redis_fake.gaps_entries,
                        truth.cars,
                        car_by_code,
                        ingestor._car_live_gap_state,
                        ts,
                    )
    finally:
        ingestor_logger.removeHandler(error_counter)
        ingestor_logger.setLevel(previous_level)

    report.handler_errors = error_counter.count
    for car_number, car in truth.cars.items():
        report.position_updates[codes.get(car_number, car_number)] = car.position_updates
    return report


# --- archive I/O ---


def fetch_archive(
    season: int,
    round_number: int,
    session_type: str,
    extra_topics: dict[str, str] | None = None,
) -> Archive:
    """Download the session's recorded live-timing streams (read-only, FastF1-cached).

    Uses fastf1._api.fetch_page — FastF1's public API exposes only parsed
    DataFrames, not the raw per-message stream this harness needs.

    Args:
        season, round_number, session_type: The session to fetch.
        extra_topics: Additional {feed topic name: FastF1 page name} streams to
            fetch beyond the four the replay uses (e.g. {"LapCount": "lap_count"}).
            The shadow-race tool feeds LapCount and WeatherData too.
    """
    cache_dir = get_ml_settings().fastf1_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)
    session = fastf1.get_session(season, round_number, session_type)
    archive: Archive = {}
    for topic, page in {**_TOPIC_PAGES, **(extra_topics or {})}.items():
        raw = _api.fetch_page(session.api_path, page)  # None when the request failed
        archive[topic] = [
            (_ts_seconds(ts), content) for ts, content in raw or [] if isinstance(content, dict)
        ]
        logger.warning("Fetched %s: %d message(s)", topic, len(archive[topic]))
    return archive


# --- several races at once ---

_TRACK_STATUS_NAMES = {"4": "SC", "5": "RED", "6": "VSC", "7": "VSC"}
_CONNECT_COVERAGE = 0.9


def detect_connect_at(archive: Archive) -> float:
    """Stream time of the first TimingData message by which (almost) every car has appeared.

    The Subscribe snapshot F1 hands a client at connect time is the full state;
    in the archive that is the first moment the accumulated messages mention
    the whole field. Monza 2026 R: 8.731s. Falls back to the default when the
    archive never covers the field.
    """
    expected: set[str] = set()
    for _, content in archive.get("DriverList", []):
        expected |= {car for car, entry in content.items() if isinstance(entry, dict)}
    if not expected:
        return _DEFAULT_CONNECT_AT_SECONDS
    seen: set[str] = set()
    for ts, content in archive.get("TimingData", []):
        seen |= {car for car, entry in content.get("Lines", {}).items() if isinstance(entry, dict)}
        if len(seen & expected) >= _CONNECT_COVERAGE * len(expected):
            return ts
    return _DEFAULT_CONNECT_AT_SECONDS


def parse_rounds(spec: str) -> list[int]:
    """'1-4,7,9-10' -> [1, 2, 3, 4, 7, 9, 10]."""
    rounds: list[int] = []
    for part in spec.split(","):
        low, _, high = part.strip().partition("-")
        rounds.extend(range(int(low), int(high or low) + 1))
    return rounds


def retired_and_hidden_cars(archive: Archive) -> tuple[set[str], set[str]]:
    """(cars flagged Retired at any point, cars hidden without ever being flagged Retired)."""
    retired: set[str] = set()
    hidden: set[str] = set()
    for _, content in archive.get("TimingData", []):
        for car, entry in content.get("Lines", {}).items():
            if not isinstance(entry, dict):
                continue
            if entry.get("Retired") is True:
                retired.add(car)
            if entry.get("ShowPosition") is False:
                hidden.add(car)
    return retired, hidden - retired


def track_status_flags(archive: Archive) -> str:
    """The safety-car / red-flag events a race had, e.g. 'SC,RED'; '-' if none."""
    codes = {
        str(content.get("Status"))
        for _, content in archive.get("TrackStatus", [])
        if isinstance(content, dict)
    }
    flags = sorted({_TRACK_STATUS_NAMES[c] for c in codes if c in _TRACK_STATUS_NAMES})
    return ",".join(flags) or "-"


@dataclass
class RaceSummary:
    round_number: int
    name: str
    skipped: str | None = None
    laps: int = 0
    retired: int = 0
    hidden_only: int = 0
    flags: str = "-"
    f1_permutation: tuple[int, int] = (0, 0)
    match_streaming: tuple[int, int] = (0, 0)
    match_fallback: tuple[int, int] = (0, 0)
    ghosts_streaming: int = 0
    ghosts_fallback: int = 0
    stale_streaming: int = 0
    stale_fallback: int = 0
    errors: int = 0


def _ghost_cars(report: ReplayReport) -> int:
    return len(set(report.ghost_retired_samples) | set(report.ghost_hidden_samples))


def summarize_race(
    round_number: int,
    name: str,
    archive: Archive,
    streaming: ReplayReport,
    fallback: ReplayReport,
) -> RaceSummary:
    retired, hidden_only = retired_and_hidden_cars(archive)
    return RaceSummary(
        round_number=round_number,
        name=name,
        laps=streaming.laps_dispatched,
        retired=len(retired),
        hidden_only=len(hidden_only),
        flags=track_status_flags(archive),
        f1_permutation=(streaming.f1_permutation_valid, streaming.f1_permutation_checked),
        match_streaming=(streaming.position_matched, streaming.position_checked),
        match_fallback=(fallback.position_matched, fallback.position_checked),
        ghosts_streaming=_ghost_cars(streaming),
        ghosts_fallback=_ghost_cars(fallback),
        stale_streaming=len(streaming.stale_lapped_samples),
        stale_fallback=len(fallback.stale_lapped_samples),
        errors=streaming.handler_errors + fallback.handler_errors,
    )


def run_batch(season: int, rounds: list[int], session_type: str) -> list[RaceSummary]:
    """Replay each round twice — F1 Position streaming, and gap-only fallback."""
    summaries: list[RaceSummary] = []
    for round_number in rounds:
        try:
            name = fastf1.get_session(season, round_number, session_type).event["EventName"]
            archive = fetch_archive(season, round_number, session_type)
        except (ValueError, OSError) as exc:  # bad round number, or the request failed
            summaries.append(RaceSummary(round_number, "?", skipped=f"{type(exc).__name__}: {exc}"))
            continue
        if not archive["TimingData"] or not archive["DriverList"]:
            summaries.append(RaceSummary(round_number, name, skipped="no archived feed"))
            continue
        connect_at = detect_connect_at(archive)
        logger.warning("Round %d %s: replaying (snapshot at %.3fs)", round_number, name, connect_at)
        streaming = replay(archive, connect_at)
        fallback = replay(archive, connect_at, strip_position_diffs=True)
        summaries.append(summarize_race(round_number, name, archive, streaming, fallback))
    return summaries


def _ratio(pair: tuple[int, int]) -> str:
    return f"{100 * pair[0] / pair[1]:5.1f}%" if pair[1] else "   n/a"


def format_batch(summaries: list[RaceSummary]) -> str:
    """One row per race, then totals over the races that ran."""
    header = (
        f"{'rd':>2} {'race':<22} {'laps':>5} {'ret':>3} {'hid':>3} {'flags':<8} "
        f"{'F1 pos valid':>12} {'match(pos)':>10} {'match(gap)':>10} "
        f"{'ghost p/g':>9} {'stale p/g':>9} {'err':>3}"
    )
    lines = [header, "-" * len(header)]
    done = [s for s in summaries if s.skipped is None]
    for s in summaries:
        if s.skipped is not None:
            lines.append(f"{s.round_number:>2} {s.name:<22} skipped: {s.skipped}")
            continue
        lines.append(
            f"{s.round_number:>2} {s.name[:22]:<22} {s.laps:>5} {s.retired:>3} {s.hidden_only:>3} "
            f"{s.flags:<8} {_ratio(s.f1_permutation):>12} {_ratio(s.match_streaming):>10} "
            f"{_ratio(s.match_fallback):>10} {s.ghosts_streaming:>4}/{s.ghosts_fallback:<4} "
            f"{s.stale_streaming:>4}/{s.stale_fallback:<4} {s.errors:>3}"
        )
    if done:
        stream = (sum(s.match_streaming[0] for s in done), sum(s.match_streaming[1] for s in done))
        gap = (sum(s.match_fallback[0] for s in done), sum(s.match_fallback[1] for s in done))
        lines += [
            "-" * len(header),
            f"{len(done)} race(s), {sum(s.laps for s in done)} lap completions; position match "
            f"{_ratio(stream).strip()} with F1 Position, {_ratio(gap).strip()} gap-only; "
            f"ghost cars {sum(s.ghosts_streaming + s.ghosts_fallback for s in done)}, "
            f"stale lapped {sum(s.stale_streaming + s.stale_fallback for s in done)}, "
            f"handler errors {sum(s.errors for s in done)}",
        ]
    return "\n".join(lines)


# --- a recording of what the live socket actually delivered (V5) ---

# Snapshot messages are placed at t=0; feed messages are shifted to start after
# this boundary, so the recording replays the way production ran: the Subscribe
# snapshot first (through _on_subscribe_result), everything later as a diff.
_RECORDING_CONNECT_AT_SECONDS = 0.5
_RECORDING_FEED_OFFSET_SECONDS = 1.0


def load_recording(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Read a RawFeedRecorder file. Returns (records, truncated).

    A recording cut off mid-write (a crash, or the ingestor killed) leaves a
    truncated gzip stream and possibly a half line; everything readable before
    that point is kept and `truncated` is True.
    """
    records: list[dict[str, Any]] = []
    truncated = False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    truncated = True
    except EOFError:
        truncated = True
    return records, truncated


def _entries_with_position(content: dict[str, Any]) -> int:
    """Car entries in one TimingData message that carry a race-order Position value."""
    count = 0
    for entry in content.get("Lines", {}).values():
        raw = entry.get("Position") if isinstance(entry, dict) else None
        if isinstance(raw, str) and raw.strip().isdigit():
            count += 1
    return count


@dataclass
class RecordingSummary:
    duration_s: float = 0.0
    messages: Counter[str] = field(default_factory=Counter)
    subscribe_snapshots: int = 0
    connections_opened: int = 0
    connections_closed: int = 0
    position_updates: int = 0
    first_position_update_s: float | None = None
    truncated: bool = False


def summarize_recording(records: list[dict[str, Any]], truncated: bool = False) -> RecordingSummary:
    summary = RecordingSummary(truncated=truncated)
    if not records:
        return summary
    start = records[0]["t"]
    summary.duration_s = records[-1]["t"] - start
    for record in records:
        topic, data = record["topic"], record["data"]
        if topic == "Subscribe":
            summary.subscribe_snapshots += 1
        elif topic == "_event":
            if data == "opened":
                summary.connections_opened += 1
            elif data == "closed":
                summary.connections_closed += 1
        else:
            summary.messages[topic] += 1
            if topic == "TimingData" and isinstance(data, dict):
                updates = _entries_with_position(data)
                if updates and summary.first_position_update_s is None:
                    summary.first_position_update_s = record["t"] - start
                summary.position_updates += updates
    return summary


def recording_to_archive(records: list[dict[str, Any]]) -> tuple[Archive, float]:
    """A recording as (Archive, connect_at) so replay() can drive the ingestor with it.

    The FIRST Subscribe snapshot becomes the connect-time state; later
    snapshots (reconnects) are not replayed as snapshots — replay() has no
    equivalent — and are counted in the summary instead.
    """
    archive: Archive = {topic: [] for topic in _TOPIC_PAGES}
    if not records:
        return archive, _RECORDING_CONNECT_AT_SECONDS
    start = records[0]["t"]
    seen_snapshot = False
    for record in records:
        topic, data = record["topic"], record["data"]
        if topic == "Subscribe":
            if seen_snapshot or not isinstance(data, dict):
                continue
            seen_snapshot = True
            for name, content in data.items():
                if name in archive and isinstance(content, dict):
                    archive[name].append((0.0, content))
        elif topic in archive and isinstance(data, dict):
            archive[topic].append((record["t"] - start + _RECORDING_FEED_OFFSET_SECONDS, data))
    return archive, _RECORDING_CONNECT_AT_SECONDS


def archive_feed_counts(archive: Archive, connect_at: float) -> tuple[Counter[str], int]:
    """(messages per topic after connect_at, Position-carrying car entries after connect_at)."""
    counts: Counter[str] = Counter()
    position_updates = 0
    for topic, messages in archive.items():
        for ts, content in messages:
            if ts > connect_at:
                counts[topic] += 1
                if topic == "TimingData":
                    position_updates += _entries_with_position(content)
    return counts, position_updates


def format_recording_report(
    summary: RecordingSummary, archive: Archive | None, archive_connect_at: float
) -> str:
    """Summary of the recording, and — with F1's archive of the same session — the comparison."""
    lines = [
        f"Recording: {summary.duration_s / 60:.1f} min, {sum(summary.messages.values())} feed "
        f"message(s), {summary.subscribe_snapshots} Subscribe snapshot(s), connections "
        f"opened/closed {summary.connections_opened}/{summary.connections_closed}"
        + ("  [TRUNCATED: file cut off mid-write]" if summary.truncated else ""),
        "",
        "Did F1's race-order Position field stream on the live socket?",
    ]
    if summary.first_position_update_s is None:
        lines.append("  NO — no TimingData feed message carried a Position value.")
    else:
        lines.append(
            f"  YES — {summary.position_updates} Position update(s); the first "
            f"{summary.first_position_update_s:.0f}s into the recording."
        )
    if archive is not None:
        archive_counts, archive_positions = archive_feed_counts(archive, archive_connect_at)
        lines += ["", "Socket vs F1's archive of the same session (feed messages after connect):"]
        lines.append(f"  {'topic':14}{'recording':>10}{'archive':>10}{'ratio':>8}")
        for topic in _TOPIC_PAGES:
            got, want = summary.messages[topic], archive_counts[topic]
            ratio = f"{100 * got / want:.0f}%" if want else "n/a"
            lines.append(f"  {topic:14}{got:>10}{want:>10}{ratio:>8}")
        ratio = (
            f"{100 * summary.position_updates / archive_positions:.0f}%"
            if archive_positions
            else "n/a"
        )
        row = f"  {'Position updates':14}{summary.position_updates:>10}"
        lines.append(row + f"{archive_positions:>10}{ratio:>8}")
        lines.append(
            "  (a recording that starts late or ends early legitimately has fewer messages)"
        )
    return "\n".join(lines)


_EXCERPT_FIELDS = (
    "GapToLeader",
    "IntervalToPositionAhead",
    "Line",
    "Position",
    "ShowPosition",
    "Retired",
    "Stopped",
    "NumberOfLaps",
    "LastLapTime",
    "InPit",
    "PitOut",
    "RacingNumber",
)
_GAP_ONLY_FIELDS = frozenset({"GapToLeader", "IntervalToPositionAhead"})


def build_excerpt(
    archive: Archive,
    cars: list[str],
    windows: list[tuple[float, float]],
    gap_stride: int,
    connect_at: float = _DEFAULT_CONNECT_AT_SECONDS,
) -> dict[str, Any]:
    """Trim an archive to the few cars and moments a unit test needs.

    Keeps every TimingData message at or before connect_at (so the Subscribe
    snapshot is intact), plus messages inside `windows`; within those, a car's
    messages that carry ONLY gap fields are thinned to 1 in gap_stride. Drops
    sectors/speeds/telemetry, which no test here reads.

    Args:
        archive: Full archive from fetch_archive.
        cars: Car numbers to keep.
        windows: (start_s, end_s) stream-time ranges to keep after connect_at.
        gap_stride: Keep 1 of every N gap-only messages per car.
        connect_at: Snapshot boundary, recorded in the result.
    Returns:
        A JSON-serialisable dict load_excerpt can turn back into an Archive.
    """
    keep = set(cars)
    gap_seen: Counter[str] = Counter()
    timing: list[list[Any]] = []
    for ts, content in archive["TimingData"]:
        in_window = ts <= connect_at or any(lo <= ts <= hi for lo, hi in windows)
        if not in_window:
            continue
        lines: dict[str, Any] = {}
        for car, entry in content.get("Lines", {}).items():
            if car not in keep or not isinstance(entry, dict):
                continue
            slim = {k: entry[k] for k in _EXCERPT_FIELDS if k in entry}
            if not slim:
                continue
            if ts > connect_at and set(slim) <= _GAP_ONLY_FIELDS:
                gap_seen[car] += 1
                if gap_seen[car] % gap_stride != 0:
                    continue
            lines[car] = slim
        if lines:
            timing.append([round(ts, 3), {"Lines": lines}])

    def _snapshot_only(topic: str, fields: tuple[str, ...] | None) -> list[list[Any]]:
        out: list[list[Any]] = []
        for ts, content in archive.get(topic, []):
            if ts > connect_at:
                continue
            slim_lines: dict[str, Any] = {}
            source = content.get("Lines", content) if topic != "DriverList" else content
            for car, entry in source.items():
                if car in keep and isinstance(entry, dict):
                    slim_lines[car] = (
                        {k: entry[k] for k in fields if k in entry} if fields else entry
                    )
            if slim_lines:
                payload = slim_lines if topic == "DriverList" else {"Lines": slim_lines}
                out.append([round(ts, 3), payload])
        return out

    return {
        "description": (
            "Real F1 live-timing messages, Italian GP 2026 (R13, Monza) race, trimmed to "
            f"cars {cars} and {len(windows)} window(s); gap-only messages thinned 1 in "
            f"{gap_stride}. Generated by backend/scripts/verify_live_feed_archive.py "
            "--write-excerpt. Ranks are relative to these cars only, not the full field."
        ),
        "connect_at": connect_at,
        "topics": {
            "TimingData": timing,
            "TimingAppData": _snapshot_only("TimingAppData", ("Stints",)),
            "DriverList": _snapshot_only("DriverList", ("Tla", "RacingNumber")),
            "TrackStatus": [
                [round(ts, 3), content] for ts, content in archive.get("TrackStatus", [])
            ],
        },
    }


def load_excerpt(path: Path) -> tuple[Archive, float]:
    """Read a build_excerpt file back into (Archive, connect_at)."""
    data = json.loads(path.read_text())
    archive: Archive = {
        topic: [(_ts_seconds(ts), content) for ts, content in messages]
        for topic, messages in data["topics"].items()
    }
    return archive, float(data["connect_at"])


# --- report ---


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}%" if den else "n/a"


def format_report(report: ReplayReport) -> str:
    lines = [
        f"Replayed {report.messages_replayed} message(s); "
        f"{report.laps_dispatched} lap completion(s); "
        f"{report.handler_errors} swallowed handler error(s).",
        "",
        "Lap-completion position vs F1's own Position field:",
        f"  matched {report.position_matched}/{report.position_checked} "
        f"({_pct(report.position_matched, report.position_checked)})",
    ]
    if report.position_mismatch_by_car:
        worst = report.position_mismatch_by_car.most_common(8)
        lines.append("  most mismatches: " + ", ".join(f"{c}={n}" for c, n in worst))
    lines += [
        "",
        "F1's own Position field forms a valid 1..N ranking at lap completions:",
        f"  {report.f1_permutation_valid}/{report.f1_permutation_checked} "
        f"({_pct(report.f1_permutation_valid, report.f1_permutation_checked)})",
        "",
        "Published tower vs F1 state "
        f"({report.tower_samples} car-samples, {report.active_samples} active):",
        f"  active-car rank != F1 Position: {report.rank_mismatch_samples} "
        f"({_pct(report.rank_mismatch_samples, report.active_samples)})",
        f"  ...after dropping ghost cars and re-ranking: {report.dense_rank_mismatch_samples} "
        f"({_pct(report.dense_rank_mismatch_samples, report.active_samples)})",
        "  published-minus-F1 rank offset (top): "
        + ", ".join(f"{k:+d}={v}" for k, v in report.rank_offset_hist.most_common(6)),
    ]
    ghosts = sorted(set(report.ghost_retired_samples) | set(report.ghost_hidden_samples))
    lines.append("  ghost cars (out of the race per F1, still published):")
    if not ghosts:
        lines.append("    none")
    for code in ghosts:
        first = report.ghost_first_out_flag_s.get(code)
        last = report.ghost_last_seen_s.get(code)
        span = (
            f"first flagged {_fmt_ts(first)}, last published {_fmt_ts(last)}"
            if (first is not None and last is not None)
            else ""
        )
        lines.append(
            f"    {code}: Retired-flag samples={report.ghost_retired_samples[code]}, "
            f"ShowPosition=false samples={report.ghost_hidden_samples[code]}  {span}"
        )
    lines.append("  stale numeric gap held for a car F1 reports as lapped:")
    if not report.stale_lapped_samples:
        lines.append("    none")
    for code, count in report.stale_lapped_samples.most_common():
        lines.append(f"    {code}: {count} sample(s)")
    lines += [
        "",
        "F1 Position-field updates seen after connect, per car: "
        + ", ".join(f"{c}={n}" for c, n in sorted(report.position_updates.items())),
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--season", type=int)
    parser.add_argument("--round", type=int, dest="round_number")
    parser.add_argument("--session-type", default="R")
    parser.add_argument("--excerpt", type=Path, help="Replay a committed excerpt file instead.")
    parser.add_argument("--write-excerpt", type=Path, help="Fetch, trim, and write an excerpt.")
    parser.add_argument("--json", type=Path, dest="json_out", help="Also write the report as JSON.")
    parser.add_argument(
        "--no-position-diffs",
        action="store_true",
        help="Strip Position/Line from replayed diffs to exercise the gap-based fallback.",
    )
    parser.add_argument(
        "--rounds",
        help='Replay several rounds of --season, e.g. "1-14" or "3,5,9": one summary row per race.',
    )
    parser.add_argument(
        "--connect-at",
        type=float,
        help="Snapshot boundary in stream seconds (default: detected from the archive).",
    )
    parser.add_argument(
        "--recording",
        type=Path,
        help="Replay a RawFeedRecorder file (what the live socket delivered); with "
        "--season/--round also compare it with F1's archive of that session.",
    )
    args = parser.parse_args()

    if args.recording is not None:
        records, truncated = load_recording(args.recording)
        summary = summarize_recording(records, truncated)
        comparison: Archive | None = None
        comparison_connect_at = _DEFAULT_CONNECT_AT_SECONDS
        if args.season is not None and args.round_number is not None:
            comparison = fetch_archive(args.season, args.round_number, args.session_type)
            comparison_connect_at = detect_connect_at(comparison)
        print(format_recording_report(summary, comparison, comparison_connect_at))
        recorded_archive, recorded_connect_at = recording_to_archive(records)
        print("\nReplaying the recording through the ingestor:\n")
        print(format_report(replay(recorded_archive, recorded_connect_at)))
        return

    if args.rounds is not None:
        if args.season is None:
            parser.error("--season is required with --rounds")
        print(format_batch(run_batch(args.season, parse_rounds(args.rounds), args.session_type)))
        return

    if args.excerpt is not None:
        archive, connect_at = load_excerpt(args.excerpt)
    else:
        if args.season is None or args.round_number is None:
            parser.error("--season and --round are required unless --excerpt is given")
        archive = fetch_archive(args.season, args.round_number, args.session_type)
        connect_at = args.connect_at if args.connect_at is not None else detect_connect_at(archive)

    if args.write_excerpt is not None:
        # LEC(16) retire story, leader RUS(63), ANT(12), NOR(1), and the lapped/
        # retiring BOT(77)/PER(11)/STR(18)/ALO(14) — windows cover race start,
        # LEC's "1 L" freeze, Retired, ShowPosition=false, first lapped cars,
        # and the final "52L"/"27L" classification strings.
        excerpt = build_excerpt(
            archive,
            cars=["16", "63", "12", "1", "77", "11", "18", "14"],
            windows=[
                (_ts_seconds("0:57:00"), _ts_seconds("1:01:30")),
                (_ts_seconds("1:11:30"), _ts_seconds("1:13:00")),
                (_ts_seconds("1:26:00"), _ts_seconds("1:27:00")),
                (_ts_seconds("1:53:00"), _ts_seconds("1:54:00")),
                (_ts_seconds("2:11:30"), _ts_seconds("2:16:00")),
                (_ts_seconds("2:55:00"), _ts_seconds("2:55:30")),
            ],
            gap_stride=4,
            connect_at=connect_at,
        )
        args.write_excerpt.parent.mkdir(parents=True, exist_ok=True)
        args.write_excerpt.write_text(json.dumps(excerpt, separators=(",", ":")))
        logger.warning("Wrote %s (%d bytes)", args.write_excerpt, args.write_excerpt.stat().st_size)
        return

    report = replay(archive, connect_at, strip_position_diffs=args.no_position_diffs)
    print(format_report(report))
    if args.json_out is not None:
        # dataclasses.asdict rebuilds a Counter from an iterable of items, which
        # turns its (key, count) pairs into keys — convert explicitly instead.
        as_dict = {
            name: dict(value) if isinstance(value, Counter) else value
            for name, value in vars(report).items()
        }
        args.json_out.write_text(json.dumps(as_dict, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    main()
