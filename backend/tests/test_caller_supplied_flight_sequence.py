"""Caller-supplied flight sequence numbers must not label a live flight.

These tests are the spec for the ingest sequence bug. They fail while a
request can store an integer that is not the PostgreSQL
otp.flight_xml_current.flight_sequence_number for that flight number,
origin, and date. They pass once a caller-supplied sequence is ignored,
or accepted only when it equals that operational row. When no operational
row exists, the flights document must not gain a sequence.

The synchronous ingest route and the batch ingestion task both call
run_feeder. The CLI entry point (python -m backend.feeder.runner) reads a
local file and is not covered here.

Sabre is not contacted. requests.post is replaced with responses the real
SabreClient parser accepts: a session envelope, the captured successful
ACS_FlightDetailRS sample, and HTTP 500 for every later Sabre action.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["POSTGRES_URI"] = (
    "dbname=falcon_sequence_test host=/var/run/postgresql user=ubuntu"
)
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "placeholder-org"
os.environ["SABRE_DOMAIN"] = "placeholder.example"

import psycopg2
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from backend.api.main import app
from backend.api.routes import flights as flights_routes
from backend.celery_app import app as celery_app
from backend.feeder.storage import DB_NAME

AIRLINE = "GF"
FLIGHT_NUMBER = "2006"
ORIGIN = "LHR"
DEPARTURE_DATE = "2026-03-19"
DEPARTURE_DATETIME = "2026-03-19T21:50:00"
# Operational sequence for GF2006 LHR on this date.
OPERATIONAL_SEQUENCE = 1001
# Operational sequence for a different flight (GF2057 BOM).
OTHER_FLIGHT_SEQUENCE = 7777
# Integer that matches no otp.flight_xml_current row.
UNKNOWN_SEQUENCE = 424242
UNLISTED_FLIGHT = "3001"
SEEDED_PASSENGER_PNR = "KEEPME"

_SAMPLE_FLIGHT_STATUS = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml"
)


class _SoapResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _session_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        "<soap-env:Body></soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _install_sabre_transport(monkeypatch):
    """Serve the captured success sample through SabreClient._post."""
    sample = _SAMPLE_FLIGHT_STATUS.read_text(encoding="utf-8")

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        if action == "SessionCreateRQ":
            return _SoapResponse(200, _session_xml())
        if action == "SessionCloseRQ":
            return _SoapResponse(200, "<ok/>")
        if action == "ACS_FlightDetailRQ":
            return _SoapResponse(200, sample)
        return _SoapResponse(500, "downstream unavailable")

    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)


def _ensure_database():
    admin = psycopg2.connect(dbname="postgres", host="/var/run/postgresql", user="ubuntu")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("falcon_sequence_test",))
        if cur.fetchone() is None:
            cur.execute("CREATE DATABASE falcon_sequence_test")
    admin.close()

    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS otp")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
                flight_sequence_number integer NOT NULL,
                flight_number text NOT NULL,
                scheduled_origin text NOT NULL,
                flight_date date NOT NULL
            )
            """
        )
        cur.execute("TRUNCATE otp.flight_xml_current")
        cur.execute(
            """
            INSERT INTO otp.flight_xml_current
                (flight_sequence_number, flight_number, scheduled_origin, flight_date)
            VALUES
                (%s, %s, %s, %s),
                (%s, %s, %s, %s)
            """,
            (
                OPERATIONAL_SEQUENCE, FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE,
                OTHER_FLIGHT_SEQUENCE, "2057", "BOM", DEPARTURE_DATE,
            ),
        )
    conn.close()


def _mongo():
    client = MongoClient(os.environ["MONGODB_URI"])
    return client, client[DB_NAME]


def _reset_mongo(db):
    for name in (
        "flights",
        "flight_status",
        "passenger_list",
        "snapshots",
        "sabre_requests",
        "changes",
        "reservations",
        "trip_reports",
        "flight_schedules",
    ):
        db[name].delete_many({})
    flights_routes._dashboard_cache.clear()


def _seed_live_flight(db, flight_number, sequence):
    """A flight already on the dashboard, with its own passenger row."""
    db["flights"].insert_one({
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "flightSequenceNumber": sequence,
    })
    db["flight_status"].insert_one({
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2020-01-01T00:00:00Z",
        "status": "SEEDED",
        "gate": "",
        "aircraft": {"type": ""},
        "legs": [],
        "schedule": {},
    })
    db["passenger_list"].insert_one({
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2020-01-01T00:00:00Z",
        "passengers": [{
            "pnr": SEEDED_PASSENGER_PNR,
            "lastName": "UNTOUCHED",
        }],
    })


def _ingest_body(flight_number, sequence):
    body = {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": DEPARTURE_DATETIME,
    }
    if sequence is not None:
        body["flightSequenceNumber"] = sequence
    return body


def _stored_sequence(db, flight_number):
    doc = db["flights"].find_one(
        {
            "airline": AIRLINE,
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
        },
        {"flightSequenceNumber": 1},
    )
    if doc is None:
        return None
    return doc.get("flightSequenceNumber")


def _dashboard_sequence(client, flight_number):
    flights_routes._dashboard_cache.clear()
    response = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    return response


def _list_sequence(client, flight_number):
    response = client.get("/flights", params={"date": DEPARTURE_DATE})
    assert response.status_code == 200, response.text
    for row in response.json():
        if row.get("flightNumber") == flight_number and row.get("origin") == ORIGIN:
            return row.get("flightSequenceNumber")
    return None


