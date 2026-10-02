"""Start the Celery worker's Fly machine when a simulation is queued (demo deployment Day 6).

Production keeps the worker machine stopped until someone runs a simulation,
and the worker stops itself again once idle (workers/idle_shutdown.py). This
is the other half: after POST /strategy/{session_id}/simulate queues a task,
the web process asks Fly's Machines API to start the worker machine.

- The machine is found by its `fly_process_group` metadata, which Fly sets
  from the process group name in fly.toml.
- If a worker machine is already started or starting, nothing is sent: Fly's
  docs do not say what starting a running machine returns.
- It never raises. The task is already queued, so a failed start is logged
  and the request still succeeds; the task runs when a worker next starts.
  The quota is not refunded (owner decision, Day 6).
- Off unless FLY_WORKER_AUTOSTART is set, so nothing is called locally.

API: https://docs.fly.io/machines/api/machines-resource (list with a
`metadata.{key}` filter, `POST .../machines/{id}/start`, Bearer token).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import httpx

from backend.core.config import get_app_settings

logger = logging.getLogger(__name__)

# A start that hangs must not hold up the simulate request for long; the task
# is queued either way.
REQUEST_TIMEOUT_SECONDS = 5.0

_RUNNING_STATES = frozenset({"started", "starting"})
_STARTABLE_STATES = frozenset({"stopped", "suspended"})


class WorkerStart(StrEnum):
    """What ensure_worker_started did, for logs and tests."""

    DISABLED = "disabled"
    MISCONFIGURED = "misconfigured"
    ALREADY_RUNNING = "already_running"
    STARTED = "started"
    NO_WORKER_MACHINE = "no_worker_machine"
    FAILED = "failed"


def _machine_list(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, list) or not all(isinstance(m, dict) for m in body):
        raise ValueError(f"expected a list of machines, got {type(body).__name__}")
    return body


async def ensure_worker_started(client: httpx.AsyncClient | None = None) -> WorkerStart:
    """Start a stopped worker machine, unless one is already running.

    Args:
        client: An httpx client to use (tests pass one with a mock transport).
            Omitted, a short-lived client is created.
    Returns:
        What happened. Errors are logged and returned as FAILED or
        MISCONFIGURED, never raised.
    """
    settings = get_app_settings()
    if not settings.fly_worker_autostart:
        return WorkerStart.DISABLED
    if not settings.fly_api_token or not settings.fly_app_name:
        logger.error(
            "FLY_WORKER_AUTOSTART is on but FLY_API_TOKEN or FLY_APP_NAME is empty; "
            "the worker machine was not started"
        )
        return WorkerStart.MISCONFIGURED

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    machines_url = f"{settings.fly_api_hostname}/v1/apps/{settings.fly_app_name}/machines"
    headers = {"Authorization": f"Bearer {settings.fly_api_token}"}
    try:
        listing = await http.get(
            machines_url,
            params={"metadata.fly_process_group": settings.fly_worker_process_group},
            headers=headers,
        )
        listing.raise_for_status()
        machines = _machine_list(listing.json())
        if any(m.get("state") in _RUNNING_STATES for m in machines):
            return WorkerStart.ALREADY_RUNNING
        startable = [m for m in machines if m.get("state") in _STARTABLE_STATES]
        if not startable:
            logger.error(
                "No stopped worker machine (process group %r) to start; states: %s",
                settings.fly_worker_process_group,
                [m.get("state") for m in machines],
            )
            return WorkerStart.NO_WORKER_MACHINE
        machine_id = startable[0]["id"]
        started = await http.post(f"{machines_url}/{machine_id}/start", headers=headers)
        started.raise_for_status()
        logger.info("Started worker machine %s for a queued simulation", machine_id)
        return WorkerStart.STARTED
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        # HTTPError: unreachable, timed out or a non-2xx; ValueError/KeyError:
        # a response body not shaped like the documented machine list.
        logger.warning("Could not start the worker machine: %s", exc)
        return WorkerStart.FAILED
    finally:
        if owns_client:
            await http.aclose()
