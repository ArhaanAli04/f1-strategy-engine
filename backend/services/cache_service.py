"""Redis caching layer: cache-aside helpers, invalidation, and a @cacheable decorator.

Builds on core/redis_client.py's raw get/set (serialisation, connection handling) and
adds the business-level layer CLAUDE.md's "ALL service methods must check Redis cache
before computing" rule expects: hit/miss metrics and key-family invalidation.

The Day 8 spec's cache_invalidate_session(session_id) doesn't match the actual key
schema in CLAUDE.md — car:latest/strategy/gaps keys are built from (season,
round_number), not the Session table's UUID (see ingest_live_session.py, which
already writes f"f1:{season}:{round}:car:{car_number}:latest"). cache_invalidate_session
here takes (season, round_number) to match what's actually written.
"""

from __future__ import annotations

import functools
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis
from prometheus_client import Counter

from backend.core.redis_client import redis_get, redis_set

_CACHE_HITS = Counter("f1_cache_hits_total", "Cache hits", ["key_pattern"])
_CACHE_MISSES = Counter("f1_cache_misses_total", "Cache misses", ["key_pattern"])

# Matches a UUID (or any long hex/hyphen id) or a plain integer key segment.
_ID_SEGMENT = re.compile(r"^[0-9a-fA-F-]{8,}$|^\d+$")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _metric_label(key: str) -> str:
    """Collapse a Redis key's UUID/int segments to '*' for a bounded-cardinality label.

    Using the raw key (which embeds UUIDs) as a Prometheus label would create
    unbounded cardinality — one label value per driver/session ever cached.

    Args:
        key: A colon-delimited Redis key.
    Returns:
        The same key with every UUID-or-integer segment replaced by "*".
    """
    return ":".join("*" if _ID_SEGMENT.match(segment) else segment for segment in key.split(":"))


async def cache_get(client: aioredis.Redis, key: str) -> Any | None:  # type: ignore[type-arg]
    """Get a cached value, emitting a hit/miss Prometheus counter.

    Args:
        client: Redis client.
        key: Cache key (see CLAUDE.md's Redis Cache Key Schema).
    Returns:
        The cached value (JSON-deserialised if possible), or None on a miss.
    """
    value = await redis_get(client, key)
    label = _metric_label(key)
    if value is None:
        _CACHE_MISSES.labels(key_pattern=label).inc()
    else:
        _CACHE_HITS.labels(key_pattern=label).inc()
    return value


async def cache_set(
    client: aioredis.Redis,  # type: ignore[type-arg]
    key: str,
    value: Any,
    ttl: int | None = None,
) -> None:
    """Set a cached value with an optional TTL.

    Args:
        client: Redis client.
        key: Cache key.
        value: Value to cache (JSON-serialised unless already a string).
        ttl: Seconds before expiry, or None for no expiry.
    Returns:
        None.
    """
    await redis_set(client, key, value, ttl)


async def _delete_matching(client: aioredis.Redis, pattern: str) -> int:  # type: ignore[type-arg]
    """Delete every key matching a Redis glob pattern, batched via SCAN.

    Args:
        client: Redis client.
        pattern: Redis glob pattern (SCAN MATCH syntax — *, ?, [...]).
    Returns:
        Number of keys deleted.
    """
    keys = [key async for key in client.scan_iter(match=pattern)]
    if not keys:
        return 0
    deleted: int = await client.delete(*keys)
    return deleted


async def cache_invalidate_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
) -> int:
    """Delete every cache entry scoped to one race weekend (season + round).

    Args:
        client: Redis client.
        season: Season year.
        round_number: Round number within the season.
    Returns:
        Number of keys deleted, matching the f1:{season}:{round_number}:* key family
        (car:latest, strategy, gaps).
    """
    return await _delete_matching(client, f"f1:{season}:{round_number}:*")


async def cache_invalidate_driver(
    client: aioredis.Redis,  # type: ignore[type-arg]
    driver_id: uuid.UUID | str,
) -> int:
    """Delete every cache entry scoped to one driver (fingerprint + strategy predictions).

    car:latest keys are scoped by car number, not driver_id (see
    f1:{season}:{round}:car:{driver_num}:latest in CLAUDE.md's key schema), so they
    are not touched here.

    Args:
        client: Redis client.
        driver_id: Driver UUID.
    Returns:
        Total number of keys deleted across the fingerprint and strategy key families.
    """
    fingerprint_deleted = await _delete_matching(client, f"f1:driver:{driver_id}:*")
    strategy_deleted = await _delete_matching(client, f"f1:*:*:strategy:{driver_id}")
    return fingerprint_deleted + strategy_deleted


def cacheable(ttl: int, key_fn: Callable[..., str]) -> Callable[[F], F]:
    """Cache-aside decorator for async service methods.

    Convention: the decorated function's first positional argument must be the Redis
    client — the same dependency-injection convention used everywhere else in this
    codebase (see core/redis_client.py's get_redis()).

    Args:
        ttl: Seconds before the cached entry expires.
        key_fn: Builds the cache key from the decorated function's arguments (called
            with the same *args, **kwargs the function itself receives).
    Returns:
        Decorator that wraps an async function with cache-get-or-compute-and-set.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = args[0]
            key = key_fn(*args, **kwargs)
            cached = await cache_get(client, key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            await cache_set(client, key, result, ttl)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
