"""Data-audit must find overnight flights the OTP list already shows.

The OTP flight list selects a row when the supplied date equals
``scheduled_departure_local::date`` or ``scheduled_arrival_local::date``.
Its date resolver documents that ``flight_date`` can be a different day for
an overnight flight. Data audit's flight and passenger comparisons currently
add ``flight_date = <supplied date>`` instead.

These tests call the product routes with that supplied date. They fail while
the audit lookup misses the PostgreSQL row and report the operational side
as not found, and they pass once the audit date matches the OTP rule.

Requires POSTGRES_URI and MONGODB_URI. The tests create ``otp.flight_xml_current``
and the ``flight_status`` / ``passenger_list`` documents they read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extras import Json
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Local departure is the day after flight_date. OTP lists this flight on
# 2026-03-02; flight_date equality does not.
OVERNIGHT_FLIGHT = "2057"
OVERNIGHT_SEQ = 88001
OVERNIGHT_FLIGHT_DATE = "2026-03-01"
OVERNIGHT_LOCAL_DATE = "2026-03-02"
OVERNIGHT_ORIGIN = "BAH"
OVERNIGHT_REGISTRATION = "A9C-OV"
OVERNIGHT_PAX = 42

# Only the local arrival date is the audit date. Departure and flight_date
# are the previous day, which is the other OTP match arm.
ARRIVAL_FLIGHT = "2060"
ARRIVAL_SEQ = 88002
ARRIVAL_FLIGHT_DATE = "2026-03-03"
ARRIVAL_LOCAL_DATE = "2026-03-04"
ARRIVAL_ORIGIN = "BAH"
ARRIVAL_REGISTRATION = "A9C-AR"
ARRIVAL_PAX = 7

# flight_date and both local dates are the same day.
SAMEDAY_FLIGHT = "2070"
SAMEDAY_SEQ = 88003
SAMEDAY_DATE = "2026-03-05"
SAMEDAY_ORIGIN = "BAH"
SAMEDAY_REGISTRATION = "A9C-DY"
SAMEDAY_PAX = 11


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must be set to a database the tests can write")
    return value


def _connect():
    return psycopg2.connect(_require_env("POSTGRES_URI"))


def _mongo():
    client = MongoClient(_require_env("MONGODB_URI"), serverSelectionTimeoutMS=5000)
    return client, client["falconeye"]


_CREATE_SQL = """
CREATE SCHEMA IF NOT EXISTS otp;
CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
    flight_sequence_number integer PRIMARY KEY,
    carrier_code text,
    flight_number text NOT NULL,
    scheduled_origin text,
    scheduled_destination text,
    actual_origin text,
    actual_destination text,
    flight_date date,
    local_flight_date date,
    leg_departure_date date,
    scheduled_departure_utc timestamp without time zone,
    estimated_block_off_utc timestamp without time zone,
    scheduled_arrival_utc timestamp without time zone,
    estimated_block_on_utc timestamp without time zone,
    actual_block_off_utc timestamp without time zone,
    actual_block_on_utc timestamp without time zone,
    actual_takeoff_utc timestamp without time zone,
    actual_touchdown_utc timestamp without time zone,
    scheduled_departure_local timestamp without time zone,
    scheduled_arrival_local timestamp without time zone,
    published_departure_local timestamp without time zone,
    published_arrival_local timestamp without time zone,
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


def _insert_flight(
    cur,
    *,
    seq: int,
    flight_number: str,
    origin: str,
    destination: str,
    flight_date: str,
    local_flight_date: str,
    departure_local: str,
    arrival_local: str,
    registration: str,
    pax: int,
):
    """Insert one OTP row.

    ``local_flight_date`` and ``leg_departure_date`` are set to ``flight_date``
    on purpose. The operational date the OTP list uses is only on
    ``scheduled_departure_local`` / ``scheduled_arrival_local``.
    """
    cur.execute(
        """
        INSERT INTO otp.flight_xml_current (
            flight_sequence_number, carrier_code, flight_number,
            scheduled_origin, scheduled_destination,
            actual_origin, actual_destination,
            flight_date, local_flight_date, leg_departure_date,
            scheduled_departure_utc, scheduled_arrival_utc,
            scheduled_departure_local, scheduled_arrival_local,
            flight_status, aircraft_type, aircraft_registration,
            service_type_code, passenger_counts, delay_details, source
        ) VALUES (
            %s, 'GF', %s,
            %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            'Scheduled', '320', %s,
            'J', %s, '[]'::jsonb, 'otp'
        )
        """,
        (
            seq,
            flight_number,
            origin,
            destination,
            origin,
            destination,
            flight_date,
            local_flight_date,
            flight_date,
            departure_local,
            arrival_local,
            departure_local,
            arrival_local,
            registration,
            Json([{"@classOfService": "Y", "Count": pax}]),
        ),
    )


