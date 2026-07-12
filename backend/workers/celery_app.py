from celery import Celery

from backend.core.config import get_redis_settings

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
