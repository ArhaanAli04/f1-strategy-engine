"""Unit tests for services/simulation_quota_service.py (fake Redis, fixed clock).

fakeredis supports WATCH/MULTI/EXEC, so these run the real optimistic
transaction, not a mock of it.
"""

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import fakeredis as fakeredis_lib
import pytest
from redis.asyncio.client import Pipeline

from backend.core.exceptions import (
    ConflictError,
    QuotaExceededError,
    f1_strategy_error_handler,
)
from backend.services import simulation_quota_service as quota

NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)  # 3 h before the reset
USER_KEY = "f1:sim_quota:user:{}:2026-10-01"
GLOBAL_KEY = "f1:sim_quota:global:2026-10-01"


def _limits(monkeypatch: pytest.MonkeyPatch, per_user: int, global_: int) -> None:
    settings = MagicMock(sim_daily_scenarios_per_user=per_user, sim_daily_scenarios_global=global_)
    monkeypatch.setattr(quota, "get_app_settings", lambda: settings)


async def _used(client: fakeredis_lib.FakeAsyncRedis, key: str) -> int:
    return int(await client.get(key) or 0)


# --- unlimited ---


@pytest.mark.unit
async def test_no_limits_configured_counts_nothing(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 0, 0)

    reservation = await quota.reserve(fakeredis, uuid.uuid4(), 4, NOW)

    assert reservation is None
    assert await fakeredis.keys("f1:sim_quota:*") == []


# --- counting ---


