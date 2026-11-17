"""Security specs for the Compose MongoDB service and Sabre token archive.

These tests describe the secure behavior. They fail while the Compose service
publishes MongoDB with a committed administrator credential, and while ingestion stores
Sabre session tokens in sabre_requests.

get_passenger_data builds a session token into its SOAP request and does not
write sabre_requests, so it is not part of the persistence spec.
"""

import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

# Placeholders only. The product credential is read from docker-compose.yml
# at runtime and is never copied into this file.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://sabre.invalid"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-sabre-user"
os.environ["SABRE_PASSWORD"] = "placeholder-sabre-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "BAH"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"

import pytest
import yaml
from pymongo import MongoClient
from pymongo.errors import PyMongoError

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SESSION_TOKEN = "PLACEHOLDER_SABRE_SESSION_TOKEN"
FIXTURE_DB_USER = "falconeye-test"
FIXTURE_DB_PASSWORD = "placeholder-db-password"
TOKEN_CONTAINER = "falconeye_token_fixture"
TOKEN_PORT = 27018
CREDENTIAL_KEYS = (
    "MONGO_INITDB_ROOT_USERNAME",
    "MONGO_INITDB_ROOT_PASSWORD",
    "MONGODB_INITDB_ROOT_USERNAME",
    "MONGODB_INITDB_ROOT_PASSWORD",
)
PERSISTED_API_TYPES = (
    "FlightStatus",
    "PassengerList",
    "Reservations",
    "TripReport_MLX",
    "TripReport_MLC",
    "FlightSchedule",
)


def _run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _docker(*args, check=True):
    """Run docker, retrying with sudo when the socket is not usable."""
    result = _run(["docker", *args])
    combined = f"{result.stderr}\n{result.stdout}".lower()
    if result.returncode != 0 and (
        "permission denied" in combined or "cannot connect to the docker daemon" in combined
    ):
        result = _run(["sudo", "docker", *args])
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed with status {result.returncode}")
    return result


def _compose(*args, check=True):
    return _docker("compose", "-f", str(COMPOSE_FILE), *args, check=check)


def _load_compose():
    with COMPOSE_FILE.open() as handle:
        return yaml.safe_load(handle)


def _mongo_service():
    return _load_compose()["services"]["mongodb"]


def _environment_map(service):
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    mapped = {}
    for item in raw:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            mapped[key] = value
    return mapped


def _is_secret_reference(value):
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("${") and "}" in text


def _committed_credential_keys(service):
    env = _environment_map(service)
    return [key for key in CREDENTIAL_KEYS if key in env and not _is_secret_reference(env[key])]


def _published_ports(service):
    return list(service.get("ports") or [])


def _host_port(entry):
    if isinstance(entry, dict):
        published = entry.get("published")
        return int(published) if published not in (None, "") else None
    parts = str(entry).split(":")
    if len(parts) == 1:
        return int(parts[0])
    if len(parts) == 2:
        return int(parts[0])
    return int(parts[-2])


def _compose_admin_credentials():
    env = _environment_map(_mongo_service())
    username = env.get("MONGODB_INITDB_ROOT_USERNAME", env.get("MONGO_INITDB_ROOT_USERNAME"))
    password = env.get("MONGODB_INITDB_ROOT_PASSWORD", env.get("MONGO_INITDB_ROOT_PASSWORD"))
    return username, password


def _client(port, username=None, password=None):
    kwargs = {"serverSelectionTimeoutMS": 4000, "connectTimeoutMS": 4000}
    if username is not None and password is not None:
        kwargs.update(username=username, password=password, authSource="admin")
    return MongoClient("127.0.0.1", port, **kwargs)


def _can_read_and_write(client):
    try:
        client["falconeye"]["exposure_probe"].insert_one({"probe": 1})
        found = client["falconeye"]["exposure_probe"].find_one({"probe": 1})
        client["falconeye"]["exposure_probe"].delete_many({"probe": 1})
        return found is not None
    except PyMongoError:
        return False
    finally:
        client.close()


def _wait_for_port(port, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _reset_db_handles():
    from backend.api import database
    from backend.feeder import storage

    storage.close()
    storage._client = None
    storage._db = None
    storage._owns_connection = False
    database.close_db()


def _mongo_uri(port, username, password):
    return (
        f"mongodb://{quote_plus(str(username))}:{quote_plus(str(password))}"
        f"@127.0.0.1:{port}/?authSource=admin"
    )


def _soap_response(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        f"<wsse:BinarySecurityToken>{SESSION_TOKEN}</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        f"<soap-env:Body>{body_xml}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


_RESPONSE_BODIES = {
    "SessionCreateRQ": "<SessionCreateRS><status>Approved</status></SessionCreateRS>",
    "ACS_FlightDetailRQ": "<ns3:ACS_FlightDetailRS><marker>1</marker></ns3:ACS_FlightDetailRS>",
    "GetPassengerListRQ": (
        "<GetPassengerListRS><PassengerInfoList><PassengerInfo>"
        "<PNRLocator>ABC123</PNRLocator>"
        "<Name_Details><LastName>SMITH</LastName><FirstName>JOHN</FirstName></Name_Details>"
        "</PassengerInfo></PassengerInfoList></GetPassengerListRS>"
    ),
    "Trip_SearchRQ": "<Trip_SearchRS><marker>1</marker></Trip_SearchRS>",
    "Trip_ReportsRQ": "<Trip_ReportsRS><marker>1</marker></Trip_ReportsRS>",
    "VerifyFlightDetailsLLSRQ": (
        "<VerifyFlightDetailsRS><ApplicationResults status=\"Complete\">"
        "<Success/></ApplicationResults><marker>1</marker></VerifyFlightDetailsRS>"
    ),
    "SessionCloseRQ": "<SessionCloseRS><marker>1</marker></SessionCloseRS>",
}


class _FakeResponse:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


def _install_sabre_stub(monkeypatch):
    captured = []

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        if isinstance(data, bytes):
            body = data.decode()
        else:
            body = data or ""
        if action != "SessionCreateRQ":
            captured.append(body)
        payload = _RESPONSE_BODIES.get(action, "<UnusedRS><marker>1</marker></UnusedRS>")
        return _FakeResponse(_soap_response(payload))

    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)
    return captured


def _run_ingestion(flight_number):
    from backend.api.routes.schedule import ScheduleLookupRequest, lookup_schedule
    from backend.feeder.runner import run_feeder

    flight = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T08:00:00",
    }
    results = run_feeder([flight])
    asyncio.run(
        lookup_schedule(
            ScheduleLookupRequest(
                airline="GF",
                flightNumber=flight_number,
                departureDate="2026-03-19",
            )
        )
    )
    return results


