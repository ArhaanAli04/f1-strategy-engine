"""Unit tests for services/cache_service.py.

cache_service.cache_lock is stubbed to a no-op (see _stub_cache_lock) since
fakeredis has no Lua/EVALSHA support (needs the lupa extra, not installed),
which redis-py's real Lock needs to release(). The single-flight lock's actual
acquire/release mechanics belong to integration tests against real Redis.
"""

import uuid
from typing import Any

import fakeredis as fakeredis_lib
import pytest

from backend.services import cache_service


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())


@pytest.mark.unit
async def test_ttl_applied_correctly(fakeredis: fakeredis_lib.FakeAsyncRedis) -> None:
    await cache_service.cache_set(fakeredis, "f1:test:ttl", {"a": 1}, ttl=30)
    await cache_service.cache_set(fakeredis, "f1:test:no_ttl", {"a": 1}, ttl=None)

    assert 0 < await fakeredis.ttl("f1:test:ttl") <= 30
    assert await fakeredis.ttl("f1:test:no_ttl") == -1  # -1 == key exists, no expiry


@pytest.mark.unit
def test_metric_label_collapses_id_segments_per_entity() -> None:
    driver_id = uuid.uuid4()
    strategy_key = f"f1:2026:10:strategy:{driver_id}:pit_window"
    driver_fingerprint_key = f"f1:driver:{driver_id}:fingerprint"

    assert cache_service._metric_label(strategy_key) == "f1:*:*:strategy:*:pit_window"
    assert cache_service._metric_label(driver_fingerprint_key) == "f1:driver:*:fingerprint"


@pytest.mark.unit
async def test_cache_invalidate_session_clears_only_that_season_round(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await fakeredis.set("f1:2026:10:car:44:latest", "x")
    await fakeredis.set("f1:2026:10:gaps", "x")
    await fakeredis.set("f1:2026:11:gaps", "x")  # different round — must survive

    deleted = await cache_service.cache_invalidate_session(fakeredis, 2026, 10)

    assert deleted == 2
    assert await fakeredis.get("f1:2026:10:car:44:latest") is None
    assert await fakeredis.get("f1:2026:10:gaps") is None
    assert await fakeredis.get("f1:2026:11:gaps") == "x"


@pytest.mark.unit
async def test_cache_invalidate_driver_clears_fingerprint_and_strategy_keys(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    driver_id = uuid.uuid4()
    other_driver_id = uuid.uuid4()
    await fakeredis.set(f"f1:driver:{driver_id}:fingerprint", "x")
    await fakeredis.set(f"f1:2026:10:strategy:{driver_id}:pit_window", "x")
    await fakeredis.set(f"f1:2026:10:strategy:{driver_id}:undercut:{other_driver_id}", "x")
    await fakeredis.set(f"f1:2026:10:strategy:{other_driver_id}:pit_window", "x")  # must survive
    await fakeredis.set("f1:2026:10:car:44:latest", "x")  # must survive

    deleted = await cache_service.cache_invalidate_driver(fakeredis, driver_id)

    assert deleted == 3
    assert await fakeredis.get(f"f1:driver:{driver_id}:fingerprint") is None
    assert await fakeredis.get(f"f1:2026:10:strategy:{driver_id}:pit_window") is None
    assert (
        await fakeredis.get(f"f1:2026:10:strategy:{driver_id}:undercut:{other_driver_id}") is None
    )
    assert await fakeredis.get(f"f1:2026:10:strategy:{other_driver_id}:pit_window") == "x"
    assert await fakeredis.get("f1:2026:10:car:44:latest") == "x"


@pytest.mark.unit
async def test_cacheable_hit_skips_computation_and_miss_writes_cache(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    call_count = 0

    @cache_service.cacheable(ttl=30, key_fn=lambda client, arg: f"f1:test:{arg}")
    async def _compute(client: Any, arg: str) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        return {"value": arg}

    first_result = await _compute(fakeredis, "a")
    assert first_result == {"value": "a"}
    assert call_count == 1
    assert await fakeredis.get("f1:test:a") is not None

    second_result = await _compute(fakeredis, "a")
    assert second_result == {"value": "a"}
    assert call_count == 1  # cache hit — no recomputation
