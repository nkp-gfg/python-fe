"""Database failures in cross-database audit must not look like a missing flight.

The comparisons under test catch PostgreSQL and MongoDB errors and treat them
as an empty lookup. These tests call the HTTP routes with a caller-supplied
date. They fail while that substitution is in place, and they pass when a
failed lookup is reported as HTTP 503 and ``pgFound`` / ``mongoFound`` are
false only after a query succeeds and returns no row.

Run (PostgreSQL listening on the local socket):

    python -m pytest backend/tests/test_data_audit_db_errors.py -q
"""

import os
import sys

os.environ["POSTGRES_URI"] = (
    "postgresql:///falconeye_audit_test?options=-c%20lock_timeout%3D400ms"
)
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json
from pymongo import MongoClient

from backend.api.database import DB_NAME
from backend.api.main import app
from backend.api.postgres import close_pool
from backend.feeder import storage as feeder_storage
import backend.api.database as database

FLIGHT = "2057"
ORIGIN = "BAH"
STORED_DATE = "2026-02-28"
INVALID_DATE = "2026-02-31"
ABSENT_DATE = "2026-04-02"
SABRE_ONLY_DATE = "2026-05-01"
AUDIT_PATHS = (
    f"/data-audit/{FLIGHT}/compare",
    f"/data-audit/{FLIGHT}/passengers",
)

_DDL = """
DROP SCHEMA IF EXISTS otp CASCADE;
CREATE SCHEMA otp;
CREATE TABLE otp.flight_xml_current (
    flight_sequence_number integer,
    flight_number text,
    scheduled_origin text,
    scheduled_destination text,
    actual_origin text,
    actual_destination text,
    flight_date date,
    scheduled_departure_utc timestamptz,
    estimated_block_off_utc timestamptz,
    scheduled_arrival_utc timestamptz,
    estimated_block_on_utc timestamptz,
    actual_block_off_utc timestamptz,
    actual_block_on_utc timestamptz,
    actual_takeoff_utc timestamptz,
    actual_touchdown_utc timestamptz,
    scheduled_departure_local timestamp,
    scheduled_arrival_local timestamp,
    flight_status text,
    aircraft_type text,
    aircraft_registration text,
    service_type_code text,
    cancel_reason_code text,
    passenger_counts jsonb,
    delay_details jsonb,
    source text
);
"""


class _MemoryCollection:
    """Equality filter for the query dicts the audit routes build.

    ``find_one`` matches ``Collection.find_one`` in pymongo 4.20.1: the filter
    is applied, ``sort`` is honored, and the first document is returned.
    """

    def __init__(self, docs):
        self._docs = [dict(doc) for doc in docs]

    def find_one(self, filter=None, *args, **kwargs):
        found = self.find(filter, *args, **kwargs)
        return found[0] if found else None

    def find(self, filter=None, *args, **kwargs):
        query = filter or {}
        matches = [dict(doc) for doc in self._docs if _matches(doc, query)]
        sort = kwargs.get("sort")
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, key=key: (
                        doc.get(key) is None,
                        str(doc.get(key)),
                    ),
                    reverse=int(direction) < 0,
                )
        return matches

    def index_information(self):
        return {"flight_lookup": {"key": [("flightNumber", 1)]}}


class _MemoryDB(dict):
    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        if name not in self:
            self[name] = _MemoryCollection([])
        return dict.__getitem__(self, name)


def _matches(doc, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and set(expected) == {"$in"}:
            if doc.get(key) not in expected["$in"]:
                return False
        elif doc.get(key) != expected:
            return False
    return True


def _flight_doc(departure_date, status, gate, passengers):
    return {
        "_id": f"flight-{departure_date}",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": departure_date,
        "flightStatus": status,
        "departureGate": gate,
        "totalPassengers": passengers,
        "aircraftType": "32N",
        "_ingestTimestamp": f"{STORED_DATE}T00:00:00Z",
    }


def _pax_doc(departure_date, passengers):
    return {
        "_id": f"pax-{departure_date}",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": departure_date,
        "totalPassengers": passengers,
        "totalSouls": passengers,
        "adultCount": passengers,
        "childCount": 0,
        "infantCount": 0,
        "passengers": [],
        "cabinSummary": [
            {"cabin": "Y", "count": passengers, "authorized": 150, "available": 30}
        ],
        "fetchedAt": f"{departure_date}T00:00:00Z",
    }


def _sabre_flight(departure_date, status, gate, passengers):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": departure_date,
        "lastUpdatedAt": f"{departure_date}T00:00:00Z",
        "flightSequenceNumber": 7,
        "summary": {
            "status": status,
            "gate": gate,
            "totalPax": passengers,
            "checkedIn": 0,
            "boarded": 0,
        },
    }


def _audit_db():
    return _MemoryDB({
        "flight_status": _MemoryCollection([
            _flight_doc(STORED_DATE, "PDC", "A3", 120),
            _flight_doc(INVALID_DATE, "OPENCI", "A12", 88),
            _flight_doc(ABSENT_DATE, "FINAL", "B4", 10),
        ]),
        "passenger_list": _MemoryCollection([
            _pax_doc(STORED_DATE, 120),
            _pax_doc(INVALID_DATE, 88),
            _pax_doc(ABSENT_DATE, 10),
        ]),
        "flights": _MemoryCollection([
            _sabre_flight(INVALID_DATE, "OPENCI", "A12", 88),
            _sabre_flight(SABRE_ONLY_DATE, "OPENCI", "C1", 40),
        ]),
    })


