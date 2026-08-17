"""Race, session, and current-race lookups.

get_current_race resolves "the currently active/upcoming race" via
fastf1.ergast.Ergast().get_race_schedule(season) — the same dead-Ergast-API
replacement scripts/ingest_live_session.py's _find_upcoming_session already
uses (raw HTTP to ergast.com has been dead since 2024). Ergast only knows
season+round, not our internal UUIDs, so the resolved (season, round) is
matched against our own races table to build the response. If that season/
round hasn't been ingested yet, this raises NotFoundError rather than
fabricating a response FastF1 UUIDs can't back — same "don't paper over a
real data gap" precedent as strategy_service.py's model-loading errors.

Day 13 caching pass: every public function here now takes a Redis client as
its first positional argument (matching strategy_service.py/telemetry_service.py's
convention) and delegates to a private `_fetch_*` function wrapped in
cache_service.cacheable. The cached function returns a JSON-serialisable dict
(`.model_dump(mode="json")`) rather than the Pydantic response model itself,
since cache_set/redis_set round-trip through json.dumps — the public function
reconstructs the response schema from that dict on both cache hit and miss.
Same split already used by strategy_service.get_optimal_pit_window (cached,
raw) vs. get_pit_window_with_explanation (uncached wrapper).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import LockNotOwnedError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.exceptions import NotFoundError
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.schemas.common import PaginatedResponse
from backend.schemas.race_schema import (
    RaceListResponse,
    RaceResponse,
    SessionResponse,
    UpcomingRaceResponse,
)

# get_or_create_circuit/get_or_create_session/resolve_scheduled_start are pure
# DB-upsert/data-mapping helpers shared with the ingest scripts (see
# scripts/_ingest_common.py's own docstring) — not a services-importing-
# services violation (CLAUDE.md's rule targets cross-service business-logic
# coupling), just reuse of the same circuit-name mapping and session-time
# resolution get_upcoming_race's FastF1-schedule fallback below needs.
from backend.scripts._ingest_common import (
    RoundSkippedError,
    get_or_create_circuit,
    get_or_create_session,
    resolve_scheduled_start,
)
from backend.services.cache_service import cache_get, cache_lock, cache_set, cacheable

DEFAULT_PAGE_SIZE = 20

# Race/session metadata is immutable once ingested (a 2023 race never changes),
# so this is the "historical race/lap data" TTL bucket from CLAUDE.md's cache
# key schema, not the "static data, infinite TTL" bucket (that's reserved for
# circuits/drivers — see driver_service.get_drivers).
RACE_DETAIL_TTL_SECONDS = 86400
SESSION_DETAIL_TTL_SECONDS = 86400
RACES_LIST_TTL_SECONDS = 86400
# Short TTL, not one of CLAUDE.md's 4 documented buckets: get_current_race
# calls the external Ergast API on every miss, which every race-day viewer
# hits — this insulates that external dependency from request volume while
# still noticing a new race weekend same-day.
CURRENT_RACE_TTL_SECONDS = 300
# Shorter TTL for the "no current race" outcome specifically (see
# get_current_race) — noticing a newly-ingested season/round sooner than a
# full 300s matters more when we know we're currently in a gap.
CURRENT_RACE_NOT_FOUND_TTL_SECONDS = 60


def _key_races_list(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int | None,
    round_number: int | None,
    page: int,
    page_size: int,
) -> str:
    season_segment = season if season is not None else "any"
    round_segment = round_number if round_number is not None else "any"
    return f"f1:races:list:{season_segment}:{round_segment}:{page}:{page_size}"


@cacheable(ttl=RACES_LIST_TTL_SECONDS, key_fn=_key_races_list)
async def _fetch_races(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int | None,
    round_number: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    filters = []
    if season is not None:
        filters.append(Race.season == season)
    if round_number is not None:
        filters.append(Race.round_number == round_number)

    count_query = select(func.count()).select_from(Race).where(*filters)
    total = (await db.execute(count_query)).scalar_one()

    query = (
        select(Race)
        .options(selectinload(Race.circuit))
        .where(*filters)
        .order_by(Race.race_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()

    return PaginatedResponse(
        items=[RaceListResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump(mode="json")


async def get_races(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int | None = None,
    round_number: int | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[RaceListResponse]:
    """List races, optionally filtered by season/round, newest first.

    Args:
        client: Redis client (cache-aside, forwarded to _fetch_races).
        db: Async DB session.
        season: Optional season year filter.
        round_number: Optional round-within-season filter.
        page: 1-indexed page number.
        page_size: Rows per page.
    Returns:
        Paginated race list with each race's circuit nested.
    """
    data = await _fetch_races(client, db, season, round_number, page, page_size)
    return PaginatedResponse[RaceListResponse].model_validate(data)


def _key_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
) -> str:
    return f"f1:race:{race_id}:detail"


@cacheable(ttl=RACE_DETAIL_TTL_SECONDS, key_fn=_key_race)
async def _fetch_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
) -> dict[str, Any]:
    query = (
        select(Race)
        .options(selectinload(Race.circuit), selectinload(Race.sessions))
        .where(Race.id == race_id)
    )
    race = (await db.execute(query)).scalar_one_or_none()
    if race is None:
        raise NotFoundError(f"Race {race_id} not found")
    return RaceResponse.model_validate(race).model_dump(mode="json")


async def get_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
) -> RaceResponse:
    """Fetch a single race with its circuit and sessions.

    Args:
        client: Redis client (cache-aside, forwarded to _fetch_race).
        db: Async DB session.
        race_id: Race to fetch.
    Returns:
        The race.
    Raises:
        NotFoundError: If no race with this ID exists.
    """
    data = await _fetch_race(client, db, race_id)
    return RaceResponse.model_validate(data)


def _key_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
    session_id: uuid.UUID,
) -> str:
    return f"f1:race:{race_id}:session:{session_id}:detail"


@cacheable(ttl=SESSION_DETAIL_TTL_SECONDS, key_fn=_key_session)
async def _fetch_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    query = select(SessionModel).where(
        SessionModel.id == session_id, SessionModel.race_id == race_id
    )
    session = (await db.execute(query)).scalar_one_or_none()
    if session is None:
        raise NotFoundError(f"Session {session_id} not found for race {race_id}")
    return SessionResponse.model_validate(session).model_dump(mode="json")


async def get_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    race_id: uuid.UUID,
    session_id: uuid.UUID,
) -> SessionResponse:
    """Fetch a single session, scoped to its parent race.

    Args:
        client: Redis client (cache-aside, forwarded to _fetch_session).
        db: Async DB session.
        race_id: Parent race.
        session_id: Session to fetch.
    Returns:
        The session.
    Raises:
        NotFoundError: If no session with this ID exists under this race.
    """
    data = await _fetch_session(client, db, race_id, session_id)
    return SessionResponse.model_validate(data)


def _key_current_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> str:
    return f"f1:current_race:{datetime.now(UTC).year}"


async def _fetch_current_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> dict[str, Any]:
    from fastf1.ergast import Ergast

    today = datetime.now(UTC).date()
    season = today.year
    schedule = Ergast().get_race_schedule(season)
    if schedule.empty:
        raise NotFoundError(f"Ergast has no race schedule for season {season}")

    target_round: int | None = None
    for _, row in schedule.iterrows():
        race_date = row["raceDate"]
        if hasattr(race_date, "date"):
            race_date = race_date.date()
        if race_date >= today:
            target_round = int(row["round"])
            break

    if target_round is None:
        target_round = int(schedule.iloc[-1]["round"])

    query = (
        select(Race)
        .options(selectinload(Race.circuit), selectinload(Race.sessions))
        .where(Race.season == season, Race.round_number == target_round)
    )
    race = (await db.execute(query)).scalar_one_or_none()
    if race is None:
        raise NotFoundError(
            f"Season {season} round {target_round} is Ergast's current race "
            "but hasn't been ingested yet"
        )
    return RaceResponse.model_validate(race).model_dump(mode="json")


_NOT_FOUND_SENTINEL_FIELD = "_not_found"


async def _read_current_race_cache(
    client: aioredis.Redis,  # type: ignore[type-arg]
    key: str,
) -> RaceResponse | None:
    """Read and interpret a cached get_current_race outcome, if present.

    Args:
        client: Redis client.
        key: Cache key from _key_current_race.
    Returns:
        The cached race, or None on a cache miss.
    Raises:
        NotFoundError: If the cached outcome is the "no current race" sentinel.
    """
    cached = await cache_get(client, key)
    if cached is None:
        return None
    if cached.get(_NOT_FOUND_SENTINEL_FIELD):
        raise NotFoundError(cached["reason"])
    return RaceResponse.model_validate(cached)


async def _fetch_and_cache_current_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    key: str,
) -> RaceResponse:
    """Call Ergast, cache the outcome under whichever TTL bucket applies, and return/raise.

    Args:
        client: Redis client.
        db: Async DB session.
        key: Cache key from _key_current_race.
    Returns:
        The current race.
    Raises:
        NotFoundError: If Ergast has no schedule for the current season, or the
            resolved season/round hasn't been ingested — cached at the shorter
            "not found" TTL.
    """
    try:
        data = await _fetch_current_race(client, db)
    except NotFoundError as exc:
        await cache_set(
            client,
            key,
            {_NOT_FOUND_SENTINEL_FIELD: True, "reason": str(exc)},
            CURRENT_RACE_NOT_FOUND_TTL_SECONDS,
        )
        raise

    await cache_set(client, key, data, CURRENT_RACE_TTL_SECONDS)
    return RaceResponse.model_validate(data)


async def get_current_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> RaceResponse:
    """Resolve the currently active/upcoming race via Ergast's schedule.

    Hand-rolled cache-aside rather than @cacheable: unlike every other
    cached lookup in this codebase, this one needs two different TTLs for
    the same key depending on outcome (a real race vs. "no current race"),
    and @cacheable only ever writes to cache on a successful return —
    a raised NotFoundError previously skipped caching entirely, so *every*
    request paid the full external Ergast round trip whenever the current
    season/round hadn't been ingested yet (confirmed as the root cause of
    /races/current's p50=13s during Day 13 load testing: all 34 requests in
    a 2-minute run hit Ergast fresh, since nothing was ever cached either
    way). The "not found" case is now cached too, just at a shorter TTL, so
    only the first request (or first few racing concurrently) pays that cost
    per CURRENT_RACE_NOT_FOUND_TTL_SECONDS window.

    Single-flight (pre-Day-14 fix): the Day 13 re-run still showed no
    improvement (p50 still 13s) because locustfile.py's RaceDayViewerUser
    calls this exactly once, in on_start(), and Locust ramps all simulated
    users up within ~10s — every one of them hits the cold cache key in that
    same burst, before any single one finishes its own ~13s Ergast call and
    populates the cache for the others. Same stampede shape cache_service.
    cacheable() already guards against, just on this hand-rolled path. Uses
    cache_service.cache_lock (same tuning as cacheable) rather than
    re-wrapping in @cacheable, since the two-TTL split still needs to stay
    hand-rolled.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
    Returns:
        The current season's next race whose date hasn't passed yet, or the
        season's final race if the season has already concluded.
    Raises:
        NotFoundError: If Ergast has no schedule for the current season, or
            the resolved season/round hasn't been ingested into our races table.
    """
    key = _key_current_race(client, db)
    cached = await _read_current_race_cache(client, key)
    if cached is not None:
        return cached

    lock = cache_lock(client, key)
    acquired = await lock.acquire()
    if not acquired:
        # Didn't win the lock within blocking_timeout — the holder is
        # unusually slow or died without releasing. Fall back to computing
        # independently rather than blocking the request indefinitely;
        # re-check the cache first in case it was populated in the interim.
        cached = await _read_current_race_cache(client, key)
        if cached is not None:
            return cached
        return await _fetch_and_cache_current_race(client, db, key)

    try:
        # Re-check: another caller may have populated the cache between our
        # first miss and winning the lock.
        cached = await _read_current_race_cache(client, key)
        if cached is not None:
            return cached
        return await _fetch_and_cache_current_race(client, db, key)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            # Our own timeout already expired and another caller took over
            # ownership — nothing left for us to release.
            pass


# --- Upcoming race (self-updating: DB first, FastF1-schedule fallback + cache) ---

# Same TTL bucket as CURRENT_RACE_TTL_SECONDS/CURRENT_RACE_NOT_FOUND_TTL_SECONDS
# above and for the same reason — the FastF1-schedule fallback path is an
# external call every cold miss would otherwise pay.
UPCOMING_RACE_TTL_SECONDS = 300
UPCOMING_RACE_NOT_FOUND_TTL_SECONDS = 60


def _key_upcoming_race() -> str:
    return f"f1:races:upcoming:{datetime.now(UTC).year}"


async def _fetch_upcoming_race_from_db(db: AsyncSession, season: int, today: date) -> Race | None:
    query = (
        select(Race)
        .options(selectinload(Race.circuit), selectinload(Race.sessions))
        .where(Race.season == season, Race.race_date >= today)
        .order_by(Race.race_date.asc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


def _race_session_scheduled_start(race: Race, session_type: str) -> datetime | None:
    for session in race.sessions:
        if session.session_type == session_type:
            return session.scheduled_start
    return None


def _to_upcoming_race_response(race: Race) -> dict[str, Any]:
    return UpcomingRaceResponse(
        id=race.id,
        season=race.season,
        round_number=race.round_number,
        race_name=race.event_name,
        circuit_id=race.circuit_id,
        race_date=race.race_date,
        scheduled_start=_race_session_scheduled_start(race, "R"),
    ).model_dump(mode="json")


async def _fetch_upcoming_race_from_fastf1(
    db: AsyncSession, season: int, today: date
) -> dict[str, Any]:
    """Self-updating fallback: pull the season schedule directly from FastF1 and
    persist it, so a brand-new season needs no manual seed script before this
    endpoint works. scripts/seed_race_schedule.py (optional, manual) can still
    pre-populate a season's schedule ahead of time — see CLAUDE.md.

    Args:
        db: Async DB session.
        season, today: Same as get_upcoming_race — passed through rather than
            recomputed, so a single "now" is used across the whole lookup.
    Returns:
        JSON-serialisable dict matching UpcomingRaceResponse.
    Raises:
        NotFoundError: FastF1 has no remaining events for this season, or the
            resolved event's circuit can't be resolved (unmapped/unseeded —
            same "don't paper over a real data gap" precedent as
            _fetch_current_race).
    """
    from fastf1 import get_event_schedule

    schedule = get_event_schedule(season, include_testing=False)
    upcoming = schedule[schedule["EventDate"].dt.date >= today].sort_values("EventDate")
    if upcoming.empty:
        raise NotFoundError(f"No upcoming race found for season {season}")
    event = upcoming.iloc[0]

    try:
        circuit = await get_or_create_circuit(db, event["Location"])
    except RoundSkippedError as exc:
        raise NotFoundError(
            f"Season {season} round {int(event['RoundNumber'])} resolved from FastF1's "
            f"schedule but its circuit could not be resolved: {exc}"
        ) from exc

    race = Race(
        id=uuid.uuid4(),
        season=season,
        round_number=int(event["RoundNumber"]),
        circuit_id=circuit.id,
        race_date=event["EventDate"].date(),
        status="scheduled",
        event_name=event["EventName"],
    )
    db.add(race)
    await db.flush()

    for session_type in ("FP1", "FP2", "FP3", "Q", "R"):
        await get_or_create_session(
            db,
            race_id=race.id,
            session_type=session_type,
            session_date=event["EventDate"].date(),
            scheduled_start=resolve_scheduled_start(event, session_type),
        )
    await db.commit()

    await db.refresh(race, attribute_names=["circuit", "sessions"])
    return _to_upcoming_race_response(race)


async def _read_upcoming_race_cache(
    client: aioredis.Redis,  # type: ignore[type-arg]
    key: str,
) -> UpcomingRaceResponse | None:
    """Read and interpret a cached get_upcoming_race outcome, if present.

    Same two-outcome cache shape as _read_current_race_cache — see that
    function's docstring.
    """
    cached = await cache_get(client, key)
    if cached is None:
        return None
    if cached.get(_NOT_FOUND_SENTINEL_FIELD):
        raise NotFoundError(cached["reason"])
    return UpcomingRaceResponse.model_validate(cached)


async def _fetch_and_cache_upcoming_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    key: str,
) -> UpcomingRaceResponse:
    today = datetime.now(UTC).date()
    season = today.year
    try:
        race = await _fetch_upcoming_race_from_db(db, season, today)
        data = (
            _to_upcoming_race_response(race)
            if race is not None
            else await _fetch_upcoming_race_from_fastf1(db, season, today)
        )
    except NotFoundError as exc:
        await cache_set(
            client,
            key,
            {_NOT_FOUND_SENTINEL_FIELD: True, "reason": str(exc)},
            UPCOMING_RACE_NOT_FOUND_TTL_SECONDS,
        )
        raise

    await cache_set(client, key, data, UPCOMING_RACE_TTL_SECONDS)
    return UpcomingRaceResponse.model_validate(data)


async def get_upcoming_race(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> UpcomingRaceResponse:
    """Resolve the next race after today in the current year — DB first, FastF1-schedule fallback.

    Checks the races table for the earliest race_date >= today in
    datetime.now(UTC).year first (fast, no external call). If nothing is
    ingested yet for the current year (e.g. a brand-new season before any
    ingestion has run), falls back to FastF1's own event schedule
    (fastf1.get_event_schedule) and persists what it finds — circuit,
    race, and all 5 sessions — so this is self-updating season over season
    with no manual seed script required (scripts/seed_race_schedule.py
    exists only as an optional, manual pre-population convenience).

    Single-flight (same rationale as get_current_race): the FastF1-schedule
    fallback is an external call every cold miss would otherwise pay, and a
    burst of concurrent requests hitting a cold cache key would otherwise
    each try to insert the same race independently.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
    Returns:
        The next race after today in the current year.
    Raises:
        NotFoundError: No race found for the current year in the DB, and
            FastF1's schedule has no remaining events for the current year
            either (season concluded), or the resolved event's circuit
            can't be resolved.
    """
    key = _key_upcoming_race()
    cached = await _read_upcoming_race_cache(client, key)
    if cached is not None:
        return cached

    lock = cache_lock(client, key)
    acquired = await lock.acquire()
    if not acquired:
        cached = await _read_upcoming_race_cache(client, key)
        if cached is not None:
            return cached
        return await _fetch_and_cache_upcoming_race(client, db, key)

    try:
        cached = await _read_upcoming_race_cache(client, key)
        if cached is not None:
            return cached
        return await _fetch_and_cache_upcoming_race(client, db, key)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass
