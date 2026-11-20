"""Selling office must survive the reservations API the card reads.

Trip Search ingestion stores pointOfSale and creationAgent. The reservations
tab loads GET /flights/{flightNumber}/reservations, including the historical
snapshot_sequence view, and the card prints creationAgent, isoCountry, and
pseudoCityCode from that JSON.

These tests fail while ReservationsResponse drops those fields. They pass
once that response keeps the values the converter stored.
"""

import copy
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import convert_reservations
from backend.feeder import storage
from backend.sabre.client import SabreClient

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-18.xml",
)

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-18",
}

# Values taken from the GF2057 Trip Search sample, not from the converter.
WEB_PNR = "WDJPBV"
WEB_AGENT = "WEB"
WEB_POS = {
    "agentDutyCode": "4",
    "agentSine": "WEB",
    "airlineVendorId": "GF",
    "bookingSource": "A0A0",
    "pseudoCityCode": "WGF",
    "homePseudoCityCode": "WGF",
    "isoCountry": "BH",
}

CITY_PNR = "ATAMUS"
CITY_AGENT = "ND1"
CITY_POS = {
    "agentDutyCode": "7",
    "agentSine": "ND1",
    "airlineVendorId": "GF",
    "bookingSource": "A0A0",
    "pseudoCityCode": "BAH",
    "homePseudoCityCode": "BAH",
    "isoCountry": "BH",
}


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


def _match_field(doc, key, expected):
    if "." in key:
        head, rest = key.split(".", 1)
        value = doc.get(head)
        if isinstance(value, list):
            return any(
                isinstance(item, dict) and _match_field(item, rest, expected)
                for item in value
            )
        if isinstance(value, dict):
            return _match_field(value, rest, expected)
        return False
    value = doc.get(key)
    if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
        if "$lte" in expected:
            return value is not None and value <= expected["$lte"]
        raise AssertionError(f"unsupported query operator in {expected}")
    return value == expected


def _matches(doc, query):
    if not query:
        return True
    return all(_match_field(doc, key, expected) for key, expected in query.items())


def _project(doc, projection):
    if not projection:
        return doc
    included = {key for key, flag in projection.items() if flag and key != "_id"}
    projected = {key: doc[key] for key in included if key in doc}
    if projection.get("_id", 1) and "_id" in doc:
        projected["_id"] = doc["_id"]
    return projected


class MemoryCollection:
    """Stand-in for the reservations and snapshots collections.

    insert_one stores the mapping it is given and adds _id when missing,
    matching pymongo.collection.Collection.insert_one. find_one returns that
    stored mapping when the filter matches. It does not drop unknown keys.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        if "_id" not in document:
            document["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(copy.deepcopy(document))
        return _InsertResult(document["_id"])

    def find_one(self, filter=None, *args, **kwargs):
        sort = kwargs.get("sort")
        projection = kwargs.get("projection")
        if args:
            projection = args[0]
        matches = [doc for doc in self.docs if _matches(doc, filter or {})]
        if sort:
            ordered = list(matches)
            for key, direction in reversed(list(sort)):
                ordered.sort(
                    key=lambda doc, field=key: doc.get(field),
                    reverse=direction < 0,
                )
            matches = ordered
        if not matches:
            return None
        return _project(copy.deepcopy(matches[0]), projection)


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


def _reservation(doc, pnr):
    matches = [item for item in doc.get("reservations", []) if item.get("pnr") == pnr]
    assert matches, f"{pnr} missing from reservations"
    return matches[0]


def _assert_selling_office(record, pnr, creation_agent, point_of_sale):
    assert record.get("creationAgent") == creation_agent, (
        f"{pnr} response creationAgent={record.get('creationAgent')!r}; "
        f"keys={sorted(record)}"
    )
    assert record.get("pointOfSale") == point_of_sale, (
        f"{pnr} response pointOfSale={record.get('pointOfSale')!r}"
    )


def _load_converted_reservations():
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        xml_text = handle.read()
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(
        body,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["origin"],
        FLIGHT["departureDate"],
    )


@pytest.fixture
def stored_reservations(monkeypatch):
    converted = _load_converted_reservations()
    for pnr, agent, pos in (
        (WEB_PNR, WEB_AGENT, WEB_POS),
        (CITY_PNR, CITY_AGENT, CITY_POS),
    ):
        stored = _reservation(converted, pnr)
        assert stored["creationAgent"] == agent
        assert stored["pointOfSale"] == pos

    db = MemoryDB()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(reservations_route, "get_db", lambda: db)
    storage.store_reservations(converted)
    storage.store_snapshot(
        request_id="req-selling-office",
        airline=FLIGHT["airline"],
        flight_number=FLIGHT["flightNumber"],
        origin=FLIGHT["origin"],
        departure_date=FLIGHT["departureDate"],
        snapshot_type="reservations",
        data=converted,
    )
    app = FastAPI()
    app.include_router(reservations_route.router)
    client = TestClient(app)
    return client


def _get_reservations(client, snapshot_sequence=None):
    params = {
        "origin": FLIGHT["origin"],
        "date": FLIGHT["departureDate"],
    }
    if snapshot_sequence is not None:
        params["snapshot_sequence"] = snapshot_sequence
    response = client.get(
        f"/flights/{FLIGHT['flightNumber']}/reservations",
        params=params,
    )
    assert response.status_code == 200, response.text[:300]
    return response.json()


def test_latest_reservations_keep_web_and_city_selling_office(stored_reservations):
    body = _get_reservations(stored_reservations)
    web = _reservation(body, WEB_PNR)
    city = _reservation(body, CITY_PNR)
    _assert_selling_office(web, WEB_PNR, WEB_AGENT, WEB_POS)
    _assert_selling_office(city, CITY_PNR, CITY_AGENT, CITY_POS)


def test_snapshot_reservations_keep_selling_office(stored_reservations):
    body = _get_reservations(stored_reservations, snapshot_sequence=1)
    web = _reservation(body, WEB_PNR)
    city = _reservation(body, CITY_PNR)
    _assert_selling_office(web, WEB_PNR, WEB_AGENT, WEB_POS)
    _assert_selling_office(city, CITY_PNR, CITY_AGENT, CITY_POS)


def test_reservation_by_pnr_keeps_selling_office(stored_reservations):
    """The detail route has no response model. It must keep the same fields."""
    response = stored_reservations.get(
        f"/flights/{FLIGHT['flightNumber']}/reservations/{WEB_PNR}",
        params={"date": FLIGHT["departureDate"]},
    )
    assert response.status_code == 200, response.text[:300]
    record = _reservation(response.json(), WEB_PNR)
    _assert_selling_office(record, WEB_PNR, WEB_AGENT, WEB_POS)
