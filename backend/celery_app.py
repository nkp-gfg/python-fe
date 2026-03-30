"""Celery application for FalconEye background tasks.

Start worker with:
    celery -A backend.celery_app worker --loglevel=info --pool=threads --concurrency=4

Start beat (scheduler) with:
    celery -A backend.celery_app beat --loglevel=info
"""

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

app = Celery(
    "falconeye",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "backend.tasks.ingestion",
        "backend.tasks.comparison",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24h
    broker_connection_retry_on_startup=True,
)
