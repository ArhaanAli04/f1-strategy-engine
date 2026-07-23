"""Unit tests for services/driver_service.py.

_stub_cache_lock stubs cache_service.cache_lock — @cacheable's internal
single-flight lock lives in cache_service.py, so patching it there covers every
@cacheable-decorated function here (_fetch_drivers, _fetch_driver_laps). Same
no-op pattern test_strategy_service.py established Day 14: fakeredis has no
Lua/EVALSHA support, which redis-py's real Lock needs to release().
"""

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import numpy as np
import pytest

from backend.core.exceptions import NotFoundError
from backend.services import cache_service, driver_service


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())


def _scalar_one_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _scalar_one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_all_result(items: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _fake_driver_with_contract() -> SimpleNamespace:
    team = SimpleNamespace(
        id=uuid.uuid4(), name="Team A", constructor_id="team_a", color_hex="#000000"
    )
    driver_id = uuid.uuid4()
    contract = SimpleNamespace(
        id=uuid.uuid4(), driver_id=driver_id, team_id=team.id, season=2026, team=team
    )
    return SimpleNamespace(
        id=driver_id,
        code="VER",
        full_name="Max Verstappen",
        nationality="NED",
        date_of_birth=date(1997, 9, 30),
        contracts=[contract],
    )


def _fake_lap(driver_id: uuid.UUID, session_id: uuid.UUID, lap_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        session_id=session_id,
        driver_id=driver_id,
        lap_number=lap_number,
        lap_time_seconds=90.0,
        compound="MEDIUM",
        tyre_age_laps=lap_number,
        is_valid=True,
        sector1_seconds=28.0,
        sector2_seconds=34.0,
        sector3_seconds=28.0,
        created_at=date(2026, 7, 20),
        sector_times=[],
    )


def _rows_result(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _synthetic_query_rows(season: int, n_drivers: int = 6, n_laps: int = 10) -> Any:
    """Row tuples in the exact column order _fit_population's laps_query/stints_query select.

    laps_query: driver_id, session_id, lap_number, sector1_seconds, sector2_seconds,
    sector3_seconds, lap_time_seconds, is_valid.
    stints_query: driver_id, session_id, compound, avg_deg_per_lap, start_lap, end_lap.
    """
    rng = np.random.default_rng(11)
    lap_rows = []
    stint_rows = []
    for i in range(n_drivers):
        driver_id = uuid.uuid4()
        session_id = uuid.uuid4()
        noise = 0.05 + i * 0.02
        base_s1, base_s2, base_s3 = 28.0 + i * 0.3, 34.0 + i * 0.2, 27.0 + i * 0.25
        for lap_number in range(1, n_laps + 1):
            s1 = base_s1 + rng.normal(0, noise)
            s2 = base_s2 + rng.normal(0, noise)
            s3 = base_s3 + rng.normal(0, noise)
            lap_rows.append((driver_id, session_id, lap_number, s1, s2, s3, s1 + s2 + s3, True))
        stint_rows.append(
            (driver_id, session_id, "MEDIUM", 0.05 + i * 0.03 + rng.normal(0, 0.005), 1, n_laps)
        )
    return lap_rows, stint_rows


def _population_row(driver_id: uuid.UUID, season: int) -> dict[str, Any]:
    return {
        "driver_id": str(driver_id),
        "season": season,
        "archetype": "aggressive",
        "cluster": 0,
        "sector_time_variance": 0.1,
        "tyre_management_index": 0.2,
        "lap_time_consistency": 0.3,
        "stint_length_tendency": 10.0,
        "umap_x": 1.0,
        "umap_y": 2.0,
    }


@pytest.mark.unit
async def test_get_drivers_returns_all_with_contracts(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    driver = _fake_driver_with_contract()
    mock_db_session.execute.return_value = _scalars_all_result([driver])

    result = await driver_service.get_drivers(fakeredis, mock_db_session)

    assert len(result) == 1
    assert len(result[0].contracts) == 1
    assert result[0].contracts[0].team is not None
    assert result[0].contracts[0].team.name == "Team A"


@pytest.mark.unit
async def test_get_driver_laps_paginates_correctly(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    driver_id = uuid.uuid4()
    session_id = uuid.uuid4()
    laps = [_fake_lap(driver_id, session_id, lap_number) for lap_number in range(1, 3)]
    mock_db_session.execute.side_effect = [
        _scalar_one_result(7),
        _scalars_all_result(laps),
    ]

    result = await driver_service.get_driver_laps(
        fakeredis, mock_db_session, driver_id, session_id, page=1, page_size=2
    )

    assert result.total == 7
    assert result.page_size == 2
    assert len(result.items) == 2


@pytest.mark.unit
async def test_driver_analysis_uses_population_cache(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    season = 2026
    session_id = uuid.uuid4()
    driver_a = uuid.uuid4()
    driver_b = uuid.uuid4()
    population = [_population_row(driver_a, season), _population_row(driver_b, season)]

    fit_mock = AsyncMock(return_value=population)
    monkeypatch.setattr(driver_service, "_fit_population", fit_mock)

    mock_db_session.execute.side_effect = [
        _scalar_one_or_none_result(season),  # _resolve_season, call 1
        _scalar_one_or_none_result(None),  # team_id lookup, call 1 (no team -> perf is None)
        _scalar_one_or_none_result(season),  # _resolve_season, call 2
        _scalar_one_or_none_result(None),  # team_id lookup, call 2
    ]

    first = await driver_service.get_driver_analysis(
        mock_db_session, fakeredis, driver_a, session_id
    )
    second = await driver_service.get_driver_analysis(
        mock_db_session, fakeredis, driver_b, session_id
    )

    assert first.archetype == "aggressive"
    assert second.driver_id == driver_b
    fit_mock.assert_awaited_once()


@pytest.mark.unit
async def test_unknown_driver_raises_not_found(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    season = 2026
    session_id = uuid.uuid4()
    known_driver = uuid.uuid4()
    unknown_driver = uuid.uuid4()
    population = [_population_row(known_driver, season)]

    monkeypatch.setattr(driver_service, "_fit_population", AsyncMock(return_value=population))
    mock_db_session.execute.return_value = _scalar_one_or_none_result(season)

    with pytest.raises(NotFoundError):
        await driver_service.get_driver_analysis(
            mock_db_session, fakeredis, unknown_driver, session_id
        )


@pytest.mark.unit
async def test_resolve_season_raises_not_found(mock_db_session: AsyncMock) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await driver_service._resolve_season(mock_db_session, uuid.uuid4())


@pytest.mark.unit
@pytest.mark.slow
async def test_fit_population_builds_features_from_synthetic_rows(
    mock_db_session: AsyncMock,
) -> None:
    """Exercises the real (unmonkeypatched) laps/stints -> features -> cluster-fit
    path, unlike test_driver_analysis_uses_population_cache above, which always
    monkeypatches _fit_population away. Runs the full PCA->KMeans->UMAP pipeline
    on synthetic data, same cost as the driver_style UMAP tests — marked slow.
    """
    season = 2026
    lap_rows, stint_rows = _synthetic_query_rows(season, n_drivers=6, n_laps=10)
    mock_db_session.execute.side_effect = [_rows_result(lap_rows), _rows_result(stint_rows)]

    result = await driver_service._fit_population(mock_db_session, season)

    assert len(result) == 6
    for row in result:
        assert row["season"] == season
        assert row["archetype"] in {
            "aggressive",
            "conservative",
            "technical",
            "balanced",
            "inconsistent",
        }


@pytest.mark.unit
async def test_performance_vs_team_avg_computes_relative_to_teammates(
    mock_db_session: AsyncMock,
) -> None:
    season = 2026
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    teammate_id = uuid.uuid4()
    team_id = uuid.uuid4()

    mock_db_session.execute.side_effect = [
        _scalar_one_or_none_result(team_id),
        _scalars_all_result([driver_id, teammate_id]),
        _rows_result([(driver_id, 91.0), (teammate_id, 90.0)]),
    ]

    result = await driver_service._performance_vs_team_avg(
        mock_db_session, driver_id, season, session_id
    )

    assert result == pytest.approx(0.5)
