"""Unit tests for services/live_race_detection.py.

The f1:{s}:{r}:gaps key has three writers — a live ingestor
("source": "live"), replay_pipeline.py ("source": "replay"), and
telemetry_service.get_session_gaps' @cacheable cache-aside (no "source").
Only the first counts as a live race.
"""

import json

import fakeredis as fakeredis_lib
import pytest

from backend.services.live_race_detection import detect_live_race, detect_live_race_sync

_LIVE_GAPS = json.dumps({"session_id": "s", "gaps": [], "source": "live"})
_REPLAY_GAPS = json.dumps({"session_id": "s", "gaps": [], "source": "replay"})
# What telemetry_service.get_session_gaps' @cacheable writes for any historical
# session someone views — the payload that used to trip a false positive.
_CACHE_GAPS = json.dumps({"session_id": "s", "gaps": []})


@pytest.mark.unit
async def test_clean_state_is_not_live(fakeredis: fakeredis_lib.FakeAsyncRedis) -> None:
    status = await detect_live_race(fakeredis)
    assert status.is_live is False
    assert status.reason is None


@pytest.mark.unit
async def test_live_source_gaps_key_is_detected(fakeredis: fakeredis_lib.FakeAsyncRedis) -> None:
    await fakeredis.setex("f1:2026:10:gaps", 30, _LIVE_GAPS)
    status = await detect_live_race(fakeredis)
    assert status.is_live is True
    assert status.reason is not None
    assert "2026 round 10" in status.reason


@pytest.mark.unit
async def test_live_source_gaps_key_detected_regardless_of_ttl(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    # The check is source-based, not TTL-based — a "source": "live" key counts
    # even with a long TTL.
    await fakeredis.setex("f1:2026:10:gaps", 600, _LIVE_GAPS)
    status = await detect_live_race(fakeredis)
    assert status.is_live is True


@pytest.mark.unit
async def test_sourceless_cache_gaps_key_is_not_live(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    # Regression: the @cacheable DB-reconstruction write (no "source", ~8s
    # TTL) fires for any historical session open in the frontend and must
    # never read as a live race.
    await fakeredis.setex("f1:2026:10:gaps", 8, _CACHE_GAPS)
    status = await detect_live_race(fakeredis)
    assert status.is_live is False


@pytest.mark.unit
async def test_replay_source_gaps_key_is_not_live(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await fakeredis.setex("f1:2026:9:gaps", 30, _REPLAY_GAPS)
    status = await detect_live_race(fakeredis)
    assert status.is_live is False


@pytest.mark.unit
async def test_gaps_final_sibling_key_is_ignored(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    # Even a "source": "live" payload on the sibling :final key must not match
    # the f1:*:*:gaps pattern.
    await fakeredis.setex("f1:2026:9:gaps:final", 60, _LIVE_GAPS)
    status = await detect_live_race(fakeredis)
    assert status.is_live is False


@pytest.mark.unit
async def test_non_json_gaps_value_is_not_live(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await fakeredis.setex("f1:2026:9:gaps", 30, "not json")
    status = await detect_live_race(fakeredis)
    assert status.is_live is False


@pytest.mark.unit
async def test_auto_detection_dedup_key_is_detected(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await fakeredis.setex("f1:2026:10:R:auto_ingestion_triggered", 14400, "1")
    status = await detect_live_race(fakeredis)
    assert status.is_live is True
    assert status.reason is not None
    assert "2026 round 10" in status.reason


@pytest.mark.unit
def test_sync_variant_detects_live_source_gaps_key() -> None:
    client = fakeredis_lib.FakeRedis(decode_responses=True)
    try:
        client.setex("f1:2026:10:gaps", 30, _LIVE_GAPS)
        status = detect_live_race_sync(client)
        assert status.is_live is True
    finally:
        client.close()


@pytest.mark.unit
def test_sync_variant_ignores_sourceless_cache_gaps_key() -> None:
    client = fakeredis_lib.FakeRedis(decode_responses=True)
    try:
        client.setex("f1:2026:10:gaps", 8, _CACHE_GAPS)
        status = detect_live_race_sync(client)
        assert status.is_live is False
    finally:
        client.close()


@pytest.mark.unit
def test_sync_variant_clean_state_is_not_live() -> None:
    client = fakeredis_lib.FakeRedis(decode_responses=True)
    try:
        status = detect_live_race_sync(client)
        assert status.is_live is False
        assert status.reason is None
    finally:
        client.close()
