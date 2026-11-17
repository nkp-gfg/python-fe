"""Flower from docker-compose.yml must not serve an outsider.

The Compose service ``flower`` uses image ``mher/flower:2.0``. That tag was
pushed on 2023-08-13, minutes after Flower 2.0.1 was published, and the image
Dockerfile runs ``pip install flower`` with command ``celery flower``. These
tests start that same command with only the environment the Compose service
sets, then act as a client that presents no credentials.

Run (the assertions that the dashboard is closed fail until the service is
fixed)::

    python3 -m venv /tmp/flower-venv
    /tmp/flower-venv/bin/pip install 'flower==2.0.1' 'celery==5.4.0' \\
        'kombu>=5.3.4,<5.5' 'redis>=5,<6' pytest pyyaml
    # redis-server must be on PATH (Ubuntu package redis-server)
    cd <repo>
    PYTHONPATH=. /tmp/flower-venv/bin/pytest -q backend/tests/test_flower_unauthenticated.py

An operator secret, when the Compose file only names it, is the placeholder
``placeholder-not-a-secret``. The outsider never sends it.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
FLOWER_VERSION = "2.0.1"
REDIS_PORT = 6397
FLOWER_PORT = 5555
BROKER_URL = f"redis://127.0.0.1:{REDIS_PORT}/0"
# Obvious stand-in used only when Compose references an auth variable by name.
AUTH_PLACEHOLDER = "placeholder-not-a-secret"
AUTH_ENV_KEYS = {
    "FLOWER_BASIC_AUTH",
    "FLOWER_OAUTH2_KEY",
    "FLOWER_OAUTH2_SECRET",
    "FLOWER_COOKIE_SECRET",
}
OUTSIDER_AUTHORIZATION = "Basic b3V0c2lkZXI6b3V0c2lkZXI="  # outsider:outsider

_INTERP = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([?-])([^}]*))?\}"
)


def _compose_flower():
    document = yaml.safe_load(COMPOSE_FILE.read_text())
    services = document.get("services") or {}
    return services.get("flower")


def _published(service: dict) -> bool:
    ports = service.get("ports") or []
    return len(ports) > 0


def _raw_environment(service: dict) -> dict[str, str]:
    raw = service.get("environment") or {}
    items: dict[str, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            key, _, value = str(entry).partition("=")
            items[key] = value
    elif isinstance(raw, dict):
        for key, value in raw.items():
            items[str(key)] = "" if value is None else str(value)
    else:
        raise AssertionError("flower environment must be a mapping or a list")
    return items


def _committed_auth_literal(raw: str) -> bool:
    """True when an auth setting's value is written into the Compose file."""
    text = raw.strip()
    if not text:
        return False
    if "${" not in text:
        return True
    for match in _INTERP.finditer(text):
        default = match.group(3)
        if default:
            return True
    return False


def _interpolate(raw: str, env: dict[str, str]) -> str:
    """Compose-spec interpolation for ${VAR}, ${VAR:-default}, and ${VAR-default}."""
    # Distinguish ':-' from '-' by rewriting the colon form before matching.
    cooked = raw.replace(":-", ":~")
    pattern = re.compile(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([~?-])([^}]*))?\}"
    )

    def replace_cooked(match: re.Match) -> str:
        name = match.group(1)
        operator = match.group(2)
        default = match.group(3) or ""
        present = name in env
        current = env.get(name, "")
        if operator is None:
            return current if present else ""
        if operator == "~":  # ':-' unset or empty -> default
            if not present or current == "":
                return default
            return current
        if operator == "-":  # '-' unset -> default; empty stays empty
            if not present:
                return default
            return current
        if operator == "?":
            if not present or current == "":
                raise AssertionError(
                    f"Compose requires {name} to start the flower service"
                )
            return current
        return current if present else ""

    # $$ is a literal dollar. Protect it, interpolate, then restore.
    protected = cooked.replace("$$", "\0")
    rendered = pattern.sub(replace_cooked, protected)
    return rendered.replace("\0", "$")


def _command(service: dict) -> list[str]:
    command = service.get("command")
    if command is None:
        return ["celery", "flower"]
    if isinstance(command, str):
        return shlex.split(command)
    return [str(part) for part in command]


def _assert_command_has_no_committed_secret(command: list[str]) -> None:
    for part in command:
        lowered = part.lower().replace("_", "-")
        if "basic-auth" in lowered or "basic_auth" in part.lower():
            _name, sep, value = part.partition("=")
            if sep and value and "${" not in value:
                raise AssertionError(
                    "flower command contains a basic-auth literal; "
                    "keep that value out of the Compose file"
                )


