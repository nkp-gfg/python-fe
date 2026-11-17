"""Spec for the Compose Redis broker.

These tests fail while Redis accepts connections that do not authenticate.
They pass once the broker requires a password, that password is part of the
broker URL, and Compose does not publish the Redis port on the host.

Run with Redis 7.4.x `redis-server` and `mongod` on PATH, and a Celery
release whose Kombu still accepts redis-py 5 (the range in requirements.txt).
Celery 5.6.3 with Kombu 5.6.x does; Celery 5.7 pulls Kombu 5.7, which refuses
redis-py 5.

    pip install 'celery==5.6.3' 'kombu>=5.6,<5.7' 'redis>=5.0,<6' pytest
    pytest backend/tests/test_unauthenticated_redis_broker.py
"""

import os
import re
import signal
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SAMPLE_FLIGHT_STATUS = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml"
)

# Obvious placeholders. These are not credentials.
SABRE_PASSWORD = "placeholder-sabre-password"
SABRE_USERNAME = "placeholder-sabre-username"
REDIS_PASSWORD_PLACEHOLDER = "placeholder-redis-password"
ATTACK_FLIGHT = "4242"

SESSION_RESPONSE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
      <soap-env:Header>
        <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
          <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
        </wsse:Security>
      </soap-env:Header>
      <soap-env:Body></soap-env:Body>
    </soap-env:Envelope>
    """
).encode()


def _service_lines(text: str, name: str) -> list[str]:
    lines = text.splitlines()
    collecting = False
    block: list[str] = []
    for line in lines:
        if re.match(rf"^  {re.escape(name)}:\s*$", line):
            collecting = True
            continue
        if collecting:
            if re.match(r"^  \S", line):
                break
            block.append(line)
    if not block:
        raise AssertionError(f"docker-compose.yml has no {name} service")
    return block


def _interpolate(value: str, env: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        default = match.group(2)
        if var in env and env[var] != "":
            return env[var]
        if default is not None:
            return default
        env[var] = REDIS_PASSWORD_PLACEHOLDER
        return env[var]

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", repl, value)


def _published_host_ports(block: list[str]) -> list[str]:
    ports: list[str] = []
    in_ports = False
    for line in block:
        if re.match(r"^    ports:\s*$", line):
            in_ports = True
            continue
        if not in_ports:
            continue
        if re.match(r"^    \S", line):
            break
        item = line.strip().lstrip("-").strip().strip("'\"")
        if not item or item.startswith("#"):
            continue
        published = re.search(r"published:\s*['\"]?(\d+)", item)
        if published:
            ports.append(published.group(1))
            continue
        # Short syntax [IP:]HOST:CONTAINER
        parts = item.split(":")
        if len(parts) == 2 and parts[0].isdigit():
            ports.append(parts[0])
        elif len(parts) == 3 and parts[1].isdigit():
            ports.append(parts[1])
    return ports


def _command_tokens(block: list[str], env: dict[str, str]) -> list[str]:
    for index, line in enumerate(block):
        match = re.match(r"^    command:\s*(.*)$", line)
        if not match:
            continue
        rest = match.group(1).strip()
        if rest.startswith("[") or rest == "" or rest in (">", "|", ">-", "|-"):
            tokens: list[str] = []
            if rest.startswith("["):
                tokens = [
                    _interpolate(part.strip().strip("'\""), env)
                    for part in rest.strip("[]").split(",")
                    if part.strip()
                ]
                return tokens
            for follower in block[index + 1 :]:
                if re.match(r"^    \S", follower):
                    break
                item = follower.strip().lstrip("-").strip().strip("'\"")
                if item:
                    tokens.append(_interpolate(item, env))
            return tokens
        return _interpolate(rest, env).split()
    return []


def _requirepass(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if token == "--requirepass" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--requirepass="):
            return token.split("=", 1)[1]
        if token == "requirepass" and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _redis_urls(text: str) -> list[str]:
    return re.findall(r"redis(?:s)?://[^\s'\"\\]+", text)


def _passwordless(urls: list[str]) -> list[str]:
    found = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.password in (None, ""):
            found.append(url)
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _non_loopback_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))
        address = probe.getsockname()[0]
    finally:
        probe.close()
    if address.startswith("127."):
        raise RuntimeError("this host has no non-loopback IPv4 address to act as a neighbor")
    return address


def _wait_until(predicate, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _SabreHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - stdlib handler name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(body)  # type: ignore[attr-defined]
        if b"SessionCreateRQ" in body:
            payload = SESSION_RESPONSE
            status = 200
        elif b"ACS_FlightDetailRQ" in body:
            payload = self.server.flight_status  # type: ignore[attr-defined]
            status = 200
        else:
            payload = b"rejected"
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def _start_process(argv: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        start_new_session=True,
        **kwargs,
    )


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def test_neighbor_cannot_read_jobs_or_enqueue_ingestion():
    """A host-network neighbor must not read Redis or run background tasks.

    The neighbor connects without AUTH, from a non-loopback address, to Redis
    started the way the Compose redis service starts it (official redis:7-alpine
    listens on every interface with protected mode compiled off and with no
    password unless the service command sets one).
    """
    compose = COMPOSE_FILE.read_text()
    env = dict(os.environ)
    redis_block = _service_lines(compose, "redis")
    flower_block = _service_lines(compose, "flower")
    published = _published_host_ports(redis_block)
    command = _command_tokens(redis_block, env)
    password = _requirepass(command)

    problems: list[str] = []
    if published:
        problems.append(
            "Compose publishes Redis on the host at port(s) " + ", ".join(published)
        )

    passwordless_files = {
        "docker-compose.yml": _passwordless(_redis_urls(compose)),
        "backend/celery_app.py": _passwordless(
            _redis_urls((REPO_ROOT / "backend" / "celery_app.py").read_text())
        ),
        "backend/api/redis_client.py": _passwordless(
            _redis_urls((REPO_ROOT / "backend" / "api" / "redis_client.py").read_text())
        ),
    }
    for name, urls in passwordless_files.items():
        if urls:
            problems.append(f"{name} has passwordless Redis URL(s): {', '.join(urls)}")

    flower_text = "\n".join(flower_block)
    flower_urls = _passwordless(_redis_urls(flower_text))
    if flower_urls:
        problems.append(
            "Flower is configured with passwordless Redis URL(s): " + ", ".join(flower_urls)
        )

    redis_bin = os.environ.get("REDIS_SERVER", "redis-server")
    mongod_bin = os.environ.get("MONGOD", "mongod")
    port = _free_port()
    mongo_port = _free_port()
    sabre_port = _free_port()
    neighbor = _non_loopback_ip()
    data_dir = Path(f"/tmp/falconeye-redis-broker-test-{uuid.uuid4().hex}")
    mongo_dir = Path(f"/tmp/falconeye-mongo-broker-test-{uuid.uuid4().hex}")
    data_dir.mkdir()
    mongo_dir.mkdir()

    if command:
        argv = [redis_bin, *command]
        if not any(token == "--port" or token.startswith("--port=") for token in argv):
            argv.extend(["--port", str(port)])
        if not any(token == "--bind" or token.startswith("--bind=") for token in argv):
            argv.extend(["--bind", "0.0.0.0"])
        if "--protected-mode" not in " ".join(argv):
            # redis:7-alpine compiles the protected-mode default to off.
            argv.extend(["--protected-mode", "no"])
    else:
        argv = [
            redis_bin,
            "--bind", "0.0.0.0",
            "--port", str(port),
            "--protected-mode", "no",
            "--save", "",
            "--appendonly", "no",
            "--dir", str(data_dir),
            "--dbfilename", "dump.rdb",
        ]
    if "--dir" not in argv and not any(token.startswith("--dir=") for token in argv):
        argv.extend(["--dir", str(data_dir)])

    app_host = "127.0.0.1"
    if password:
        app_url = f"redis://:{quote(password, safe='')}@{app_host}:{port}/0"
    else:
        app_url = f"redis://{app_host}:{port}/0"
    os.environ["REDIS_URL"] = app_url

    redis_proc = mongo_proc = worker = None
    httpd = None
    redis_log = data_dir / "redis.log"
    mongo_log = mongo_dir / "mongod.log"
    worker_log_path = data_dir / "worker.log"
    try:
        redis_log_handle = redis_log.open("w")
        redis_proc = _start_process(argv, stdout=redis_log_handle, stderr=subprocess.STDOUT, cwd=str(data_dir))
        mongo_log_handle = mongo_log.open("w")
        mongo_proc = _start_process(
            [
                mongod_bin,
                "--dbpath", str(mongo_dir),
                "--bind_ip", "127.0.0.1",
                "--port", str(mongo_port),
                "--nounixsocket",
            ],
            stdout=mongo_log_handle,
            stderr=subprocess.STDOUT,
        )

        import redis
        from pymongo import MongoClient

        def redis_up() -> bool:
            try:
                client = redis.Redis(
                    host=app_host,
                    port=port,
                    password=password,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                )
                return client.ping() is True
            except Exception:
                return False

        def mongo_up() -> bool:
            try:
                MongoClient(
                    f"mongodb://127.0.0.1:{mongo_port}",
                    serverSelectionTimeoutMS=200,
                ).admin.command("ping")
                return True
            except Exception:
                return False

        if not _wait_until(redis_up, 10):
            raise RuntimeError(f"redis-server did not start:\n{redis_log.read_text()[-2000:]}")
        if not _wait_until(mongo_up, 20):
            raise RuntimeError(f"mongod did not start:\n{mongo_log.read_text()[-2000:]}")

        httpd = ThreadingHTTPServer(("127.0.0.1", sabre_port), _SabreHandler)
        httpd.requests = []  # type: ignore[attr-defined]
        httpd.flight_status = SAMPLE_FLIGHT_STATUS.read_bytes()  # type: ignore[attr-defined]
        import threading

        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        worker_env = os.environ.copy()
        worker_env.update(
            {
                "REDIS_URL": app_url,
                "MONGODB_URI": f"mongodb://127.0.0.1:{mongo_port}",
                "SABRE_BASE_URL": f"http://127.0.0.1:{sabre_port}",
                "SABRE_CPAID": "placeholder-cpaid",
                "SABRE_USERNAME": SABRE_USERNAME,
                "SABRE_PASSWORD": SABRE_PASSWORD,
                "SABRE_PSEUDO_CITY_CODE": "placeholder-pcc",
                "SABRE_ORGANIZATION": "placeholder-org",
                "SABRE_DOMAIN": "placeholder-domain",
                "SABRE_API_DELAY_SECONDS": "0",
                "PYTHONPATH": str(REPO_ROOT),
            }
        )
        worker_log = worker_log_path.open("w")
        worker = _start_process(
            [
                sys.executable,
                "-m", "celery",
                "-A", "backend.celery_app",
                "worker",
                "--pool=threads",
                "--concurrency=4",
                "--loglevel=info",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ],
            cwd=str(REPO_ROOT),
            env=worker_env,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
        )

        def worker_ready() -> bool:
            if worker.poll() is not None:
                return True
            return " ready." in worker_log_path.read_text(errors="replace")

        if not _wait_until(worker_ready, 30) or worker.poll() is not None:
            raise RuntimeError(
                "Celery worker did not become ready:\n"
                + worker_log_path.read_text(errors="replace")[-4000:]
            )

        sys.path.insert(0, str(REPO_ROOT))
        from backend.api import redis_client

        redis_client.close_redis()
        job_id = str(uuid.uuid4())
        compare_job = str(uuid.uuid4())
        pnr_job = str(uuid.uuid4())
        redis_client.create_job(job_id, 1, job_type="ingestion")
        redis_client.create_job(compare_job, 1, job_type="comparison")
        redis_client.create_job(pnr_job, 1, job_type="pnr_comparison")
        redis_client.cache_set("flights:neighbor-test", {"flightNumber": ATTACK_FLIGHT}, ttl=60)

        received_events: list[str] = []

        def listen():
            try:
                attacker_events = redis.Redis(
                    host=neighbor,
                    port=port,
                    password=None,
                    socket_timeout=3,
                    decode_responses=True,
                )
                pubsub = attacker_events.pubsub()
                pubsub.subscribe(redis_client.SSE_CHANNEL)
                started = time.time()
                while time.time() - started < 3:
                    message = pubsub.get_message(timeout=1)
                    if message and message.get("type") == "message":
                        received_events.append(message.get("data") or "")
                        return
            except Exception:
                return

        listener = threading.Thread(target=listen)
        listener.start()
        time.sleep(0.3)
        redis_client.publish_event(job_id, "progress", {"message": "neighbor-test"})
        listener.join(timeout=4)

        attacker = redis.Redis(
            host=neighbor,
            port=port,
            password=None,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        try:
            ping_ok = attacker.ping() is True
        except Exception:
            ping_ok = False
        job_raw = cache_raw = None
        if ping_ok:
            try:
                job_raw = attacker.get(f"job:{job_id}")
            except Exception:
                job_raw = None
            try:
                cache_raw = attacker.get("cache:flights:neighbor-test")
            except Exception:
                cache_raw = None

        if ping_ok:
            problems.append(
                f"unauthenticated PING from {neighbor}:{port} succeeded"
            )
        if job_raw and ATTACK_FLIGHT not in job_raw and job_id in job_raw:
            problems.append("unauthenticated client read the ingestion job record")
        elif job_raw:
            problems.append("unauthenticated client read the ingestion job record")
        if cache_raw and ATTACK_FLIGHT in cache_raw:
            problems.append("unauthenticated client read the flight cache")
        if any("neighbor-test" in event for event in received_events):
            problems.append("unauthenticated client received the job event channel")

        enqueued = False
        if ping_ok:
            from celery import Celery

            attacker_url = f"redis://{neighbor}:{port}/0"
            attacker_app = Celery("neighbor", broker=attacker_url, backend=attacker_url)
            attacker_app.conf.update(
                task_serializer="json",
                accept_content=["json"],
                result_serializer="json",
            )
            payload = [
                {
                    "airline": "GF",
                    "flightNumber": ATTACK_FLIGHT,
                    "origin": "LHR",
                    "departureDate": "2026-03-19",
                    "departureDateTime": "2026-03-19T08:00:00",
                }
            ]
            try:
                attacker_app.send_task(
                    "falconeye.ingest_batch", args=[job_id, payload]
                )
                attacker_app.send_task(
                    "falconeye.compare_flights",
                    args=[compare_job, "2026-03-19", None],
                )
                attacker_app.send_task(
                    "falconeye.compare_pnr_cross_db",
                    args=[pnr_job, "2026-03-19"],
                )
                enqueued = True
            except Exception:
                enqueued = False

        mongo = MongoClient(
            f"mongodb://127.0.0.1:{mongo_port}", serverSelectionTimeoutMS=2000
        )
        database = mongo["falconeye"]

        def attack_finished() -> bool:
            bodies = b"\n".join(httpd.requests)  # type: ignore[attr-defined]
            sabre_hit = (
                SABRE_PASSWORD.encode() in bodies and ATTACK_FLIGHT.encode() in bodies
            )
            stored = database["sabre_requests"].count_documents(
                {"flight.flightNumber": ATTACK_FLIGHT}
            ) or database["flights"].count_documents({"flightNumber": ATTACK_FLIGHT})
            ingest = redis_client.get_job(job_id) or {}
            # "running" is only the task having started. Wait until Sabre was
            # called and the flight was stored, or the task reached a terminal state.
            return bool(sabre_hit and stored) or ingest.get("status") in {
                "completed", "partial", "failed",
            }

        if enqueued:
            _wait_until(attack_finished, 25)

        bodies = b"\n".join(httpd.requests)  # type: ignore[attr-defined]
        sabre_called = (
            SABRE_PASSWORD.encode() in bodies and ATTACK_FLIGHT.encode() in bodies
        )
        stored_request = database["sabre_requests"].find_one(
            {"flight.flightNumber": ATTACK_FLIGHT}
        )
        stored_flight = database["flights"].find_one({"flightNumber": ATTACK_FLIGHT})
        ingest_status = (redis_client.get_job(job_id) or {}).get("status")
        compare_status = (redis_client.get_job(compare_job) or {}).get("status")
        pnr_status = (redis_client.get_job(pnr_job) or {}).get("status")

        if sabre_called:
            problems.append(
                "worker sent the attacker flight and the Sabre password from its environment"
            )
        if stored_request is not None or stored_flight is not None:
            problems.append("worker wrote the attacker flight into MongoDB")
        if ingest_status not in (None, "accepted"):
            problems.append(f"ingest task ran (job status {ingest_status})")
        if compare_status not in (None, "accepted"):
            problems.append(f"compare_flights task ran (job status {compare_status})")
        if pnr_status not in (None, "accepted"):
            problems.append(f"compare_pnr_cross_db task ran (job status {pnr_status})")

        if ping_ok:
            try:
                meta_keys = attacker.keys("celery-task-meta-*")
            except Exception:
                meta_keys = []
            if meta_keys:
                problems.append("unauthenticated client read Celery result keys")

        assert not problems, "broker is exposed:\n- " + "\n- ".join(problems)
    finally:
        if httpd is not None:
            httpd.shutdown()
        _stop_process(worker)
        _stop_process(redis_proc)
        _stop_process(mongo_proc)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