def _seed():
    with _connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
            cur.execute("TRUNCATE otp.flight_xml_current")
            _insert_flight(
                cur,
                seq=OVERNIGHT_SEQ,
                flight_number=OVERNIGHT_FLIGHT,
                origin=OVERNIGHT_ORIGIN,
                destination="LHR",
                flight_date=OVERNIGHT_FLIGHT_DATE,
                local_flight_date=OVERNIGHT_FLIGHT_DATE,
                departure_local=f"{OVERNIGHT_LOCAL_DATE} 01:20:00",
                arrival_local=f"{OVERNIGHT_LOCAL_DATE} 09:05:00",
                registration=OVERNIGHT_REGISTRATION,
                pax=OVERNIGHT_PAX,
            )
            _insert_flight(
                cur,
                seq=ARRIVAL_SEQ,
                flight_number=ARRIVAL_FLIGHT,
                origin=ARRIVAL_ORIGIN,
                destination="CDG",
                flight_date=ARRIVAL_FLIGHT_DATE,
                local_flight_date=ARRIVAL_FLIGHT_DATE,
                departure_local=f"{ARRIVAL_FLIGHT_DATE} 22:40:00",
                arrival_local=f"{ARRIVAL_LOCAL_DATE} 05:10:00",
                registration=ARRIVAL_REGISTRATION,
                pax=ARRIVAL_PAX,
            )
            _insert_flight(
                cur,
                seq=SAMEDAY_SEQ,
                flight_number=SAMEDAY_FLIGHT,
                origin=SAMEDAY_ORIGIN,
                destination="DXB",
                flight_date=SAMEDAY_DATE,
                local_flight_date=SAMEDAY_DATE,
                departure_local=f"{SAMEDAY_DATE} 10:00:00",
                arrival_local=f"{SAMEDAY_DATE} 14:00:00",
                registration=SAMEDAY_REGISTRATION,
                pax=SAMEDAY_PAX,
            )

    client, db = _mongo()
    try:
        db["flight_status"].delete_many({})
        db["passenger_list"].delete_many({})
        for flight_number, origin, departure_date, registration, pax in (
            (OVERNIGHT_FLIGHT, OVERNIGHT_ORIGIN, OVERNIGHT_LOCAL_DATE,
             "SABRE-OV", OVERNIGHT_PAX),
            (ARRIVAL_FLIGHT, ARRIVAL_ORIGIN, ARRIVAL_LOCAL_DATE,
             "SABRE-AR", ARRIVAL_PAX),
            (SAMEDAY_FLIGHT, SAMEDAY_ORIGIN, SAMEDAY_DATE,
             "SABRE-DY", SAMEDAY_PAX),
        ):
            # departureDate is the Sabre/local date the today board sends
            # to ingestion, not the OTP flight_date column.
            db["flight_status"].insert_one({
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": departure_date,
                "flightStatus": "Scheduled",
                "aircraftType": "320",
                "aircraftRegistration": registration,
                "_ingestTimestamp": departure_date + "T02:00:00Z",
            })
            db["passenger_list"].insert_one({
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": departure_date,
                "fetchedAt": departure_date + "T02:00:00Z",
                "totalPassengers": pax,
                "cabinSummary": [{"cabin": "Y", "count": pax}],
                "passengers": [],
            })
    finally:
        client.close()


@pytest.fixture(scope="module")
def api():
    _require_env("POSTGRES_URI")
    _require_env("MONGODB_URI")
    _seed()
    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app) as client:
        yield client


