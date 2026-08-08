"""Unit tests for services/circuit_service.py.

_stub_cache_lock stubs cache_service.cache_lock — @cacheable's internal
single-flight lock lives in cache_service.py, so patching it there covers the
@cacheable-decorated _fetch_circuit_outline below. Same no-op pattern
test_telemetry_service.py/test_race_service.py established: fakeredis has no
Lua/EVALSHA support, which redis-py's real Lock needs to release().
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import pytest

from backend.core.exceptions import NotFoundError
from backend.services import cache_service, circuit_service


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())


def _scalar_one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _fake_circuit(circuit_id: uuid.UUID, map_geometry: dict[str, Any] | None) -> Any:
    class _FakeCircuit:
        id = circuit_id

    circuit = _FakeCircuit()
    circuit.map_geometry = map_geometry  # type: ignore[attr-defined]
    return circuit


@pytest.mark.unit
async def test_get_circuit_outline_returns_geometry_when_present(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    circuit_id = uuid.uuid4()
    geometry = {
        "viewbox": "0 0 1000 1000",
        "points": [[1.0, 2.0], [3.0, 4.0]],
        "corners": [{"number": 1, "x": 1.5, "y": 2.5}, {"number": 2, "x": 3.5, "y": 4.5}],
        "source": {"season": 2025, "round": 24, "session_type": "R"},
        "transform": {
            "rotation_degrees": 92.0,
            "center_x": 10.0,
            "center_y": -5.0,
            "scale": 0.5,
            "viewbox_center": 500.0,
        },
    }
    circuit = _fake_circuit(circuit_id, geometry)
    mock_db_session.execute.return_value = _scalar_one_or_none_result(circuit)

    result = await circuit_service.get_circuit_outline(fakeredis, mock_db_session, circuit_id)

    assert result.circuit_id == circuit_id
    assert result.viewbox == "0 0 1000 1000"
    assert result.points == [[1.0, 2.0], [3.0, 4.0]]
    assert result.source == geometry["source"]
    assert result.transform is not None
    assert result.transform.rotation_degrees == 92.0
    assert len(result.corners) == 2
    assert result.corners[0].number == 1
    assert result.corners[0].x == 1.5


@pytest.mark.unit
async def test_get_circuit_outline_defaults_corners_to_empty_when_missing(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    circuit_id = uuid.uuid4()
    geometry = {"viewbox": "0 0 1000 1000", "points": [[1.0, 2.0]]}
    circuit = _fake_circuit(circuit_id, geometry)
    mock_db_session.execute.return_value = _scalar_one_or_none_result(circuit)

    result = await circuit_service.get_circuit_outline(fakeredis, mock_db_session, circuit_id)

    assert result.corners == []


@pytest.mark.unit
async def test_get_circuit_outline_raises_not_found_when_circuit_missing(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await circuit_service.get_circuit_outline(fakeredis, mock_db_session, uuid.uuid4())


@pytest.mark.unit
async def test_get_circuit_outline_raises_not_found_when_geometry_not_yet_extracted(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    circuit_id = uuid.uuid4()
    circuit = _fake_circuit(circuit_id, None)
    mock_db_session.execute.return_value = _scalar_one_or_none_result(circuit)

    with pytest.raises(NotFoundError):
        await circuit_service.get_circuit_outline(fakeredis, mock_db_session, circuit_id)


@pytest.mark.unit
async def test_get_circuit_outline_cache_hit_skips_db_query(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    circuit_id = uuid.uuid4()
    cached = {
        "circuit_id": str(circuit_id),
        "viewbox": "0 0 1000 1000",
        "points": [[1.0, 2.0]],
        "source": None,
    }
    key = circuit_service._key_circuit_outline(fakeredis, mock_db_session, circuit_id)
    await cache_service.cache_set(fakeredis, key, cached, ttl=None)

    result = await circuit_service.get_circuit_outline(fakeredis, mock_db_session, circuit_id)

    assert result.circuit_id == circuit_id
    mock_db_session.execute.assert_not_called()
