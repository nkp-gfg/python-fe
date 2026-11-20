"""Spec tests for passenger-count comparison across carriers.

These tests fail while flight comparison drops the airline code before joining
Sabre flight state to OTP. They pass once each Sabre carrier joins only the OTP
row for that carrier, and every anomaly names the airline.

The Sabre HTTP call is replaced with a local response built in the same SOAP
shape the client parses. Session, conversion, storage, and comparison all run
the product code. Sabre credentials in the environment are placeholders.
"""

import os
import re
from datetime import datetime, timezone

os.environ["MONGODB_URI"] = os.environ.get(
    "MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ["POSTGRES_URI"] = os.environ.get(
    "POSTGRES_URI",
    "postgresql://postgres:placeholder@127.0.0.1:5432/falconeye",
)
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json
from pymongo import MongoClient

from backend.api.main import app
from backend.feeder.comparison import compare_flights_for_date
from backend.sabre import client as sabre_client

# Airline code in the outbound Sabre body -> passenger rows that carrier returns.
PAX_BY_AIRLINE: dict[str, int] = {}

ORIGIN = "BAH"
FLIGHT = "9057"


class _Response:
    def __init__(self, text: str):
        self.status_code = 200
        self.text = text


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        f"<soap-env:Body>{inner}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger_list_xml(count: int) -> str:
    passengers = []
    for index in range(count):
        passengers.append(
            "<PassengerInfo>"
            "<Name_Details><LastName>PAX</LastName>"
            f"<FirstName>N{index}</FirstName></Name_Details>"
            f"<PNRLocator>P{index:04d}</PNRLocator>"
            f"<PassengerID>ID{index}</PassengerID>"
            "<BookingClass>Y</BookingClass><Cabin>Y</Cabin>"
            "</PassengerInfo>"
        )
    inner = (
        "<GetPassengerListRS>"
        "<PassengerInfoList>"
        + "".join(passengers)
        + "</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _envelope(inner)


def _fake_sabre_post(url, data=None, headers=None, timeout=None):
    """Stand in for the Sabre HTTP response only. The client still parses it."""
    action = (headers or {}).get("SOAPAction", "")
    body = data.decode() if isinstance(data, bytes) else (data or "")
    if action in ("SessionCreateRQ", "SessionCloseRQ"):
        xml = _envelope("")
    elif action == "ACS_FlightDetailRQ":
        xml = _envelope(
            "<ns3:ACS_FlightDetailRS>"
            "<ItineraryResponseList><ItineraryInfoResponse>"
            "<FlightStatus>OPEN</FlightStatus><DepartureGate>A1</DepartureGate>"
            "</ItineraryInfoResponse></ItineraryResponseList>"
            "</ns3:ACS_FlightDetailRS>"
        )
    elif action == "GetPassengerListRQ":
        match = re.search(r"Airline>([A-Z0-9]{2})<", body)
        assert match, "passenger-list request did not carry an airline code"
        count = PAX_BY_AIRLINE[match.group(1)] if "DisplayCode>RV<" in body else 0
        xml = _passenger_list_xml(count)
    elif action == "Trip_SearchRQ":
        xml = _envelope(
            '<Trip_SearchRS><ReservationsList NumberResults="0">'
            "</ReservationsList></Trip_SearchRS>"
        )
    elif action == "Trip_ReportsRQ":
        xml = _envelope(
            '<Trip_ReportsRS><PassengerList kind="none"></PassengerList>'
            "</Trip_ReportsRS>"
        )
    elif action == "VerifyFlightDetailsLLSRQ":
        xml = _envelope(
            '<VerifyFlightDetailsRS><ApplicationResults status="Complete"/>'
            "</VerifyFlightDetailsRS>"
        )
    else:
        raise AssertionError(f"unexpected Sabre action {action}")
    return _Response(xml)


@pytest.fixture(scope="module")
def http():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sabre_client.requests, "post", _fake_sabre_post)
        with TestClient(app) as client:
            yield client


@pytest.fixture(scope="module")
def pg():
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS otp;
            CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
                flight_sequence_number integer,
                carrier_code text,
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
        )
    yield conn
    conn.close()


def _clear(flight_number: str, departure_date: str):
    db = MongoClient(os.environ["MONGODB_URI"])["falconeye"]
    query = {"flightNumber": flight_number, "departureDate": departure_date}
    for name in (
        "flights",
        "passenger_list",
        "flight_status",
        "snapshots",
        "reservations",
        "trip_reports",
        "flight_schedules",
        "changes",
    ):
        db[name].delete_many(query)
    db["sabre_requests"].delete_many({"flight.flightNumber": flight_number})
    with psycopg2.connect(os.environ["POSTGRES_URI"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM otp.flight_xml_current WHERE flight_number = %s",
                (flight_number,),
            )
        conn.commit()


def _insert_otp(conn, carrier: str, flight_number: str, departure_date: str, total: int, seq: int):
    local = datetime.fromisoformat(f"{departure_date}T08:00:00")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otp.flight_xml_current (
                flight_sequence_number, carrier_code, flight_number,
                scheduled_origin, scheduled_destination, flight_date,
                scheduled_departure_local, scheduled_arrival_local,
                scheduled_departure_utc, flight_status, aircraft_type,
                passenger_counts, source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                seq,
                carrier,
                flight_number,
                ORIGIN,
                "LHR",
                departure_date,
                local,
                local,
                local.replace(tzinfo=timezone.utc),
                "SCH",
                "32N",
                Json([{"Count": total, "ClassOfService": "Y"}]),
                "test",
            ),
        )
    conn.commit()


