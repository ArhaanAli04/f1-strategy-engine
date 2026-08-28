"""Demo Replay control: availability, curated-session listing, start/stop (Day 43 Part 4).

Backs the /demo/replay/* endpoints (apis/v1/demo.py). A Demo Replay runs
backend/scripts/replay_pipeline.py as a detached subprocess and tracks it in
one Redis key, f1:demo:replay:state (a single global replay — the simplest
sufficient design for a portfolio demo).

Two distinct gates the caller/UI must not conflate:
- **availability** (get_replay_availability) reflects ONLY live-race
  detection — the hard safety block. A replay must never overlap a real live
  ingestion (both write the same f1:{season}:{round}:gaps / position keys).
- **status** (get_replay_status) reports whether a demo replay is currently
  running. That is a normal state the UI handles on its own (show a "stop"
  control), not a reason to hide the feature.

start_replay enforces both (409 on a live race, 409 if a replay is already
running) plus validates the requested session against CURATED_SESSIONS.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis

from backend.core.exceptions import ConflictError, NotFoundError, ValidationError
from backend.schemas.demo_schema import (
    CuratedSessionResponse,
    CuratedSessionsResponse,
    ReplayAvailableResponse,
    ReplayStartResponse,
    ReplayStatusResponse,
    ReplayStopResponse,
)
from backend.services.live_race_detection import detect_live_race

# Single global replay-state key. JSON payload:
#   {replay_id, session_id, race_name, start_lap, end_lap, pid, started_at}
# See CLAUDE.md's Redis Cache Key Schema. Public because
# race_detection_worker.py's kill-switch reads/clears the same key.
DEMO_REPLAY_STATE_KEY = "f1:demo:replay:state"
# Sentinel written by the atomic NX claim before the real payload — see
# start_replay. Distinct from any real JSON payload.
_STATE_CLAIM_SENTINEL = "pending"
# Generous vs. a curated window's real ~20-minute playout — a stale key only
# means "available" flips back on early, never a corrupted state.
_STATE_TTL_SECONDS = 2 * 60 * 60

# os.WNOHANG is POSIX-only (value 1); absent on Windows, where the code path
# that uses it (_process_is_alive) is never reached anyway.
_WNOHANG: int = getattr(os, "WNOHANG", 1)

# The three curated Demo Replay sessions — session_ids, circuits, and lap
# windows are fixed (see docs/day43-handoff.md section 3). The UI offers
# exactly these; start_replay rejects anything else.
CURATED_SESSIONS: tuple[CuratedSessionResponse, ...] = (
    CuratedSessionResponse(
        session_id=uuid.UUID("7da820bf-5e8c-49bb-b19f-cdd88325af87"),
        race_name="British Grand Prix 2026",
        circuit_name="Silverstone Circuit",
        description=(
            "Full Safety Car around lap 46-47 triggers a pit stampede (4 then 8 "
            "then 5 stops across laps 46-48); the field bunches behind leader LEC "
            "by laps 50-51 with lapped cars overtaking, visible through to the flag."
        ),
        start_lap=43,
        end_lap=52,
        estimated_duration_minutes=22,
    ),
    CuratedSessionResponse(
        session_id=uuid.UUID("da57b9fd-4976-4fce-91a1-c7d0aac9c619"),
        race_name="Belgian Grand Prix 2026",
        circuit_name="Circuit de Spa-Francorchamps",
        description=(
            "Two Virtual Safety Car periods between laps 17-20 trigger a 7-stop "
            "cluster right at lap 20 — a clean undercut/overcut battle window."
        ),
        start_lap=14,
        end_lap=23,
        estimated_duration_minutes=19,
    ),
    CuratedSessionResponse(
        session_id=uuid.UUID("dd1a9280-1230-4f34-8b2d-f8b0256a3df4"),
        race_name="Canadian Grand Prix 2026",
        circuit_name="Circuit Gilles Villeneuve",
        description=(
            "VSC deployed between laps 29-31 triggers a 5-then-6-stop cluster — a "
            "second, differently-timed undercut fight at a different circuit."
        ),
        start_lap=26,
        end_lap=35,
        estimated_duration_minutes=19,
    ),
)

_CURATED_BY_ID: dict[uuid.UUID, CuratedSessionResponse] = {
    s.session_id: s for s in CURATED_SESSIONS
}


def list_curated_sessions() -> CuratedSessionsResponse:
    """Return the three curated Demo Replay sessions with their fixed metadata.

    Returns:
        CuratedSessionsResponse wrapping CURATED_SESSIONS.
    """
    return CuratedSessionsResponse(sessions=list(CURATED_SESSIONS))


async def get_replay_availability(
    client: aioredis.Redis,  # type: ignore[type-arg]
) -> ReplayAvailableResponse:
    """Whether a Demo Replay may be started right now — live-race gate only.

    Args:
        client: Async Redis client.
    Returns:
        ReplayAvailableResponse: available is False (with a reason) when a real
        live ingestion is detected; True otherwise. Does NOT consider whether a
        demo replay is already running — see get_replay_status for that.
    """
    status = await detect_live_race(client)
    if status.is_live:
        return ReplayAvailableResponse(available=False, reason=status.reason)
    return ReplayAvailableResponse(available=True, reason=None)


def _proc_is_zombie(pid: int) -> bool:
    """True if /proc reports the PID as a zombie / dead. Best-effort — False if unknown.

    os.kill(pid, 0) succeeds for a zombie (exited but not yet reaped), so a
    /proc state check is needed on top of it on Linux.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat_file:
            stat = stat_file.read()
    except OSError:
        return False  # No /proc (non-Linux POSIX) — nothing more we can tell.
    # Format: "pid (comm) state ...". comm can itself contain ") ", so split
    # on the LAST ") " to isolate the state char that follows it.
    state = stat.rpartition(b") ")[2][:1]
    return state in (b"Z", b"X", b"x")