def _rewrite_broker(url: str) -> str:
    """Point the Compose hostname ``redis`` at the Redis this test starts."""
    return (
        url.replace("redis://redis:6379", f"redis://127.0.0.1:{REDIS_PORT}")
        .replace("redis://redis:", f"redis://127.0.0.1:")
    )


def _http(method: str, url: str, data: dict | None = None, headers: dict | None = None):
    encoded = None
    request_headers = dict(headers or {})
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode()
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )
    request = urllib.request.Request(
        url, data=encoded, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers)


def _wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"nothing accepted connections on 127.0.0.1:{port}")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _tail(path: Path, limit: int = 40) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-limit:])


@pytest.fixture(scope="module")
def exposure():
    import flower

    assert flower.__version__ == FLOWER_VERSION, flower.__version__
    service = _compose_flower()
    if service is None or not _published(service):
        yield {"exposed": False}
        return

    raw_env = _raw_environment(service)
    for key, raw in raw_env.items():
        if key in AUTH_ENV_KEYS and _committed_auth_literal(raw):
            raise AssertionError(
                f"{key} is written into docker-compose.yml; "
                "reference it by name instead"
            )
    command = _command(service)
    _assert_command_has_no_committed_secret(command)
    if command[0] == "celery":
        command[0] = str(Path(sys.executable).with_name("celery"))

    operator_env: dict[str, str] = {}
    for raw in list(raw_env.values()) + command:
        for match in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", str(raw)):
            name = match.group(1)
            if name in AUTH_ENV_KEYS:
                operator_env[name] = AUTH_PLACEHOLDER

    rendered = {
        key: _rewrite_broker(_interpolate(value, operator_env))
        for key, value in raw_env.items()
    }
    rendered.setdefault("CELERY_BROKER_URL", BROKER_URL)
    rendered.setdefault("CELERY_RESULT_BACKEND", rendered["CELERY_BROKER_URL"])

    log_dir = Path("/tmp/flower-exposure")
    log_dir.mkdir(parents=True, exist_ok=True)
    redis_log = log_dir / "redis.log"
    flower_log = log_dir / "flower.log"
    worker_log = log_dir / "worker.log"

    redis_proc = flower_proc = worker_proc = None
    try:
        redis_proc = subprocess.Popen(
            [
                "redis-server",
                "--bind", "127.0.0.1",
                "--port", str(REDIS_PORT),
                "--protected-mode", "yes",
                "--save", "",
                "--appendonly", "no",
                "--dir", "/tmp",
                "--dbfilename", "",
            ],
            stdout=redis_log.open("w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _wait_port(REDIS_PORT)

        flower_env = os.environ.copy()
        for key in list(flower_env):
            if key.startswith("FLOWER_") or key.startswith("CELERY_"):
                flower_env.pop(key, None)
        flower_env.update(rendered)
        # Harness bind only. Compose publishes the container port on the host;
        # authentication does not depend on this address.
        if not any(part.startswith("--address") for part in command):
            command = command + ["--address=127.0.0.1", f"--port={FLOWER_PORT}"]
        worker_env = os.environ.copy()
        worker_env["CELERY_BROKER_URL"] = rendered["CELERY_BROKER_URL"]
        worker_env["CELERY_RESULT_BACKEND"] = rendered["CELERY_RESULT_BACKEND"]
        worker_env["PYTHONPATH"] = str(REPO_ROOT)
        worker_proc = subprocess.Popen(
            [
                str(Path(sys.executable).with_name("celery")),
                "-A", "backend.tests.flower_hold_worker",
                "worker",
                "--loglevel=info",
                "--pool=threads",
                "--concurrency=2",
                "-n", "falconeye-hold@localhost",
                "-E",
            ],
            cwd=REPO_ROOT,
            env=worker_env,
            stdout=worker_log.open("w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        os.environ["REDIS_URL"] = rendered["CELERY_BROKER_URL"]
        from backend.celery_app import app as celery_app

        deadline = time.time() + 30
        while time.time() < deadline:
            if worker_proc.poll() is not None:
                raise AssertionError("worker exited\n" + _tail(worker_log))
            ping = celery_app.control.inspect(timeout=1.0).ping() or {}
            if any("falconeye-hold" in name for name in ping):
                break
            time.sleep(0.3)
        else:
            raise AssertionError("worker did not answer ping\n" + _tail(worker_log))

        flower_proc = subprocess.Popen(
            command,
            cwd=log_dir,
            env=flower_env,
            stdout=flower_log.open("w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _wait_port(FLOWER_PORT)
        status, body, _headers = _http("GET", f"http://127.0.0.1:{FLOWER_PORT}/healthcheck")
        if status != 200 or body.strip() != "OK":
            raise AssertionError(
                "flower healthcheck failed\n" + _tail(flower_log)
            )
        # Redis pub/sub does not replay events. Wait until Flower's receiver
        # has seen the worker, or until the UI refuses the outsider.
        deadline = time.time() + 20
        while time.time() < deadline:
            status, body, _headers = _http(
                "GET", f"http://127.0.0.1:{FLOWER_PORT}/workers?json=1"
            )
            if status in (401, 403) or "falconeye-hold" in body:
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                "flower did not observe the worker\n" + _tail(flower_log)
            )

        from backend.tasks.comparison import run_flight_comparison, run_pnr_comparison
        from backend.tasks.ingestion import run_batch_ingestion

        ingest_job = str(uuid.uuid4())
        compare_job = str(uuid.uuid4())
        pnr_job = str(uuid.uuid4())
        flight_number = "2057"
        payloads = [
            {
                "airline": "GF",
                "flightNumber": flight_number,
                "origin": "BAH",
                "departureDate": date(2026, 11, 17).isoformat(),
                "departureDateTime": datetime(2026, 11, 17, 10, 0, 0).isoformat(),
            }
        ]
        # Same call the batch route makes after model_dump: delay(job_id, payloads).
        ingest_result = run_batch_ingestion.delay(ingest_job, payloads)
        compare_result = run_flight_comparison.delay(
            compare_job, "2026-11-17", [flight_number]
        )
        pnr_result = run_pnr_comparison.delay(pnr_job, "2026-11-17")

        from backend.celery_app import app as celery_app
        from celery.result import AsyncResult

        deadline = time.time() + 30
        while time.time() < deadline:
            if AsyncResult(ingest_result.id, app=celery_app).state == "STARTED":
                break
            if worker_proc.poll() is not None:
                raise AssertionError("worker exited\n" + _tail(worker_log))
            time.sleep(0.25)
        else:
            raise AssertionError(
                "ingestion task did not start\n" + _tail(worker_log)
            )

        base = f"http://127.0.0.1:{FLOWER_PORT}"
        # Flower records the task only after it receives the worker event.
        deadline = time.time() + 30
        datatable = None
        while time.time() < deadline:
            datatable = _http(
                "POST",
                base + "/tasks/datatable",
                data={
                    "draw": "1",
                    "start": "0",
                    "length": "50",
                    "search[value]": "",
                    "order[0][column]": "0",
                    "order[0][dir]": "desc",
                    "columns[0][data]": "name",
                },
            )
            if datatable[0] in (401, 403) or ingest_job in datatable[1]:
                break
            time.sleep(0.5)
        else:
            last = datatable[1][:400] if datatable else ""
            raise AssertionError(
                f"flower never recorded the ingestion task (last status "
                f"{None if datatable is None else datatable[0]}): {last}\n"
                + _tail(flower_log)
            )

        yield {
            "exposed": True,
            "base": base,
            "ingest_job": ingest_job,
            "compare_job": compare_job,
            "pnr_job": pnr_job,
            "flight_number": flight_number,
            "ingest_task_id": ingest_result.id,
            "compare_task_id": compare_result.id,
            "pnr_task_id": pnr_result.id,
            "worker_name": "falconeye-hold@localhost",
            "datatable": datatable,
            "broker": rendered["CELERY_BROKER_URL"],
        }
    finally:
        _stop(worker_proc)
        _stop(flower_proc)
        _stop(redis_proc)


def _assert_closed(status: int, body: str, marker: str, where: str) -> None:
    assert marker not in body, f"{where} included {marker!r} with status {status}"
    assert status in (401, 403), f"{where} returned {status} to a client with no credentials"


def test_task_pages_hide_flight_arguments(exposure):
    if not exposure["exposed"]:
        return
    base = exposure["base"]
    marker = exposure["ingest_job"]
    flight = exposure["flight_number"]

    status, body, _headers = exposure["datatable"]
    _assert_closed(status, body, marker, "POST /tasks/datatable")
    assert flight not in body

    for method, path in (
        ("GET", "/"),
        ("GET", "/workers"),
        ("GET", "/workers?json=1"),
        ("GET", "/tasks"),
        ("GET", "/broker"),
        ("GET", f"/task/{exposure['ingest_task_id']}"),
        ("GET", f"/worker/{urllib.parse.quote(exposure['worker_name'], safe='')}"),
    ):
        status, body, _headers = _http(method, base + path)
        _assert_closed(status, body, marker, f"{method} {path}")
        assert exposure["compare_job"] not in body
        assert exposure["pnr_job"] not in body
        assert flight not in body

    # A made-up Authorization header is not a login.
    status, body, _headers = _http(
        "GET",
        base + "/",
        headers={"Authorization": OUTSIDER_AUTHORIZATION},
    )
    _assert_closed(status, body, marker, "GET / with an unrecognized Authorization header")


def test_control_api_does_not_revoke_or_stop_the_worker(exposure):
    if not exposure["exposed"]:
        return
    from backend.celery_app import app as celery_app
    from celery.result import AsyncResult

    base = exposure["base"]
    task_id = exposure["ingest_task_id"]
    worker = urllib.parse.quote(exposure["worker_name"], safe="")
    marker = exposure["ingest_job"]
    task_name = "falconeye.ingest_batch"

    before = AsyncResult(task_id, app=celery_app).state
    assert before == "STARTED", before

    endpoints = [
        ("GET", "/api/workers", None),
        ("GET", "/api/tasks", None),
        ("GET", "/api/task/types", None),
        ("GET", "/api/queues/length", None),
        ("GET", f"/api/task/info/{task_id}", None),
        ("GET", f"/api/task/result/{task_id}", None),
        ("POST", f"/api/task/revoke/{task_id}", {"terminate": "true", "signal": "SIGTERM"}),
        ("POST", f"/api/task/abort/{task_id}", {}),
        ("POST", f"/api/worker/shutdown/{worker}", {}),
        ("POST", f"/api/worker/pool/restart/{worker}", {}),
        ("POST", f"/api/worker/pool/grow/{worker}", {"n": "1"}),
        ("POST", f"/api/worker/pool/shrink/{worker}", {"n": "1"}),
        ("POST", f"/api/worker/pool/autoscale/{worker}", {"min": "1", "max": "2"}),
        ("POST", f"/api/worker/queue/add-consumer/{worker}", {"queue": "celery"}),
        ("POST", f"/api/worker/queue/cancel-consumer/{worker}", {"queue": "celery"}),
        ("POST", f"/api/task/async-apply/{task_name}", None),
        ("POST", f"/api/task/send-task/{task_name}", None),
        ("POST", f"/api/task/timeout/{task_name}", {"workername": exposure["worker_name"], "soft": "1", "hard": "2"}),
        ("POST", f"/api/task/rate-limit/{task_name}", {"workername": exposure["worker_name"], "ratelimit": "1/m"}),
    ]
    # JSON body for the apply/send endpoints, matching Flower's parser.
    json_endpoints = {
        f"/api/task/async-apply/{task_name}",
        f"/api/task/send-task/{task_name}",
    }

    for method, path, form in endpoints:
        if path in json_endpoints:
            payload = b'{"args":["outsider","[]"]}'
            request = urllib.request.Request(
                base + path,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    status, body = response.status, response.read().decode()
            except urllib.error.HTTPError as exc:
                status, body = exc.code, exc.read().decode()
        else:
            status, body, _headers = _http(method, base + path, data=form)
        _assert_closed(status, body, marker, f"{method} {path}")

    time.sleep(1.5)
    after = AsyncResult(task_id, app=celery_app).state
    assert after != "REVOKED", after
    assert after == "STARTED", after

    ping = celery_app.control.inspect(timeout=2.0).ping() or {}
    assert any(exposure["worker_name"] in name for name in ping), ping


def test_metrics_and_health_do_not_include_task_arguments(exposure):
    """Flower leaves /healthcheck and /metrics off the auth decorator.

    They must stay free of ingestion arguments. This is the only pair of
    routes the tests allow to answer an outsider with 200.
    """
    if not exposure["exposed"]:
        return
    base = exposure["base"]
    for path in ("/healthcheck", "/metrics"):
        status, body, _headers = _http("GET", base + path)
        assert exposure["ingest_job"] not in body
        assert exposure["flight_number"] not in body
        assert exposure["compare_job"] not in body
        assert status == 200, (path, status, body[:200])
