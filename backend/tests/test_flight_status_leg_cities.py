"""Flight-status legs must keep ACS LegInfo stations and times.

ACS_FlightDetailRS legs use LegCity, ControllingCityInd, LegStatus, LegDate,
LegDepartureTime, and LegArrivalTime. The stored leg keeps the dashboard's
city / controllingCity / status keys and also keeps date, departureTime, and
arrivalTime. controllingCity is true when ControllingCityInd is present and
non-empty. The dashboard destination stays the passenger-list destination
when that document has one, and otherwise uses the first stored leg city
that is not the origin.

These tests fail while convert_flight_status drops those fields.
"""

import copy
from pathlib import Path

import pytest

from backend.api.routes import flights as flights_routes
from backend.api.snapshot_versioning import get_snapshot_data_as_of
from backend.feeder.converter import convert_flight_status
from backend.feeder.storage import init_db, store_flight_status, store_snapshot
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"
)

# Stored shape produced from the committed GF2057 flight-detail sample.
GF2057_LEGS = [
    {
        "city": "BOM",
        "controllingCity": True,
        "status": "FINAL",
        "date": "2026-03-19",
        "departureTime": "05:40PM",
        "arrivalTime": "",
    },
    {
        "city": "DMM",
        "controllingCity": False,
        "status": "",
        "date": "",
        "departureTime": "",
        "arrivalTime": "09:25PM",
    },
]


