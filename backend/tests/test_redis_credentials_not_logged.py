"""REDIS_URL credentials must not appear in worker logs.

The Celery worker is started with ``--loglevel=info``, which sets the root
logger to INFO. The first call that builds the Redis pool then logs the raw
``REDIS_URL``. These tests fail while that line includes a username or
password and pass once only the host, port, and database index are logged.

Callers that share this pool log and are not invoked separately:
``create_job``, ``get_job``, ``update_job``, ``set_job_progress``,
``set_ingestion_job_finished``, ``set_job_completed``, ``set_job_failed``,
``cache_get``, ``cache_set``, ``cache_delete``, ``cache_invalidate_pattern``,
``publish_event``, and ``subscribe_events``. Each reaches the same log
through ``get_redis``. Celery's own broker banner is not this log line.
"""

import io
import logging
import os
import re
import socket
import subprocess
import sys
import time

import pytest
import redis

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api import redis_client
from backend.celery_app import app as celery_app

USER = "placeholder-user"
SECRET = "placeholder-secret"
HOST = "127.0.0.1"
CLOSED_PORT = 6399


def _tcp_url(scheme: str, db: int, *, user: str | None, password: str | None, query: str = "") -> str:
    auth = ""
    if user and password:
        auth = f"{user}:{password}@"
    elif password:
        auth = f":{password}@"
    elif user:
        auth = f"{user}@"
    return f"{scheme}://{auth}{HOST}:{CLOSED_PORT}/{db}{query}"


TCP_CASES = [
    ("userinfo", _tcp_url("redis", 8, user=USER, password=SECRET), "8"),
    ("password-only", _tcp_url("redis", 9, user=None, password=SECRET), "9"),
    ("rediss-userinfo", _tcp_url("rediss", 6, user=USER, password=SECRET), "6"),
    ("query-password", _tcp_url("redis", 4, user=None, password=None, query=f"?password={SECRET}"), "4"),
    (
        "user-and-query-password",
        _tcp_url("redis", 3, user=USER, password=None, query=f"?password={SECRET}"),
        "3",
    ),
    (
        "unix-query-password",
        f"unix:///tmp/placeholder-redis.sock?db=5&password={SECRET}",
        "5",
    ),
]


class _RecordHandler(logging.Handler):
    def __init__(self, records: list):
        super().__init__(level=logging.DEBUG)
        self.records = records

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class WorkerLogs:
    def __init__(self):
        self.buffer = io.StringIO()
        self.records: list[logging.LogRecord] = []
        self._handler = _RecordHandler(self.records)
        self._root = logging.getLogger()
        self._prior_handlers = list(self._root.handlers)
        self._prior_level = self._root.level

    def start(self) -> None:
        # Same setup the worker uses for ``--loglevel=info``.
        celery_app.log.setup(logging.INFO)
        self._root.setLevel(logging.INFO)
        for handler in self._root.handlers:
            if isinstance(handler, logging.StreamHandler) and hasattr(handler, "setStream"):
                handler.setStream(self.buffer)
        self._root.addHandler(self._handler)

    def reset(self) -> None:
        self.buffer.seek(0)
        self.buffer.truncate(0)
        self.records.clear()

    def text(self) -> str:
        rendered = []
        for record in self.records:
            rendered.append(record.getMessage())
            rendered.append(repr(record.args))
            rendered.append(self._handler.format(record))
        return self.buffer.getvalue() + "\n" + "\n".join(rendered)

    def stop(self) -> None:
        self._root.removeHandler(self._handler)
        self._root.handlers = list(self._prior_handlers)
        self._root.setLevel(self._prior_level)


@pytest.fixture(scope="module")
def worker_logs():
    logs = WorkerLogs()
    logs.start()
    yield logs
    logs.stop()
    redis_client.close_redis()


def _use_url(monkeypatch, logs: WorkerLogs, url: str) -> None:
    monkeypatch.setenv("REDIS_URL", url)
    redis_client.close_redis()
    logs.reset()


def _assert_redacted(logs: WorkerLogs, db: str, *, port: str | None) -> None:
    text = logs.text()
    assert SECRET not in text
    assert USER not in text
    assert re.search(rf"(?:/|db=|db\s+|database\s+){re.escape(db)}(?:\D|$)", text)
    if port is not None:
        assert HOST in text
        assert port in text
    else:
        assert "placeholder-redis.sock" in text


@pytest.mark.parametrize(
    ("label", "url", "db"),
    TCP_CASES,
    ids=[case[0] for case in TCP_CASES],
)
def test_pool_creation_omits_redis_credentials(label, url, db, worker_logs, monkeypatch):
    """get_redis is what API startup and every job helper call to build the pool."""
    _use_url(monkeypatch, worker_logs, url)
    redis_client.get_redis()
    _assert_redacted(
        worker_logs,
        db,
        port=None if label == "unix-query-password" else str(CLOSED_PORT),
    )


def test_worker_job_update_omits_redis_credentials(worker_logs, monkeypatch):
    """Ingestion and comparison tasks call set_job_running, which builds the pool."""
    url = _tcp_url("redis", 8, user=USER, password=SECRET)
    _use_url(monkeypatch, worker_logs, url)
    with pytest.raises(redis.exceptions.RedisError):
        redis_client.set_job_running("job-placeholder")
    _assert_redacted(worker_logs, "8", port=str(CLOSED_PORT))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _wait_until_ready(port: int) -> None:
    client = redis.Redis(
        host=HOST,
        port=port,
        username=USER,
        password=SECRET,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    deadline = time.monotonic() + 5
    last_error = None
    while time.monotonic() < deadline:
        try:
            if client.ping():
                return
        except Exception as exc:  # server still starting
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"redis-server did not accept authenticated ping: {last_error!r}")


@pytest.fixture
def authenticated_redis():
    port = _free_port()
    # Redis 7 only accepts AUTH for an ACL user. requirepass alone rejects a username.
    config = "\n".join(
        [
            f"bind {HOST}",
            f"port {port}",
            "protected-mode yes",
            "daemonize no",
            'save ""',
            "appendonly no",
            'logfile ""',
            "loglevel warning",
            "user default off",
            f"user {USER} on >{SECRET} ~* &* +@all",
            "",
        ]
    )
    process = subprocess.Popen(
        ["redis-server", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.stdin.write(config.encode())
    process.stdin.close()
    try:
        _wait_until_ready(port)
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_successful_worker_connection_omits_redis_credentials(
    worker_logs, monkeypatch, authenticated_redis
):
    """A completed ping and job update must still omit the URL credentials."""
    port = authenticated_redis
    url = f"redis://{USER}:{SECRET}@{HOST}:{port}/8"
    _use_url(monkeypatch, worker_logs, url)
    client = redis_client.get_redis()
    assert client.ping() is True
    _assert_redacted(worker_logs, "8", port=str(port))

    worker_logs.reset()
    redis_client.close_redis()
    redis_client.set_job_running("job-placeholder")
    _assert_redacted(worker_logs, "8", port=str(port))
