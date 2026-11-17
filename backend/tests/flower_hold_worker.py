"""Celery worker used only by the Flower exposure tests.

It registers the same task names the product enqueues
(``falconeye.ingest_batch``, ``falconeye.compare_flights``,
``falconeye.compare_pnr_cross_db``) and holds each one open. Flower learns
task arguments from the worker's ``task-received`` event, which Celery sends
before the task body runs.
"""

import os
import time

from celery import Celery

broker = os.environ["CELERY_BROKER_URL"]
backend = os.environ.get("CELERY_RESULT_BACKEND", broker)

app = Celery("falconeye", broker=broker, backend=backend)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    enable_utc=True,
    timezone="UTC",
)


def _hold(*_args, **_kwargs):
    time.sleep(180)
    return {"held": True}


@app.task(bind=True, name="falconeye.ingest_batch")
def ingest_batch(self, job_id, payloads):
    return _hold()


@app.task(bind=True, name="falconeye.compare_flights")
def compare_flights(self, job_id, date, flight_numbers=None):
    return _hold()


@app.task(bind=True, name="falconeye.compare_pnr_cross_db")
def compare_pnr(self, job_id, date):
    return _hold()
