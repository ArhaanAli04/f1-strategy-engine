"""What replay_pipeline.py and replay_playback.py share: the live-race guard,
the SIGTERM handler and the replay Redis keys.

Kept separate and light (no FastF1, pandas or ML imports) so replay_playback.py
can use it without importing replay_pipeline.py, which loads all of those.
"""

import logging
import sys
from types import FrameType

import redis

from backend.core.config import get_redis_settings
from backend.services.live_race_detection import detect_live_race_sync

logger = logging.getLogger(__name__)

# Live ingestion refreshes the gaps key every few seconds straight from real
# TimingData messages (_GAPS_KEY_TTL_SECONDS=30 in ingest_live_session.py) — a
# replay only refreshes it once per LAP (a lap takes ~90-100 real seconds), so
# a matching short TTL would expire mid-lap and fall through to the broken DB
# reconstruction the replay gaps exist to avoid. Generous on purpose; refreshed
# every lap anyway.
GAPS_KEY_TTL_SECONDS = 600
# Matches ingest_live_session.py's _POSITION_KEY_TTL_SECONDS — a replay
# publishes at the same real ~1Hz cadence live Position.z updates at.
POSITION_KEY_TTL_SECONDS = 3
# Written once at startup and never refreshed mid-replay (no live DriverList
# topic to re-trigger it) — generous flat TTL rather than trying to predict
# total replay duration up front.
CAR_NUMBER_KEY_TTL_SECONDS = 4 * 60 * 60


def gaps_key(season: int, round_number: int) -> str:
    return f"f1:{season}:{round_number}:gaps"


def position_key(season: int, round_number: int, car_number: str) -> str:
    return f"f1:{season}:{round_number}:car:{car_number}:position"


def car_number_key(season: int, round_number: int, driver_id: str) -> str:
    return f"f1:{season}:{round_number}:driver:{driver_id}:car_number"


def reraise_sigterm_as_interrupt(signum: int, frame: FrameType | None) -> None:
    """Route SIGTERM into the replay's graceful KeyboardInterrupt shutdown.

    /demo/replay/stop and race_detection_worker.py's kill-switch both stop a
    replay with SIGTERM. Without this handler SIGTERM's default action kills
    the process outright, skipping the finally blocks that stop playback and
    delete the replay's gaps key.
    """
    raise KeyboardInterrupt


def guard_against_live_race() -> None:
    """Abort the process if a real live race is currently being ingested.

    A replay and a live ingestor both write f1:{season}:{round}:gaps /
    :car:{n}:position — running them at once corrupts the shared keys. The
    /demo/replay/start endpoint performs the same check before launching a
    replay as a subprocess; this is the direct-CLI-invocation backstop
    (Day 43 Part 3.2), with no bypass flag by design.
    """
    redis_client: redis.Redis = redis.Redis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    try:
        status = detect_live_race_sync(redis_client)
    finally:
        redis_client.close()

    if status.is_live:
        logger.error(
            "Refusing to start replay: %s. Wait until the live session finishes.",
            status.reason,
        )
        sys.exit(1)
