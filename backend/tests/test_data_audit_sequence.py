"""Data-audit sequence selection.

These tests fail while a comparison requested for one OTP flight sequence can
return Sabre flight fields or passenger totals from a different departure of
the same flight number. They pass once that departure is the one compared.

Run:
    pip install -r requirements.txt pytest httpx psycopg2-binary
    # MongoDB listening on MONGODB_URI (default mongodb://127.0.0.1:27017)
    # PostgreSQL role that can create a database (default postgresql:///falcon_eye)
    pytest backend/tests/test_data_audit_sequence.py -q
"""

import os

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("POSTGRES_URI", "postgresql:///falcon_eye")

import psycopg2
import psycopg2.extras
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.database import get_db
from backend.api.postgres import close_pool
from backend.feeder import storage
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.api.routes.data_audit import router

FLIGHT = "2152"
SELECTED = {
    "seq": 1001,
    "origin": "BAH",
    "destination": "DOH",
    "date": "2026-03-01",
    "status": "ON TIME",
    "passengers": 3,
    "fetched_at": "2026-03-01T01:00:00Z",
}
OTHER = {
    "seq": 2002,
    "origin": "MCT",
    "destination": "BAH",
    "date": "2026-03-02",
    "status": "DELAYED",
    "passengers": 7,
    "fetched_at": "2026-03-02T01:00:00Z",
}

