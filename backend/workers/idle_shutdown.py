"""Stop the Celery worker after a stretch with no task (demo deployment Day 6).

In production the worker machine is started only when a simulation is queued
(see docs/internal/demo-deployment-plan-2026.md) and should not keep running,
and billing, afterwards. Fly stops a machine when its process exits, and with
the `on-failure` restart policy a clean exit (code 0) is not restarted. So the
worker ends itself once it has been idle for WORKER_IDLE_EXIT_MINUTES.

It ends itself with SIGTERM, Celery's warm shutdown: the worker stops taking
messages, finishes any task it is running and exits with code 0. A message
delivered but not yet started goes back to the queue, because tasks are acked
late (task_acks_late in celery_app.py), so a simulation queued at the moment of
shutdown is not lost: it runs when the worker next starts.

Off unless WORKER_IDLE_EXIT_MINUTES is above 0, so the local worker is never
stopped by this.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# How often the watchdog thread checks; the exit can land up to this much
# later than the idle limit, which is fine against a limit measured in minutes.
POLL_SECONDS = 15.0


def stop_this_worker() -> None:
    """Ask this Celery worker process for a warm shutdown (SIGTERM).

    Returns:
        None. Celery's own SIGTERM handler does the shutdown.
    """
    os.kill(os.getpid(), signal.SIGTERM)


class IdleWatchdog:
    """Calls on_idle once, after idle_limit_seconds with no task running.

    task_started/task_finished are called from Celery's task signals in the
    worker's main thread; check runs on the watchdog thread, hence the lock.
    The idle clock starts at construction (worker boot), so a worker started
    for a task that never arrives still stops.
    """

    def __init__(
        self,
        idle_limit_seconds: float,
        on_idle: Callable[[], None] = stop_this_worker,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_limit_seconds = idle_limit_seconds
        self._on_idle = on_idle
        self._clock = clock
        self._lock = threading.Lock()
        self._running_tasks = 0
        self._last_activity = clock()
        self._fired = False

    def task_started(self) -> None:
        """Record that a task began; the worker is not idle while it runs."""
        with self._lock:
            self._running_tasks += 1
            self._last_activity = self._clock()

    def task_finished(self) -> None:
        """Record that a task ended; the idle clock restarts from now."""
        with self._lock:
            self._running_tasks = max(0, self._running_tasks - 1)
            self._last_activity = self._clock()

    def check(self) -> bool:
        """Fire on_idle if the worker has been idle long enough.

        Returns:
            True if on_idle was called by this check, False otherwise. It is
            called at most once over the watchdog's life.
        """
        with self._lock:
            if self._fired or self._running_tasks > 0:
                return False
            idle_seconds = self._clock() - self._last_activity
            if idle_seconds < self._idle_limit_seconds:
                return False
            self._fired = True
        logger.info("Worker idle for %.0fs with no task; shutting down", idle_seconds)
        self._on_idle()
        return True

    def run(self, poll_seconds: float = POLL_SECONDS) -> None:
        """Check every poll_seconds until on_idle has been called.

        Args:
            poll_seconds: Seconds between checks.
        Returns:
            None, once on_idle has been called.
        """
        while not self.check():
            time.sleep(poll_seconds)


def start_idle_watchdog(idle_exit_minutes: int) -> IdleWatchdog | None:
    """Start the watchdog on a daemon thread, if idle exit is configured.

    Args:
        idle_exit_minutes: WORKER_IDLE_EXIT_MINUTES; 0 or below turns it off.
    Returns:
        The running watchdog, for the task signals to report to, or None when
        it is off.
    """
    if idle_exit_minutes <= 0:
        return None
    watchdog = IdleWatchdog(idle_limit_seconds=idle_exit_minutes * 60)
    threading.Thread(target=watchdog.run, name="idle-watchdog", daemon=True).start()
    logger.info("Idle shutdown on: the worker exits after %d idle minute(s)", idle_exit_minutes)
    return watchdog
