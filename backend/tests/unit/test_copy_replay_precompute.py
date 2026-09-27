"""Unit tests for scripts/copy_replay_precompute.py's id translation (no database)."""

import uuid

import pytest

from backend.scripts import copy_replay_precompute as copy

LOCAL_RUS, LOCAL_VER = uuid.uuid4(), uuid.uuid4()
PROD_RUS, PROD_VER = uuid.uuid4(), uuid.uuid4()


@pytest.mark.unit
def test_asyncpg_url_adds_the_driver_only_when_missing() -> None:
    assert copy.asyncpg_url("postgresql://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert copy.asyncpg_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


@pytest.mark.unit
def test_driver_map_matches_drivers_by_code() -> None:
    driver_map = copy.build_driver_map(
        {LOCAL_RUS: "RUS", LOCAL_VER: "VER"},
        {"VER": PROD_VER, "RUS": PROD_RUS, "HAM": uuid.uuid4()},
    )

    assert driver_map == {LOCAL_RUS: PROD_RUS, LOCAL_VER: PROD_VER}


@pytest.mark.unit
def test_driver_map_refuses_a_driver_missing_from_the_target() -> None:
    with pytest.raises(ValueError, match="LIN"):
        copy.build_driver_map({LOCAL_RUS: "RUS", uuid.uuid4(): "LIN"}, {"RUS": PROD_RUS})


@pytest.mark.unit
def test_translate_snapshot_rewrites_every_id_and_keeps_the_rest() -> None:
    local_session, prod_session = uuid.uuid4(), uuid.uuid4()
    payload = {
        "session_id": str(local_session),
        "source": "replay",
        "gaps": [
            {"driver_id": str(LOCAL_RUS), "position": 1, "gap_to_ahead_seconds": 0.0},
            {"driver_id": str(LOCAL_VER), "position": 2, "gap_to_ahead_seconds": 1.4},
        ],
    }

    translated = copy.translate_snapshot(
        payload, {LOCAL_RUS: PROD_RUS, LOCAL_VER: PROD_VER}, prod_session
    )

    assert translated == {
        "session_id": str(prod_session),
        "source": "replay",
        "gaps": [
            {"driver_id": str(PROD_RUS), "position": 1, "gap_to_ahead_seconds": 0.0},
            {"driver_id": str(PROD_VER), "position": 2, "gap_to_ahead_seconds": 1.4},
        ],
    }
    assert payload["session_id"] == str(local_session)  # the source payload is untouched


@pytest.mark.unit
def test_translate_snapshot_refuses_an_unknown_driver() -> None:
    payload = {"session_id": "s", "gaps": [{"driver_id": str(uuid.uuid4())}]}
    with pytest.raises(KeyError):
        copy.translate_snapshot(payload, {LOCAL_RUS: PROD_RUS}, uuid.uuid4())


@pytest.mark.unit
async def test_run_refuses_to_copy_a_database_onto_itself() -> None:
    with pytest.raises(ValueError, match="same database"):
        await copy.run("postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db", dry_run=True)
