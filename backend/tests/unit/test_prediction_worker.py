"""Unit tests for workers/prediction_worker.py.

mock_db_session (AsyncMock spec'd to AsyncSession) stands in for the DB; the real
fakeredis fixture stands in for Redis. _cumulative_race_time is monkeypatched to a
recording stub in the test below rather than mocked at the db.execute() level, since
the behavior under test is which `up_to_lap` argument _build_race_state passes to it
per driver, not _cumulative_race_time's own SQL summing logic (already covered
indirectly by strategy_service's equivalent tests).
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.workers import prediction_worker


@pytest.mark.unit
async def test_build_race_state_seeds_cumulative_time_from_current_lap_for_every_driver(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every driver's cumulative_race_time_seconds must be seeded as-of the same
    current_lap, not each driver's own independently-latest ingested lap — otherwise
    normal async ingestion skew bakes a fake multi-lap time gap into the simulation's
    starting point (the bug this test guards against: driver A's latest DB row is
    ahead of current_lap, driver B's is behind it; both must still be queried at
    current_lap).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    requesting_driver_id = uuid.uuid4()
    driver_a_id = uuid.uuid4()
    driver_b_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    lap_a = SimpleNamespace(
        driver_id=driver_a_id, lap_number=56, compound="MEDIUM", tyre_age_laps=10, position=1
    )
    lap_b = SimpleNamespace(
        driver_id=driver_b_id, lap_number=54, compound="HARD", tyre_age_laps=20, position=2
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [lap_a, lap_b]

    position_result = MagicMock()
    position_result.all.return_value = [
        (driver_a_id, lap_a.position),
        (driver_b_id, lap_b.position),
    ]

    mock_db_session.execute.side_effect = [context_result, latest_laps_result, position_result]

    # Weather cache hit so _resolve_weather never falls through to a 3rd db.execute().
    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    recorded_up_to_laps: dict[uuid.UUID, int] = {}

    async def _fake_cumulative_race_time(
        db: AsyncSession, sid: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
    ) -> float:
        recorded_up_to_laps[driver_id] = up_to_lap
        return 0.0

    monkeypatch.setattr(prediction_worker, "_cumulative_race_time", _fake_cumulative_race_time)

    await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        requesting_driver_id,
        current_lap,
        "SOFT",
        3,
        58,
    )

    assert recorded_up_to_laps[driver_a_id] == current_lap
    assert recorded_up_to_laps[driver_b_id] == current_lap
    assert recorded_up_to_laps[driver_a_id] != lap_a.lap_number
    assert recorded_up_to_laps[driver_b_id] != lap_b.lap_number


@pytest.mark.unit
async def test_build_race_state_starting_position_uses_current_lap_not_final_position(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """starting_position must reflect the driver's position AT current_lap, not
    their absolute-latest DB row's position — for a completed session (or any
    session where ingestion has continued past current_lap), that latest row is
    the FINAL classification position, not the position at the point the what-if
    actually starts (the bug this test guards against: the driver's lap 58 row
    shows position=9, but their real position at lap 55 was 10 — starting_position
    must come out as 10, not 9).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    # This driver's absolute-latest DB row is the race's final lap (58), where
    # they classified P9 — but at lap 55 (current_lap) they were P10.
    final_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=58, compound="MEDIUM", tyre_age_laps=17, position=9
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [final_lap]

    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10)]

    mock_db_session.execute.side_effect = [context_result, latest_laps_result, position_result]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    async def _stub_cumulative_race_time(*args: Any, **kwargs: Any) -> float:
        return 0.0

    monkeypatch.setattr(prediction_worker, "_cumulative_race_time", _stub_cumulative_race_time)

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
    )

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10
    assert driver_state.starting_position != final_lap.position


@pytest.mark.unit
async def test_build_race_state_position_query_filters_by_session_id(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The position-as-of-current_lap query must filter by session_id on the
    outer select, not just inside its subquery — otherwise (the regression this
    test guards against) the join matches ANY session's LapData row sharing
    (driver_id, lap_number), which is a real collision: this codebase's
    multi-season training corpus means the same driver commonly has a row at
    the same lap_number in unrelated sessions, each with a different position.

    Simulates driver X with a lap_number=55 row in the target session
    (position=10, the value that must be used) and a different session
    (other_session_id, position=99, what an unfiltered join could accidentally
    pick up instead) — asserts the constructed query's WHERE clause actually
    filters on session_id (proving the SQL-level fix, not just the mocked
    result), and that _build_race_state threads the correct value through.
    """
    session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    latest_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=55, compound="MEDIUM", tyre_age_laps=14, position=10
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [latest_lap]

    # What a correctly session_id-filtered query returns from a real DB — the
    # other_session_id=99 row is excluded by Postgres, not filtered by this mock.
    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10)]

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        if len(captured_queries) == 1:
            return context_result
        if len(captured_queries) == 2:
            return latest_laps_result
        if len(captured_queries) == 3:
            return position_result
        raise AssertionError(f"unexpected extra db.execute call: {query}")

    mock_db_session.execute.side_effect = _execute_side_effect

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    async def _stub_cumulative_race_time(*args: Any, **kwargs: Any) -> float:
        return 0.0

    monkeypatch.setattr(prediction_worker, "_cumulative_race_time", _stub_cumulative_race_time)

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
    )

    position_query = captured_queries[2]
    assert position_query.whereclause is not None, (
        "position_query must filter by session_id on the outer select — without "
        "it, the join matches this driver's lap_number row in ANY session, not "
        "just this one"
    )
    compiled = str(position_query.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert session_id.hex in compiled.replace("-", "")
    assert other_session_id.hex not in compiled.replace("-", "")

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10