_OTP_DDL = """
CREATE SCHEMA IF NOT EXISTS otp;
CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
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


def _ensure_postgres():
    uri = os.environ["POSTGRES_URI"]
    admin = psycopg2.connect(dbname="postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("falcon_eye",))
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE falcon_eye")
    finally:
        admin.close()

    conn = psycopg2.connect(uri)
    try:
        with conn.cursor() as cur:
            cur.execute(_OTP_DDL)
        conn.commit()
    finally:
        conn.close()
    close_pool()


def _pg_connect():
    return psycopg2.connect(os.environ["POSTGRES_URI"])


def _flight_status_document(departure):
    raw = {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": departure["status"],
                "ScheduledDepartureDate": departure["date"],
                "AircraftType": "A320" if departure["origin"] == "BAH" else "B789",
                "AircraftRegistration": "A9C-" + departure["origin"],
                "DepartureGate": "A1" if departure["origin"] == "BAH" else "B9",
            }
        },
        "LegInfoList": {"LegInfo": []},
        "PassengerCounts": [],
    }
    return convert_flight_status(raw, "GF", FLIGHT, departure["origin"], departure["date"])


def _passenger_list_document(departure):
    count = departure["passengers"]
    passengers = []
    for index in range(count):
        passengers.append({
            "Name_Details": {"LastName": f"PAX{index}", "FirstName": "TEST"},
            "PNRLocator": {"#text": f"{departure['origin']}{index:04d}"},
            "Cabin": "Y",
            "Indicators": {"Indicator": "Revenue"},
        })
    raw = {
        "ItineraryInfo": {
            "Itinerary": {
                "Destination": departure["destination"],
                "AircraftType": "A320" if departure["origin"] == "BAH" else "B789",
            },
            "CabinInfoList": {
                "CabinInfo": [{
                    "Cabin": "Y",
                    "Count": str(count),
                    "Authorized": "180",
                    "Available": str(180 - count),
                }]
            },
            "DepartureArrival_Dates": {},
        },
        "PassengerInfoList": {"PassengerInfo": passengers},
    }
    document = convert_passenger_list(
        raw, "GF", FLIGHT, departure["date"], departure["origin"])
    document["fetchedAt"] = departure["fetched_at"]
    return document


def _insert_otp(departure, passenger_count):
    counts = [{"@classOfService": "Y", "Count": passenger_count}]
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO otp.flight_xml_current (
                    flight_sequence_number, flight_number, scheduled_origin,
                    scheduled_destination, flight_date, flight_status,
                    passenger_counts, source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    departure["seq"],
                    FLIGHT,
                    departure["origin"],
                    departure["destination"],
                    departure["date"],
                    departure["status"],
                    psycopg2.extras.Json(counts),
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _ingest(departure):
    """Persist one departure the way ingestion does.

    The other departure is stored first by the fixture. flight_status documents
    produced by the converter have no _ingestTimestamp, which is the only sort
    key the flight comparison uses, so the earlier insert is the one MongoDB
    returns for a flight-number-only query.
    """
    status_doc = _flight_status_document(departure)
    assert "flightSequenceNumber" not in status_doc
    assert "_ingestTimestamp" not in status_doc
    storage.store_flight_status(status_doc)

    passenger_doc = _passenger_list_document(departure)
    assert "flightSequenceNumber" not in passenger_doc
    storage.store_passenger_list(passenger_doc)

    storage.update_flight_state(
        "GF",
        FLIGHT,
        departure["origin"],
        departure["date"],
        "snapshot-" + str(departure["seq"]),
        "flight_status",
        flight_sequence_number=departure["seq"],
    )


def _reset_stores():
    _ensure_postgres()
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE otp.flight_xml_current")
        conn.commit()
    finally:
        conn.close()

    db = get_db()
    storage.init_db(db)
    for name in ("flight_status", "passenger_list", "flights"):
        db[name].delete_many({})


def _seed(selected_otp_passengers, other_otp_passengers):
    _reset_stores()
    # Earlier departure of this flight number is ingested first, then the
    # requested one. Passenger lists carry distinct fetchedAt values.
    _ingest(OTHER)
    _ingest(SELECTED)
    _insert_otp(SELECTED, selected_otp_passengers)
    _insert_otp(OTHER, other_otp_passengers)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _rows(body):
    return {row["field"]: row for row in body["rows"]}


def test_flight_comparison_uses_the_departure_for_the_requested_sequence(client):
    _seed(selected_otp_passengers=SELECTED["passengers"], other_otp_passengers=OTHER["passengers"])

    response = client.get(f"/data-audit/{FLIGHT}/compare", params={"seq": SELECTED["seq"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    rows = _rows(body)

    assert rows["Origin"]["pgValue"] == SELECTED["origin"]
    assert rows["Origin"]["mongoValue"] == SELECTED["origin"]
    assert rows["Origin"]["match"] == "match"
    assert rows["Flight Date"]["pgValue"] == SELECTED["date"]
    assert rows["Flight Date"]["mongoValue"] == SELECTED["date"]
    assert rows["Flight Date"]["match"] == "match"
    assert rows["Sequence Number"]["pgValue"] == str(SELECTED["seq"])
    assert rows["Sequence Number"]["mongoValue"] == str(SELECTED["seq"])
    assert rows["Sequence Number"]["match"] == "match"

    other = client.get(f"/data-audit/{FLIGHT}/compare", params={"seq": OTHER["seq"]})
    assert other.status_code == 200, other.text
    other_rows = _rows(other.json())
    assert other_rows["Origin"]["mongoValue"] == OTHER["origin"]
    assert other_rows["Flight Date"]["mongoValue"] == OTHER["date"]
    assert other_rows["Sequence Number"]["mongoValue"] == str(OTHER["seq"])
    assert other_rows["Sequence Number"]["match"] == "match"


def test_passenger_comparison_uses_the_departure_for_the_requested_sequence(client):
    _seed(selected_otp_passengers=SELECTED["passengers"], other_otp_passengers=OTHER["passengers"])

    response = client.get(f"/data-audit/{FLIGHT}/passengers", params={"seq": SELECTED["seq"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    total = _rows(body)["Total Passengers"]
    assert total["pgValue"] == str(SELECTED["passengers"])
    assert total["mongoValue"] == str(SELECTED["passengers"])
    assert total["match"] == "match"

    other = client.get(f"/data-audit/{FLIGHT}/passengers", params={"seq": OTHER["seq"]})
    assert other.status_code == 200, other.text
    other_total = _rows(other.json())["Total Passengers"]
    assert other_total["pgValue"] == str(OTHER["passengers"])
    assert other_total["mongoValue"] == str(OTHER["passengers"])
    assert other_total["match"] == "match"


def test_passenger_comparison_does_not_match_another_departures_total(client):
    """OTP for the requested sequence agrees with the other departure's manifest.

    The Sabre total for the requested departure itself is different, so the
    comparison must report a mismatch against that departure.
    """
    _seed(selected_otp_passengers=OTHER["passengers"], other_otp_passengers=OTHER["passengers"])

    response = client.get(f"/data-audit/{FLIGHT}/passengers", params={"seq": SELECTED["seq"]})
    assert response.status_code == 200, response.text
    total = _rows(response.json())["Total Passengers"]
    assert total["pgValue"] == str(OTHER["passengers"])
    assert total["mongoValue"] == str(SELECTED["passengers"])
    assert total["match"] == "mismatch"
