"""Unit tests for workers/alert_worker.py — the FCM dispatch task's engine cleanup.

`_dispatch` runs under `asyncio.run`, so the pooled engine must be disposed on every exit
path once a session was opened: a connection left bound to a closed event loop breaks the
next task (same flaw the V3 shadow race found in prediction_worker._persist_and_publish).
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from backend.schemas.alert_schema import AlertType
from backend.workers import alert_worker


class _FailingSession:
    """Stands in for `session_factory()`; the query inside the block raises."""

    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT ...", {}, Exception("connection reset"))


class _SubscriptionsSession:
    """Returns one subscription following the driver, so the FCM path is reached."""

    def __init__(self, subscription: MagicMock) -> None:
        self._subscription = subscription

    async def __aenter__(self) -> "_SubscriptionsSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value.all.return_value = [self._subscription]
        return result


def _stub_engine(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(alert_worker, "get_engine", lambda: engine)
    return engine


def _prediction(driver_id: uuid.UUID, pit_probability: float) -> dict[str, Any]:
    return {"driver_id": str(driver_id), "pit_probability": pit_probability}


@pytest.mark.unit
async def test_dispatch_disposes_the_engine_even_when_the_query_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _stub_engine(monkeypatch)
    monkeypatch.setattr(alert_worker, "_get_session_factory", lambda: _FailingSession)
    send_fcm = MagicMock()
    monkeypatch.setattr(alert_worker, "_send_fcm", send_fcm)

    with pytest.raises(OperationalError):
        await alert_worker._dispatch(_prediction(uuid.uuid4(), 0.9))

    engine.dispose.assert_awaited_once()
    send_fcm.assert_not_called()


@pytest.mark.unit
async def test_dispatch_disposes_the_engine_and_sends_to_matching_subscribers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_id = uuid.uuid4()
    subscription = MagicMock()
    subscription.driver_ids = [str(driver_id)]
    subscription.alert_types = [AlertType.PIT_WINDOW_OPEN.value]
    engine = _stub_engine(monkeypatch)
    monkeypatch.setattr(
        alert_worker, "_get_session_factory", lambda: lambda: _SubscriptionsSession(subscription)
    )
    send_fcm = MagicMock()
    monkeypatch.setattr(alert_worker, "_send_fcm", send_fcm)

    await alert_worker._dispatch(_prediction(driver_id, 0.9))

    engine.dispose.assert_awaited_once()
    send_fcm.assert_called_once()


@pytest.mark.unit
async def test_dispatch_below_threshold_never_opens_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _stub_engine(monkeypatch)
    get_factory = MagicMock()
    monkeypatch.setattr(alert_worker, "_get_session_factory", get_factory)

    await alert_worker._dispatch(_prediction(uuid.uuid4(), 0.1))

    get_factory.assert_not_called()
    engine.dispose.assert_not_awaited()
