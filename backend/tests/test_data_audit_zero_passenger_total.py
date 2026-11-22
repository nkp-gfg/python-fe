"""Data-audit comparisons must keep a summed passenger total of zero.

These tests fail while OTP passenger totals of zero are dropped by a
truthiness check in the flight field map and the passenger comparison.
They pass once zero is stringified only after an explicit None check, so
"0" equals "0" and "0" beside a positive Sabre total is a mismatch.

The React data-audit page only renders the API row and summary. It is not
covered here. Cabin rows, the OTP flight list, and the feeder comparison
keep a numeric zero and are not part of this defect.
"""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from backend.api.routes import data_audit

# Load converter.py directly. Importing backend.feeder runs feeder/__init__.py,
# which imports the Sabre runner. The functions below are still the feeder's.
_converter_spec = importlib.util.spec_from_file_location(
    "falconeye_feeder_converter",
    _REPO_ROOT / "backend" / "feeder" / "converter.py",
)
_converter = importlib.util.module_from_spec(_converter_spec)
_converter_spec.loader.exec_module(_converter)
convert_flight_status = _converter.convert_flight_status
convert_passenger_list = _converter.convert_passenger_list

FLIGHT = "2057"
ORIGIN = "BAH"
FLIGHT_DATE = "2026-03-19"
DECOY_FLIGHT = "9999"

ZERO_COUNTS = [
    {"@classOfService": "Y", "Count": 0},
    {"@classOfService": "J", "Count": 0},
]
POSITIVE_COUNTS = [
    {"@classOfService": "Y", "Count": 3},
    {"@classOfService": "J", "Count": 1},
]


def _passenger(last_name: str) -> dict:
    return {
        "Name_Details": {"LastName": last_name, "FirstName": "TEST"},
        "PNRLocator": "ABC123",
        "PassengerID": "1",
        "LineNumber": "1",
        "BookingClass": "Y",
        "Cabin": "Y",
        "Destination": "LHR",
        "PassengerType": "F",
        "Indicators": {"Indicator": "Revenue"},
    }


def _manifest(flight_number: str, passengers: list[dict], cabin_count: int) -> dict:
    """Build the passenger_list document the feeder stores."""
    raw = {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "LHR", "AircraftType": "32N"},
            "CabinInfoList": {
                "CabinInfo": {
                    "Cabin": "Y",
                    "Count": str(cabin_count),
                    "Authorized": "180",
                    "Available": str(180 - cabin_count),
                    "Destination": "LHR",
                }
            },
            "DepartureGate": "A1",
        },
        "PassengerInfoList": {"PassengerInfo": passengers} if passengers else {},
    }
    doc = convert_passenger_list(raw, "GF", flight_number, FLIGHT_DATE, ORIGIN)
    doc["_id"] = f"pax-{flight_number}"
    return doc


def _flight_status(flight_number: str, total_passengers):
    """Build the flight_status document the feeder stores.

    ``total_passengers`` is written only when it is an int. The converter
    itself does not set that field; the comparison reads it when present.
    """
    raw = {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": "OPEN",
                "ScheduledDepartureDate": FLIGHT_DATE,
                "AircraftType": "32N",
                "AircraftRegistration": "A9C-XX",
            }
        },
        "PassengerCounts": [
            {"@classOfService": "Y", "Booked": "0", "Authorized": "150"},
            {"@classOfService": "J", "Booked": "0", "Authorized": "12"},
        ],
    }
    doc = convert_flight_status(raw, "GF", flight_number, ORIGIN, FLIGHT_DATE)
    if isinstance(total_passengers, int):
        doc["totalPassengers"] = total_passengers
    doc["_id"] = f"fs-{flight_number}"
    return doc


def _otp_row(flight_number: str, counts, flight_date: str = FLIGHT_DATE) -> dict:
    return {
        "flight_sequence_number": 7,
        "flight_number": flight_number,
        "scheduled_origin": ORIGIN,
        "scheduled_destination": "LHR",
        "actual_origin": ORIGIN,
        "actual_destination": "LHR",
        "flight_date": flight_date,
        "flight_status": "Scheduled",
        "aircraft_type": "32N",
        "aircraft_registration": "A9C-XX",
        "passenger_counts": counts,
    }


