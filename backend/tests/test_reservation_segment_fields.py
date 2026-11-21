"""Reservations responses must keep the segment fields Sabre Trip Search stored.

The reservations tab loads GET /flights/{flightNumber}/reservations, including
the historical view selected with snapshot_sequence. Ingestion already stores
equipment, the operating carrier, the codeshare flag, and the connection flags
on each air segment. These tests parse a captured Trip_SearchRS the same way
SabreClient does, store it through the feeder storage functions, and request
both reads. They fail while ReservationsResponse drops those fields.
"""

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import backend.api.main as main_mod
import backend.api.routes.reservations as reservations_route
import backend.feeder.storage as storage
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

SAMPLE = (
    ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)
FLIGHT = "2057"
ORIGIN = "BOM"
DEPARTURE_DATE = "2026-03-19"

# Fields the converter writes and the reservations itinerary needs.
SEGMENT_FIELDS = (
    "equipmentType",
    "operatingAirline",
    "operatingFlightNumber",
    "isCodeShare",
    "scheduleChangeIndicator",
    "inboundConnection",
    "outboundConnection",
)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    """In-memory stand-in for the find_one/insert_one calls this path makes.

    Query matching covers equality and $lte, which are the operators in
    get_reservations, get_snapshot_data_as_of, and store_snapshot.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"mem-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=field: (
                        doc.get(field) is None,
                        doc.get(field),
                    ),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        found = copy.deepcopy(matches[0])
        if projection:
            return {
                key: found.get(key)
                for key, include in projection.items()
                if include
            }
        return found

    def index_information(self):
        return {}

    def create_index(self, keys, **kwargs):
        return kwargs.get("name")

    def drop_index(self, name):
        return None


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]

    def command(self, name, *args, **kwargs):
        if name == "ping":
            return {"ok": 1}
        raise AssertionError(f"unexpected database command {name}")


def _matches(document, query):
    for key, expected in query.items():
        if isinstance(expected, dict):
            if set(expected) != {"$lte"}:
                raise AssertionError(f"unhandled query operator in {expected}")
            actual = document.get(key)
            if actual is None or actual > expected["$lte"]:
                return False
        elif document.get(key) != expected:
            return False
    return True


class _RedisPing:
    def ping(self):
        return True


def _load_converted_reservations():
    """Parse the captured SOAP body the way SabreClient.get_reservations does."""
    xml_text = SAMPLE.read_text(encoding="utf-8")
    parsed = SabreClient._parse_xml(xml_text)
    raw = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(raw, "GF", FLIGHT, ORIGIN, DEPARTURE_DATE)


def _stored_segments(doc):
    return [seg for res in doc["reservations"] for seg in res["segments"]]


def _assert_response_keeps_segment_fields(stored_doc, body):
    assert body["flightNumber"] == FLIGHT
    assert body["departureAirport"] == ORIGIN
    assert body["departureDate"] == DEPARTURE_DATE
    stored_reservations = stored_doc["reservations"]
    assert len(body["reservations"]) == len(stored_reservations)
    compared = 0
    for stored_res, response_res in zip(
        stored_reservations, body["reservations"], strict=True
    ):
        assert response_res["pnr"] == stored_res["pnr"]
        assert len(response_res["segments"]) == len(stored_res["segments"])
        for stored_seg, response_seg in zip(
            stored_res["segments"], response_res["segments"], strict=True
        ):
            compared += 1
            for field in SEGMENT_FIELDS:
                assert field in response_seg, (
                    f"{stored_res['pnr']} "
                    f"{stored_seg['departureAirport']}-{stored_seg['arrivalAirport']} "
                    f"dropped {field}; stored value was {stored_seg[field]!r}"
                )
                assert response_seg[field] == stored_seg[field]
    assert compared > 0


def _prepare_database():
    converted = _load_converted_reservations()
    stored_segments = _stored_segments(converted)
    bus = [
        seg
        for seg in stored_segments
        if seg["departureAirport"] == "DMM"
        and seg["arrivalAirport"] == "BAH"
        and seg["equipmentType"] == "BUS"
    ]
    assert bus, "Trip Search conversion did not retain equipment BUS on DMM-BAH"
    assert all(
        seg["operatingAirline"] == "GF" and seg["operatingFlightNumber"] == "5023"
        for seg in bus
    )
    assert any(seg["operatingAirline"] == "AI" for seg in stored_segments)

    memory = MemoryDB()
    storage.init_db(memory)
    reservations_route.get_db = lambda: memory
    main_mod.get_db = lambda: memory
    main_mod.get_redis = lambda: _RedisPing()
    storage.store_reservations(converted)
    storage.store_snapshot(
        request_id="req-reservation-fields",
        airline="GF",
        flight_number=FLIGHT,
        origin=ORIGIN,
        departure_date=DEPARTURE_DATE,
        snapshot_type="reservations",
        data=converted,
    )
    return converted


_STORED = _prepare_database()


def _client():
    return TestClient(main_mod.app)


def test_latest_reservations_keep_equipment_and_operating_carrier():
    with _client() as client:
        response = client.get(
            f"/flights/{FLIGHT}/reservations",
            params={"origin": ORIGIN, "date": DEPARTURE_DATE},
        )
    assert response.status_code == 200, response.text[:400]
    _assert_response_keeps_segment_fields(_STORED, response.json())


def test_snapshot_reservations_keep_equipment_and_operating_carrier():
    with _client() as client:
        response = client.get(
            f"/flights/{FLIGHT}/reservations",
            params={
                "origin": ORIGIN,
                "date": DEPARTURE_DATE,
                "snapshot_sequence": 1,
            },
        )
    assert response.status_code == 200, response.text[:400]
    _assert_response_keeps_segment_fields(_STORED, response.json())
