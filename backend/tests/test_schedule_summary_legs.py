"""Published schedule summary must follow the ingested itinerary.

Sabre VerifyFlightDetailsRS may contain many OriginDestinationOption
elements, and each option may contain many FlightSegment elements. The
summary destination, arrival, and arrival terminal shown on the flight
dashboard are taken from the stored schedule document.

These tests call the live lookup route and the feeder with a SOAP body
parsed by SabreClient (xmltodict, the same parser the product ships).
They fail while the summary is copied from the first segment of the
first option.
"""

import copy
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "BAH")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")

from backend.api.routes import flights as flights_routes
from backend.api.routes import schedule as schedule_routes
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError


DEPARTURE_DATE = "2026-03-25"

# Itinerary being worked: GF501 BAH-DOH-LHR. The final arrival is LHR,
# not the intermediate DOH stop. A second and third city pair for the
# same flight number are returned around it.
MATCHING_OPTION = {
    "origin_tz": "AST",
    "destination_tz": "GMT",
    "segments": [
        {
            "origin": "BAH",
            "destination": "DOH",
            "departure": "03-25T08:05",
            "arrival": "03-25T08:55",
            "origin_terminal": "1",
            "destination_terminal": "2",
            "equipment": "32N",
            "elapsed": "00.50",
            "accumulated": "00.50",
            "miles": "100",
            "meal": "S",
        },
        {
            "origin": "DOH",
            "destination": "LHR",
            "departure": "03-25T10:30",
            "arrival": "03-25T18:45",
            "origin_terminal": "2",
            "destination_terminal": "4",
            "equipment": "789",
            "elapsed": "07.15",
            "accumulated": "10.40",
            "miles": "3200",
            "meal": "M",
        },
    ],
}

DECOY_BEFORE = {
    "origin_tz": "GST",
    "destination_tz": "GST",
    "segments": [
        {
            "origin": "MCT",
            "destination": "DXB",
            "departure": "03-25T05:00",
            "arrival": "03-25T06:20",
            "origin_terminal": "9",
            "destination_terminal": "1",
            "equipment": "320",
            "elapsed": "01.20",
            "accumulated": "01.20",
            "miles": "220",
            "meal": "N",
        },
    ],
}

DECOY_AFTER = {
    "origin_tz": "GST",
    "destination_tz": "AST",
    "segments": [
        {
            "origin": "AUH",
            "destination": "KWI",
            "departure": "03-25T14:00",
            "arrival": "03-25T15:10",
            "origin_terminal": "3",
            "destination_terminal": "1",
            "equipment": "32A",
            "elapsed": "01.10",
            "accumulated": "01.10",
            "miles": "540",
            "meal": "N",
        },
    ],
}

DIRECT_OPTION = {
    "origin_tz": "AST",
    "destination_tz": "GMT",
    "segments": [
        {
            "origin": "BAH",
            "destination": "LHR",
            "departure": "03-25T08:05",
            "arrival": "03-25T14:30",
            "origin_terminal": "1",
            "destination_terminal": "4",
            "equipment": "789",
            "elapsed": "07.25",
            "accumulated": "07.25",
            "miles": "3300",
            "meal": "M",
        },
    ],
}


def _segment_xml(segment, flight_number):
    return f"""
        <FlightSegment AirMilesFlown="{segment["miles"]}"
                       ArrivalDateTime="{segment["arrival"]}"
                       DepartureDateTime="{segment["departure"]}"
                       ElapsedTime="{segment["elapsed"]}"
                       AccumulatedElapsedTime="{segment["accumulated"]}"
                       FlightNumber="{flight_number}">
          <DestinationLocation LocationCode="{segment["destination"]}" Terminal="{segment["destination_terminal"]}"/>
          <Equipment AirEquipType="{segment["equipment"]}"/>
          <MarketingAirline Code="GF" FlightNumber="{flight_number}"/>
          <Meal Code="{segment["meal"]}"/>
          <OriginLocation LocationCode="{segment["origin"]}" Terminal="{segment["origin_terminal"]}"/>
        </FlightSegment>"""


def _option_xml(option, flight_number):
    segments = "".join(_segment_xml(segment, flight_number) for segment in option["segments"])
    return f"""
        <OriginDestinationOption OriginTimeZone="{option["origin_tz"]}"
                                 DestinationTimeZone="{option["destination_tz"]}">
          {segments}
        </OriginDestinationOption>"""


