"""Driver listing, style-fingerprint analysis, and paginated lap history.

get_driver_analysis uses a two-tier Redis cache, since services/ml/driver_style.py's
PCA(4)->KMeans(5)->UMAP(2D) pipeline fits over an entire season's driver population,
not one driver at a time:

- f1:driver_style:fit:{season} (new key, TTL 3600s — not yet in CLAUDE.md's Redis
  schema table, added here per Day 10 discussion) holds every driver's cluster
  assignment for that season. The first request for any driver in a season pays
  the fit cost once; every other driver that season hits this cache instead of
  triggering a refit.
- f1:driver:{driver_id}:fingerprint (existing key in CLAUDE.md's schema, same TTL)
  is checked first as a fast path, and is populated as a side effect of a
  population fit — every driver in the fitted population gets its own entry
  written at once, not just the one that was requested.

Both caches store only the season-level style profile (archetype/cluster/UMAP).
performance_vs_team_avg_seconds is session-relative, not season-relative, so it
is always computed fresh — caching it under either key would return a stale
number for a different session_id on the next request.

"Performance vs team average" is defined as this driver's mean valid lap time in
the session minus the mean valid lap time across all of that driver's season
teammates in the same session — the only performance signal the current schema
supports (no results/points table exists; see CLAUDE.md's data model).
"""

from __future__ import annotations

import uuid
from typing import Any

import pandas as pd
import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.exceptions import NotFoundError
from backend.models.driver import Driver, DriverContract
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint
from backend.schemas.common import PaginatedResponse
from backend.schemas.driver_schema import DriverAnalysisResponse, DriverResponse
from backend.schemas.telemetry_schema import LapDataResponse
from backend.services.cache_service import cache_get, cache_set
from backend.services.ml.driver_style import build_driver_style_features, fit_driver_style_clusters

DRIVER_STYLE_FIT_TTL_SECONDS = 3600
DRIVER_FINGERPRINT_TTL_SECONDS = 3600
DEFAULT_PAGE_SIZE = 20


def _fingerprint_key(driver_id: uuid.UUID | str) -> str:
    return f"f1:driver:{driver_id}:fingerprint"


def _population_fit_key(season: int) -> str:
    return f"f1:driver_style:fit:{season}"


async def get_drivers(db: AsyncSession) -> list[DriverResponse]:
    """List all drivers with their team contract history.

    Args:
        db: Async DB session.
    Returns:
        Every driver, contracts (and each contract's team) eagerly loaded.
    """
    query = (
        select(Driver)
        .options(selectinload(Driver.contracts).selectinload(DriverContract.team))
        .order_by(Driver.full_name)
    )
    rows = (await db.execute(query)).scalars().all()
    return [DriverResponse.model_validate(d) for d in rows]


