"""Daily Strategy Simulator quotas (docs/internal/demo-deployment-plan-2026.md, Day 5).

The simulator runs a ~10 s Monte Carlo per scenario on the worker, which in
production starts on demand. A public demo caps it per account and for the
whole site, per UTC day, counted in scenarios: a single plan is 1 and a
comparison is its number of scenarios (up to 4).

Two Redis counters per day, both expiring after QUOTA_KEY_TTL_SECONDS:
  f1:sim_quota:user:{user_id}:{YYYY-MM-DD}
  f1:sim_quota:global:{YYYY-MM-DD}

reserve() checks and increments both in ONE optimistic transaction (WATCH
both keys, read, MULTI/EXEC; retried if either key changed meanwhile), so two
requests racing for the last scenarios can never both get them, and a request
is never counted against one limit but not the other. A request that would go
over either limit is refused whole: a 4-scenario comparison with 3 left runs
nothing. refund() gives a reservation back when the simulation could not be
queued.

Limits come from AppSettings.sim_daily_scenarios_per_user / _global; 0 means
unlimited, and with both 0 nothing is counted at all (local development).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import redis.asyncio as aioredis
from redis.exceptions import WatchError

from backend.core.config import get_app_settings
from backend.core.exceptions import ConflictError, QuotaExceededError
from backend.schemas.simulate_schema import QuotaCounter, SimulationQuotaResponse

# A day's keys outlive the day by two hours, so a refund just after midnight
# still finds the counter it reserved from.
QUOTA_KEY_TTL_SECONDS = 26 * 60 * 60

# Optimistic-transaction retries before giving up. Contention needs another
# reserve on the same keys between our WATCH and EXEC, a few milliseconds, so
# real traffic retries once at most.
_MAX_ATTEMPTS = 20


@dataclass(frozen=True)
class QuotaReservation:
    """Scenarios taken from today's quotas, for refund() if the run is not queued."""

    user_id: uuid.UUID
    day: date
    scenarios: int


def _user_key(user_id: uuid.UUID, day: date) -> str:
    return f"f1:sim_quota:user:{user_id}:{day.isoformat()}"


def _global_key(day: date) -> str:
    return f"f1:sim_quota:global:{day.isoformat()}"


def _resets_at(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)


def _counter(limit: int, used: int) -> QuotaCounter:
    if limit <= 0:
        return QuotaCounter(limit=None, used=used, remaining=None)
    return QuotaCounter(limit=limit, used=used, remaining=max(limit - used, 0))


def _refusal(
    scope: str, limit: int, used: int, scenarios: int, now: datetime
) -> QuotaExceededError:
    remaining = max(limit - used, 0)
    resets_at = _resets_at(now.date())
    if scope == "user":
        message = (
            f"You have {remaining} of {limit} simulation scenarios left today and this "
            f"request needs {scenarios}. Your quota resets at 00:00 UTC."
        )
    else:
        message = (
            f"The demo's daily limit of {limit} simulation scenarios has "
            f"{remaining} left and this request needs {scenarios}. "
            "It resets at 00:00 UTC."
        )
    return QuotaExceededError(
        message,
        detail={
            "scope": scope,
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "requested": scenarios,
            "resets_at": resets_at.isoformat(),
        },
        retry_after_seconds=max(int((resets_at - now).total_seconds()), 1),
    )


async def reserve(
    client: aioredis.Redis,  # type: ignore[type-arg]
    user_id: uuid.UUID,
    scenarios: int,
    now: datetime | None = None,
) -> QuotaReservation | None:
    """Take `scenarios` from today's per-user and global quotas, or refuse.

    Args:
        client: Async Redis client.
        user_id: The account running the simulation.
        scenarios: 1 for a single plan, or the comparison's scenario count.
        now: The current time (UTC); injected in tests.
    Returns:
        The reservation, for refund(); None when no quota is configured.
    Raises:
        QuotaExceededError: either limit would be exceeded; nothing is taken.
        ConflictError: the counters kept changing under the transaction.
    """
    settings = get_app_settings()
    user_limit = settings.sim_daily_scenarios_per_user
    global_limit = settings.sim_daily_scenarios_global
    if user_limit <= 0 and global_limit <= 0:
        return None

    now = now or datetime.now(UTC)
    day = now.date()
    user_key, global_key = _user_key(user_id, day), _global_key(day)
    async with client.pipeline(transaction=True) as pipe:
        for _ in range(_MAX_ATTEMPTS):
            try:
                await pipe.watch(user_key, global_key)
                user_used = int(await pipe.get(user_key) or 0)
                global_used = int(await pipe.get(global_key) or 0)
                if user_limit > 0 and user_used + scenarios > user_limit:
                    raise _refusal("user", user_limit, user_used, scenarios, now)
                if global_limit > 0 and global_used + scenarios > global_limit:
                    raise _refusal("global", global_limit, global_used, scenarios, now)
                pipe.multi()
                pipe.incrby(user_key, scenarios)
                pipe.expire(user_key, QUOTA_KEY_TTL_SECONDS)
                pipe.incrby(global_key, scenarios)
                pipe.expire(global_key, QUOTA_KEY_TTL_SECONDS)
                await pipe.execute()
                return QuotaReservation(user_id, day, scenarios)
            except WatchError:
                continue
    raise ConflictError("Too many simulations are starting at once; please try again")


async def refund(
    client: aioredis.Redis,  # type: ignore[type-arg]
    reservation: QuotaReservation | None,
) -> None:
    """Give a reservation back, when its simulation could not be queued.

    Args:
        client: Async Redis client.
        reservation: What reserve() returned; None (no quota configured) is a no-op.
    Returns:
        None.
    """
    if reservation is None:
        return
    async with client.pipeline(transaction=True) as pipe:
        pipe.decrby(_user_key(reservation.user_id, reservation.day), reservation.scenarios)
        pipe.decrby(_global_key(reservation.day), reservation.scenarios)
        await pipe.execute()


async def get_quota(
    client: aioredis.Redis,  # type: ignore[type-arg]
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> SimulationQuotaResponse:
    """Today's usage and what is left, for the caller and for the whole site.

    Args:
        client: Async Redis client.
        user_id: The caller.
        now: The current time (UTC); injected in tests.
    Returns:
        Both quotas; a limit and remaining of None mean that quota is unlimited.
    """
    settings = get_app_settings()
    now = now or datetime.now(UTC)
    day = now.date()
    user_used, global_used = await client.mget(_user_key(user_id, day), _global_key(day))
    return SimulationQuotaResponse(
        day=day,
        resets_at=_resets_at(day),
        user_quota=_counter(settings.sim_daily_scenarios_per_user, int(user_used or 0)),
        global_quota=_counter(settings.sim_daily_scenarios_global, int(global_used or 0)),
    )