def _process_is_alive(pid: int) -> bool:
    """Best-effort check that a PID is still running (and is not a zombie).

    POSIX only: os.kill(pid, 0) probes without delivering a signal. On
    Windows os.kill treats any non-CTRL signal (including 0) as
    TerminateProcess, so the probe would kill the target — there we skip the
    check and rely on an explicit stop / the state key's TTL instead. Every
    real deployment (backend + worker) runs in a Linux container.

    The replay subprocess is a child of this process (uvicorn) and nobody
    wait()s on it, so on exit it lingers as a zombie that os.kill(pid, 0)
    still reports as alive. This reaps it via a non-blocking waitpid and,
    failing that, checks /proc state — either way a dead/zombie replay reads
    as not-alive so get_replay_status can self-heal.
    """
    if sys.platform == "win32":
        return True

    try:
        reaped_pid, _ = os.waitpid(pid, _WNOHANG)
    except ChildProcessError:
        pass  # Not our child (another worker launched it, or already reaped).
    except OSError:
        return False
    else:
        if reaped_pid == pid:
            return False  # Our child exited and is now reaped.

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Alive, just not ours to signal.

    return not _proc_is_zombie(pid)


async def get_replay_status(
    client: aioredis.Redis,  # type: ignore[type-arg]
) -> ReplayStatusResponse:
    """Current Demo Replay state, read from f1:demo:replay:state.

    Self-heals a stale key: a replay that finished on its own (not via
    /demo/replay/stop or the kill-switch) leaves the state key until its 2h
    TTL. If the tracked PID is gone, the key is cleared here and the status
    reports not-running.

    Args:
        client: Async Redis client.
    Returns:
        ReplayStatusResponse: running True with details when a replay is
        active, running False (all fields None) otherwise. A bare claim
        sentinel (a start in progress) also reads as not-yet-running.
    """
    raw = await client.get(DEMO_REPLAY_STATE_KEY)
    if raw is None or raw == _STATE_CLAIM_SENTINEL:
        return ReplayStatusResponse(running=False)
    state = json.loads(raw)

    pid = state.get("pid")
    if isinstance(pid, int) and not _process_is_alive(pid):
        await client.delete(DEMO_REPLAY_STATE_KEY)
        return ReplayStatusResponse(running=False)

    return ReplayStatusResponse(
        running=True,
        replay_id=uuid.UUID(state["replay_id"]),
        session_id=uuid.UUID(state["session_id"]),
        race_name=state["race_name"],
        start_lap=state["start_lap"],
        end_lap=state["end_lap"],
        started_at=datetime.fromisoformat(state["started_at"]),
    )