def _flight_detail_body(xml_text):
    """Same parse and body extract as SabreClient.get_flight_status."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _envelope(inner_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{inner_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _detail_xml(legs_xml, origin="BAH", flight="1001", status="OPEN"):
    legs = "".join(legs_xml)
    return _envelope(
        '<ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">'
        "<ItineraryResponseList><ItineraryInfoResponse>"
        f"<Airline>GF</Airline><Flight>{flight}</Flight>"
        f"<Origin>{origin}</Origin>"
        "<ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>"
        f"<FlightStatus>{status}</FlightStatus>"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        f"<LegInfoList>{legs}</LegInfoList>"
        "</ns3:ACS_FlightDetailRS>"
    )


def _leg_view(leg):
    return {
        "city": leg.get("city", None),
        "controllingCity": leg.get("controllingCity", None),
        "status": leg.get("status", None),
        "date": leg.get("date", None),
        "departureTime": leg.get("departureTime", None),
        "arrivalTime": leg.get("arrivalTime", None),
    }


def _convert_xml(xml_text, origin, flight_number="2057"):
    body = _flight_detail_body(xml_text)
    return convert_flight_status(body, "GF", flight_number, origin, "2026-03-19")


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


def _match(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and not (
                value is not None and value <= expected["$lte"]
            ):
                return False
        elif value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, way in reversed(key):
                self.docs.sort(
                    key=lambda doc, field=field: doc.get(field) or "",
                    reverse=way < 0,
                )
        else:
            self.docs.sort(
                key=lambda doc, key=key: doc.get(key) or "",
                reverse=(direction or 1) < 0,
            )
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        if sort:
            matches = list(_Cursor(matches).sort(sort))
        return matches[0] if matches else None

    def find(self, query, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        cursor = _Cursor(matches)
        if sort:
            cursor.sort(sort)
        return cursor


class _DB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


@pytest.fixture()
def db(monkeypatch):
    memory = _DB()
    init_db(memory)
    monkeypatch.setattr(flights_routes, "get_db", lambda: memory)
    flights_routes._dashboard_cache.clear()
    return memory


def test_sample_xml_legs_keep_cities_times_and_controlling_marker():
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    assert [_leg_view(leg) for leg in doc["legs"]] == GF2057_LEGS


def test_single_leginfo_element_is_normalized():
    xml = _detail_xml(
        [
            "<LegInfo><LegCity>DXB</LegCity>"
            "<ControllingCityInd>Y</ControllingCityInd>"
            "<LegStatus>OPEN</LegStatus>"
            "<LegDate>2026-03-19</LegDate>"
            "<LegDepartureTime>01:15AM</LegDepartureTime>"
            "</LegInfo>"
        ],
        origin="BAH",
    )
    doc = _convert_xml(xml, "BAH", flight_number="1001")
    assert [_leg_view(leg) for leg in doc["legs"]] == [
        {
            "city": "DXB",
            "controllingCity": True,
            "status": "OPEN",
            "date": "2026-03-19",
            "departureTime": "01:15AM",
            "arrivalTime": "",
        }
    ]


def test_empty_controlling_city_indicator_is_false():
    xml = _detail_xml(
        [
            "<LegInfo><LegCity>BAH</LegCity>"
            "<ControllingCityInd></ControllingCityInd>"
            "<LegStatus>OPEN</LegStatus></LegInfo>",
            "<LegInfo><LegCity>MCT</LegCity>"
            "<LegArrivalTime>06:40AM</LegArrivalTime></LegInfo>",
        ]
    )
    doc = _convert_xml(xml, "BAH", flight_number="1001")
    assert [_leg_view(leg) for leg in doc["legs"]] == [
        {
            "city": "BAH",
            "controllingCity": False,
            "status": "OPEN",
            "date": "",
            "departureTime": "",
            "arrivalTime": "",
        },
        {
            "city": "MCT",
            "controllingCity": False,
            "status": "",
            "date": "",
            "departureTime": "",
            "arrivalTime": "06:40AM",
        },
    ]


def test_status_endpoints_return_stored_sample_legs(db):
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    store_flight_status(doc)
    store_snapshot(
        "request-1", "GF", "2057", "BOM", "2026-03-19", "flight_status", doc
    )

    current = flights_routes.get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    history = flights_routes.get_flight_status_history(
        "2057", origin="BOM", date="2026-03-19", limit=20
    )
    historical = flights_routes.get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=1
    )

    assert [_leg_view(leg) for leg in current["legs"]] == GF2057_LEGS
    assert "_raw" not in current and "_id" not in current
    assert [_leg_view(leg) for leg in history[0]["legs"]] == GF2057_LEGS
    assert [_leg_view(leg) for leg in historical["legs"]] == GF2057_LEGS
    assert historical["snapshotSequenceNumber"] == 1


def test_dashboard_uses_downline_leg_city_when_passenger_list_is_missing(db):
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    store_flight_status(doc)

    dashboard = flights_routes.get_flight_dashboard(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    assert dashboard["route"] == {
        "origin": "BOM",
        "destination": "DMM",
        "departureDate": "2026-03-19",
    }
    assert [_leg_view(leg) for leg in dashboard["flightStatus"]["legs"]] == GF2057_LEGS


def test_dashboard_uses_downline_leg_city_when_passenger_destination_is_blank(db):
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    store_flight_status(doc)
    db["passenger_list"].insert_one(
        {
            "flightNumber": "2057",
            "origin": "BOM",
            "departureDate": "2026-03-19",
            "destination": "",
            "cabinSummary": [{"cabin": "Y", "destination": ""}],
            "passengers": [],
            "totalPassengers": 0,
            "fetchedAt": "2026-03-19T00:00:00Z",
        }
    )

    dashboard = flights_routes.get_flight_dashboard(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    assert dashboard["route"]["destination"] == "DMM"


def test_passenger_list_destination_wins_over_leg_city():
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    passenger = {
        "destination": "BAH",
        "cabinSummary": [{"cabin": "Y", "destination": "MCT"}],
        "passengers": [],
    }
    assert flights_routes._extract_destination(doc, passenger) == "BAH"
    payload = flights_routes._build_dashboard_payload(
        doc, passenger, "BOM", "2026-03-19", {}
    )
    assert payload["route"]["destination"] == "BAH"


def test_cabin_destination_wins_over_leg_city_when_passenger_destination_is_blank():
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    passenger = {
        "destination": "",
        "cabinSummary": [{"cabin": "Y", "destination": "MCT"}],
        "passengers": [],
    }
    assert flights_routes._extract_destination(doc, passenger) == "MCT"


def test_flight_list_summaries_use_downline_leg_city_without_passenger_destination():
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    blank_passenger = {
        "destination": "",
        "cabinSummary": [],
        "passengers": [],
        "totalPassengers": 0,
    }
    assert flights_routes._summarize_flight_for_list(doc, None)["destination"] == "DMM"
    assert (
        flights_routes._summarize_flight_for_list(doc, blank_passenger)["destination"]
        == "DMM"
    )
    assert (
        flights_routes._summarize_flight_for_list_fast(doc, None)["destination"]
        == "DMM"
    )
    assert (
        flights_routes._summarize_flight_for_list_fast(doc, blank_passenger)[
            "destination"
        ]
        == "DMM"
    )
    kept = flights_routes._summarize_flight_for_list_fast(
        doc, {"destination": "BAH", "passengers": [], "totalPassengers": 0}
    )
    assert kept["destination"] == "BAH"


def test_historical_snapshot_reader_returns_converted_leg_cities(db):
    doc = _convert_xml(SAMPLE_XML.read_text(encoding="utf-8"), "BOM")
    store_snapshot(
        "request-1", "GF", "2057", "BOM", "2026-03-19", "flight_status", doc
    )
    loaded = get_snapshot_data_as_of(
        db,
        flight_number="2057",
        snapshot_type="flight_status",
        snapshot_sequence=3,
        origin="BOM",
        departure_date="2026-03-19",
    )
    assert [_leg_view(leg) for leg in loaded["legs"]] == GF2057_LEGS
