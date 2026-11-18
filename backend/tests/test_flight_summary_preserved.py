"""Flight summary status and gate must survive later ingest steps.

POST /flights/ingest stores Sabre flight status and gate on the flight
summary, then stores passenger counts on that same summary. GET /compare/flights
reads Sabre status and gate only from that summary. These tests fail while a
later update replaces the summary object instead of merging the fields it owns.

They are expected to fail until summary updates merge field-by-field.
"""

import os
import sys

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "HDQ"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ.pop("POSTGRES_URI", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from backend.api.database import close_db
from backend.api.main import app
from backend.feeder.comparison import load_sabre_flights
from backend.feeder.storage import close as close_storage
from backend.feeder.storage import get_db
from backend.sabre.client import SabreClient
from pymongo import MongoClient

# The delay does not change the stored summary. Zero it so the four passenger
# calls and the other Sabre calls do not sleep between requests.
SabreClient.API_CALL_DELAY = 0

DATE = "2026-03-19"
AIRLINE = "GF"
ORIGIN = "LHR"
PASSENGER_COUNTS = {
    "totalPax": 1,
    "adultCount": 1,
    "childCount": 0,
    "infantCount": 0,
    "totalSouls": 1,
    "checkedIn": 1,
    "boarded": 0,
    "revenue": 1,
}


class SabreScript:
    """Which SOAP bodies the stand-in HTTP transport returns for one test."""

    status = "CANCELLED"
    gate = "A12"
    passenger_http_status = 200

    @classmethod
    def reset(cls):
        cls.status = "CANCELLED"
        cls.gate = "A12"
        cls.passenger_http_status = 200


def _envelope(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _flight_status_xml() -> str:
    # Body shape read by SabreClient._extract_body and convert_flight_status.
    return _envelope(
        "<ns3:ACS_FlightDetailRS>"
        "<ItineraryResponseList><ItineraryInfoResponse>"
        f"<FlightStatus>{SabreScript.status}</FlightStatus>"
        f"<DepartureGate>{SabreScript.gate}</DepartureGate>"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )


def _passenger_list_xml() -> str:
    # One checked-in revenue adult. Repeated display-code calls dedupe to this passenger.
    return _envelope(
        "<GetPassengerListRS>"
        "<PassengerInfoList><PassengerInfo>"
        "<Name_Details><LastName>SMITH</LastName><FirstName>JOHN</FirstName></Name_Details>"
        "<PNRLocator>ABC123</PNRLocator>"
        "<PassengerID>1</PassengerID>"
        "<CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>"
        "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
        "<Indicators><Indicator>Revenue</Indicator></Indicators>"
        "</PassengerInfo></PassengerInfoList>"
        "</GetPassengerListRS>"
    )


def _empty_named(tag: str, inner: str = "") -> str:
    return _envelope(f"<{tag}>{inner}</{tag}>")


def _fake_post(url, data=None, headers=None, timeout=None):
    action = (headers or {}).get("SOAPAction")
    if action == "SessionCreateRQ":
        text = (
            "<wsse:BinarySecurityToken>placeholder-session</wsse:BinarySecurityToken>"
        )
        return _Response(text, 200)
    if action == "SessionCloseRQ":
        return _Response("<ok/>", 200)
    if action == "ACS_FlightDetailRQ":
        return _Response(_flight_status_xml(), 200)
    if action == "GetPassengerListRQ":
        if SabreScript.passenger_http_status != 200:
            return _Response("passenger list unavailable", SabreScript.passenger_http_status)
        return _Response(_passenger_list_xml(), 200)
    if action == "Trip_SearchRQ":
        return _Response(
            _empty_named("Trip_SearchRS", '<ReservationsList NumberResults="0"/>'),
            200,
        )
    if action == "Trip_ReportsRQ":
        return _Response(_empty_named("Trip_ReportsRS", "<Unused>1</Unused>"), 200)
    if action == "VerifyFlightDetailsLLSRQ":
        return _Response(
            _empty_named(
                "VerifyFlightDetailsRS",
                '<ApplicationResults status="Complete"/>',
            ),
            200,
        )
    raise AssertionError(f"unexpected Sabre action {action}")


class _Response:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


def _reset_database():
    close_storage()
    close_db()
    MongoClient(os.environ["MONGODB_URI"]).drop_database("falconeye")


def _ingest(api: TestClient, flight_number: str):
    response = api.post(
        "/flights/ingest",
        json={
            "airline": AIRLINE,
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DATE,
            "departureDateTime": f"{DATE}T21:50:00",
        },
    )
    return response


def _flight_document(flight_number: str) -> dict:
    document = get_db()["flights"].find_one(
        {
            "airline": AIRLINE,
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DATE,
        }
    )
    assert document is not None, f"flights document missing for {flight_number}"
    return document


def _latest_flight_status(flight_number: str) -> dict:
    document = get_db()["flight_status"].find_one(
        {"flightNumber": flight_number, "origin": ORIGIN, "departureDate": DATE},
        sort=[("fetchedAt", -1)],
    )
    assert document is not None, "flight_status collection is missing the flight"
    return document


def _assert_summary(flight_number: str, status: str, gate: str):
    summary = _flight_document(flight_number).get("summary") or {}
    assert summary.get("status") == status
    assert summary.get("gate") == gate
    for field, expected in PASSENGER_COUNTS.items():
        assert summary.get(field) == expected, field

    stored = _latest_flight_status(flight_number)
    assert stored.get("status") == status
    assert stored.get("gate") == gate

    compared = load_sabre_flights(DATE, [flight_number]).to_dicts()
    assert len(compared) == 1
    row = compared[0]
    assert row["sabre_status"] == status
    assert row["sabre_gate"] == gate
    assert row["sabre_totalPax"] == PASSENGER_COUNTS["totalPax"]
    assert row["sabre_checkedIn"] == PASSENGER_COUNTS["checkedIn"]
    assert row["sabre_boarded"] == PASSENGER_COUNTS["boarded"]


def _sabre_status_in_report(api: TestClient, flight_number: str) -> str:
    response = api.get(
        "/compare/flights",
        params={"date": DATE, "flight_numbers": flight_number},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    matches = [
        anomaly
        for anomaly in report.get("anomalies", [])
        if anomaly.get("flightNumber") == flight_number
        and anomaly.get("type") == "SABRE_ONLY"
    ]
    assert len(matches) == 1, report
    return matches[0].get("sabre_status")


def test_passenger_list_ingest_keeps_sabre_status_and_gate(monkeypatch):
    """A normal ingest must leave status and gate in place for the comparison."""
    monkeypatch.setattr("backend.sabre.client.requests.post", _fake_post)
    SabreScript.reset()
    SabreScript.status = "CANCELLED"
    SabreScript.gate = "A12"
    _reset_database()

    with TestClient(app) as api:
        ingested = _ingest(api, "2006")
        assert ingested.status_code == 200, ingested.text

        _assert_summary("2006", "CANCELLED", "A12")
        assert _sabre_status_in_report(api, "2006") == "CANCELLED"


def test_flight_status_ingest_keeps_passenger_counts(monkeypatch):
    """A later flight-status update must replace status and gate only."""
    monkeypatch.setattr("backend.sabre.client.requests.post", _fake_post)
    SabreScript.reset()
    SabreScript.status = "OPENCI"
    SabreScript.gate = "B41"
    _reset_database()

    with TestClient(app) as api:
        first = _ingest(api, "2057")
        assert first.status_code == 200, first.text
        # Counts are what the passenger-list step stored. This holds even while
        # the status overwrite bug is present, and is the state the next update
        # must not erase.
        summary = _flight_document("2057").get("summary") or {}
        for field, expected in PASSENGER_COUNTS.items():
            assert summary.get(field) == expected, field

        SabreScript.status = "DELAYED"
        SabreScript.gate = "C3"
        SabreScript.passenger_http_status = 500
        second = _ingest(api, "2057")
        # Passenger-list calls fail, so the route reports the ingest as failed,
        # after the flight-status step has already written the summary.
        assert second.status_code == 409, second.text

        _assert_summary("2057", "DELAYED", "C3")
        assert _sabre_status_in_report(api, "2057") == "DELAYED"