def verify_flight_details_response(options, flight_number):
    """SOAP envelope in the shape SabreClient._extract_body accepts.

    Element names and cardinalities follow
    sabre_docs/VerifyFlightDetailsLLS2.0.1RQ/VerifyFlightDetailsLLS2.0.1RS.xsd.
    Repeated sibling elements are left for xmltodict to turn into lists.
    """
    options_xml = "".join(_option_xml(option, flight_number) for option in options)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader" eb:version="1.0" soap-env:mustUnderstand="1">
      <eb:Action>VerifyFlightDetailsLLSRS</eb:Action>
    </eb:MessageHeader>
  </soap-env:Header>
  <soap-env:Body>
    <VerifyFlightDetailsRS xmlns="http://webservices.sabre.com/sabreXML/2011/10" Version="2.0.1">
      <stl:ApplicationResults xmlns:stl="http://services.sabre.com/STL/v01" status="Complete">
        <stl:Success timeStamp="2026-03-25T00:00:00"/>
      </stl:ApplicationResults>
      <OriginDestinationOptions>
        {options_xml}
      </OriginDestinationOptions>
    </VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>"""


SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <wsse:BinarySecurityToken xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">placeholder-token</wsse:BinarySecurityToken>
  </soap-env:Body>
</soap-env:Envelope>"""


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        query = query or {}
        matches = [
            doc for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class MemoryDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = MemoryCollection()
        return dict.__getitem__(self, name)


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _install_sabre_transport(monkeypatch, response_xml, captured):
    """Stand in for the network only. Parsing stays in SabreClient."""

    def post(url, data=None, headers=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else (data or "")
        action = (headers or {}).get("SOAPAction", "")
        captured.append({"action": action, "body": body})
        if "SessionCreateRQ" in body or action == "SessionCreateRQ":
            return _Response(SESSION_RESPONSE)
        if "SessionCloseRQ" in body or action == "SessionCloseRQ":
            return _Response("<ok/>")
        if action == "VerifyFlightDetailsLLSRQ" or "VerifyFlightDetailsRQ" in body:
            return _Response(response_xml)
        raise AssertionError(f"unexpected Sabre call {action}")

    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)
    monkeypatch.setattr("backend.sabre.client.requests.post", post)


def _patch_other_ingestion_apis(monkeypatch):
    """Other Sabre calls are outside this bug. The feeder catches SabreError and continues."""

    def fail(self, *args, **kwargs):
        raise SabreError("not part of the schedule summary path")

    monkeypatch.setattr(SabreClient, "get_flight_status", fail)
    monkeypatch.setattr(SabreClient, "get_passenger_list", fail)
    monkeypatch.setattr(SabreClient, "get_reservations", fail)
    monkeypatch.setattr(SabreClient, "get_trip_report", fail)


def _assert_bah_lhr_summary(doc):
    assert doc["success"] is True
    assert doc["origin"] == "BAH"
    assert doc["originTerminal"] == "1"
    assert doc["scheduledDeparture"] == "03-25T08:05"
    assert doc["destination"] == "LHR"
    assert doc["destinationTerminal"] == "4"
    assert doc["scheduledArrival"] == "03-25T18:45"
    assert doc["destinationTimeZone"] == "GMT"
    assert [
        (segment["origin"], segment["destination"], segment["arrivalDateTime"])
        for segment in doc["segments"]
    ] == [
        ("BAH", "DOH", "03-25T08:55"),
        ("DOH", "LHR", "03-25T18:45"),
    ]


@pytest.fixture
def db(monkeypatch):
    memory = MemoryDB()
    monkeypatch.setattr(storage, "get_db", lambda: memory)
    monkeypatch.setattr(schedule_routes, "get_db", lambda: memory)
    monkeypatch.setattr(flights_routes, "get_db", lambda: memory)
    flights_routes._dashboard_cache.clear()
    return memory


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(schedule_routes.router)
    app.include_router(flights_routes.router)
    return TestClient(app)


def _seed_flight_status(db, flight_number):
    db["flight_status"].insert_one({
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": "2026-03-25T01:00:00Z",
        "status": "SCHEDULED",
    })


def test_ingestion_stores_final_leg_of_matching_option_and_dashboard_shows_it(monkeypatch, db, api):
    """Feeder knows the flight origin. A later option for that origin must win,
    and the Live Ops schedule card must show that option's final arrival.
    """
    flight_number = "501"
    captured = []
    _install_sabre_transport(
        monkeypatch,
        verify_flight_details_response(
            [DECOY_BEFORE, MATCHING_OPTION, DECOY_AFTER], flight_number),
        captured,
    )
    _patch_other_ingestion_apis(monkeypatch)

    result = run_feeder([{
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": f"{DEPARTURE_DATE}T08:05:00",
        "flightSequenceNumber": 1,
    }])

    schedule_result = result["results"][0]["apis"]["schedule"]
    assert schedule_result["destination"] == "LHR"
    assert schedule_result["scheduledArrival"] == "03-25T18:45"
    assert any(
        call["action"] == "VerifyFlightDetailsLLSRQ" for call in captured
    ), "ingestion did not call VerifyFlightDetails"

    stored = [
        doc for doc in db["flight_schedules"].docs
        if doc.get("flightNumber") == flight_number
    ]
    assert len(stored) == 1
    _assert_bah_lhr_summary(stored[0])

    _seed_flight_status(db, flight_number)
    dashboard = api.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": "BAH", "date": DEPARTURE_DATE},
    )
    assert dashboard.status_code == 200
    card = dashboard.json()["schedule"]
    assert card["origin"] == "BAH"
    assert card["destination"] == "LHR"
    assert card["scheduledArrival"] == "03-25T18:45"
    assert card["destinationTerminal"] == "4"
    assert len(card["segments"]) == 2

    stored_lookup = api.get(
        f"/flights/{flight_number}/schedule",
        params={"date": DEPARTURE_DATE},
    )
    assert stored_lookup.status_code == 200
    _assert_bah_lhr_summary(stored_lookup.json())


