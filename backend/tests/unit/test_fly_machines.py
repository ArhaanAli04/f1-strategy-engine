"""Unit tests for core/fly_machines.py: starting the worker machine on demand.

Fly's Machines API is replaced by an httpx.MockTransport, so the tests see
the real requests the helper sends.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from backend.core import fly_machines
from backend.core.fly_machines import WorkerStart, ensure_worker_started

HOST = "http://_api.internal:4280"
MACHINES = f"{HOST}/v1/apps/f1-strategy/machines"
TOKEN = "fly-token-for-tests"  # noqa: S105 — sent only to the mock transport


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "fly_worker_autostart": True,
        "fly_api_token": TOKEN,
        "fly_app_name": "f1-strategy",
        "fly_api_hostname": HOST,
        "fly_worker_process_group": "worker",
        **overrides,
    }
    monkeypatch.setattr(fly_machines, "get_app_settings", lambda: MagicMock(**values))


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeFly:
    """Records requests; answers the list with `machines` and every start with `start_status`."""

    def __init__(self, machines: list[dict[str, Any]], start_status: int = 200) -> None:
        self.machines = machines
        self.start_status = start_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=self.machines)
        return httpx.Response(self.start_status, json={"previous_state": "stopped"})

    @property
    def starts(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.method == "POST"]


@pytest.mark.unit
async def test_starts_the_stopped_worker_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    fly = FakeFly([{"id": "m-worker", "state": "stopped"}])

    result = await ensure_worker_started(_client(fly))

    assert result is WorkerStart.STARTED
    listing = fly.requests[0]
    assert str(listing.url) == f"{MACHINES}?metadata.fly_process_group=worker"
    assert listing.headers["Authorization"] == f"Bearer {TOKEN}"
    assert fly.starts == [f"{MACHINES}/m-worker/start"]
    assert fly.requests[1].headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["started", "starting"])
async def test_does_nothing_when_a_worker_is_already_running(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _settings(monkeypatch)
    fly = FakeFly([{"id": "m-old", "state": "stopped"}, {"id": "m-live", "state": state}])

    assert await ensure_worker_started(_client(fly)) is WorkerStart.ALREADY_RUNNING
    assert fly.starts == []


@pytest.mark.unit
async def test_starts_a_suspended_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    fly = FakeFly([{"id": "m-gone", "state": "destroyed"}, {"id": "m-s", "state": "suspended"}])

    assert await ensure_worker_started(_client(fly)) is WorkerStart.STARTED
    assert fly.starts == [f"{MACHINES}/m-s/start"]


@pytest.mark.unit
async def test_no_worker_machine_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    fly = FakeFly([{"id": "m-gone", "state": "destroyed"}])

    assert await ensure_worker_started(_client(fly)) is WorkerStart.NO_WORKER_MACHINE
    assert fly.starts == []


@pytest.mark.unit
async def test_a_failed_start_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    fly = FakeFly([{"id": "m-worker", "state": "stopped"}], start_status=500)

    assert await ensure_worker_started(_client(fly)) is WorkerStart.FAILED


@pytest.mark.unit
async def test_an_unreachable_api_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to _api.internal", request=request)

    assert await ensure_worker_started(_client(unreachable)) is WorkerStart.FAILED


@pytest.mark.unit
@pytest.mark.parametrize("body", [{"error": "unauthorized"}, [{"state": "stopped"}], "not json"])
async def test_an_unexpected_response_body_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    _settings(monkeypatch)

    def odd(request: httpx.Request) -> httpx.Response:
        if body == "not json":
            return httpx.Response(200, content=b"<html>")
        return httpx.Response(200, json=body)

    assert await ensure_worker_started(_client(odd)) is WorkerStart.FAILED


@pytest.mark.unit
async def test_off_by_default_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, fly_worker_autostart=False)
    fly = FakeFly([{"id": "m-worker", "state": "stopped"}])

    assert await ensure_worker_started(_client(fly)) is WorkerStart.DISABLED
    assert fly.requests == []


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["fly_api_token", "fly_app_name"])
async def test_on_without_token_or_app_name_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _settings(monkeypatch, **{missing: ""})
    fly = FakeFly([{"id": "m-worker", "state": "stopped"}])

    assert await ensure_worker_started(_client(fly)) is WorkerStart.MISCONFIGURED
    assert fly.requests == []


@pytest.mark.unit
def test_the_real_settings_default_to_off() -> None:
    from backend.core.config import AppSettings

    assert AppSettings().fly_worker_autostart is False
