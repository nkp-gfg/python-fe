"""Estimated purge time from Sabre Trip Search must be stored and shown.

Trip_SearchRS puts EstimatedPurgeTimestamp on BookingDetails. Some bookings
use the flight date and others use a later date. convert_reservations copies
CreationTimestamp and UpdateTimestamp from that element and drops the purge
time, and the reservations response model then drops any field it does not
declare. These tests fail until each stored reservation keeps the purge time,
both reservation reads return it, and the reservation card shows it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservations_routes
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.feeder import storage as feeder_storage
from backend.sabre.client import SabreClient

REPO = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO / "sample_data" / "sample_xmlfiles"

# Values read from the committed Trip Search captures. A booking held past the
# flight date must not collapse to the same purge time as one purged that night.
MARCH_18 = SAMPLE_DIR / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
MARCH_19 = SAMPLE_DIR / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
LATER_THAN_FLIGHT = {
    ("2026-03-18", "CQVJQD"): "2026-03-27T00:00:00",
    ("2026-03-19", "DQGZWU"): "2027-02-12T00:00:00",
}
FLIGHT_DATE_PURGE = {
    ("2026-03-18", "GCPVKA"): "2026-03-18T00:00:00",
    ("2026-03-19", "FOMUXD"): "2026-03-19T00:00:00",
}


def _trip_search_body(path: Path):
    """Run the same SOAP parse the Sabre client uses on a captured response."""
    xml_text = path.read_text(encoding="utf-8")
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "Trip_SearchRS")


def _booking_times(body):
    """Read BookingDetails the same way convert_reservations locates it."""
    data = _strip_ns(body)
    raw_reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )
    times = {}
    for reservation in raw_reservations:
        pnr = reservation.get("@Locator", "")
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        booking = inner.get("BookingDetails", {}) or {}
        times[pnr] = {
            "created": booking.get("CreationTimestamp", ""),
            "purge": booking.get("EstimatedPurgeTimestamp", ""),
        }
    return times


def _convert(path: Path, departure_date: str):
    body = _trip_search_body(path)
    document = convert_reservations(body, "GF", "2057", "BOM", departure_date)
    return body, document


class _FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query, sort=None):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    def insert_one(self, doc):
        self.docs.append(doc)

        class _Result:
            inserted_id = "stored-reservation-doc"

        return _Result()

    @staticmethod
    def _matches(doc, query):
        for key, expected in query.items():
            if isinstance(expected, dict) and "$lte" in expected:
                actual = doc.get(key)
                if actual is None or actual > expected["$lte"]:
                    return False
                continue
            if key == "reservations.pnr":
                pnrs = [item.get("pnr") for item in doc.get("reservations", [])]
                if expected not in pnrs:
                    return False
                continue
            if doc.get(key) != expected:
                return False
        return True


class _FakeDB:
    def __init__(self, reservations_docs, snapshot_docs):
        self._collections = {
            "reservations": _FakeCollection(reservations_docs),
            "snapshots": _FakeCollection(snapshot_docs),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _api_client(document, snapshots=None):
    app = FastAPI()
    app.include_router(reservations_routes.router)
    database = _FakeDB([document], snapshots or [])
    app.dependency_overrides.clear()
    return app, database


@pytest.fixture
def api(monkeypatch):
    body, document = _convert(MARCH_18, "2026-03-18")
    snapshot = {
        "flightNumber": "2057",
        "snapshotType": "reservations",
        "sequenceNumber": 4,
        "origin": "BOM",
        "departureDate": "2026-03-18",
        "data": document,
    }
    app, database = _api_client(document, [snapshot])
    monkeypatch.setattr(reservations_routes, "get_db", lambda: database)
    with TestClient(app) as client:
        yield client, body, document


def test_converter_keeps_each_booking_purge_time():
    for path, departure_date in ((MARCH_18, "2026-03-18"), (MARCH_19, "2026-03-19")):
        body, document = _convert(path, departure_date)
        expected = _booking_times(body)
        stored = {item["pnr"]: item for item in document["reservations"]}

        assert expected, f"{path.name} produced no bookings"
        assert set(stored) == set(expected)

        for pnr, times in expected.items():
            assert isinstance(times["purge"], str) and times["purge"], (
                f"{pnr} BookingDetails had no EstimatedPurgeTimestamp"
            )
            created = stored[pnr].get("createdAt")
            purge = stored[pnr].get("estimatedPurgeTimestamp")
            assert created == times["created"]
            assert purge == times["purge"], (
                f"{pnr} stored {purge!r}, Sabre sent {times['purge']!r}"
            )

        later = LATER_THAN_FLIGHT[departure_date, "CQVJQD" if departure_date == "2026-03-18" else "DQGZWU"]
        same_night = FLIGHT_DATE_PURGE[departure_date, "GCPVKA" if departure_date == "2026-03-18" else "FOMUXD"]
        later_pnr = "CQVJQD" if departure_date == "2026-03-18" else "DQGZWU"
        same_pnr = "GCPVKA" if departure_date == "2026-03-18" else "FOMUXD"
        assert stored[later_pnr]["estimatedPurgeTimestamp"] == later
        assert stored[same_pnr]["estimatedPurgeTimestamp"] == same_night
        assert later != same_night


def test_store_reservations_persists_purge_time(monkeypatch):
    _body, document = _convert(MARCH_18, "2026-03-18")
    collection = _FakeCollection([])
    monkeypatch.setattr(
        feeder_storage, "get_db", lambda: {"reservations": collection}
    )

    feeder_storage.store_reservations(document)

    stored = {item["pnr"]: item for item in collection.docs[0]["reservations"]}
    later = stored["CQVJQD"].get("estimatedPurgeTimestamp")
    same_night = stored["GCPVKA"].get("estimatedPurgeTimestamp")
    assert later == "2026-03-27T00:00:00"
    assert same_night == "2026-03-18T00:00:00"


def test_list_endpoint_returns_distinct_purge_times(api):
    client, _body, _document = api
    response = client.get(
        "/flights/2057/reservations", params={"origin": "BOM", "date": "2026-03-18"}
    )
    assert response.status_code == 200, response.text[:300]
    stored = {item["pnr"]: item for item in response.json()["reservations"]}
    later = stored["CQVJQD"].get("estimatedPurgeTimestamp")
    same_night = stored["GCPVKA"].get("estimatedPurgeTimestamp")
    assert later == "2026-03-27T00:00:00"
    assert same_night == "2026-03-18T00:00:00"


def test_snapshot_list_returns_purge_time(api):
    client, _body, _document = api
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 4},
    )
    assert response.status_code == 200, response.text[:300]
    stored = {item["pnr"]: item for item in response.json()["reservations"]}
    later = stored["CQVJQD"].get("estimatedPurgeTimestamp")
    assert later == "2026-03-27T00:00:00"


def test_pnr_endpoint_returns_purge_time(api):
    client, _body, _document = api
    response = client.get(
        "/flights/2057/reservations/CQVJQD", params={"date": "2026-03-18"}
    )
    assert response.status_code == 200, response.text[:300]
    match = response.json()["reservations"][0]
    assert match.get("pnr") == "CQVJQD"
    purge = match.get("estimatedPurgeTimestamp")
    assert purge == "2026-03-27T00:00:00"


def _interface_body(source, name):
    marker = f"export interface {name} {{"
    start = source.index(marker) + len(marker)
    depth = 1
    for index, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"{name} interface not closed")


def test_reservation_type_declares_purge_time():
    source = (REPO / "frontend" / "lib" / "types.ts").read_text(encoding="utf-8")
    interface = _interface_body(source, "Reservation")
    assert "estimatedPurgeTimestamp" in interface


def test_reservation_card_shows_distinct_purge_dates():
    script = Path(__file__).with_name("render_reservation_card.mjs")
    env = os.environ.copy()
    env["TZ"] = "UTC"
    completed = subprocess.run(
        ["node", str(script)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        completed.stdout[-2000:] + "\n" + completed.stderr[-2000:]
    )
