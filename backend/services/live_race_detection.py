"""Detect whether a real live race is currently being ingested.

Shared by replay_pipeline.py's CLI guard (Day 43 Part 3.2) and the
/demo/replay/* control endpoints (Part 4): a Demo Replay must never run
concurrently with a real live ingestion. Both write the same
f1:{season}:{round}:gaps and f1:{season}:{round}:car:{n}:position Redis keys,
so overlapping them corrupts the Timing Tower and Circuit Map and misleads
anyone watching.

Two independent signals — either one positive means "a live race is running":

1. An f1:{season}:{round}:gaps key whose JSON payload has `"source": "live"`.
   Only ingest_live_session.py's _publish_live_gaps stamps that. The SAME key
   is also written by replay_pipeline.py (`"source": "replay"`) and — for any
   historical session someone opens in the frontend — by
   telemetry_service.get_session_gaps' @cacheable cache-aside (no `"source"`
   at all, ~8s TTL). An earlier version of this check keyed off remaining TTL
   instead and misread both of those as phantom live races; the explicit
   marker removes that ambiguity entirely.
2. An auto-detection dedup key f1:{season}:{round}:R:auto_ingestion_triggered
   (race_detection_worker.py sets it, 4h TTL) — present whenever Celery Beat
   has auto-launched a live ingestor for a race, including the brief window
   before that ingestor's first gaps publish.

A manually-run `make ingest-live` sets signal 1 but not signal 2; an
auto-detected race sets both. Checking both covers every launch path.
"""

from __future__ import annotations

import json
from typing import NamedTuple

import redis
import redis.asyncio as aioredis

# f1:{season}:{round}:gaps only — the trailing-anchored pattern does NOT match
# the sibling f1:{season}:{round}:gaps:final / :last_good keys.
_GAPS_KEY_PATTERN = "f1:*:*:gaps"
_AUTO_TRIGGER_KEY_PATTERN = "f1:*:*:R:auto_ingestion_triggered"

# The gaps-payload "source" value that means "a live ingestor wrote this".
_LIVE_GAPS_SOURCE = "live"


class LiveRaceStatus(NamedTuple):
    """Result of a live-race check.

    Attributes:
        is_live: True if a real live ingestion appears to be running.
        reason: Human-readable explanation when is_live is True, else None.
    """

    is_live: bool
    reason: str | None


def _season_round_label(key: str) -> str:
    """Turn "f1:2026:10:gaps" into "2026 round 10" for a log/response message."""
    parts = key.split(":")
    if len(parts) >= 3:
        return f"{parts[1]} round {parts[2]}"
    return key


def _is_live_written(raw_value: object) -> bool:
    """True only if a gaps key's stored payload is JSON with "source": "live".

    ingest_live_session.py._publish_live_gaps is the only writer that stamps
    it. A replay's payload ("source": "replay") and the @cacheable
    DB-reconstruction payload (no "source") both return False here.
    """
    if not isinstance(raw_value, (str, bytes, bytearray)):
        return False
    try:
        payload = json.loads(raw_value)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("source") == _LIVE_GAPS_SOURCE


def detect_live_race_sync(client: redis.Redis) -> LiveRaceStatus:  # type: ignore[type-arg]
    """Synchronous live-race check, for replay_pipeline.py's CLI guard.

    Uses SCAN (cursor-based, non-blocking) rather than KEYS. Runs only at
    replay start, not on a hot path.

    Args:
        client: A synchronous Redis client (decode_responses=True expected).
    Returns:
        LiveRaceStatus — is_live plus a reason string when positive.
    """
    for raw_key in client.scan_iter(match=_GAPS_KEY_PATTERN):
        key = str(raw_key)
        if _is_live_written(client.get(key)):
            return LiveRaceStatus(True, f"live timing feed active for {_season_round_label(key)}")

    for raw_key in client.scan_iter(match=_AUTO_TRIGGER_KEY_PATTERN):
        key = str(raw_key)
        return LiveRaceStatus(
            True, f"auto race detection has launched ingestion for {_season_round_label(key)}"
        )

    return LiveRaceStatus(False, None)


async def detect_live_race(client: aioredis.Redis) -> LiveRaceStatus:  # type: ignore[type-arg]
    """Async live-race check, for the /demo/replay/* endpoints.

    Args:
        client: An async Redis client (decode_responses=True expected).
    Returns:
        LiveRaceStatus — is_live plus a reason string when positive.
    """
    async for raw_key in client.scan_iter(match=_GAPS_KEY_PATTERN):
        key = str(raw_key)
        if _is_live_written(await client.get(key)):
            return LiveRaceStatus(True, f"live timing feed active for {_season_round_label(key)}")

    async for raw_key in client.scan_iter(match=_AUTO_TRIGGER_KEY_PATTERN):
        key = str(raw_key)
        return LiveRaceStatus(
            True, f"auto race detection has launched ingestion for {_season_round_label(key)}"
        )

    return LiveRaceStatus(False, None)
