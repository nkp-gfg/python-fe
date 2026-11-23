"""Electronic-ticket marker changes must be recorded and shown.

Sabre's GetPassengerList guide defines CorpID as ``T`` when the traveler has
an electronic ticket and blank otherwise. The feeder stores that value as
``corpId``. These tests post two manifests through the unauthenticated
ingest endpoint and require a stored change, plus the flight timeline,
passenger timeline, and activity feed, to show the marker appearing or
clearing.

They fail while change detection skips ``corpId``.

Passenger VCR serial numbers and reservation ``eTicket`` flags are separate
stored fields and are not the marker these tests require.

Run (MongoDB must be reachable at ``MONGODB_URI``):
    pytest backend/tests/test_eticket_corp_id_change_detection.py -q
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder")
os.environ.setdefault("SABRE_PASSWORD", "placeholder")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "placeholder")
os.environ.setdefault("SABRE_ORGANIZATION", "placeholder")
os.environ.setdefault("SABRE_DOMAIN", "placeholder")
os.environ.pop("POSTGRES_URI", None)

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.sabre.client import SabreClient, SabreError

DEPARTURE_DATE = "2026-03-18"
ORIGIN = "LHR"
AIRLINE = "GF"

_SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""


def _passenger_xml(passenger):
    corp = passenger.get("corpId") or ""
    corp_xml = f"<CorpID>{corp}</CorpID>" if corp else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{passenger["lastName"]}</LastName>
            <FirstName>{passenger["firstName"]}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{passenger["pnr"]}</PNRLocator>
          <PassengerID>{passenger["passengerId"]}</PassengerID>
          <LineNumber>{passenger["lineNumber"]}</LineNumber>
          {corp_xml}
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{passenger["seat"]}</Seat>
          <Destination>DMM</Destination>
          <BoardingPassFlag>true</BoardingPassFlag>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>
            <Indicator>CheckedIn</Indicator>
            <Indicator>NotBoarded</Indicator>
            <Indicator>Revenue</Indicator>
          </Indicators>
          <CheckIn_Info>
            <CheckInStatus>true</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
          <VCR_Info>
            <VCR_Data inUse="true" type="adult">
              <AirlineNumber>072</AirlineNumber>
              <CouponNumber>1</CouponNumber>
              <SerialNumber>{passenger["ticketNumber"]}</SerialNumber>
            </VCR_Data>
          </VCR_Info>
        </PassengerInfo>
    """


