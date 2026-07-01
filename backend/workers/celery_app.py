import os

from celery import Celery

app = Celery(
    "f1_worker",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379"),
)
