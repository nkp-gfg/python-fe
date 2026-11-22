"""Ticket serial, coupon, and in-use changes must be stored as passenger change events.

These tests follow the live ingestion path: an unauthenticated
POST /flights/ingest pulls a Sabre passenger list, conversion copies
SerialNumber / CouponNumber / inUse onto the manifest, and the next
ingest diffs that manifest. They fail while passenger change detection
omits those fields, and pass once a matching passenger emits a change
for each field that differs.

The Celery batch task (backend.tasks.ingestion.run_batch_ingestion) is
not executed here. It calls the same run_feeder entry point this file
drives through POST /flights/ingest. Reservation ticket numbers
(convert_reservations TicketingInfo) are a different document and are
not part of this contract.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Placeholders only. Set before importing the app so load_dotenv does not
# replace them, and so SabreClient never reads a real credential.
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "HDQ"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["REDIS_URL"] = (
    "redis://127.0.0.1:6399/0?socket_connect_timeout=0.2&socket_timeout=0.2"
)
os.environ.pop("POSTGRES_URI", None)

import time

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from backend.api.main import app
from backend.sabre.client import requests as sabre_requests


ORIGIN = "LHR"
DEPARTURE_DATE = "2026-03-18"
DEPARTURE_AT = "2026-03-18T21:50:00"

# Synthetic ticket values. Not credentials.
SERIAL_A = "1000000001"
SERIAL_B = "1000000002"
SERIAL_STABLE = "1000000003"
COUPON_A = "1"
COUPON_B = "2"


def _envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _session_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        "<soap-env:Body/>"
        "</soap-env:Envelope>"
    )


def _passenger_list_xml(passengers: list[dict]) -> str:
    blocks = []
    for pax in passengers:
        pnr_xml = (
            f"<PNRLocator>{pax['pnr']}</PNRLocator>" if pax.get("pnr") else ""
        )
        in_use = "true" if pax["vcrInUse"] else "false"
        blocks.append(
            "<PassengerInfo>"
            "<Name_Details>"
            f"<LastName>{pax['lastName']}</LastName>"
            f"<FirstName>{pax['firstName']}</FirstName>"
            "</Name_Details>"
            f"{pnr_xml}"
            f"<PassengerID>{pax['passengerId']}</PassengerID>"
            f"<LineNumber>{pax['lineNumber']}</LineNumber>"
            "<BookingClass>Y</BookingClass>"
            "<Cabin>Y</Cabin>"
            "<Destination>DMM</Destination>"
            "<PassengerType>F</PassengerType>"
            "<BagCount>0</BagCount>"
            "<CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>"
            "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
            "<VCR_Info>"
            f'<VCR_Data inUse="{in_use}" type="adult">'
            "<AirlineNumber>072</AirlineNumber>"
            f"<CouponNumber>{pax['coupon']}</CouponNumber>"
            f"<SerialNumber>{pax['serial']}</SerialNumber>"
            "</VCR_Data>"
            "</VCR_Info>"
            "</PassengerInfo>"
        )
    body = (
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>9101</Flight>"
        "<Origin>LHR</Origin><Destination>DMM</Destination>"
        "</Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{''.join(blocks)}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _envelope(body)


class _SoapResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _SabreSoap:
    """Stand-in for the Sabre HTTP endpoint only.

    SabreClient still builds the request, parses the SOAP XML, and returns
    the body dict the feeder converts. Each GetPassengerListRQ call receives
    the XML currently installed in ``passenger_xml``.
    """

    def __init__(self):
        self.passenger_xml = ""
        self.actions: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(sabre_requests, "post", self._post)

    def _post(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        self.actions.append(action)
        if action == "SessionCreateRQ":
            return _SoapResponse(200, _session_xml())
        if action == "SessionCloseRQ":
            return _SoapResponse(200, _envelope(""))
        if action == "ACS_FlightDetailRQ":
            # An empty element parses as None. Include the itinerary object
            # convert_flight_status reads so this call completes and the
            # feeder continues into the passenger list.
            return _SoapResponse(
                200,
                _envelope(
                    "<ns3:ACS_FlightDetailRS>"
                    "<ItineraryResponseList><ItineraryInfoResponse>"
                    "<FlightStatus>OPEN</FlightStatus>"
                    "</ItineraryInfoResponse></ItineraryResponseList>"
                    "</ns3:ACS_FlightDetailRS>"
                ),
            )
        if action == "GetPassengerListRQ":
            return _SoapResponse(200, self.passenger_xml)
        if action == "Trip_SearchRQ":
            return _SoapResponse(
                200,
                _envelope('<Trip_SearchRS><ReservationsList NumberResults="0"/></Trip_SearchRS>'),
            )
        if action == "Trip_ReportsRQ":
            # Trip reports are not part of passenger change detection.
            # A non-200 response becomes SabreError and is caught after
            # the passenger list has already been stored and diffed.
            return _SoapResponse(500, "trip report unavailable")
        if action == "VerifyFlightDetailsLLSRQ":
            return _SoapResponse(
                200,
                _envelope(
                    "<VerifyFlightDetailsRS>"
                    '<ApplicationResults status="Incomplete"/>'
                    "</VerifyFlightDetailsRS>"
                ),
            )
        return _SoapResponse(500, f"unexpected SOAPAction {action}")


@pytest.fixture(scope="module")
def api():
    uri = os.environ["MONGODB_URI"]
    assert uri.startswith("mongodb://127.0.0.1"), uri
    MongoClient(uri).drop_database("falconeye")
    with TestClient(app) as client:
        yield client


def _ingest(client, flight_number: str):
    response = client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "departureDateTime": DEPARTURE_AT,
        },
    )
    return response


def _ingest_pair(client, monkeypatch, flight_number: str, before: list[dict], after: list[dict]):
    soap = _SabreSoap()
    soap.install(monkeypatch)
    soap.passenger_xml = _passenger_list_xml(before)
    first = _ingest(client, flight_number)
    assert first.status_code == 200, first.text
    assert "authorization" not in {name.lower() for name in first.request.headers}
    # fetchedAt is second-resolution; the later manifest must sort after the first.
    time.sleep(1.1)
    soap.passenger_xml = _passenger_list_xml(after)
    second = _ingest(client, flight_number)
    assert second.status_code == 200, second.text
    assert "authorization" not in {name.lower() for name in second.request.headers}
    assert soap.actions.count("GetPassengerListRQ") == 8
    return first, second


def _manifest(client, flight_number: str) -> list[dict]:
    response = client.get(
        f"/flights/{flight_number}/passengers",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()["passengers"]


def _changes(client, flight_number: str) -> list[dict]:
    response = client.get(
        f"/flights/{flight_number}/changes",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE, "limit": 100},
    )
    if response.status_code == 404:
        return []
    assert response.status_code == 200, response.text
    return response.json()


def _require_field_change(changes: list[dict], field: str, old: str, new: str, passenger_match: dict):
    matched = []
    for change in changes:
        if change.get("field") != field:
            continue
        passenger = change.get("passenger") or {}
        if all(passenger.get(key) == value for key, value in passenger_match.items()):
            matched.append(change)
    assert matched, (
        f"expected a stored change for {field} {old} -> {new} "
        f"on {passenger_match}, got {changes}"
    )
    assert len(matched) == 1
    assert str(matched[0].get("oldValue")) == old
    assert str(matched[0].get("newValue")) == new
    assert matched[0].get("changeType")
    return matched[0]


def test_unauthenticated_ingest_records_ticket_serial_coupon_and_in_use(api, monkeypatch):
    """Same traveler, later Sabre manifest: serial, coupon, and in-use each emit an event."""
    flight_number = "9101"
    serial_pax = {
        "pnr": "AAA111",
        "lastName": "SERIAL",
        "firstName": "SWAP",
        "passengerId": "P1",
        "lineNumber": 1,
        "coupon": COUPON_A,
        "vcrInUse": True,
    }
    coupon_pax = {
        "pnr": "BBB222",
        "lastName": "COUPON",
        "firstName": "SWAP",
        "passengerId": "P2",
        "lineNumber": 2,
        "serial": SERIAL_STABLE,
        "vcrInUse": True,
    }
    in_use_pax = {
        "pnr": "CCC333",
        "lastName": "INUSE",
        "firstName": "SWAP",
        "passengerId": "P3",
        "lineNumber": 3,
        "serial": SERIAL_STABLE,
        "coupon": COUPON_A,
    }
    stable_pax = {
        "pnr": "DDD444",
        "lastName": "STABLE",
        "firstName": "PAX",
        "passengerId": "P4",
        "lineNumber": 4,
        "serial": SERIAL_STABLE,
        "coupon": COUPON_A,
        "vcrInUse": True,
    }
    before = [
        {**serial_pax, "serial": SERIAL_A},
        {**coupon_pax, "coupon": COUPON_A},
        {**in_use_pax, "vcrInUse": True},
        stable_pax,
    ]
    after = [
        {**serial_pax, "serial": SERIAL_B},
        {**coupon_pax, "coupon": COUPON_B},
        {**in_use_pax, "vcrInUse": False},
        stable_pax,
    ]

    _first, second = _ingest_pair(api, monkeypatch, flight_number, before, after)
    passenger_list = second.json()["result"]["apis"]["passengerList"]
    assert passenger_list["isDuplicate"] is False

    manifest = {pax["pnr"]: pax for pax in _manifest(api, flight_number)}
    assert manifest["AAA111"]["ticketNumber"] == SERIAL_B
    assert manifest["AAA111"]["vcrCouponNumber"] == COUPON_A
    assert manifest["AAA111"]["vcrInUse"] is True
    assert manifest["BBB222"]["ticketNumber"] == SERIAL_STABLE
    assert manifest["BBB222"]["vcrCouponNumber"] == COUPON_B
    assert manifest["CCC333"]["vcrInUse"] is False
    assert manifest["CCC333"]["ticketNumber"] == SERIAL_STABLE
    assert manifest["DDD444"]["ticketNumber"] == SERIAL_STABLE
    assert passenger_list["changesStored"] == 3, (
        "the new ticket serial, coupon, and in-use flag are on the manifest, "
        f"but passenger change detection stored {passenger_list['changesStored']} events"
    )

    changes = _changes(api, flight_number)
    serial_change = _require_field_change(
        changes, "ticketNumber", SERIAL_A, SERIAL_B,
        {"pnr": "AAA111", "lastName": "SERIAL", "firstName": "SWAP"},
    )
    coupon_change = _require_field_change(
        changes, "vcrCouponNumber", COUPON_A, COUPON_B,
        {"pnr": "BBB222", "lastName": "COUPON", "firstName": "SWAP"},
    )
    in_use_change = _require_field_change(
        changes, "vcrInUse", "True", "False",
        {"pnr": "CCC333", "lastName": "INUSE", "firstName": "SWAP"},
    )
    stable_fields = [
        change.get("field")
        for change in changes
        if (change.get("passenger") or {}).get("pnr") == "DDD444"
    ]
    assert stable_fields == []

    timeline = api.get(
        f"/flights/{flight_number}/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert timeline.status_code == 200, timeline.text
    timeline_details = [
        event["details"]
        for event in timeline.json()["events"]
        if event.get("details", {}).get("field")
        in {"ticketNumber", "vcrCouponNumber", "vcrInUse"}
    ]
    assert {
        (item["field"], item["oldValue"], item["newValue"])
        for item in timeline_details
    } == {
        ("ticketNumber", SERIAL_A, SERIAL_B),
        ("vcrCouponNumber", COUPON_A, COUPON_B),
        ("vcrInUse", "True", "False"),
    }

    passenger_timeline = api.get(
        f"/flights/{flight_number}/passengers/AAA111/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert passenger_timeline.status_code == 200, passenger_timeline.text
    serial_events = [
        event for event in passenger_timeline.json()["events"]
        if event.get("details", {}).get("field") == "ticketNumber"
    ]
    assert len(serial_events) == 1
    assert serial_events[0]["details"]["oldValue"] == SERIAL_A
    assert serial_events[0]["details"]["newValue"] == SERIAL_B

    feed = api.get("/activity/feed", params={"date": DEPARTURE_DATE, "limit": 50})
    assert feed.status_code == 200, feed.text
    descriptions = {
        ((event.get("passenger") or {}).get("pnr"), event.get("description", ""))
        for event in feed.json()["events"]
        if event.get("flightNumber") == flight_number
    }
    expected_types = {
        serial_change["changeType"],
        coupon_change["changeType"],
        in_use_change["changeType"],
    }
    feed_types = {
        event.get("eventType")
        for event in feed.json()["events"]
        if event.get("flightNumber") == flight_number
        and (event.get("passenger") or {}).get("pnr") in {"AAA111", "BBB222", "CCC333"}
    }
    assert expected_types <= feed_types
    assert any(
        pnr == "AAA111" and SERIAL_A in text and SERIAL_B in text
        for pnr, text in descriptions
    )
    assert any(
        pnr == "BBB222" and COUPON_A in text and COUPON_B in text
        for pnr, text in descriptions
    )
    assert any(
        pnr == "CCC333" and "True" in text and "False" in text
        for pnr, text in descriptions
    )


def test_line_number_passenger_ticket_swap_is_recorded(api, monkeypatch):
    """Travelers with no PNR are keyed by line number and still record a serial swap."""
    flight_number = "9102"
    before = [{
        "lastName": "CREW",
        "firstName": "MEMBER",
        "passengerId": "STAFF1",
        "lineNumber": 7,
        "serial": SERIAL_A,
        "coupon": COUPON_A,
        "vcrInUse": True,
    }]
    after = [{
        "lastName": "CREW",
        "firstName": "MEMBER",
        "passengerId": "STAFF1",
        "lineNumber": 7,
        "serial": SERIAL_B,
        "coupon": COUPON_A,
        "vcrInUse": True,
    }]
    _first, second = _ingest_pair(api, monkeypatch, flight_number, before, after)
    stored = second.json()["result"]["apis"]["passengerList"]["changesStored"]

    manifest = _manifest(api, flight_number)
    assert len(manifest) == 1
    assert manifest[0]["pnr"] == ""
    assert manifest[0]["ticketNumber"] == SERIAL_B
    assert manifest[0]["lineNumber"] == 7
    assert stored == 1, (
        "the reissued serial is on the manifest, "
        f"but passenger change detection stored {stored} events"
    )

    changes = _changes(api, flight_number)
    _require_field_change(
        changes, "ticketNumber", SERIAL_A, SERIAL_B,
        {"pnr": "", "lastName": "CREW", "firstName": "MEMBER"},
    )

    timeline = api.get(
        f"/flights/{flight_number}/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert timeline.status_code == 200, timeline.text
    serial_events = [
        event for event in timeline.json()["events"]
        if event.get("details", {}).get("field") == "ticketNumber"
    ]
    assert len(serial_events) == 1
    assert serial_events[0]["details"]["oldValue"] == SERIAL_A
    assert serial_events[0]["details"]["newValue"] == SERIAL_B
    assert serial_events[0]["passenger"]["lastName"] == "CREW"


def test_unchanged_ticket_fields_do_not_emit_events(api, monkeypatch):
    """A second pull of the same serial, coupon, and in-use flag is not a swap."""
    flight_number = "9103"
    passenger = [{
        "pnr": "EEE555",
        "lastName": "SAME",
        "firstName": "TICKET",
        "passengerId": "P9",
        "lineNumber": 1,
        "serial": SERIAL_A,
        "coupon": COUPON_A,
        "vcrInUse": True,
    }]
    _first, second = _ingest_pair(api, monkeypatch, flight_number, passenger, passenger)
    assert second.json()["result"]["apis"]["passengerList"]["changesStored"] == 0
    manifest = _manifest(api, flight_number)
    assert manifest[0]["ticketNumber"] == SERIAL_A
    assert manifest[0]["vcrCouponNumber"] == COUPON_A
    assert manifest[0]["vcrInUse"] is True
    ticket_changes = [
        change for change in _changes(api, flight_number)
        if change.get("field") in {"ticketNumber", "vcrCouponNumber", "vcrInUse"}
    ]
    assert ticket_changes == []
