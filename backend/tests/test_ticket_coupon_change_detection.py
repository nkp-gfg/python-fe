"""Passenger reticket fields must produce change events.

Two unauthenticated ingests of the same flight store passenger-list snapshots.
The Sabre transport below only substitutes the HTTP response bytes. Parsing,
manifest conversion, snapshot storage, change detection, and the activity
feed / passenger timeline are the product code.

These tests fail while coupon number, plating-carrier code, and ticket serial
are omitted from the passenger diff. A seat change on the same path is the
control that the pipeline does write events for fields it compares.
"""

import re

import pytest
from fastapi.testclient import TestClient

from backend.api.database import get_db
from backend.api.main import app
from backend.sabre import client as sabre_client

DEPARTURE_DATE = "2026-03-18"
ORIGIN = "LHR"
SERIAL = "2243462282"
PLATING = "220"
COUPON = "3"

# Mutable Sabre passenger-list state, keyed by flight number.
# The ingest request cannot carry these values; they arrive in the SOAP body.
MANIFESTS: dict[str, dict] = {}


class _SoapResponse:
    """Stand-in for the HTTP response object requests.post returns.

    backend.sabre.client.SabreClient._post reads only status_code and text.
    """

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


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


def _flight_status_xml() -> str:
    body = (
        "<ns3:ACS_FlightDetailRS>"
        "<ns3:ItineraryResponseList><ns3:ItineraryInfoResponse>"
        "<ns3:FlightStatus>OPEN</ns3:FlightStatus>"
        "<ns3:DepartureGate>A1</ns3:DepartureGate>"
        "</ns3:ItineraryInfoResponse></ns3:ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )
    return _envelope(body)


def _passenger_xml(flight_number: str) -> str:
    manifest = MANIFESTS[flight_number]
    passengers = []
    for pax in manifest["passengers"]:
        passengers.append(
            "<PassengerInfo>"
            "<Name_Details>"
            f"<LastName>{pax['lastName']}</LastName>"
            f"<FirstName>{pax['firstName']}</FirstName>"
            "</Name_Details>"
            f'<PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>'
            f"<PassengerID>{pax['passengerId']}</PassengerID>"
            "<LineNumber>42</LineNumber>"
            "<BookingClass>Y</BookingClass>"
            "<Cabin>Y</Cabin>"
            f"<Seat>{pax['seat']}</Seat>"
            "<Destination>DMM</Destination>"
            "<PassengerType>F</PassengerType>"
            "<BagCount>1</BagCount>"
            "<CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>"
            "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
            "<VCR_Info><VCR_Data inUse=\"true\" type=\"adult\">"
            f"<AirlineNumber>{pax['platingCarrier']}</AirlineNumber>"
            f"<CouponNumber>{pax['coupon']}</CouponNumber>"
            f"<SerialNumber>{pax['serial']}</SerialNumber>"
            "</VCR_Data></VCR_Info>"
            "</PassengerInfo>"
        )
    body = (
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline>"
        f"<Flight>{flight_number}</Flight>"
        f"<DepartureDate>{DEPARTURE_DATE}</DepartureDate>"
        f"<Origin>{ORIGIN}</Origin>"
        "<Destination>DMM</Destination>"
        "</Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{''.join(passengers)}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _envelope(body)


def _reservations_xml() -> str:
    return _envelope('<Trip_SearchRS><ReservationsList NumberResults="0"/></Trip_SearchRS>')


def _trip_report_xml() -> str:
    # An empty element parses as None. convert_trip_report calls .get on PassengerList.
    return _envelope(
        "<Trip_ReportsRS><PassengerList>"
        "<Passenger><Name><LastName></LastName><FirstName></FirstName></Name></Passenger>"
        "</PassengerList></Trip_ReportsRS>"
    )


def _schedule_xml(flight_number: str) -> str:
    body = (
        "<VerifyFlightDetailsRS>"
        '<ApplicationResults status="Complete"/>'
        "<OriginDestinationOptions><OriginDestinationOption>"
        '<FlightSegment DepartureDateTime="03-18T21:50" ArrivalDateTime="03-19T05:00">'
        '<OriginLocation LocationCode="LHR"/>'
        '<DestinationLocation LocationCode="DMM"/>'
        f'<MarketingAirline Code="GF" FlightNumber="{flight_number}"/>'
        "</FlightSegment>"
        "</OriginDestinationOption></OriginDestinationOptions>"
        "</VerifyFlightDetailsRS>"
    )
    return _envelope(body)


def _flight_number_from_request(body: str) -> str:
    match = re.search(r"<(?:v4:)?Flight>(\d+)</(?:v4:)?Flight>", body)
    if match:
        return match.group(1)
    match = re.search(r'FlightNumber="(\d+)"', body)
    if not match:
        raise AssertionError("Sabre request did not contain a flight number")
    return match.group(1)


def _post(url, data=None, headers=None, timeout=None, **kwargs):
    action = (headers or {}).get("SOAPAction", "")
    request_xml = data if isinstance(data, str) else ""
    if action == "SessionCreateRQ":
        xml = _session_xml()
    elif action == "SessionCloseRQ":
        xml = _session_xml()
    elif action == "ACS_FlightDetailRQ":
        xml = _flight_status_xml()
    elif action == "GetPassengerListRQ":
        xml = _passenger_xml(_flight_number_from_request(request_xml))
    elif action == "Trip_SearchRQ":
        xml = _reservations_xml()
    elif action == "Trip_ReportsRQ":
        xml = _trip_report_xml()
    elif action == "VerifyFlightDetailsLLSRQ":
        xml = _schedule_xml(_flight_number_from_request(request_xml))
    else:
        raise AssertionError(f"unexpected Sabre action {action}")
    return _SoapResponse(xml)


@pytest.fixture(scope="module")
def api():
    original = sabre_client.requests.post
    sabre_client.requests.post = _post
    with TestClient(app) as client:
        database = get_db()
        for name in database.list_collection_names():
            database[name].delete_many({})
        yield client
    sabre_client.requests.post = original


def _passenger(pnr, last_name, first_name, *, coupon=COUPON, plating=PLATING,
               serial=SERIAL, seat="20C", passenger_id="89C0AD550101"):
    return {
        "pnr": pnr,
        "lastName": last_name,
        "firstName": first_name,
        "coupon": coupon,
        "platingCarrier": plating,
        "serial": serial,
        "seat": seat,
        "passengerId": passenger_id,
    }


def _arm(flight_number: str, passengers: list[dict]) -> None:
    MANIFESTS[flight_number] = {"passengers": passengers}


def _ingest(api: TestClient, flight_number: str) -> dict:
    response = api.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "departureDateTime": f"{DEPARTURE_DATE}T21:50:00",
            # Ignored by SabreIngestRequest. Must not become the stored coupon.
            "vcrCouponNumber": "9",
            "vcrAirlineNumber": "999",
            "ticketNumber": "0000000000",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stored_passengers(ingest_body: dict) -> list[dict]:
    snapshot_id = ingest_body["result"]["apis"]["passengerList"]["snapshotId"]
    doc = get_db()["snapshots"].find_one(
        {"snapshotId": snapshot_id, "snapshotType": "passenger_list"},
    )
    assert doc is not None, "ingest did not store a passenger-list snapshot"
    return doc["data"]["passengers"]


def _changes(api: TestClient, flight_number: str) -> list[dict]:
    response = api.get(
        f"/flights/{flight_number}/changes",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE, "limit": 100},
    )
    if response.status_code == 404:
        return []
    assert response.status_code == 200, response.text
    return response.json()


def _event(changes: list[dict], pnr: str, field: str) -> dict:
    matches = [
        change for change in changes
        if change.get("field") == field and (change.get("passenger") or {}).get("pnr") == pnr
    ]
    assert matches, (
        f"no change event for pnr={pnr} field={field}; "
        f"got {[(c.get('field'), (c.get('passenger') or {}).get('pnr')) for c in changes]}"
    )
    assert len(matches) == 1, matches
    return matches[0]


def _assert_feed_and_timeline(api: TestClient, flight_number: str, pnr: str, change: dict) -> None:
    feed = api.get("/activity/feed", params={"date": DEPARTURE_DATE, "limit": 200})
    assert feed.status_code == 200, feed.text
    feed_hits = [
        event for event in feed.json()["events"]
        if event.get("flightNumber") == flight_number
        and (event.get("passenger") or {}).get("pnr") == pnr
        and event.get("eventType") == change["changeType"]
    ]
    assert feed_hits, feed.json()

    timeline = api.get(
        f"/flights/{flight_number}/passengers/{pnr}/timeline",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert timeline.status_code == 200, timeline.text
    details = [
        event["details"] for event in timeline.json()["events"]
        if event.get("details", {}).get("field") == change["field"]
    ]
    assert details, timeline.json()
    assert details[0]["oldValue"] == change["oldValue"]
    assert details[0]["newValue"] == change["newValue"]


def test_seat_change_is_written_on_the_ingest_path(api: TestClient):
    """Control: a field the differ already compares is stored and returned."""
    flight = "9101"
    traveler = _passenger("SGKMWT", "NOCULAK", "EWA MRS")
    _arm(flight, [traveler])
    first = _ingest(api, flight)
    assert first["result"]["apis"]["passengerList"]["changesStored"] == 0
    stored = _stored_passengers(first)
    by_pnr = {pax["pnr"]: pax for pax in stored}
    assert by_pnr["SGKMWT"]["vcrCouponNumber"] == COUPON
    assert by_pnr["SGKMWT"]["vcrAirlineNumber"] == PLATING
    assert by_pnr["SGKMWT"]["ticketNumber"] == SERIAL
    assert by_pnr["SGKMWT"]["seat"] == "20C"

    traveler["seat"] = "21A"
    _arm(flight, [traveler])
    second = _ingest(api, flight)
    assert second["result"]["apis"]["passengerList"]["changesStored"] >= 1
    change = _event(_changes(api, flight), "SGKMWT", "seat")
    assert change["oldValue"] == "20C"
    assert change["newValue"] == "21A"
    _assert_feed_and_timeline(api, flight, "SGKMWT", change)


def test_coupon_change_same_serial_reaches_feed_and_timeline(api: TestClient):
    flight = "9102"
    changed = _passenger("SGKMWT", "NOCULAK", "EWA MRS", passenger_id="89C0AD550101")
    stable = _passenger(
        "ABC123", "SMITH", "JOHN", coupon="1", serial="1111111111",
        seat="12A", passenger_id="89C0AD550102",
    )
    _arm(flight, [changed, stable])
    first = _ingest(api, flight)
    stored = {pax["pnr"]: pax for pax in _stored_passengers(first)}
    # Request body extras must not override the virtual coupon record.
    assert stored["SGKMWT"]["vcrCouponNumber"] == "3"
    assert stored["SGKMWT"]["vcrAirlineNumber"] == "220"
    assert stored["SGKMWT"]["ticketNumber"] == SERIAL

    changed["coupon"] = "4"
    _arm(flight, [changed, stable])
    second = _ingest(api, flight)
    assert second["result"]["apis"]["passengerList"]["isDuplicate"] is False
    stored_after = {pax["pnr"]: pax for pax in _stored_passengers(second)}
    assert stored_after["SGKMWT"]["vcrCouponNumber"] == "4"
    assert stored_after["SGKMWT"]["ticketNumber"] == SERIAL
    assert stored_after["ABC123"]["vcrCouponNumber"] == "1"
    changes = _changes(api, flight)
    change = _event(changes, "SGKMWT", "vcrCouponNumber")
    assert change["oldValue"] == "3"
    assert change["newValue"] == "4"
    assert not [
        item for item in changes
        if item.get("field") == "vcrCouponNumber"
        and (item.get("passenger") or {}).get("pnr") == "ABC123"
    ]
    _assert_feed_and_timeline(api, flight, "SGKMWT", change)


def test_plating_carrier_change_same_serial_reaches_feed_and_timeline(api: TestClient):
    flight = "9103"
    traveler = _passenger("SGKMWT", "NOCULAK", "EWA MRS")
    _arm(flight, [traveler])
    _ingest(api, flight)

    traveler["platingCarrier"] = "072"
    _arm(flight, [traveler])
    second = _ingest(api, flight)
    stored_after = {pax["pnr"]: pax for pax in _stored_passengers(second)}
    assert stored_after["SGKMWT"]["vcrAirlineNumber"] == "072"
    assert stored_after["SGKMWT"]["ticketNumber"] == SERIAL
    assert stored_after["SGKMWT"]["vcrCouponNumber"] == COUPON
    change = _event(_changes(api, flight), "SGKMWT", "vcrAirlineNumber")
    assert change["oldValue"] == "220"
    assert change["newValue"] == "072"
    _assert_feed_and_timeline(api, flight, "SGKMWT", change)


def test_ticket_serial_change_reaches_feed_and_timeline(api: TestClient):
    flight = "9104"
    traveler = _passenger("SGKMWT", "NOCULAK", "EWA MRS")
    _arm(flight, [traveler])
    _ingest(api, flight)

    traveler["serial"] = "2243462290"
    _arm(flight, [traveler])
    second = _ingest(api, flight)
    stored_after = {pax["pnr"]: pax for pax in _stored_passengers(second)}
    assert stored_after["SGKMWT"]["ticketNumber"] == "2243462290"
    assert stored_after["SGKMWT"]["vcrCouponNumber"] == COUPON
    assert stored_after["SGKMWT"]["vcrAirlineNumber"] == PLATING
    change = _event(_changes(api, flight), "SGKMWT", "ticketNumber")
    assert change["oldValue"] == SERIAL
    assert change["newValue"] == "2243462290"
    _assert_feed_and_timeline(api, flight, "SGKMWT", change)