def _launch_replay_subprocess(
    session_id: uuid.UUID, start_lap: int, end_lap: int
) -> subprocess.Popen[bytes]:
    """Launch replay_pipeline.py detached, scoped to the curated lap window.

    --no-alert-worker: real Alert DB rows are written by prediction_worker's
    evaluate_threats wiring (Day 42) regardless, so the separate FCM-push
    alert_worker subprocess is unnecessary here and would be orphaned on a
    hard stop. --rate fast keeps the demo experience predictable.

    Args:
        session_id: A validated curated session (already checked by start_replay).
        start_lap, end_lap: The curated window bounds.
    Returns:
        The Popen handle (its .pid is tracked in Redis).
    """
    return subprocess.Popen(  # noqa: S603 — fixed argv, no shell; session_id is an allowlisted UUID
        [
            sys.executable,
            "-m",
            "backend.scripts.replay_pipeline",
            "--session-id",
            str(session_id),
            "--start-lap",
            str(start_lap),
            "--end-lap",
            str(end_lap),
            "--rate",
            "fast",
            "--no-alert-worker",
        ],
        start_new_session=True,
    )


async def start_replay(
    client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
) -> ReplayStartResponse:
    """Validate, guard, and launch a Demo Replay for one curated session.

    Args:
        client: Async Redis client.
        session_id: Must be one of CURATED_SESSIONS.
    Returns:
        ReplayStartResponse with a fresh replay_id and the curated window.
    Raises:
        ValidationError: session_id is not a curated session (422).
        ConflictError: a real live race is detected, or a demo replay is
            already running (409).
    """
    curated = _CURATED_BY_ID.get(session_id)
    if curated is None:
        raise ValidationError("session_id is not one of the curated demo replay sessions")

    live = await detect_live_race(client)
    if live.is_live:
        raise ConflictError(
            f"Cannot start a demo replay while a live race is active: {live.reason}"
        )

    # Atomically claim the single global slot before spending time on the
    # subprocess launch — a losing racer sees the key already set.
    claimed = await client.set(
        DEMO_REPLAY_STATE_KEY, _STATE_CLAIM_SENTINEL, nx=True, ex=_STATE_TTL_SECONDS
    )
    if not claimed:
        raise ConflictError("A demo replay is already running")

    try:
        process = _launch_replay_subprocess(session_id, curated.start_lap, curated.end_lap)
    except OSError:
        await client.delete(DEMO_REPLAY_STATE_KEY)
        raise

    replay_id = uuid.uuid4()
    state = {
        "replay_id": str(replay_id),
        "session_id": str(session_id),
        "race_name": curated.race_name,
        "start_lap": curated.start_lap,
        "end_lap": curated.end_lap,
        "pid": process.pid,
        "started_at": datetime.now(UTC).isoformat(),
    }
    await client.set(DEMO_REPLAY_STATE_KEY, json.dumps(state), ex=_STATE_TTL_SECONDS)

    return ReplayStartResponse(
        replay_id=replay_id,
        session_id=session_id,
        race_name=curated.race_name,
        start_lap=curated.start_lap,
        end_lap=curated.end_lap,
    )


async def stop_replay(
    client: aioredis.Redis,  # type: ignore[type-arg]
) -> ReplayStopResponse:
    """Terminate the running Demo Replay subprocess and clear its state key.

    Sends SIGTERM — replay_pipeline.py installs a handler that turns it into
    its existing graceful KeyboardInterrupt shutdown (position thread stopped,
    keys left to TTL out). A pid that is already gone is not an error.

    Args:
        client: Async Redis client.
    Returns:
        ReplayStopResponse with the session_id that was stopped.
    Raises:
        NotFoundError: no demo replay is running (404).
    """
    raw = await client.get(DEMO_REPLAY_STATE_KEY)
    if raw is None or raw == _STATE_CLAIM_SENTINEL:
        raise NotFoundError("No demo replay is running")

    state = json.loads(raw)
    pid = state.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass  # Already exited, or not ours to signal — clearing the key is enough.

    await client.delete(DEMO_REPLAY_STATE_KEY)
    return ReplayStopResponse(stopped=True, session_id=uuid.UUID(state["session_id"]))
