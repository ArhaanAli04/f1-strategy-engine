"""Circuit outline lookups.

See scripts/extract_circuit_outlines.py for how Circuit.map_geometry is
populated (Circuit Map Panel — CLAUDE.md's Planned Feature: Live Circuit Map).
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.exceptions import NotFoundError
from backend.models.race import Circuit
from backend.schemas.circuit_schema import CircuitOutlineResponse
from backend.services.cache_service import cacheable

# Outline geometry is static once extracted (same "infinite TTL, static data"
# bucket as f1:drivers:all) — see CLAUDE.md's Redis Cache Key Schema.
CIRCUIT_OUTLINE_TTL_SECONDS = None


def _key_circuit_outline(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    circuit_id: uuid.UUID,
) -> str:
    return f"f1:circuit:{circuit_id}:outline"


@cacheable(ttl=CIRCUIT_OUTLINE_TTL_SECONDS, key_fn=_key_circuit_outline)
async def _fetch_circuit_outline(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    circuit_id: uuid.UUID,
) -> dict[str, Any]:
    circuit = (
        await db.execute(select(Circuit).where(Circuit.id == circuit_id))
    ).scalar_one_or_none()
    if circuit is None:
        raise NotFoundError(f"Circuit {circuit_id} not found")
    if circuit.map_geometry is None:
        raise NotFoundError(
            f"Circuit {circuit_id} has no outline yet — run scripts/extract_circuit_outlines.py"
        )

    geometry = circuit.map_geometry
    return CircuitOutlineResponse(
        circuit_id=circuit.id,
        viewbox=geometry["viewbox"],
        points=geometry["points"],
        corners=geometry.get("corners", []),
        source=geometry.get("source"),
        transform=geometry.get("transform"),
    ).model_dump(mode="json")


async def get_circuit_outline(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    circuit_id: uuid.UUID,
) -> CircuitOutlineResponse:
    """Fetch a circuit's extracted track outline geometry.

    Args:
        client: Redis client (cache-aside, forwarded to _fetch_circuit_outline).
        db: Async DB session.
        circuit_id: Circuit to fetch.
    Returns:
        The circuit's outline (SVG viewBox + normalized point polyline).
    Raises:
        NotFoundError: No circuit with this ID exists, or its outline hasn't
            been extracted yet (Circuit.map_geometry is null) — not cached,
            since this is expected to be transient (until the extraction
            script runs once for this circuit).
    """
    data = await _fetch_circuit_outline(client, db, circuit_id)
    return CircuitOutlineResponse.model_validate(data)
