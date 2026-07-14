"""Prometheus metric objects shared across the FastAPI app and Celery workers.

Centralised here (rather than defined inline where used) so both process types
(backend and worker share one codebase, see CLAUDE.md's Dockerfile.backend /
Dockerfile.worker split) import the same objects instead of redefining a metric
name at import time, which prometheus_client raises "Duplicated timeseries in
CollectorRegistry" for.

f1_cache_hits_total / f1_cache_misses_total are NOT here — they already live in
cache_service.py, defined next to the key-cardinality-collapsing logic that
labels them.
"""

from prometheus_client import Counter, Gauge, Histogram

f1_strategy_predictions_total = Counter(
    "f1_strategy_predictions_total",
    "Total strategy predictions persisted by run_strategy_prediction",
)

f1_ml_inference_duration_seconds = Histogram(
    "f1_ml_inference_duration_seconds",
    "ML model inference latency in seconds",
    ["model"],
)

f1_active_websocket_connections = Gauge(
    "f1_active_websocket_connections",
    "Currently connected /ws/telemetry clients",
)

f1_celery_task_duration_seconds = Histogram(
    "f1_celery_task_duration_seconds",
    "Celery task execution latency in seconds",
    ["task"],
)

f1_celery_tasks_succeeded_total = Counter(
    "f1_celery_tasks_succeeded_total",
    "Total Celery tasks completed successfully",
    ["task"],
)

f1_celery_tasks_failed_total = Counter(
    "f1_celery_tasks_failed_total",
    "Total Celery tasks that raised an exception",
    ["task"],
)

f1_celery_queue_depth = Gauge(
    "f1_celery_queue_depth",
    "Current Redis LLEN per Celery queue",
    ["queue"],
)