def _field(body: dict, name: str) -> dict:
    for row in body["rows"]:
        if row["field"] == name:
            return row
    raise AssertionError(f"{name} missing from {body['rows']}")


def _otp_flight(api, date: str, flight_number: str) -> dict:
    response = api.get("/otp/flights", params={"date": date})
    assert response.status_code == 200, response.text
    matches = [
        row for row in response.json()
        if row["flightNumber"] == flight_number
    ]
    assert matches, (
        f"OTP list for {date} did not include {flight_number}; "
        "the fixture is not on the operational board"
    )
    assert len(matches) == 1
    return matches[0]


def test_otp_lists_overnight_departure_on_local_date(api):
    """The board date is the local departure date, and flight_date differs."""
    flight = _otp_flight(api, OVERNIGHT_LOCAL_DATE, OVERNIGHT_FLIGHT)
    assert flight["flightDate"] == OVERNIGHT_FLIGHT_DATE
    assert flight["sabreDepartureDate"] == OVERNIGHT_LOCAL_DATE
    assert flight["flightSequenceNumber"] == OVERNIGHT_SEQ
    assert flight["aircraftRegistration"] == OVERNIGHT_REGISTRATION


def test_flight_compare_finds_pg_row_for_local_departure_date(api):
    """Audit date is the local departure date the OTP list just matched."""
    _otp_flight(api, OVERNIGHT_LOCAL_DATE, OVERNIGHT_FLIGHT)
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={"date": OVERNIGHT_LOCAL_DATE, "origin": OVERNIGHT_ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mongoFound"] is True
    assert body["pgFound"] is True
    assert body["date"] == OVERNIGHT_LOCAL_DATE
    assert body["sequenceNumber"] == OVERNIGHT_SEQ
    registration = _field(body, "Aircraft Registration")
    assert registration["pgValue"] == OVERNIGHT_REGISTRATION


def test_flight_compare_finds_pg_row_when_only_the_date_is_supplied(api):
    """The page can submit a flight number and date with origin left blank."""
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={"date": OVERNIGHT_LOCAL_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mongoFound"] is True
    assert body["pgFound"] is True
    assert _field(body, "Aircraft Registration")["pgValue"] == OVERNIGHT_REGISTRATION


def test_flight_compare_with_sequence_still_uses_the_local_date(api):
    """Sequence is an extra filter. It must not bring back a flight_date lookup."""
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={
            "date": OVERNIGHT_LOCAL_DATE,
            "origin": OVERNIGHT_ORIGIN,
            "seq": OVERNIGHT_SEQ,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    assert _field(body, "Aircraft Registration")["pgValue"] == OVERNIGHT_REGISTRATION

    missed = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={
            "date": "2026-04-01",
            "origin": OVERNIGHT_ORIGIN,
            "seq": OVERNIGHT_SEQ,
        },
    )
    assert missed.status_code == 404, missed.text


def test_passenger_compare_finds_pg_counts_for_local_departure_date(api):
    _otp_flight(api, OVERNIGHT_LOCAL_DATE, OVERNIGHT_FLIGHT)
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/passengers",
        params={"date": OVERNIGHT_LOCAL_DATE, "origin": OVERNIGHT_ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mongoFound"] is True
    assert body["pgFound"] is True
    total = _field(body, "Total Passengers")
    assert total["pgValue"] == str(OVERNIGHT_PAX)
    economy = _field(body, "Economy Count")
    assert economy["pgValue"] == str(OVERNIGHT_PAX)


def test_passenger_compare_with_sequence_uses_the_local_date(api):
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/passengers",
        params={
            "date": OVERNIGHT_LOCAL_DATE,
            "origin": OVERNIGHT_ORIGIN,
            "seq": OVERNIGHT_SEQ,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    assert _field(body, "Total Passengers")["pgValue"] == str(OVERNIGHT_PAX)

    missed = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/passengers",
        params={
            "date": "2026-04-01",
            "seq": OVERNIGHT_SEQ,
        },
    )
    assert missed.status_code == 404, missed.text


def test_otp_lists_overnight_flight_on_local_arrival_date(api):
    flight = _otp_flight(api, ARRIVAL_LOCAL_DATE, ARRIVAL_FLIGHT)
    assert flight["flightDate"] == ARRIVAL_FLIGHT_DATE
    assert flight["sabreDepartureDate"] == ARRIVAL_FLIGHT_DATE
    assert flight["scheduledArrivalLocal"].startswith(ARRIVAL_LOCAL_DATE)
    assert flight["aircraftRegistration"] == ARRIVAL_REGISTRATION


def test_flight_compare_finds_pg_row_for_local_arrival_date(api):
    """The supplied date matches local arrival and neither flight_date nor local departure."""
    _otp_flight(api, ARRIVAL_LOCAL_DATE, ARRIVAL_FLIGHT)
    response = api.get(
        f"/data-audit/{ARRIVAL_FLIGHT}/compare",
        params={"date": ARRIVAL_LOCAL_DATE, "origin": ARRIVAL_ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mongoFound"] is True
    assert body["pgFound"] is True
    assert body["sequenceNumber"] == ARRIVAL_SEQ
    assert _field(body, "Aircraft Registration")["pgValue"] == ARRIVAL_REGISTRATION


def test_passenger_compare_finds_pg_counts_for_local_arrival_date(api):
    response = api.get(
        f"/data-audit/{ARRIVAL_FLIGHT}/passengers",
        params={"date": ARRIVAL_LOCAL_DATE, "origin": ARRIVAL_ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mongoFound"] is True
    assert body["pgFound"] is True
    assert _field(body, "Total Passengers")["pgValue"] == str(ARRIVAL_PAX)


def test_same_day_flight_is_still_found(api):
    response = api.get(
        f"/data-audit/{SAMEDAY_FLIGHT}/compare",
        params={"date": SAMEDAY_DATE, "origin": SAMEDAY_ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    assert _field(body, "Aircraft Registration")["pgValue"] == SAMEDAY_REGISTRATION

    passengers = api.get(
        f"/data-audit/{SAMEDAY_FLIGHT}/passengers",
        params={"date": SAMEDAY_DATE, "origin": SAMEDAY_ORIGIN},
    )
    assert passengers.status_code == 200, passengers.text
    pax = passengers.json()
    assert pax["pgFound"] is True
    assert _field(pax, "Total Passengers")["pgValue"] == str(SAMEDAY_PAX)


def test_unrelated_date_does_not_return_the_overnight_flight(api):
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={"date": "2026-04-01", "origin": OVERNIGHT_ORIGIN},
    )
    assert response.status_code == 404, response.text
    passengers = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/passengers",
        params={"date": "2026-04-01", "origin": OVERNIGHT_ORIGIN},
    )
    assert passengers.status_code == 404, passengers.text


def test_other_origin_does_not_return_the_overnight_flight(api):
    response = api.get(
        f"/data-audit/{OVERNIGHT_FLIGHT}/compare",
        params={"date": OVERNIGHT_LOCAL_DATE, "origin": "LHR"},
    )
    assert response.status_code == 404, response.text


def test_sequence_lookup_finds_overnight_flight_by_local_departure_date():
    """Ingest omits flightSequenceNumber and passes the Sabre/local date.

    ``_lookup_flight_sequence_number`` is the other PostgreSQL read that
    equates that date to ``flight_date``. A same-day row is the control.
    """
    _require_env("POSTGRES_URI")
    _seed()
    from backend.feeder.runner import _lookup_flight_sequence_number

    assert _lookup_flight_sequence_number(
        SAMEDAY_FLIGHT, SAMEDAY_ORIGIN, SAMEDAY_DATE,
    ) == SAMEDAY_SEQ
    # Ingest passes the Sabre departure date, which is the local departure
    # date. For this overnight row that date is not flight_date.
    assert _lookup_flight_sequence_number(
        OVERNIGHT_FLIGHT, OVERNIGHT_ORIGIN, OVERNIGHT_LOCAL_DATE,
    ) == OVERNIGHT_SEQ
    assert _lookup_flight_sequence_number(
        OVERNIGHT_FLIGHT, OVERNIGHT_ORIGIN, "2026-04-01",
    ) is None
