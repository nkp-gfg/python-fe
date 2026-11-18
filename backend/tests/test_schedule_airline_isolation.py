"""Schedule lookups must not replace another airline's operational flight.

The dashboard and the stored-schedule endpoint select a flight_schedules
document by flight number and departure date. These tests drive the live
lookup and the ingestion pipeline through SabreClient, convert_schedule, and
store_flight_schedule, then read the dashboard back. A VerifyFlightDetails
response is supplied at requests.post from the SOAP body the client built and
from the VerifyFlightDetailsRS shape in sabre_docs. While the bug is present
the cross-airline cases fail because the newest document is returned.

Run:
    PYTHON_DOTENV_DISABLED=1 MONGODB_URI=mongodb://127.0.0.1:27017 \\
        python3 -m pytest backend/tests/test_schedule_airline_isolation.py -q
"""

import os
import re
import sys

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "TEST"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "PCC"
os.environ["SABRE_ORGANIZATION"] = "ORG"
os.environ["SABRE_DOMAIN"] = "DEFAULT"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
import requests
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import flights as flights_routes
from backend.api.database import get_db

DEPARTURE_DATE = "2026-06-01"
OPERATIONAL_AIRLINE = "GF"
OTHER_AIRLINE = "BA"
ORIGIN = "BAH"

# Timetable already stored for the operational flight.
SEEDED = {
    "origin": "BAH",
    "destination": "LHR",
    "scheduledDeparture": "06-01T08:15",
    "scheduledArrival": "06-01T12:40",
    "aircraftType": "789",
    "elapsedTime": "0385",
    "airMilesFlown": 3200,
    "mealCode": "M",
}

# Published timetable returned when the SOAP request asks for GF.
SAME_AIRLINE_UPDATE = {
    "origin": "BAH",
    "destination": "LHR",
    "scheduledDeparture": "06-01T09:45",
    "scheduledArrival": "06-01T14:10",
    "aircraftType": "788",
    "elapsedTime": "0390",
    "airMilesFlown": 3201,
    "mealCode": "M",
}

# Published timetable returned when the SOAP request asks for another carrier.
OTHER_CARRIER = {
    "origin": "JFK",
    "destination": "LAX",
    "scheduledDeparture": "06-01T18:05",
    "scheduledArrival": "06-01T21:20",
    "aircraftType": "32A",
    "elapsedTime": "0315",
    "airMilesFlown": 2475,
    "mealCode": "S",
}

_AIRLINE_RE = re.compile(r'MarketingAirline Code="([A-Z0-9]{2})" FlightNumber="(\d{1,5})"')
_ORIGIN_RE = re.compile(r'OriginLocation LocationCode="([^"]*)"')
_DEST_RE = re.compile(r'DestinationLocation LocationCode="([^"]*)"')

stub_mode = {"value": "complete"}
verify_requests = []


def _response(status_code, text):
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = os.environ["SABRE_BASE_URL"]
    resp.encoding = "utf-8"
    resp._content = text.encode("utf-8")
    return resp


def _schedule_xml(airline, flight_number, timetable, status):
    """VerifyFlightDetailsRS envelope.

    Element names and attributes are those read by convert_schedule and
    declared in sabre_docs/VerifyFlightDetailsLLS2.0.1RQ. The soap-env prefix
    is the one SabreClient._extract_body looks up.
    """
    segment = ""
    if status == "Complete":
        segment = f"""
        <OriginDestinationOptions>
          <OriginDestinationOption OriginTimeZone="GMT+3" DestinationTimeZone="GMT+0">
            <FlightSegment DepartureDateTime="{timetable["scheduledDeparture"]}"
                           ArrivalDateTime="{timetable["scheduledArrival"]}"
                           AirMilesFlown="{timetable["airMilesFlown"]}"
                           ElapsedTime="{timetable["elapsedTime"]}"
                           AccumulatedElapsedTime="{timetable["elapsedTime"]}">
              <DestinationLocation LocationCode="{timetable["destination"]}" Terminal="5"/>
              <Equipment AirEquipType="{timetable["aircraftType"]}"/>
              <MarketingAirline Code="{airline}" FlightNumber="{flight_number}"/>
              <Meal Code="{timetable["mealCode"]}"/>
              <OriginLocation LocationCode="{timetable["origin"]}" Terminal="1"/>
            </FlightSegment>
          </OriginDestinationOption>
        </OriginDestinationOptions>"""
    error = ""
    if status != "Complete":
        error = """
          <stl:Error type="BusinessLogic">
            <stl:SystemSpecificResults>
              <stl:Message code="0001">FLIGHT NOT FOUND</stl:Message>
            </stl:SystemSpecificResults>
          </stl:Error>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <VerifyFlightDetailsRS xmlns="http://webservices.sabre.com/sabreXML/2011/10" Version="2.0.1">
      <stl:ApplicationResults status="{status}" xmlns:stl="http://services.sabre.com/STL/v01">{error}
      </stl:ApplicationResults>
      {segment}
    </VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _sabre_post(url, data=None, headers=None, timeout=None):
    action = (headers or {}).get("SOAPAction", "")
    body = data if isinstance(data, str) else (data or b"").decode("utf-8")
    if action == "SessionCreateRQ":
        return _response(
            200,
            """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body><SessionCreateRS/></soap-env:Body>