class _Collection:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def find_one(self, query, sort=None):
        found = [
            doc for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        if not found:
            return None
        if sort:
            for key, direction in reversed(sort):
                found.sort(
                    key=lambda doc: str(doc.get(key) or ""),
                    reverse=direction < 0,
                )
        return deepcopy(found[0])


class _Database:
    def __init__(self, flight_status: list[dict], passenger_list: list[dict]):
        self._collections = {
            "flight_status": _Collection(flight_status),
            "passenger_list": _Collection(passenger_list),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections[name]


class _AuditWorld:
    """Stand-in for query_all / Mongo at the boundary those fetchers call.

    The WHERE clauses follow ``_fetch_pg``. JSONB values are already Python
    lists, which is what psycopg2 2.9.13's global jsonb caster returns before
    ``query_all`` copies each RealDictRow into a dict. A JSON string is also
    accepted because ``_sum_pax`` and ``_parse_pg_pax_counts`` load non-lists.
    """

    def __init__(self):
        self.pg: list[dict] = []
        self.flight_status: list[dict] = []
        self.passenger_list: list[dict] = []
        app = FastAPI()
        app.include_router(data_audit.router)
        self.client = TestClient(app)

    def query_all(self, sql, params):
        assert "FROM otp.flight_xml_current" in sql
        values = list(params)
        flight_number = values.pop(0)
        seq = values.pop(0) if "flight_sequence_number = %s" in sql else None
        flight_date = values.pop(0) if "flight_date = %s" in sql else None
        origin = values.pop(0) if "scheduled_origin = %s" in sql else None
        assert values == []

        matched = []
        for row in self.pg:
            if row["flight_number"] != flight_number:
                continue
            if seq is not None and row["flight_sequence_number"] != seq:
                continue
            if flight_date is not None and str(row["flight_date"]) != str(flight_date):
                continue
            if origin is not None and row["scheduled_origin"] != origin:
                continue
            matched.append(row)
        matched.sort(key=lambda row: str(row["flight_date"]), reverse=True)
        return [dict(matched[0])] if matched else []

    def get_db(self):
        return _Database(self.flight_status, self.passenger_list)


@pytest.fixture
def world(monkeypatch):
    audit = _AuditWorld()
    monkeypatch.setattr(data_audit, "query_all", audit.query_all)
    monkeypatch.setattr(data_audit, "get_db", audit.get_db)
    return audit


def _assert_open_route(path: str) -> None:
    assert data_audit.router.dependencies == []
    routes = [
        route for route in data_audit.router.routes
        if isinstance(route, APIRoute) and route.path == path
    ]
    assert len(routes) == 1
    assert routes[0].dependencies == []
    assert routes[0].dependant.dependencies == []


def _get(world: _AuditWorld, path: str) -> dict:
    _assert_open_route(path)
    response = world.client.get(
        path.replace("{flight_number}", FLIGHT),
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {key.lower() for key in response.request.headers}
    return response.json()


def _total(body: dict) -> dict:
    matches = [row for row in body["rows"] if row["field"] == "Total Passengers"]
    assert len(matches) == 1, body["rows"]
    return matches[0]


def _seed_flight(world: _AuditWorld, counts, total_passengers) -> None:
    world.pg = [
        _otp_row(DECOY_FLIGHT, POSITIVE_COUNTS, flight_date="2026-03-20"),
        _otp_row(FLIGHT, counts),
    ]
    world.flight_status = [
        _flight_status(DECOY_FLIGHT, 4),
        _flight_status(FLIGHT, total_passengers),
    ]
    world.passenger_list = []


def _seed_passengers(world: _AuditWorld, counts, passengers: list[dict], cabin_count: int) -> None:
    world.pg = [
        _otp_row(DECOY_FLIGHT, POSITIVE_COUNTS, flight_date="2026-03-20"),
        _otp_row(FLIGHT, counts),
    ]
    world.flight_status = []
    world.passenger_list = [
        _manifest(DECOY_FLIGHT, [_passenger("DECOY")], cabin_count=1),
        _manifest(FLIGHT, passengers, cabin_count=cabin_count),
    ]


@pytest.mark.parametrize("counts", [ZERO_COUNTS, json.dumps(ZERO_COUNTS)])
def test_flight_compare_zero_otp_against_positive_sabre_is_mismatch(world, counts):
    _seed_flight(world, counts, total_passengers=4)
    body = _get(world, "/data-audit/{flight_number}/compare")
    total = _total(body)
    assert total["pgValue"] == "0"
    assert total["mongoValue"] == "4"
    assert total["match"] == "mismatch"
    assert body["summary"]["mismatch"] == sum(
        1 for row in body["rows"] if row["match"] == "mismatch"
    )


@pytest.mark.parametrize("counts", [ZERO_COUNTS, json.dumps(ZERO_COUNTS)])
def test_flight_compare_zero_otp_against_zero_sabre_is_match(world, counts):
    _seed_flight(world, counts, total_passengers=0)
    body = _get(world, "/data-audit/{flight_number}/compare")
    total = _total(body)
    assert total["pgValue"] == "0"
    assert total["mongoValue"] == "0"
    assert total["match"] == "match"
    assert "Total Passengers" not in [
        row["field"] for row in body["rows"] if row["match"] == "mismatch"
    ]


@pytest.mark.parametrize("counts", [ZERO_COUNTS, json.dumps(ZERO_COUNTS)])
def test_flight_compare_zero_otp_keeps_zero_when_sabre_total_is_absent(world, counts):
    status = _flight_status(FLIGHT, total_passengers=None)
    assert "totalPassengers" not in status
    _seed_flight(world, counts, total_passengers=None)
    body = _get(world, "/data-audit/{flight_number}/compare")
    total = _total(body)
    assert total["pgValue"] == "0"
    assert total["mongoValue"] is None
    assert total["match"] == "pg_only"


@pytest.mark.parametrize("counts", [ZERO_COUNTS, json.dumps(ZERO_COUNTS)])
def test_passenger_compare_zero_otp_against_positive_manifest_is_mismatch(world, counts):
    passengers = [_passenger("SMITH"), _passenger("JONES")]
    _seed_passengers(world, counts, passengers, cabin_count=0)
    stored = world.passenger_list[1]
    assert stored["totalPassengers"] == len(passengers)
    body = _get(world, "/data-audit/{flight_number}/passengers")
    total = _total(body)
    assert total["pgValue"] == "0"
    assert total["mongoValue"] == str(len(passengers))
    assert total["match"] == "mismatch"
    assert body["summary"]["mismatch"] == sum(
        1 for row in body["rows"] if row["match"] == "mismatch"
    )


@pytest.mark.parametrize("counts", [ZERO_COUNTS, json.dumps(ZERO_COUNTS)])
def test_passenger_compare_zero_otp_against_zero_manifest_is_match(world, counts):
    _seed_passengers(world, counts, passengers=[], cabin_count=0)
    stored = world.passenger_list[1]
    assert stored["totalPassengers"] == 0
    body = _get(world, "/data-audit/{flight_number}/passengers")
    total = _total(body)
    assert total["pgValue"] == "0"
    assert total["mongoValue"] == "0"
    assert total["match"] == "match"
    assert "Total Passengers" not in [
        row["field"] for row in body["rows"] if row["match"] == "mismatch"
    ]


def test_nonzero_passenger_totals_still_compare_as_strings(world):
    _seed_flight(world, POSITIVE_COUNTS, total_passengers=4)
    flight = _get(world, "/data-audit/{flight_number}/compare")
    flight_total = _total(flight)
    assert flight_total["pgValue"] == "4"
    assert flight_total["mongoValue"] == "4"
    assert flight_total["match"] == "match"

    _seed_flight(world, POSITIVE_COUNTS, total_passengers=9)
    mismatched = _get(world, "/data-audit/{flight_number}/compare")
    mismatched_total = _total(mismatched)
    assert mismatched_total["pgValue"] == "4"
    assert mismatched_total["mongoValue"] == "9"
    assert mismatched_total["match"] == "mismatch"

    _seed_passengers(world, POSITIVE_COUNTS, [_passenger("SMITH")], cabin_count=1)
    passengers = _get(world, "/data-audit/{flight_number}/passengers")
    passenger_total = _total(passengers)
    assert passenger_total["pgValue"] == "4"
    assert passenger_total["mongoValue"] == "1"
    assert passenger_total["match"] == "mismatch"