def _ingest(http: TestClient, airline: str, departure_date: str, passengers: int, seq: int):
    PAX_BY_AIRLINE[airline] = passengers
    response = http.post(
        "/flights/ingest",
        json={
            "airline": airline,
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": departure_date,
            "departureDateTime": f"{departure_date}T08:00:00",
            "flightSequenceNumber": seq,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["flight"]["airline"] == airline
    return body


def _stored_counts(departure_date: str) -> dict[str, int]:
    db = MongoClient(os.environ["MONGODB_URI"])["falconeye"]
    docs = list(db["flights"].find(
        {"flightNumber": FLIGHT, "departureDate": departure_date, "origin": ORIGIN},
        {"_id": 0, "airline": 1, "summary": 1},
    ))
    return {doc["airline"]: (doc.get("summary") or {}).get("totalPax") for doc in docs}


def _pax_mismatches(report: dict) -> list[dict]:
    return [row for row in report["anomalies"] if row["type"] == "PAX_COUNT_MISMATCH"]


def _assert_anomalies_name_airline(report: dict):
    for row in report["anomalies"]:
        airline = row.get("airline")
        assert airline in {"GF", "BA"}, row
        assert airline in row.get("message", ""), row


def test_matching_carriers_are_not_reported_as_count_mismatches(http, pg):
    """GF and BA both match their own OTP counts for one flight number."""
    day = "2026-11-21"
    _clear(FLIGHT, day)
    _insert_otp(pg, "GF", FLIGHT, day, 120, 1)
    _insert_otp(pg, "BA", FLIGHT, day, 40, 2)
    _ingest(http, "GF", day, 120, 1)
    _ingest(http, "BA", day, 40, 2)

    assert _stored_counts(day) == {"GF": 120, "BA": 40}

    response = http.get("/compare/flights", params={"date": day, "flight_numbers": FLIGHT})
    assert response.status_code == 200, response.text
    report = response.json()
    direct = compare_flights_for_date(day, [FLIGHT])

    for result in (report, direct):
        assert result["summary"]["matchedFlights"] == 2, result
        assert result["summary"]["paxCountMismatches"] == 0, result
        assert _pax_mismatches(result) == []
        assert result["flightsCompared"] == 2, result
        _assert_anomalies_name_airline(result)


def test_second_carrier_is_not_a_gulf_air_count_gap(http, pg):
    """BA shares the flight number and origin, and OTP only has the GF row."""
    day = "2026-11-22"
    _clear(FLIGHT, day)
    _insert_otp(pg, "GF", FLIGHT, day, 120, 11)
    _ingest(http, "GF", day, 120, 11)
    _ingest(http, "BA", day, 40, 12)

    assert _stored_counts(day) == {"GF": 120, "BA": 40}

    response = http.get("/compare/flights", params={"date": day, "flight_numbers": FLIGHT})
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["summary"]["paxCountMismatches"] == 0, report
    assert _pax_mismatches(report) == []
    sabre_only = [row for row in report["anomalies"] if row["type"] == "SABRE_ONLY"]
    assert len(sabre_only) == 1, report
    assert sabre_only[0]["airline"] == "BA"
    assert sabre_only[0]["flightNumber"] == FLIGHT
    assert sabre_only[0]["origin"] == ORIGIN
    assert "BA" in sabre_only[0]["message"]
    assert report["summary"]["matchedFlights"] == 1


def test_real_gulf_air_gap_names_that_carrier_only(http, pg):
    """GF is 10 passengers short. BA's different count must not be a second gap."""
    day = "2026-11-23"
    _clear(FLIGHT, day)
    _insert_otp(pg, "GF", FLIGHT, day, 100, 21)
    _ingest(http, "GF", day, 90, 21)
    _ingest(http, "BA", day, 40, 22)

    response = http.get("/compare/flights", params={"date": day, "flight_numbers": FLIGHT})
    assert response.status_code == 200, response.text
    report = response.json()
    mismatches = _pax_mismatches(report)

    assert len(mismatches) == 1, report
    gap = mismatches[0]
    assert gap["airline"] == "GF"
    assert gap["flightNumber"] == FLIGHT
    assert gap["origin"] == ORIGIN
    assert gap["sabre_totalPax"] == 90
    assert gap["otp_totalPax"] == 100
    assert gap["difference"] == -10
    assert "GF" in gap["message"]
    assert all(row.get("sabre_totalPax") != 40 for row in mismatches)
    _assert_anomalies_name_airline(report)


def test_passenger_rollup_keeps_each_carrier(http, pg):
    """The passenger comparison must not collapse two carriers into one flight."""
    day = "2026-11-24"
    _clear(FLIGHT, day)
    _ingest(http, "GF", day, 120, 31)
    _ingest(http, "BA", day, 40, 32)

    response = http.get(
        "/compare/passengers", params={"date": day, "flight_numbers": FLIGHT})
    assert response.status_code == 200, response.text
    report = response.json()
    seen = {(row.get("airline"), row.get("totalPax")) for row in report["flights"]}
    assert seen == {("GF", 120), ("BA", 40)}, report


def test_data_audit_passenger_total_uses_the_otp_carrier(http, pg):
    """A later BA ingest must not become the Sabre total next to the GF OTP row."""
    day = "2026-11-25"
    _clear(FLIGHT, day)
    _insert_otp(pg, "GF", FLIGHT, day, 120, 41)
    _ingest(http, "GF", day, 120, 41)
    # fetchedAt is second-resolution; separate the two writes so "latest" is BA.
    import time
    time.sleep(1.1)
    _ingest(http, "BA", day, 40, 42)

    response = http.get(
        f"/data-audit/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": day},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    totals = [row for row in rows if row["field"] == "Total Passengers"]
    assert len(totals) == 1, rows
    assert totals[0]["pgValue"] == "120"
    assert totals[0]["mongoValue"] == "120"
    assert totals[0]["match"] == "match"