</soap-env:Envelope>""",
        )
    if action == "SessionCloseRQ":
        return _response(200, "<ok/>")
    if action == "VerifyFlightDetailsLLSRQ":
        airline_match = _AIRLINE_RE.search(body)
        origin_match = _ORIGIN_RE.search(body)
        dest_match = _DEST_RE.search(body)
        if not airline_match or not origin_match or not dest_match:
            raise AssertionError("VerifyFlightDetails request is missing airline or locations")
        airline, flight_number = airline_match.group(1), airline_match.group(2)
        verify_requests.append({
            "airline": airline,
            "flightNumber": flight_number,
            "origin": origin_match.group(1),
            "destination": dest_match.group(1),
        })
        status = "Complete" if stub_mode["value"] == "complete" else "Incomplete"
        timetable = SAME_AIRLINE_UPDATE if airline == OPERATIONAL_AIRLINE else OTHER_CARRIER
        return _response(200, _schedule_xml(airline, flight_number, timetable, status))
    return _response(500, "sabre stub has no payload for this action")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    verify_requests.clear()
    stub_mode["value"] = "complete"
    flights_routes._dashboard_cache.clear()
    db = get_db()
    for name in (
        "flight_status",
        "passenger_list",
        "flight_schedules",
        "sabre_requests",
        "flights",
        "snapshots",
        "changes",
        "reservations",
        "trip_reports",
    ):
        db[name].delete_many({})
    monkeypatch.setattr("backend.sabre.client.requests.post", _sabre_post)
    yield
    flights_routes._dashboard_cache.clear()


def _seed_operational_flight(flight_number):
    db = get_db()
    db["flight_status"].insert_one({
        "airline": OPERATIONAL_AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2026-06-01T00:00:00Z",
        "status": "ON TIME",
        "legs": [{"city": ORIGIN}, {"city": SEEDED["destination"]}],
    })
    db["passenger_list"].insert_one({
        "airline": OPERATIONAL_AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2026-06-01T00:00:00Z",
        "totalPassengers": 1,
        "passengers": [{
            "pnr": "ABC123",
            "lastName": "SMITH",
            "firstName": "JOHN",
            "cabin": "Y",
            "isCheckedIn": False,
            "isBoarded": False,
        }],
    })
    db["flight_schedules"].insert_one({
        "airline": OPERATIONAL_AIRLINE,
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2026-06-01T00:00:00Z",
        "success": True,
        "error": None,
        **SEEDED,
        "originTerminal": "1",
        "destinationTerminal": "5",
        "segments": [],
    })


def _assert_not_other_carrier(schedule):
    assert schedule["airline"] == OPERATIONAL_AIRLINE
    assert schedule["origin"] == SEEDED["origin"]
    assert schedule["destination"] == SEEDED["destination"]
    assert schedule["aircraftType"] != OTHER_CARRIER["aircraftType"]
    assert schedule["scheduledDeparture"] != OTHER_CARRIER["scheduledDeparture"]
    assert schedule["scheduledArrival"] != OTHER_CARRIER["scheduledArrival"]
    assert schedule["elapsedTime"] != OTHER_CARRIER["elapsedTime"]
    assert schedule["airMilesFlown"] != OTHER_CARRIER["airMilesFlown"]
    assert schedule["origin"] != OTHER_CARRIER["origin"]
    assert schedule["destination"] != OTHER_CARRIER["destination"]


def _dashboard(client, flight_number):
    flights_routes._dashboard_cache.clear()
    response = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stored_schedule(client, flight_number):
    response = client.get(
        f"/flights/{flight_number}/schedule",
        params={"date": DEPARTURE_DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_other_airline_lookup_does_not_replace_operational_schedule(client):
    flight_number = "8801"
    _seed_operational_flight(flight_number)

    client.post("/flights/schedule/lookup", json={
        "airline": OTHER_AIRLINE,
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })

    dashboard = _dashboard(client, flight_number)
    _assert_not_other_carrier(dashboard["schedule"])
    assert dashboard["schedule"]["scheduledDeparture"] == SEEDED["scheduledDeparture"]
    assert dashboard["schedule"]["aircraftType"] == SEEDED["aircraftType"]
    assert dashboard["flightStatus"]["aircraft"]["type"] == SEEDED["aircraftType"]
    assert dashboard["insights"]["flightInfo"]["aircraftType"] == SEEDED["aircraftType"]
    assert dashboard["insights"]["flightInfo"]["airMilesFlown"] == SEEDED["airMilesFlown"]
    assert dashboard["insights"]["flightInfo"]["elapsedTime"] == SEEDED["elapsedTime"]

    stored = _stored_schedule(client, flight_number)
    _assert_not_other_carrier(stored)
    assert stored["aircraftType"] == SEEDED["aircraftType"]
    assert stored["scheduledDeparture"] == SEEDED["scheduledDeparture"]


def test_same_airline_lookup_refreshes_operational_schedule(client):
    flight_number = "8802"
    _seed_operational_flight(flight_number)

    looked_up = client.post("/flights/schedule/lookup", json={
        "airline": OPERATIONAL_AIRLINE,
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })
    assert looked_up.status_code == 200, looked_up.text
    assert verify_requests[-1]["airline"] == OPERATIONAL_AIRLINE
    assert verify_requests[-1]["origin"] == ""
    assert verify_requests[-1]["destination"] == ""

    dashboard = _dashboard(client, flight_number)
    schedule = dashboard["schedule"]
    assert schedule["airline"] == OPERATIONAL_AIRLINE
    assert schedule["origin"] == SAME_AIRLINE_UPDATE["origin"]
    assert schedule["destination"] == SAME_AIRLINE_UPDATE["destination"]
    assert schedule["scheduledDeparture"] == SAME_AIRLINE_UPDATE["scheduledDeparture"]
    assert schedule["scheduledArrival"] == SAME_AIRLINE_UPDATE["scheduledArrival"]
    assert schedule["aircraftType"] == SAME_AIRLINE_UPDATE["aircraftType"]
    assert schedule["airMilesFlown"] == SAME_AIRLINE_UPDATE["airMilesFlown"]
    assert dashboard["flightStatus"]["aircraft"]["type"] == SAME_AIRLINE_UPDATE["aircraftType"]
    assert dashboard["insights"]["flightInfo"]["aircraftType"] == SAME_AIRLINE_UPDATE["aircraftType"]

    stored = _stored_schedule(client, flight_number)
    assert stored["airline"] == OPERATIONAL_AIRLINE
    assert stored["aircraftType"] == SAME_AIRLINE_UPDATE["aircraftType"]
    assert stored["scheduledDeparture"] == SAME_AIRLINE_UPDATE["scheduledDeparture"]


def test_incomplete_other_airline_lookup_is_not_stored(client):
    flight_number = "8803"
    _seed_operational_flight(flight_number)
    stub_mode["value"] = "incomplete"

    client.post("/flights/schedule/lookup", json={
        "airline": OTHER_AIRLINE,
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })

    dashboard = _dashboard(client, flight_number)
    assert dashboard["schedule"]["airline"] == OPERATIONAL_AIRLINE
    assert dashboard["schedule"]["aircraftType"] == SEEDED["aircraftType"]
    assert dashboard["schedule"]["origin"] == SEEDED["origin"]
    stored = _stored_schedule(client, flight_number)
    assert stored["airline"] == OPERATIONAL_AIRLINE
    assert stored["aircraftType"] == SEEDED["aircraftType"]


def test_other_airline_ingest_does_not_replace_operational_schedule(client):
    flight_number = "8804"
    _seed_operational_flight(flight_number)

    client.post("/flights/ingest", json={
        "airline": OTHER_AIRLINE,
        "flightNumber": flight_number,
        "origin": "JFK",
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": "2026-06-01T18:05:00",
    })

    dashboard = _dashboard(client, flight_number)
    _assert_not_other_carrier(dashboard["schedule"])
    assert dashboard["schedule"]["aircraftType"] == SEEDED["aircraftType"]
    assert dashboard["schedule"]["scheduledDeparture"] == SEEDED["scheduledDeparture"]
    assert dashboard["insights"]["flightInfo"]["aircraftType"] == SEEDED["aircraftType"]
    assert dashboard["flightStatus"]["aircraft"]["type"] == SEEDED["aircraftType"]

    stored = _stored_schedule(client, flight_number)
    _assert_not_other_carrier(stored)
    assert stored["aircraftType"] == SEEDED["aircraftType"]