def _passenger_list_response(passengers):
    body = "\n".join(_passenger_xml(p) for p in passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>0000</Flight>
          <DepartureDate>{DEPARTURE_DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _ScriptedSabre:
    """Return scripted SOAP bodies from SabreClient._post.

    Parsing, conversion, storage, and change detection stay on the real path.
    Each ingest issues four GetPassengerListRQ calls; all four receive the
    same manifest, and the next ingest advances to the next manifest.
    """

    def __init__(self, manifests):
        self._manifests = list(manifests)
        self.passenger_list_calls = 0

    def handle(self, action, body):
        if action == "SessionCreateRQ":
            return (_SESSION_XML, 200, 1, body)
        if action == "SessionCloseRQ":
            return ("<ok/>", 200, 1, body)
        if action == "GetPassengerListRQ":
            index = self.passenger_list_calls // 4
            self.passenger_list_calls += 1
            if index >= len(self._manifests):
                raise SabreError("no further scripted passenger list")
            return (self._manifests[index], 200, 1, body)
        raise SabreError(f"no scripted response for {action}")


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


def _clear_flight(flight_number):
    """Remove prior rows for this flight so snapshot sequences start at 1."""
    from backend.api.database import get_db

    db = get_db()
    flight_query = {
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
    }
    for collection in ("snapshots", "changes", "passenger_list", "flights"):
        db[collection].delete_many(flight_query)
    db["sabre_requests"].delete_many({
        "flight.flightNumber": flight_number,
        "flight.origin": ORIGIN,
        "flight.departureDate": DEPARTURE_DATE,
    })


def _ingest(api, script, flight_number):
    payload = {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": f"{DEPARTURE_DATE}T21:50:00",
    }
    def _post(_client, action, body, timeout=30, endpoint=None):
        return script.handle(action, body)

    with patch.object(SabreClient, "_post", _post):
        response = api.post("/flights/ingest", json=payload)
    assert response.status_code not in (401, 403), response.text
    assert response.status_code in (200, 409), response.text
    return response


def _passenger_by_pnr(api, flight_number, pnr, snapshot_sequence=None):
    params = {"origin": ORIGIN, "date": DEPARTURE_DATE}
    if snapshot_sequence is not None:
        params["snapshot_sequence"] = snapshot_sequence
    response = api.get(f"/flights/{flight_number}/passengers", params=params)
    assert response.status_code == 200, response.text
    matches = [p for p in response.json()["passengers"] if p["pnr"] == pnr]
    assert len(matches) == 1, response.json()["passengers"]
    return matches[0]


def _changes(api, flight_number):
    response = api.get(
        f"/flights/{flight_number}/changes",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE, "limit": 1000},
    )
    if response.status_code == 404:
        return []
    assert response.status_code == 200, response.text
    return response.json()


def _corp_changes(changes, pnr):
    return [
        change for change in changes
        if change.get("field") == "corpId"
        and (change.get("passenger") or {}).get("pnr") == pnr
    ]


def _blank(value):
    return value is None or value == ""


def _assert_marker_visible(api, flight_number, pnr, change):
    flight_timeline = api.get(
        f"/flights/{flight_number}/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert flight_timeline.status_code == 200, flight_timeline.text
    flight_events = [
        event for event in flight_timeline.json()["events"]
        if event.get("details", {}).get("field") == "corpId"
        and (event.get("passenger") or {}).get("pnr") == pnr
        and event.get("details", {}).get("oldValue") == change.get("oldValue")
        and event.get("details", {}).get("newValue") == change.get("newValue")
    ]
    assert flight_events, flight_timeline.json()["events"]

    passenger_timeline = api.get(
        f"/flights/{flight_number}/passengers/{pnr}/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert passenger_timeline.status_code == 200, passenger_timeline.text
    passenger_events = [
        event for event in passenger_timeline.json()["events"]
        if event.get("details", {}).get("field") == "corpId"
        and event.get("details", {}).get("oldValue") == change.get("oldValue")
        and event.get("details", {}).get("newValue") == change.get("newValue")
    ]
    assert passenger_events, passenger_timeline.json()["events"]

    feed = api.get(
        "/activity/feed",
        params={"date": DEPARTURE_DATE, "limit": 200},
    )
    assert feed.status_code == 200, feed.text
    feed_events = [
        event for event in feed.json()["events"]
        if event.get("flightNumber") == flight_number
        and (event.get("passenger") or {}).get("pnr") == pnr
        and event.get("eventType") == change.get("changeType")
    ]
    assert feed_events, feed.json()["events"]


def test_cleared_electronic_ticket_marker_is_recorded(api):
    """T removed on a later manifest must show as a change for that traveler."""
    flight_number = "9101"
    _clear_flight(flight_number)
    ticketed = {
        "pnr": "TKT001",
        "lastName": "SMITH",
        "firstName": "JANE",
        "passengerId": "PAX9101A",
        "lineNumber": 1,
        "corpId": "T",
        "seat": "21A",
        "ticketNumber": "0000000001",
    }
    other = {
        "pnr": "TKT002",
        "lastName": "JONES",
        "firstName": "JOHN",
        "passengerId": "PAX9101B",
        "lineNumber": 2,
        "corpId": "",
        "seat": "21B",
        "ticketNumber": "0000000002",
    }
    later_other = dict(other, seat="22B")
    script = _ScriptedSabre([
        _passenger_list_response([ticketed, other]),
        _passenger_list_response([dict(ticketed, corpId=""), later_other]),
    ])

    _ingest(api, script, flight_number)
    _ingest(api, script, flight_number)
    assert script.passenger_list_calls == 8

    before = _passenger_by_pnr(api, flight_number, "TKT001", snapshot_sequence=1)
    after = _passenger_by_pnr(api, flight_number, "TKT001", snapshot_sequence=2)
    assert before["corpId"] == "T"
    assert _blank(after["corpId"])
    assert after["ticketNumber"] == "0000000001"
    assert after["seat"] == "21A"

    changes = _changes(api, flight_number)
    seat_changes = [
        change for change in changes
        if change.get("field") == "seat"
        and (change.get("passenger") or {}).get("pnr") == "TKT002"
    ]
    assert seat_changes, changes

    corp_changes = _corp_changes(changes, "TKT001")
    assert len(corp_changes) == 1, changes
    change = corp_changes[0]
    assert change["oldValue"] == "T"
    assert _blank(change.get("newValue"))
    _assert_marker_visible(api, flight_number, "TKT001", change)


def test_issued_electronic_ticket_marker_is_recorded(api):
    """A blank CorpID that later becomes T must show as a change."""
    flight_number = "9102"
    _clear_flight(flight_number)
    passenger = {
        "pnr": "TKT003",
        "lastName": "BROWN",
        "firstName": "ALEX",
        "passengerId": "PAX9102A",
        "lineNumber": 1,
        "corpId": "",
        "seat": "14C",
        "ticketNumber": "0000000003",
    }
    script = _ScriptedSabre([
        _passenger_list_response([passenger]),
        _passenger_list_response([dict(passenger, corpId="T")]),
    ])

    _ingest(api, script, flight_number)
    _ingest(api, script, flight_number)

    before = _passenger_by_pnr(api, flight_number, "TKT003", snapshot_sequence=1)
    after = _passenger_by_pnr(api, flight_number, "TKT003", snapshot_sequence=2)
    assert _blank(before["corpId"])
    assert after["corpId"] == "T"
    assert before["ticketNumber"] == after["ticketNumber"] == "0000000003"

    changes = _changes(api, flight_number)
    corp_changes = _corp_changes(changes, "TKT003")
    assert len(corp_changes) == 1, changes
    change = corp_changes[0]
    assert _blank(change.get("oldValue"))
    assert change["newValue"] == "T"
    _assert_marker_visible(api, flight_number, "TKT003", change)


def test_unchanged_electronic_ticket_marker_is_not_recorded(api):
    """The same CorpID on both manifests must not produce a corpId change."""
    flight_number = "9103"
    _clear_flight(flight_number)
    passenger = {
        "pnr": "TKT004",
        "lastName": "GREEN",
        "firstName": "SAM",
        "passengerId": "PAX9103A",
        "lineNumber": 1,
        "corpId": "T",
        "seat": "10A",
        "ticketNumber": "0000000004",
    }
    script = _ScriptedSabre([
        _passenger_list_response([passenger]),
        _passenger_list_response([dict(passenger, seat="10B")]),
    ])

    _ingest(api, script, flight_number)
    _ingest(api, script, flight_number)

    after = _passenger_by_pnr(api, flight_number, "TKT004", snapshot_sequence=2)
    assert after["corpId"] == "T"
    assert after["seat"] == "10B"

    changes = _changes(api, flight_number)
    assert _corp_changes(changes, "TKT004") == []
    assert any(
        change.get("field") == "seat"
        and (change.get("passenger") or {}).get("pnr") == "TKT004"
        for change in changes
    )
