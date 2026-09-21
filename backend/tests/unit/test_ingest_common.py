"""Unit tests for scripts/_ingest_common.py.

Currently scoped to get_or_create_session's total_laps create/backfill/
no-overwrite behavior (docs/live-race-ingestion-and-strategy-gaps-monza-
2026.md Issue A) — the rest of this module's helpers had no dedicated test
file before this change either; this file exists to cover the new logic,
not to retroactively backfill coverage for everything else in the module.
"""

import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.scripts import _ingest_common


def _scalar_one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.unit
async def test_get_or_create_session_sets_total_laps_on_a_new_row(
    mock_db_session: AsyncMock,
) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)
    race_id = uuid.uuid4()

    session_row = await _ingest_common.get_or_create_session(
        mock_db_session,
        race_id=race_id,
        session_type="R",
        session_date=date(2026, 9, 6),
        total_laps=53,
    )

    assert session_row.total_laps == 53
    mock_db_session.add.assert_called_once()
    mock_db_session.flush.assert_awaited_once()


@pytest.mark.unit
async def test_get_or_create_session_new_row_defaults_total_laps_to_none(
    mock_db_session: AsyncMock,
) -> None:
    """A non-race-like session (FP/Q) — caller passes no total_laps at all,
    same as ingest_historical.py does for a session where
    fastf1_session.total_laps itself is None."""
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    session_row = await _ingest_common.get_or_create_session(
        mock_db_session,
        race_id=uuid.uuid4(),
        session_type="FP1",
        session_date=date(2026, 9, 4),
    )

    assert session_row.total_laps is None


@pytest.mark.unit
async def test_get_or_create_session_backfills_an_existing_null_row(
    mock_db_session: AsyncMock,
) -> None:
    """The live-ingested-then-later-re-processed scenario: a Session row
    already exists (created by ingest_live_session.py, which cannot reliably
    resolve total_laps at session start) with total_laps still NULL, and
    ingest_historical.py now has a real value from a completed race."""
    existing = MagicMock()
    existing.total_laps = None
    mock_db_session.execute.return_value = _scalar_one_or_none_result(existing)

    session_row = await _ingest_common.get_or_create_session(
        mock_db_session,
        race_id=uuid.uuid4(),
        session_type="R",
        session_date=date(2026, 9, 6),
        total_laps=53,
    )

    assert session_row is existing
    assert session_row.total_laps == 53
    mock_db_session.add.assert_not_called()  # no new row created
    mock_db_session.flush.assert_awaited_once()


@pytest.mark.unit
async def test_get_or_create_session_never_overwrites_an_existing_value(
    mock_db_session: AsyncMock,
) -> None:
    """A real, already-known total_laps is never replaced by a different
    caller-supplied value — this function only ever fills a gap, it does
    not adjudicate between two sources (see its own docstring)."""
    existing = MagicMock()
    existing.total_laps = 53
    mock_db_session.execute.return_value = _scalar_one_or_none_result(existing)

    session_row = await _ingest_common.get_or_create_session(
        mock_db_session,
        race_id=uuid.uuid4(),
        session_type="R",
        session_date=date(2026, 9, 6),
        total_laps=99,  # a different, wrong value — must be ignored
    )

    assert session_row.total_laps == 53
    mock_db_session.flush.assert_not_awaited()


@pytest.mark.unit
async def test_get_or_create_session_no_op_when_caller_has_no_value(
    mock_db_session: AsyncMock,
) -> None:
    """An existing row with total_laps still NULL, and the caller ALSO has
    no value to offer (total_laps=None, the default) — nothing to backfill,
    no extra flush."""
    existing = MagicMock()
    existing.total_laps = None
    mock_db_session.execute.return_value = _scalar_one_or_none_result(existing)

    session_row = await _ingest_common.get_or_create_session(
        mock_db_session,
        race_id=uuid.uuid4(),
        session_type="R",
        session_date=date(2026, 9, 6),
    )

    assert session_row.total_laps is None
    mock_db_session.flush.assert_not_awaited()