def _ensure_otp_database():
    admin = psycopg2.connect(dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'falconeye_audit_test'"
        )
        if cur.fetchone() is None:
            cur.execute("CREATE DATABASE falconeye_audit_test")
    admin.close()

    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute(
            """
            INSERT INTO otp.flight_xml_current (
                flight_sequence_number, flight_number, scheduled_origin,
                scheduled_destination, flight_date, flight_status, aircraft_type,
                passenger_counts, scheduled_departure_local, scheduled_arrival_local,
                source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                7,
                FLIGHT,
                ORIGIN,
                "LHR",
                STORED_DATE,
                "PDC",
                "32N",
                Json([{"@classOfService": "Y", "Count": 120}]),
                f"{STORED_DATE} 10:00:00",
                f"{STORED_DATE} 14:00:00",
                "otp",
            ),
        )
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def otp_schema():
    _ensure_otp_database()
    yield


@pytest.fixture
def client():
    close_pool()
    database._client = None
    database._db = _audit_db()
    feeder_storage.init_db(database._db)
    with TestClient(app) as test_client:
        yield test_client
    close_pool()
    database._db = None
    feeder_storage._db = None


@pytest.fixture
def mongo_down_client():
    close_pool()
    mongo = MongoClient(
        "mongodb://127.0.0.1:1",
        serverSelectionTimeoutMS=400,
        connectTimeoutMS=400,
    )
    database._client = mongo
    database._db = mongo[DB_NAME]
    with TestClient(app) as test_client:
        yield test_client
    close_pool()
    mongo.close()
    database._client = None
    database._db = None
    feeder_storage._db = None


def _assert_unavailable(response):
    assert response.status_code == 503, response.text
    body = response.json()
    assert "pgFound" not in body
    assert "mongoFound" not in body
    assert "anomalies" not in body


def _lock_otp_table():
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("LOCK TABLE otp.flight_xml_current IN ACCESS EXCLUSIVE MODE")
    return conn


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_invalid_calendar_date_is_not_a_completed_audit(client, path):
    response = client.get(path, params={"date": INVALID_DATE, "origin": ORIGIN})
    _assert_unavailable(response)


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_locked_otp_table_does_not_report_existing_flight_missing(client, path):
    lock = _lock_otp_table()
    try:
        response = client.get(
            path, params={"date": STORED_DATE, "origin": ORIGIN}
        )
    finally:
        lock.rollback()
        lock.close()
    _assert_unavailable(response)


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_mongo_outage_is_not_a_completed_audit(mongo_down_client, path):
    response = mongo_down_client.get(
        path, params={"date": STORED_DATE, "origin": ORIGIN}
    )
    _assert_unavailable(response)


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_both_stores_failing_is_not_reported_as_not_found(mongo_down_client, path):
    response = mongo_down_client.get(
        path, params={"date": INVALID_DATE, "origin": ORIGIN}
    )
    _assert_unavailable(response)
    assert response.json()["detail"] != (
        f"Flight {FLIGHT} not found in either database"
    )
    assert response.json()["detail"] != (
        f"Passenger data for {FLIGHT} not found in either database"
    )


def test_failed_lookup_does_not_hide_the_stored_flight(client):
    failed = client.get(
        f"/data-audit/{FLIGHT}/compare",
        params={"date": INVALID_DATE, "origin": ORIGIN},
    )
    _assert_unavailable(failed)

    stored = client.get(
        f"/data-audit/{FLIGHT}/compare",
        params={"date": STORED_DATE, "origin": ORIGIN},
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    status = next(row for row in body["rows"] if row["field"] == "Flight Status")
    assert status["match"] == "match"
    assert status["pgValue"] == "PDC"
    assert status["mongoValue"] == "PDC"
    passengers = next(
        row for row in body["rows"] if row["field"] == "Total Passengers"
    )
    assert passengers["match"] == "match"
    assert passengers["pgValue"] == "120"


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_successful_lookup_can_still_report_a_real_absence(client, path):
    response = client.get(path, params={"date": ABSENT_DATE, "origin": ORIGIN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is False
    assert body["mongoFound"] is True
    assert any(row["match"] == "mongo_only" for row in body["rows"])


@pytest.mark.parametrize("path", AUDIT_PATHS)
def test_successful_lookup_reports_both_stores(client, path):
    response = client.get(path, params={"date": STORED_DATE, "origin": ORIGIN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True


def test_batch_compare_invalid_date_is_not_a_sabre_only_anomaly(client):
    response = client.get("/compare/flights", params={"date": INVALID_DATE})
    _assert_unavailable(response)


def test_batch_compare_still_reports_a_real_sabre_only_flight(client):
    response = client.get("/compare/flights", params={"date": SABRE_ONLY_DATE})
    assert response.status_code == 200, response.text
    anomalies = response.json()["anomalies"]
    assert any(
        item["type"] == "SABRE_ONLY" and item["flightNumber"] == FLIGHT
        for item in anomalies
    )
