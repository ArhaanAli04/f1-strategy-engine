import logging
import ssl
import threading
import time
from typing import Any

import redis
from celery import Celery
from celery.signals import task_postrun, task_prerun, worker_init

from backend.core.config import get_redis_settings
from backend.core.metrics import (
    f1_celery_queue_depth,
    f1_celery_task_duration_seconds,
    f1_celery_tasks_failed_total,
    f1_celery_tasks_succeeded_total,
)

logger = logging.getLogger(__name__)

_redis_url = get_redis_settings().redis_url
# Upstash's REDIS_URL is rediss:// (TLS) — Celery's redis transport raises
# ValueError('A rediss:// URL must have parameter ssl_cert_reqs ...') at boot
# without an explicit value (confirmed Day 24: every worker pod crash-looped
# on this against production Upstash). Local docker-compose's REDIS_URL is
# plain redis://, so this stays inert there.
_redis_url_is_tls = _redis_url.startswith("rediss://")

app = Celery(
    "f1_worker",
    broker=_redis_url,
    backend=_redis_url,
    include=[
        "backend.workers.telemetry_worker",
        "backend.workers.prediction_worker",
        "backend.workers.alert_worker",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Celery's default (10) caps concurrent producer-side broker connections
    # for .delay()/.apply_async() calls — this is the API process's outbound
    # connection pool, unrelated to worker concurrency. Confirmed via Day 13
    # load testing as the reason POST /strategy/{session_id}/simulate took
    # ~12s median just to return its 202 Accepted at 100 concurrent users:
    # that call is a single Redis broker publish and should be near-instant
    # regardless of queue depth, but 100 concurrent requests were serializing
    # behind only 10 broker connections. Raised to comfortably cover expected
    # concurrent race-day viewers.
    broker_pool_limit=50,
    # Race-day resilience: a task (e.g. run_race_simulation, 65-88s per the
    # Day 18 load test) is acked only after it finishes, not the moment a
    # worker picks it up — so if the worker process dies mid-task (OOM, pod
    # eviction, node drain), Celery re-delivers the task to another worker
    # instead of losing it silently. task_reject_on_worker_lost makes that
    # redelivery explicit even when the connection to the broker itself drops
    # mid-task (SIGKILL, not a clean disconnect) — without it, an
    # already-delivered-but-never-acked message can be left in limbo rather
    # than requeued. Together these trade "a task might run twice" (acceptable
    # here — a duplicate StrategyPrediction row or a redundant race-simulation
    # result overwriting the same task_id, not a destructive or irreversible
    # side effect) for "a task is never silently dropped".
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="telemetry_queue",
    task_routes={
        "process_lap": {"queue": "telemetry_queue"},
        "run_strategy_prediction": {"queue": "prediction_queue"},
        "run_race_simulation": {"queue": "prediction_queue"},
        "dispatch_alert": {"queue": "alert_queue"},
    },
)

if _redis_url_is_tls:
    # CERT_REQUIRED verifies Upstash's certificate against the system CA
    # bundle — the same trust level redis-py/aioredis already default to
    # elsewhere in this app; Celery's redis transport just requires it stated
    # explicitly rather than assuming a default.
    app.conf.update(
        broker_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED},
        redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED},
    )

# --- Prometheus instrumentation (Day 12) ---
#
# Relies on the worker running with --pool=solo (see Dockerfile.worker) —
# a single process, single thread, so start_http_server + the module-level
# _task_start_times dict below need no locking and no cross-process
# aggregation (prefork's forked children would otherwise each get an
# isolated copy of these metric objects, and only one could bind the port).

_METRICS_PORT = 9090
_QUEUE_DEPTH_POLL_SECONDS = 5
_MONITORED_QUEUES = ("telemetry_queue", "prediction_queue", "alert_queue")

_task_start_times: dict[str, float] = {}


def _poll_queue_depth() -> None:
    """Background loop: set f1_celery_queue_depth from each monitored queue's Redis LLEN."""
    client = (
        redis.Redis.from_url(_redis_url, decode_responses=True, ssl_cert_reqs="required")
        if _redis_url_is_tls
        else redis.Redis.from_url(_redis_url, decode_responses=True)
    )
    while True:
        for queue in _MONITORED_QUEUES:
            try:
                depth = client.llen(queue)
                f1_celery_queue_depth.labels(queue=queue).set(depth)
            except redis.RedisError:
                logger.exception("Failed to poll queue depth for %s", queue)
        time.sleep(_QUEUE_DEPTH_POLL_SECONDS)


@worker_init.connect  # type: ignore[untyped-decorator]
def _on_worker_init(**kwargs: Any) -> None:
    """Start the metrics HTTP server and queue-depth poller once, at worker boot."""
    from prometheus_client import start_http_server

    start_http_server(_METRICS_PORT)
    logger.info("Celery metrics server listening on :%d", _METRICS_PORT)

    threading.Thread(target=_poll_queue_depth, daemon=True).start()


@task_prerun.connect  # type: ignore[untyped-decorator]
def _on_task_prerun(task_id: str, **kwargs: Any) -> None:
    _task_start_times[task_id] = time.perf_counter()


@task_postrun.connect  # type: ignore[untyped-decorator]
def _on_task_postrun(task_id: str, task: Any, state: str, **kwargs: Any) -> None:
    task_name = task.name if task is not None else "unknown"
    start = _task_start_times.pop(task_id, None)
    if start is not None:
        f1_celery_task_duration_seconds.labels(task=task_name).observe(time.perf_counter() - start)

    if state == "SUCCESS":
        f1_celery_tasks_succeeded_total.labels(task=task_name).inc()
    elif state == "FAILURE":
        f1_celery_tasks_failed_total.labels(task=task_name).inc()