@pytest.fixture
def api(monkeypatch):
    _ensure_database()
    _install_sabre_transport(monkeypatch)
    mongo_client, db = _mongo()
    _reset_mongo(db)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    with TestClient(app) as client:
        yield client, db
    mongo_client.close()


def test_matching_sequence_is_ingested_from_sabre(api):
    """A sequence that equals the operational row may be stored."""
    client, db = api
    response = client.post(
        "/flights/ingest",
        json=_ingest_body(FLIGHT_NUMBER, OPERATIONAL_SEQUENCE),
    )
    # Later Sabre actions are unavailable; flight status still persists.
    assert response.status_code in (200, 409), response.text
    status = db["flight_status"].find_one(
        {"flightNumber": FLIGHT_NUMBER, "origin": ORIGIN, "departureDate": DEPARTURE_DATE},
        sort=[("fetchedAt", -1)],
    )
    assert status is not None
    assert status.get("status") == "OPENCI"
    assert status.get("gate") == "B41"
    assert _stored_sequence(db, FLIGHT_NUMBER) == OPERATIONAL_SEQUENCE
    dashboard = _dashboard_sequence(client, FLIGHT_NUMBER)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightSequenceNumber"] == OPERATIONAL_SEQUENCE


def test_omitted_sequence_uses_operational_row(api):
    """When the caller omits the sequence, the PostgreSQL row is stored."""
    client, db = api
    response = client.post("/flights/ingest", json=_ingest_body(FLIGHT_NUMBER, None))
    assert response.status_code in (200, 409), response.text
    assert _stored_sequence(db, FLIGHT_NUMBER) == OPERATIONAL_SEQUENCE
    dashboard = _dashboard_sequence(client, FLIGHT_NUMBER)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightSequenceNumber"] == OPERATIONAL_SEQUENCE


def test_sync_ingest_does_not_apply_another_flights_sequence(api):
    """POST /flights/ingest must not relabel this flight with another flight's sequence."""
    client, db = api
    _seed_live_flight(db, FLIGHT_NUMBER, OPERATIONAL_SEQUENCE)
    response = client.post(
        "/flights/ingest",
        json=_ingest_body(FLIGHT_NUMBER, OTHER_FLIGHT_SEQUENCE),
    )
    stored = _stored_sequence(db, FLIGHT_NUMBER)
    assert stored == OPERATIONAL_SEQUENCE, (
        f"POST /flights/ingest stored flightSequenceNumber={stored!r} "
        f"after the caller sent {OTHER_FLIGHT_SEQUENCE} "
        f"(operational row is {OPERATIONAL_SEQUENCE}; HTTP {response.status_code})"
    )
    dashboard = _dashboard_sequence(client, FLIGHT_NUMBER)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightSequenceNumber"] == OPERATIONAL_SEQUENCE
    assert _list_sequence(client, FLIGHT_NUMBER) == OPERATIONAL_SEQUENCE
    kept = db["passenger_list"].find_one(
        {"flightNumber": FLIGHT_NUMBER, "passengers.pnr": SEEDED_PASSENGER_PNR}
    )
    assert kept is not None


def test_batch_ingest_does_not_apply_another_flights_sequence(api):
    """POST /flights/ingest/batch must not relabel this flight either."""
    client, db = api
    _seed_live_flight(db, FLIGHT_NUMBER, OPERATIONAL_SEQUENCE)
    response = client.post(
        "/flights/ingest/batch",
        json={"flights": [_ingest_body(FLIGHT_NUMBER, OTHER_FLIGHT_SEQUENCE)]},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    job = client.get(f"/flights/ingest/jobs/{job_id}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] in {"completed", "partial", "failed"}, job.text
    stored = _stored_sequence(db, FLIGHT_NUMBER)
    assert stored == OPERATIONAL_SEQUENCE, (
        f"batch ingest stored flightSequenceNumber={stored!r} "
        f"after the caller sent {OTHER_FLIGHT_SEQUENCE} "
        f"(operational row is {OPERATIONAL_SEQUENCE}; job {job.json()['status']})"
    )
    dashboard = _dashboard_sequence(client, FLIGHT_NUMBER)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightSequenceNumber"] == OPERATIONAL_SEQUENCE
    assert _list_sequence(client, FLIGHT_NUMBER) == OPERATIONAL_SEQUENCE


def test_sequence_without_operational_row_is_not_stored(api):
    """A caller integer is not stored when PostgreSQL has no row for the flight."""
    client, db = api
    _seed_live_flight(db, UNLISTED_FLIGHT, None)
    # Insert stored None explicitly; remove the key so the live record has no sequence.
    db["flights"].update_one(
        {"flightNumber": UNLISTED_FLIGHT},
        {"$unset": {"flightSequenceNumber": ""}},
    )
    response = client.post(
        "/flights/ingest",
        json=_ingest_body(UNLISTED_FLIGHT, UNKNOWN_SEQUENCE),
    )
    stored = _stored_sequence(db, UNLISTED_FLIGHT)
    assert stored is None, (
        f"ingest stored flightSequenceNumber={stored!r} for a flight with no "
        f"operational row (caller sent {UNKNOWN_SEQUENCE}; HTTP {response.status_code})"
    )
    dashboard = _dashboard_sequence(client, UNLISTED_FLIGHT)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightSequenceNumber"] is None