@pytest.fixture(scope="module")
def compose_mongodb():
    _compose("down", "-v", "--remove-orphans", check=False)
    _docker("rm", "-f", "falconeye_mongodb", check=False)
    _compose("up", "-d", "mongodb")
    ports = _published_ports(_mongo_service())
    host_ports = [port for port in (_host_port(entry) for entry in ports) if port]
    if host_ports:
        assert _wait_for_port(host_ports[0]), "published MongoDB port did not accept connections"
    yield {"ports": host_ports}
    _compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture(scope="module")
def token_mongodb():
    image = _mongo_service()["image"]
    _docker("rm", "-f", TOKEN_CONTAINER, check=False)
    _docker(
        "run",
        "-d",
        "--name",
        TOKEN_CONTAINER,
        "-p",
        f"127.0.0.1:{TOKEN_PORT}:27017",
        "-e",
        f"MONGO_INITDB_ROOT_USERNAME={FIXTURE_DB_USER}",
        "-e",
        f"MONGO_INITDB_ROOT_PASSWORD={FIXTURE_DB_PASSWORD}",
        image,
    )
    assert _wait_for_port(TOKEN_PORT), "fixture MongoDB port did not accept connections"
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        if _can_read_and_write(_client(TOKEN_PORT, FIXTURE_DB_USER, FIXTURE_DB_PASSWORD)):
            ready = True
            break
        time.sleep(1)
    assert ready, "fixture MongoDB did not accept its placeholder login"
    yield
    _docker("rm", "-f", TOKEN_CONTAINER, check=False)


def test_compose_mongodb_is_not_published_on_a_host_port():
    published = _published_ports(_mongo_service())
    assert published == []


def test_compose_mongodb_credential_is_not_committed():
    committed = _committed_credential_keys(_mongo_service())
    assert committed == []


def test_published_committed_credential_cannot_read_or_write(compose_mongodb):
    username, password = _compose_admin_credentials()
    ports = compose_mongodb["ports"]
    targets = ports or [27017]
    for port in targets:
        anonymous = _client(port)
        committed = _client(port, username, password)
        assert _can_read_and_write(anonymous) is False
        assert _can_read_and_write(committed) is False


def test_ingestion_paths_do_not_persist_sabre_session_tokens(token_mongodb, monkeypatch):
    os.environ["MONGODB_URI"] = _mongo_uri(TOKEN_PORT, FIXTURE_DB_USER, FIXTURE_DB_PASSWORD)
    _reset_db_handles()
    captured = _install_sabre_stub(monkeypatch)
    _run_ingestion("9001")

    from backend.feeder.storage import get_db

    docs = list(get_db()["sabre_requests"].find({}))
    api_types = [doc.get("apiType") for doc in docs]
    assert captured, "authenticated Sabre calls did not embed a session token"
    assert all(SESSION_TOKEN in body for body in captured)
    for api_type in PERSISTED_API_TYPES:
        assert api_type in api_types
    assert api_types.count("FlightSchedule") >= 2
    for doc in docs:
        archived = json.dumps(doc, default=str)
        assert SESSION_TOKEN not in archived
        assert not (doc.get("metadata") or {}).get("sessionToken")


def test_outsider_cannot_read_tokens_or_rewrite_dashboard_passengers(
    compose_mongodb, monkeypatch
):
    username, password = _compose_admin_credentials()
    ports = compose_mongodb["ports"]
    targets = ports or [27017]
    for port in targets:
        assert _can_read_and_write(_client(port)) is False
        if not _can_read_and_write(_client(port, username, password)):
            continue

        os.environ["MONGODB_URI"] = _mongo_uri(port, username, password)
        _reset_db_handles()
        captured = _install_sabre_stub(monkeypatch)
        _run_ingestion("9002")
        assert captured, "ingestion did not send a Sabre session token"

        outsider = _client(port, username, password)
        problems = []
        try:
            archived = list(outsider["falconeye"]["sabre_requests"].find({}))
            if not archived:
                problems.append("no-archived-requests")
            elif any(SESSION_TOKEN in json.dumps(doc, default=str) for doc in archived):
                problems.append("session-token-readable")

            from backend.api.routes.passengers import get_passengers

            before = get_passengers(
                "9002", origin="BAH", date="2026-03-19", snapshot_sequence=None
            )
            original_name = before["passengers"][0]["lastName"]
            result = outsider["falconeye"]["passenger_list"].update_one(
                {"flightNumber": "9002"},
                {"$set": {"passengers.0.lastName": "OUTSIDER_REWRITE"}},
            )
            after = get_passengers(
                "9002", origin="BAH", date="2026-03-19", snapshot_sequence=None
            )
            if result.modified_count and after["passengers"][0]["lastName"] != original_name:
                problems.append("passenger-list-rewritten")
        finally:
            outsider.close()
        assert problems == []
