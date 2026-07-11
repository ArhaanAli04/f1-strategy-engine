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
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.exceptions import NotFoundError
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.schemas.common import PaginatedResponse
from backend.schemas.race_schema import RaceListResponse, RaceResponse, SessionResponse

DEFAULT_PAGE_SIZE = 20


async def get_races(
    db: AsyncSession,
    season: int | None = None,
    round_number: int | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[RaceListResponse]:
    """List races, optionally filtered by season/round, newest first.

    Args:
        db: Async DB session.
        season: Optional season year filter.
        round_number: Optional round-within-season filter.
        page: 1-indexed page number.
        page_size: Rows per page.
    Returns:
        Paginated race list with each race's circuit nested.
    """
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
        .order_by(Race.season.desc(), Race.round_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()

    return PaginatedResponse(
        items=[RaceListResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_race(db: AsyncSession, race_id: uuid.UUID) -> RaceResponse:
    """Fetch a single race with its circuit and sessions.

    Args:
        db: Async DB session.
        race_id: Race to fetch.
    Returns:
        The race.
    Raises:
        NotFoundError: If no race with this ID exists.
    """
    query = (
        select(Race)
        .options(selectinload(Race.circuit), selectinload(Race.sessions))
        .where(Race.id == race_id)
    )
    race = (await db.execute(query)).scalar_one_or_none()
    if race is None:
        raise NotFoundError(f"Race {race_id} not found")
    return RaceResponse.model_validate(race)


async def get_session(
    db: AsyncSession, race_id: uuid.UUID, session_id: uuid.UUID
) -> SessionResponse:
    """Fetch a single session, scoped to its parent race.

    Args:
        db: Async DB session.
        race_id: Parent race.
        session_id: Session to fetch.
    Returns:
        The session.
    Raises:
        NotFoundError: If no session with this ID exists under this race.
    """
    query = select(SessionModel).where(
        SessionModel.id == session_id, SessionModel.race_id == race_id
    )
    session = (await db.execute(query)).scalar_one_or_none()
    if session is None:
        raise NotFoundError(f"Session {session_id} not found for race {race_id}")
    return SessionResponse.model_validate(session)


async def get_current_race(db: AsyncSession) -> RaceResponse:
    """Resolve the currently active/upcoming race via Ergast's schedule.

    Args:
        db: Async DB session.
    Returns:
        The current season's next race whose date hasn't passed yet, or the
        season's final race if the season has already concluded.
    Raises:
        NotFoundError: If Ergast has no schedule for the current season, or
            the resolved season/round hasn't been ingested into our races table.
    """
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
    return RaceResponse.model_validate(race)