@pytest.mark.unit
async def test_a_single_plan_counts_one_against_both_quotas(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()

    reservation = await quota.reserve(fakeredis, user, 1, NOW)

    assert reservation == quota.QuotaReservation(user, date(2026, 10, 1), 1)
    assert await _used(fakeredis, USER_KEY.format(user)) == 1
    assert await _used(fakeredis, GLOBAL_KEY) == 1
    assert 0 < await fakeredis.ttl(GLOBAL_KEY) <= quota.QUOTA_KEY_TTL_SECONDS


@pytest.mark.unit
async def test_a_four_scenario_comparison_uses_four(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()

    await quota.reserve(fakeredis, user, 4, NOW)

    assert await _used(fakeredis, USER_KEY.format(user)) == 4
    assert await _used(fakeredis, GLOBAL_KEY) == 4


@pytest.mark.unit
async def test_a_new_utc_day_starts_from_zero(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()
    await quota.reserve(fakeredis, user, 5, NOW)

    tomorrow = datetime(2026, 10, 2, 0, 1, tzinfo=UTC)
    reservation = await quota.reserve(fakeredis, user, 5, tomorrow)

    assert reservation is not None
    assert reservation.day == date(2026, 10, 2)


# --- refusals ---


@pytest.mark.unit
async def test_the_sixth_scenario_of_the_day_is_refused(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()
    for _ in range(5):
        await quota.reserve(fakeredis, user, 1, NOW)

    with pytest.raises(QuotaExceededError) as refused:
        await quota.reserve(fakeredis, user, 1, NOW)

    error = refused.value
    assert error.status_code == 429
    assert error.detail == {
        "scope": "user",
        "limit": 5,
        "used": 5,
        "remaining": 0,
        "requested": 1,
        "resets_at": "2026-10-02T00:00:00+00:00",
    }
    assert error.retry_after_seconds == 3 * 60 * 60
    assert "0 of 5" in error.message
    assert await _used(fakeredis, GLOBAL_KEY) == 5  # the refused request took nothing


@pytest.mark.unit
async def test_a_comparison_larger_than_what_is_left_is_refused_whole(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()
    await quota.reserve(fakeredis, user, 2, NOW)

    with pytest.raises(QuotaExceededError) as refused:
        await quota.reserve(fakeredis, user, 4, NOW)

    assert refused.value.detail["remaining"] == 3
    assert await _used(fakeredis, USER_KEY.format(user)) == 2
    assert await _used(fakeredis, GLOBAL_KEY) == 2


@pytest.mark.unit
async def test_the_global_limit_refuses_another_user_with_quota_left(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    for _ in range(3):
        await quota.reserve(fakeredis, uuid.uuid4(), 5, NOW)

    with pytest.raises(QuotaExceededError) as refused:
        await quota.reserve(fakeredis, uuid.uuid4(), 1, NOW)

    assert refused.value.detail["scope"] == "global"
    assert "daily limit of 15" in refused.value.message


@pytest.mark.unit
async def test_only_a_global_limit_still_limits_everyone(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 0, 2)
    user = uuid.uuid4()

    await quota.reserve(fakeredis, user, 2, NOW)

    with pytest.raises(QuotaExceededError):
        await quota.reserve(fakeredis, user, 1, NOW)


def _interfere_once(
    monkeypatch: pytest.MonkeyPatch, other: fakeredis_lib.FakeAsyncRedis
) -> list[str]:
    """Make another client take a global scenario between our WATCH and EXEC:
    right after reserve()'s first attempt has read both counters, so that
    attempt decides on stale numbers and its EXEC must be refused. Returns the
    GETs seen, so a test can tell the transaction really ran twice.
    (asyncio.gather on fakeredis never interleaves two reserves, so a real
    race needs this.)"""
    gets: list[str] = []
    original: Any = Pipeline.immediate_execute_command

    async def interleaved(self: Any, *args: Any, **options: Any) -> Any:
        result = await original(self, *args, **options)
        if args[0] == "GET":
            gets.append(args[1])
            if len(gets) == 2:
                await other.incr(GLOBAL_KEY)
        return result

    monkeypatch.setattr(Pipeline, "immediate_execute_command", interleaved)
    return gets


@pytest.mark.unit
async def test_a_concurrent_change_makes_reserve_retry_and_count_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 2)
    server = fakeredis_lib.FakeServer()
    client = fakeredis_lib.FakeAsyncRedis(server=server, decode_responses=True)
    other = fakeredis_lib.FakeAsyncRedis(server=server, decode_responses=True)
    gets = _interfere_once(monkeypatch, other)

    reservation = await quota.reserve(client, uuid.uuid4(), 1, NOW)

    assert reservation is not None
    assert len(gets) == 4  # two reads per attempt: the first EXEC was refused
    assert await _used(client, GLOBAL_KEY) == 2  # the other client's 1 + ours


@pytest.mark.unit
async def test_a_concurrent_change_that_uses_the_last_scenario_refuses_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 1)
    server = fakeredis_lib.FakeServer()
    client = fakeredis_lib.FakeAsyncRedis(server=server, decode_responses=True)
    other = fakeredis_lib.FakeAsyncRedis(server=server, decode_responses=True)
    gets = _interfere_once(monkeypatch, other)

    with pytest.raises(QuotaExceededError):
        await quota.reserve(client, uuid.uuid4(), 1, NOW)

    assert len(gets) == 4
    assert await _used(client, GLOBAL_KEY) == 1  # only the other client's


@pytest.mark.unit
async def test_reserve_gives_up_if_the_counters_never_settle(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    monkeypatch.setattr(quota, "_MAX_ATTEMPTS", 0)

    with pytest.raises(ConflictError):
        await quota.reserve(fakeredis, uuid.uuid4(), 1, NOW)


# --- refund ---


@pytest.mark.unit
async def test_refund_gives_the_reservation_back(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    user = uuid.uuid4()
    await quota.reserve(fakeredis, user, 1, NOW)
    reservation = await quota.reserve(fakeredis, user, 3, NOW)

    await quota.refund(fakeredis, reservation)

    assert await _used(fakeredis, USER_KEY.format(user)) == 1
    assert await _used(fakeredis, GLOBAL_KEY) == 1


@pytest.mark.unit
async def test_refund_of_no_reservation_does_nothing(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await quota.refund(fakeredis, None)
    assert await fakeredis.keys("f1:sim_quota:*") == []


# --- status ---


@pytest.mark.unit
async def test_quota_status_shows_used_and_remaining_for_user_and_site(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 5, 15)
    me, other = uuid.uuid4(), uuid.uuid4()
    await quota.reserve(fakeredis, me, 2, NOW)
    await quota.reserve(fakeredis, other, 4, NOW)

    status = await quota.get_quota(fakeredis, me, NOW)

    assert status.day == date(2026, 10, 1)
    assert status.resets_at == datetime(2026, 10, 2, tzinfo=UTC)
    assert (status.user_quota.limit, status.user_quota.used, status.user_quota.remaining) == (
        5,
        2,
        3,
    )
    assert (
        status.global_quota.limit,
        status.global_quota.used,
        status.global_quota.remaining,
    ) == (15, 6, 9)


@pytest.mark.unit
async def test_quota_status_is_unlimited_when_not_configured(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, 0, 0)

    status = await quota.get_quota(fakeredis, uuid.uuid4(), NOW)

    assert status.user_quota.limit is None
    assert status.user_quota.remaining is None
    assert status.global_quota.used == 0


# --- the 429 response ---


@pytest.mark.unit
async def test_quota_refusal_is_a_429_with_retry_after() -> None:
    error = QuotaExceededError("No scenarios left", {"scope": "user"}, retry_after_seconds=600)
    request = MagicMock()
    request.url.path = "/api/v1/strategy/s/simulate"

    response = await f1_strategy_error_handler(request, error)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "600"
    assert json.loads(bytes(response.body)) == {
        "error": "QUOTA_EXCEEDED",
        "message": "No scenarios left",
        "detail": {"scope": "user"},
    }