def test_lookup_without_origin_rejects_multiple_options_without_storing(monkeypatch, db, api):
    """A lookup that does not name an origin must not pick a city pair or store one."""
    flight_number = "502"
    captured = []
    _install_sabre_transport(
        monkeypatch,
        verify_flight_details_response(
            [DECOY_BEFORE, MATCHING_OPTION, DECOY_AFTER], flight_number),
        captured,
    )

    response = api.post("/flights/schedule/lookup", json={
        "airline": "GF",
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })

    assert response.status_code >= 400
    assert any(
        call["action"] == "VerifyFlightDetailsLLSRQ" for call in captured
    ), "lookup did not ask Sabre before refusing"
    assert db["flight_schedules"].docs == []


def test_lookup_with_origin_uses_final_leg_of_that_option(monkeypatch, db, api):
    flight_number = "503"
    captured = []
    _install_sabre_transport(
        monkeypatch,
        verify_flight_details_response(
            [DECOY_BEFORE, MATCHING_OPTION, DECOY_AFTER], flight_number),
        captured,
    )

    response = api.post("/flights/schedule/lookup", json={
        "airline": "GF",
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
        "origin": "BAH",
    })

    assert response.status_code == 200
    _assert_bah_lhr_summary(response.json())
    stored = db["flight_schedules"].docs
    assert len(stored) == 1
    _assert_bah_lhr_summary(stored[0])
    assert any(call["action"] == "VerifyFlightDetailsLLSRQ" for call in captured)


def test_lookup_single_option_multi_leg_uses_final_arrival(monkeypatch, db, api):
    """One itinerary with two segments still has to summarize the last segment."""
    flight_number = "504"
    _install_sabre_transport(
        monkeypatch,
        verify_flight_details_response([MATCHING_OPTION], flight_number),
        [],
    )

    response = api.post("/flights/schedule/lookup", json={
        "airline": "GF",
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })

    assert response.status_code == 200
    _assert_bah_lhr_summary(response.json())


def test_direct_flight_summary_stays_on_its_only_segment(monkeypatch, db, api):
    flight_number = "505"
    _install_sabre_transport(
        monkeypatch,
        verify_flight_details_response([DIRECT_OPTION], flight_number),
        [],
    )

    response = api.post("/flights/schedule/lookup", json={
        "airline": "GF",
        "flightNumber": flight_number,
        "departureDate": DEPARTURE_DATE,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "BAH"
    assert body["destination"] == "LHR"
    assert body["scheduledArrival"] == "03-25T14:30"
    assert body["destinationTerminal"] == "4"
    assert len(body["segments"]) == 1
