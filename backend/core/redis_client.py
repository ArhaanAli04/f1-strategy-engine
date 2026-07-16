import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_pool: aioredis.ConnectionPool | None = None  # type: ignore[type-arg]


def _get_pool() -> aioredis.ConnectionPool:  # type: ignore[type-arg]
    global _pool
    if _pool is None:
        from backend.core.config import get_redis_settings

        settings = get_redis_settings()
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            # A pub/sub subscription (see /ws/telemetry/{session_id}) pins a
            # dedicated connection from this pool for the entire WS
            # connection's lifetime, unlike a plain command which borrows and
            # returns one immediately. At the old default of 50 this capped
            # concurrent WS viewers per pod at ~50 regardless of API/worker
            # scaling — confirmed via tests/load/ws_load_test.py. 250 covers
            # the 200-connection load test with headroom for the REST routes'
            # per-request borrow/return traffic on top.
            max_connections=250,
        )
    return _pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:  # type: ignore[type-arg]
    """FastAPI dependency that yields a Redis client per request."""
    client: aioredis.Redis = aioredis.Redis(connection_pool=_get_pool())  # type: ignore[type-arg]
    try:
        yield client
    finally:
        await client.aclose()  # type: ignore[attr-defined]


async def redis_get(client: aioredis.Redis, key: str) -> Any | None:  # type: ignore[type-arg]
    """Get a value, deserialising JSON if possible."""
    value: str | None = await client.get(key)
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


async def redis_set(
    client: aioredis.Redis,  # type: ignore[type-arg]
    key: str,
    value: Any,
    ttl: int | None = None,
) -> None:
    """Set a value, serialising to JSON unless already a string."""
    serialized: str = value if isinstance(value, str) else json.dumps(value)
    if ttl is not None:
        await client.setex(key, ttl, serialized)
    else:
        await client.set(key, serialized)


async def redis_delete(client: aioredis.Redis, key: str) -> None:  # type: ignore[type-arg]
    await client.delete(key)


async def redis_expire(client: aioredis.Redis, key: str, ttl: int) -> None:  # type: ignore[type-arg]
    await client.expire(key, ttl)
