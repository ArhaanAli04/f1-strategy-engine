import logging
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
    task_default_queue="telemetry_queue",
    task_routes={
        "process_lap": {"queue": "telemetry_queue"},
        "run_strategy_prediction": {"queue": "prediction_queue"},
        "run_race_simulation": {"queue": "prediction_queue"},
        "dispatch_alert": {"queue": "alert_queue"},
    },
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
    client = redis.Redis.from_url(_redis_url, decode_responses=True)
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
