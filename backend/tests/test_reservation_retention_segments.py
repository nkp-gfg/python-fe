"""PNR retention holds on non-air Trip Search segments must be kept.

Trip_SearchRS itineraries in the captured Gulf Air responses contain an air
leg followed by a General segment (line type OTH, status HK, a station, a
future date, and the text PNR RETENTION). convert_reservations currently
appends a segment only when an Air child is present, and the reservation
read models only describe air fields. These tests are the spec for the fix:
they fail while that hold is discarded and pass once every General segment
is stored and returned.

Required fields on each kept General segment (camelCase, same object as the
air segments):

- segmentType: "General"
- lineType: Line @Type (OTH for these holds)
- status: Line @Status (HK)
- location: Location @LocationCode
- date: DateTime
- text: Text (PNR RETENTION)

Air segments stay in itinerary order and keep departureAirport,
arrivalAirport, flightNumber, status, and departureDate.

Not covered here: the reservation card
(frontend/components/dashboard/reservation-view.tsx) only renders air
fields from the segments array this API returns. It has no test runner
and does not render segmentType, lineType, location, date, or text.
"""

import copy
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservation_routes
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient


SAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "sample_data", "sample_xmlfiles"
)
SAMPLES = (
    ("Trip_SearchRS_GF2057_BOM_2026-03-19.xml", "2026-03-19"),
    ("Trip_SearchRS_GF2057_BOM_2026-03-18.xml", "2026-03-18"),
)
AIRLINE = "GF"
FLIGHT_NUMBER = "2057"
ORIGIN = "BOM"


def _parse_trip_search(filename):
    """Parse a captured SOAP envelope the same way SabreClient.get_reservations does."""
    path = os.path.join(SAMPLE_DIR, filename)
    with open(path, encoding="utf-8") as handle:
        xml_text = handle.read()
    parsed = SabreClient._parse_xml(xml_text)
    parser = SabreClient.__new__(SabreClient)
    return parser._extract_body(parsed, "Trip_SearchRS")


def _source_reservations(body):
    data = _strip_ns(body)
    return _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )


def _source_segments(reservation):
    inner = (reservation.get("GetReservationRS") or {}).get("Reservation") or {}
    passenger_reservation = inner.get("PassengerReservation") or {}
    return _ensure_list(
        (passenger_reservation.get("Segments") or {}).get("Segment", [])
    )


def _expected_segment(segment):
    """Map one parsed Trip Search segment to the fields the product must keep."""
    if not isinstance(segment, dict):
        return None
    if segment.get("Air"):
        air = segment["Air"]
        return {
            "departureAirport": air.get("DepartureAirport", ""),
            "arrivalAirport": air.get("ArrivalAirport", ""),
            "flightNumber": air.get("FlightNumber", ""),
            "status": air.get("ActionCode", ""),
            "departureDate": air.get("DepartureDateTime", ""),
        }
    general = segment.get("General")
    if not general:
        return None
    if isinstance(general, list):
        general = general[0] if general else {}
    if not isinstance(general, dict):
        return None
    line = general.get("Line") or {}
    if not isinstance(line, dict):
        line = {}
    location = general.get("Location") or {}
    if not isinstance(location, dict):
        location = {}
    return {
        "segmentType": "General",
        "lineType": line.get("@Type", ""),
        "status": line.get("@Status", ""),
        "location": location.get("@LocationCode", ""),
        "date": general.get("DateTime", ""),
        "text": general.get("Text", ""),
    }


def _expected_itineraries(body):
    expected = []
    for reservation in _source_reservations(body):
        segments = []
        for segment in _source_segments(reservation):
            mapped = _expected_segment(segment)
            if mapped is not None:
                segments.append(mapped)
        expected.append({"pnr": reservation.get("@Locator", ""), "segments": segments})
    return expected


def _assert_itineraries(actual_reservations, expected):
    by_pnr = {reservation.get("pnr"): reservation for reservation in actual_reservations}
    retention_holds = 0
    for item in expected:
        got = by_pnr.get(item["pnr"])
        assert got is not None, item["pnr"]
        got_segments = got.get("segments") or []
        assert len(got_segments) == len(item["segments"]), (
            f"{item['pnr']} kept {len(got_segments)} segments, "
            f"source itinerary has {len(item['segments'])}"
        )
        for index, (actual, wanted) in enumerate(zip(got_segments, item["segments"])):
            for key, value in wanted.items():
                assert actual.get(key) == value, (
                    f"{item['pnr']} segment {index} field {key}: "
                    f"{actual.get(key)!r} != {value!r}"
                )
            if wanted.get("text") == "PNR RETENTION":
                retention_holds += 1
    assert retention_holds > 0


def _converted(filename, departure_date):
    body = _parse_trip_search(filename)
    document = convert_reservations(
        body, AIRLINE, FLIGHT_NUMBER, ORIGIN, departure_date
    )
    return body, document


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None):
        return copy.deepcopy(self._document)


class _Database:
    def __init__(self, reservations_doc, snapshot_doc):
        self._collections = {
            "reservations": _Collection(reservations_doc),
            "snapshots": _Collection(snapshot_doc),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _client_for(document):
    snapshot = {
        "data": document,
        "sequenceNumber": 2,
        "capturedAt": "2026-03-19T00:00:00Z",
    }
    database = _Database(document, snapshot)

    def get_db():
        return database

    reservation_routes.get_db = get_db
    app = FastAPI()
    app.include_router(reservation_routes.router)
    return TestClient(app)


@pytest.mark.parametrize("filename,departure_date", SAMPLES)
def test_convert_reservations_keeps_general_retention_segments(
    filename, departure_date
):
    body, document = _converted(filename, departure_date)
    _assert_itineraries(document["reservations"], _expected_itineraries(body))


@pytest.mark.parametrize("filename,departure_date", SAMPLES)
def test_reservations_endpoint_returns_retention_hold(filename, departure_date):
    body, document = _converted(filename, departure_date)
    expected = _expected_itineraries(body)
    client = _client_for(document)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations",
        params={"origin": ORIGIN, "date": departure_date},
    )
    assert response.status_code == 200
    _assert_itineraries(response.json()["reservations"], expected)


@pytest.mark.parametrize("filename,departure_date", SAMPLES)
def test_reservation_snapshot_endpoint_returns_retention_hold(
    filename, departure_date
):
    body, document = _converted(filename, departure_date)
    expected = _expected_itineraries(body)
    client = _client_for(document)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations",
        params={
            "origin": ORIGIN,
            "date": departure_date,
            "snapshot_sequence": 2,
        },
    )
    assert response.status_code == 200
    _assert_itineraries(response.json()["reservations"], expected)


@pytest.mark.parametrize("filename,departure_date", SAMPLES)
def test_reservation_by_pnr_returns_retention_hold(filename, departure_date):
    body, document = _converted(filename, departure_date)
    expected = _expected_itineraries(body)
    held = next(
        item
        for item in expected
        if any(segment.get("text") == "PNR RETENTION" for segment in item["segments"])
    )
    client = _client_for(document)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations/{held['pnr']}",
        params={"date": departure_date},
    )
    assert response.status_code == 200
    _assert_itineraries(response.json()["reservations"], [held])