async def _resolve_season(db: AsyncSession, session_id: uuid.UUID) -> int:
    query = (
        select(Race.season)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    season = (await db.execute(query)).scalar_one_or_none()
    if season is None:
        raise NotFoundError(f"Session {session_id} not found")
    return int(season)


async def _fit_population(db: AsyncSession, season: int) -> list[dict[str, Any]]:
    """Fit driver-style clusters for every driver with data in a season.

    Args:
        db: Async DB session.
        season: Season year to fit over.
    Returns:
        One JSON-serialisable dict per driver-season assignment.
    """
    laps_query = (
        select(
            LapData.driver_id,
            LapData.session_id,
            LapData.lap_number,
            LapData.sector1_seconds,
            LapData.sector2_seconds,
            LapData.sector3_seconds,
            LapData.lap_time_seconds,
            LapData.is_valid,
        )
        .join(SessionModel, LapData.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .where(Race.season == season)
    )
    laps_rows = (await db.execute(laps_query)).all()
    laps_df = pd.DataFrame(
        laps_rows,
        columns=[
            "driver_id",
            "session_id",
            "lap_number",
            "sector1_seconds",
            "sector2_seconds",
            "sector3_seconds",
            "lap_time_seconds",
            "is_valid",
        ],
    )
    laps_df["season"] = season

    stints_query = (
        select(
            TireStint.driver_id,
            TireStint.session_id,
            TireStint.compound,
            TireStint.avg_deg_per_lap,
            TireStint.start_lap,
            TireStint.end_lap,
        )
        .join(SessionModel, TireStint.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .where(Race.season == season)
    )
    stints_rows = (await db.execute(stints_query)).all()
    stints_df = pd.DataFrame(
        stints_rows,
        columns=["driver_id", "session_id", "compound", "avg_deg_per_lap", "start_lap", "end_lap"],
    )
    stints_df["season"] = season

    features = build_driver_style_features(laps_df, stints_df)
    if features.empty:
        raise NotFoundError(
            f"Not enough lap/stint data to build driver-style profiles for season {season}"
        )

    fitted = fit_driver_style_clusters(features)
    return [
        {
            "driver_id": str(row["driver_id"]),
            "season": int(row["season"]),
            "archetype": row["archetype"],
            "cluster": int(row["cluster"]),
            "sector_time_variance": float(row["sector_time_variance"]),
            "tyre_management_index": float(row["tyre_management_index"]),
            "lap_time_consistency": float(row["lap_time_consistency"]),
            "stint_length_tendency": float(row["stint_length_tendency"]),
            "umap_x": float(row["umap_x"]),
            "umap_y": float(row["umap_y"]),
        }
        for _, row in fitted.assignments.iterrows()
    ]


async def _performance_vs_team_avg(
    db: AsyncSession, driver_id: uuid.UUID, season: int, session_id: uuid.UUID
) -> float | None:
    """This driver's mean valid lap time minus their season teammates' mean, same session.

    Args:
        db: Async DB session.
        driver_id: Driver to compare.
        season: Season the team contract is looked up for.
        session_id: Session the lap times are drawn from.
    Returns:
        Seconds relative to team average (negative = faster than teammates),
        or None if the driver has no team contract or no lap data this session.
    """
    team_id = (
        await db.execute(
            select(DriverContract.team_id).where(
                DriverContract.driver_id == driver_id, DriverContract.season == season
            )
        )
    ).scalar_one_or_none()
    if team_id is None:
        return None

    team_driver_ids = (
        (
            await db.execute(
                select(DriverContract.driver_id).where(
                    DriverContract.team_id == team_id, DriverContract.season == season
                )
            )
        )
        .scalars()
        .all()
    )
    if not team_driver_ids:
        return None

    avg_query = (
        select(LapData.driver_id, func.avg(LapData.lap_time_seconds))
        .where(
            LapData.session_id == session_id,
            LapData.driver_id.in_(team_driver_ids),
            LapData.is_valid.is_(True),
            LapData.lap_time_seconds.is_not(None),
        )
        .group_by(LapData.driver_id)
    )
    averages: dict[uuid.UUID, float] = {
        row[0]: row[1] for row in (await db.execute(avg_query)).all()
    }
    driver_avg = averages.get(driver_id)
    if driver_avg is None or not averages:
        return None

    team_avg = sum(averages.values()) / len(averages)
    return float(driver_avg) - float(team_avg)


async def get_driver_analysis(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    driver_id: uuid.UUID,
    session_id: uuid.UUID,
) -> DriverAnalysisResponse:
    """Driver-style fingerprint plus session-relative performance vs teammates.

    Args:
        db: Async DB session.
        redis_client: Redis client for the two-tier style-fit cache.
        driver_id: Driver to analyse.
        session_id: Session used to resolve the season and to compute
            performance_vs_team_avg_seconds.
    Returns:
        Archetype, cluster, raw style features, UMAP coordinates, and
        session-relative performance vs team average.
    Raises:
        NotFoundError: If the session doesn't exist, the season has too little
            data to fit style clusters, or this driver has no assignment in it.
    """
    season = await _resolve_season(db, session_id)

    style = await cache_get(redis_client, _fingerprint_key(driver_id))
    if not isinstance(style, dict) or style.get("season") != season:
        population_key = _population_fit_key(season)
        population = await cache_get(redis_client, population_key)
        if population is None:
            population = await _fit_population(db, season)
            await cache_set(
                redis_client, population_key, population, ttl=DRIVER_STYLE_FIT_TTL_SECONDS
            )
            for row in population:
                await cache_set(
                    redis_client,
                    _fingerprint_key(row["driver_id"]),
                    row,
                    ttl=DRIVER_FINGERPRINT_TTL_SECONDS,
                )

        style = next((row for row in population if row["driver_id"] == str(driver_id)), None)
        if style is None:
            raise NotFoundError(
                f"No driver-style profile for driver {driver_id} in season {season} "
                "(insufficient lap/stint data)"
            )

    performance = await _performance_vs_team_avg(db, driver_id, season, session_id)

    return DriverAnalysisResponse(
        driver_id=driver_id,
        season=season,
        archetype=style["archetype"],
        cluster=style["cluster"],
        sector_time_variance=style["sector_time_variance"],
        tyre_management_index=style["tyre_management_index"],
        lap_time_consistency=style["lap_time_consistency"],
        stint_length_tendency=style["stint_length_tendency"],
        umap_x=style["umap_x"],
        umap_y=style["umap_y"],
        performance_vs_team_avg_seconds=performance,
    )


async def get_driver_laps(
    db: AsyncSession,
    driver_id: uuid.UUID,
    session_id: uuid.UUID,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[LapDataResponse]:
    """Paginated lap history for one driver in one session.

    Args:
        db: Async DB session.
        driver_id: Driver whose laps to fetch.
        session_id: Session to scope laps to.
        page: 1-indexed page number.
        page_size: Rows per page.
    Returns:
        Laps ordered by lap number, each including its tire compound (for
        client-side tire-compound coloring) and sector times.
    """
    filters = (LapData.driver_id == driver_id, LapData.session_id == session_id)

    total = (
        await db.execute(select(func.count()).select_from(LapData).where(*filters))
    ).scalar_one()

    query = (
        select(LapData)
        .options(selectinload(LapData.sector_times))
        .where(*filters)
        .order_by(LapData.lap_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()

    return PaginatedResponse(
        items=[LapDataResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
