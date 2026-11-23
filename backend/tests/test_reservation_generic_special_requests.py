"""Reservation-level Sabre generic service requests must survive conversion and the reservations API.

Trip_SearchRS carries GenericSpecialRequests on the reservation record (agency
contact CTCT lines, NXTM fare text, another system's RLOC). That list is not
the passenger SpecialRequests list and not SpecialServices/SpecialService.
convert_reservations has to copy each code, free text, and airline onto the
stored reservation as genericSpecialRequests, and both reservation read
endpoints have to return those rows after response shaping.

These tests fail while that list is dropped. They are the spec for the fix.

Not covered here:
- frontend ReservationCard (frontend/components/dashboard/reservation-view.tsx)
  is not exported and this repo has no frontend test runner, so the card's
  rendering of the rows is untested.
- Passenger-level GenericSpecialRequest elements under SpecialRequests are a
  different list and are not asserted.
- diff_reservations does not read this list; change detection of later edits
  to the lines is untested.
"""

import copy
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

_REPO = Path(__file__).resolve().parents[2]
_SAMPLES = sorted((_REPO / "sample_data" / "sample_xmlfiles").glob("Trip_SearchRS_*.xml"))
_SAMPLE_NAME = re.compile(
    r"Trip_SearchRS_([A-Z0-9]{2})(\d+)_([A-Z]{3})_(\d{4}-\d{2}-\d{2})\.xml$"
)

# Lines on the committed GF2057 / BOM / 2026-03-18 Trip Search response.
_CITED_SAMPLE = "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
_CITED_NOTICES = (
    {
        "code": "CTCT",
        "freeText": "BAH 17213777 RUMCO TRAVELS REF SAJIT/PAX CTC",
        "airline": "GF",
    },
    {
        "code": "CTCT",
        "freeText": "IAH 713-329-4500 ATPI - IATA 45541635",
        "airline": "GF",
    },
    {
        "code": "NXTM",
        "freeText": "FC-BOM GF DMM 350.32QBLIT3IA NUC350.32END ROE90.488005",
        "airline": "GF",
    },
    {
        "code": "RLOC",
        "freeText": "SWI1GG7S6ZV",
        "airline": "GF",
    },
)


def _trip_search_body(xml_text):
    """Parse a Trip Search SOAP envelope the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not read instance state. Skip SabreClient.__init__,
    # which only loads credentials, so the test does not need those values.
    client = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "Trip_SearchRS")


def _notice_from_request(item):
    return {
        "code": item.get("Code") or "",
        "freeText": item.get("FreeText") or "",
        "airline": item.get("AirlineCode") or "",
    }


def _expected_notices(raw_body):
    """Reservation-level GenericSpecialRequests after the same namespace strip the converter applies."""
    data = _strip_ns(raw_body)
    raw_reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get("Reservation", [])
    )
    expected = []
    for reservation in raw_reservations:
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        requests = _ensure_list(inner.get("GenericSpecialRequests"))
        expected.append([
            _notice_from_request(item)
            for item in requests
            if isinstance(item, dict)
        ])
    return expected


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None, projection=None):
        return self._document


class _Database:
    """In-memory stand-in for the two reads the reservations routes perform.

    store_reservations and store_snapshot persist the converted document as-is.
    find_one returns that document; it does not reshape reservation fields.
    """

    def __init__(self, reservations_doc, snapshot_doc):
        self._collections = {
            "reservations": _Collection(reservations_doc),
            "snapshots": _Collection(snapshot_doc),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _api_client(monkeypatch, converted):
    snapshot = {
        "data": copy.deepcopy(converted),
        "sequenceNumber": 4,
        "capturedAt": "2026-03-18T00:00:00Z",
    }
    database = _Database(copy.deepcopy(converted), snapshot)
    monkeypatch.setattr(reservations_route, "get_db", lambda: database)
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def _flight_from_name(path: Path):
    match = _SAMPLE_NAME.match(path.name)
    assert match, path.name
    airline, flight_number, origin, departure_date = match.groups()
    return airline, flight_number, origin, departure_date


def _notices(payload):
    return [reservation.get("genericSpecialRequests") for reservation in payload["reservations"]]


def _converted_case(sample_path: Path):
    airline, flight_number, origin, departure_date = _flight_from_name(sample_path)
    raw_body = _trip_search_body(sample_path.read_text(encoding="utf-8"))
    expected = _expected_notices(raw_body)
    converted = convert_reservations(
        raw_body, airline, flight_number, origin, departure_date,
    )
    return airline, flight_number, origin, departure_date, expected, converted


@pytest.mark.parametrize("sample_path", _SAMPLES, ids=lambda path: path.name)
def test_convert_reservations_stores_reservation_level_notices(sample_path):
    _, _, _, _, expected, converted = _converted_case(sample_path)
    assert _notices(converted) == expected


@pytest.mark.parametrize("sample_path", _SAMPLES, ids=lambda path: path.name)
def test_reservations_list_returns_reservation_level_notices(sample_path, monkeypatch):
    _, flight_number, origin, departure_date, expected, converted = _converted_case(sample_path)
    client = _api_client(monkeypatch, converted)

    latest = client.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": origin, "date": departure_date},
    )
    assert latest.status_code == 200
    assert _notices(latest.json()) == expected
    assert "_raw" not in latest.json()

    as_of = client.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": origin, "date": departure_date, "snapshot_sequence": 4},
    )
    assert as_of.status_code == 200
    assert _notices(as_of.json()) == expected

    if sample_path.name == _CITED_SAMPLE:
        returned = [
            row
            for rows in _notices(latest.json())
            if rows
            for row in rows
        ]
        for notice in _CITED_NOTICES:
            assert notice in returned


@pytest.mark.parametrize("sample_path", _SAMPLES, ids=lambda path: path.name)
def test_reservation_by_pnr_returns_reservation_level_notices(sample_path, monkeypatch):
    _, flight_number, _, departure_date, expected, converted = _converted_case(sample_path)
    populated = next((index for index, rows in enumerate(expected) if rows), 0)
    pnr = converted["reservations"][populated]["pnr"]
    client = _api_client(monkeypatch, converted)
    response = client.get(
        f"/flights/{flight_number}/reservations/{pnr}",
        params={"date": departure_date},
    )
    assert response.status_code == 200
    assert _notices(response.json()) == [expected[populated]]
