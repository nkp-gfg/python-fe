"""Kiosk check-in counts from ACS Flight Detail must survive on cabin totals.

Sabre sends ``PassengerCounts/Kiosk`` on each cabin. ``convert_flight_status``
builds the cabin object the flight-status collection and the status, history,
dashboard, and flight-list routes return. That object must keep the parsed
kiosk count under ``kiosk`` (the same single-word key ``CabinCount`` already
declares). These tests fail while the count is dropped.

``diff_flight_status`` is not covered. It never reads ``Kiosk``; it only
compares ``booked``, ``onBoard``, and ``boardingPasses`` on an already
converted document. The live ``POST /flights/ingest`` call is not covered
either: it opens a Sabre session. Ingestion stores the document this module
builds with ``convert_flight_status``, which is the function the feeder calls.

The dashboard historical view is not covered. ``_fetch_dashboard_data_parallel``
calls ``get_snapshot_data_as_of`` with positional arguments, and that helper
accepts every argument after ``db`` by keyword only, so the historical
dashboard fetch errors before it can return cabin counts. The live dashboard
and the status route's ``snapshot_sequence`` query are covered.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import flights
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_DIR = _REPO_ROOT / "sample_data" / "sample_xmlfiles"
_TOKEN_RE = re.compile(
    r"(<wsse:BinarySecurityToken\b[^>]*>)(.*?)(</wsse:BinarySecurityToken>)",
    re.DOTALL,
)
_COUNTS_RE = re.compile(r"<PassengerCounts\b.*?</PassengerCounts>", re.DOTALL)

# Distinct per cabin, including an explicit zero, so a constant default cannot pass.
_SAMPLE_KIOSK = {
    "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml": {"Y": 17, "J": 0},
    "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml": {"Y": 0, "J": 6},
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml": {"Y": 11, "J": 2},
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml": {"Y": 4, "J": 9},
}


def _load_flight_detail_xml(name: str) -> str:
    xml = (_SAMPLE_DIR / name).read_text(encoding="utf-8")
    return _TOKEN_RE.sub(r"\1placeholder\3", xml)


def _with_kiosk_values(xml: str, values: dict[str, int]) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        cabin = re.search(r'classOfService="([^"]+)"', block)
        assert cabin is not None
        value = values[cabin.group(1)]
        updated, count = re.subn(
            r"<Kiosk>\d+</Kiosk>",
            f"<Kiosk>{value}</Kiosk>",
            block,
            count=1,
        )
        assert count == 1, f"PassengerCounts for {cabin.group(1)} has no Kiosk element"
        return updated

    return _COUNTS_RE.sub(repl, xml)


def _parse_flight_detail(xml: str) -> dict:
    """Same parse and body extract ``SabreClient.get_flight_status`` uses."""
    parsed = SabreClient._parse_xml(xml)
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "ns3:ACS_FlightDetailRS")


def _parsed_kiosk_counts(body: dict) -> dict[str, int]:
    counts = body.get("PassengerCounts")
    if isinstance(counts, dict):
        counts = [counts]
    found = {}
    for cabin in counts or []:
        found[cabin.get("@classOfService")] = int(cabin["Kiosk"])
    return found


def _flight_identity(body: dict) -> tuple[str, str, str, str]:
    itin = body["ItineraryResponseList"]["ItineraryInfoResponse"]
    airline = itin["Airline"]
    number = str(itin["Flight"])
    origin = itin["Origin"]
    departure = itin.get("ScheduledDepartureDate") or itin.get("DepartureDate")
    return airline, number, origin, departure


def _convert(xml: str) -> tuple[dict, dict[str, int], tuple[str, str, str, str]]:
    body = _parse_flight_detail(xml)
    expected = _parsed_kiosk_counts(body)
    airline, number, origin, departure = _flight_identity(body)
    doc = convert_flight_status(body, airline, number, origin, departure)
    return doc, expected, (airline, number, origin, departure)


def _assert_kiosk(passenger_counts: dict, expected: dict[str, int]) -> None:
    assert set(passenger_counts) == set(expected)
    for cabin, value in expected.items():
        stored = passenger_counts[cabin]
        assert stored["kiosk"] == value
        assert isinstance(stored["kiosk"], int)


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, order in reversed(key):
                self._docs.sort(key=lambda doc, field=field: doc.get(field), reverse=order < 0)
        else:
            self._docs.sort(key=lambda doc: doc.get(key), reverse=(direction or 1) < 0)
        return self

    def limit(self, count: int):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            if "$lte" not in expected and actual != expected:
                return False
        elif actual != expected:
            return False
    return True


def _eval_expr(expr, doc: dict):
    if isinstance(expr, dict) and "$ifNull" in expr:
        path, fallback = expr["$ifNull"]
        assert isinstance(path, str) and path.startswith("$")
        value = doc.get(path[1:])
        return fallback if value is None else value
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    return expr


class _Collection:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        matched = [copy.deepcopy(doc) for doc in self._docs if _matches(doc, query)]
        if sort:
            for field, order in reversed(sort):
                matched.sort(key=lambda doc, field=field: doc.get(field), reverse=order < 0)
        if not matched:
            return None
        doc = matched[0]
        if projection:
            for key, include in projection.items():
                if include == 0:
                    doc.pop(key, None)
        return doc

    def find(self, query, projection=None):
        docs = []
        for doc in self._docs:
            if not _matches(doc, query):
                continue
            copied = copy.deepcopy(doc)
            if projection:
                for key, include in projection.items():
                    if include == 0:
                        copied.pop(key, None)
            docs.append(copied)
        return _Cursor(docs)

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = [copy.deepcopy(doc) for doc in self._docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif "$sort" in stage:
                for field, order in stage["$sort"].items():
                    docs.sort(key=lambda doc, field=field: doc.get(field), reverse=order < 0)
            elif "$group" in stage:
                spec = stage["$group"]
                grouped: dict = {}
                order: list = []
                for doc in docs:
                    identity = {
                        key: _eval_expr(value, doc)
                        for key, value in spec["_id"].items()
                    }
                    marker = tuple(sorted(identity.items()))
                    if marker in grouped:
                        continue
                    row = {"_id": identity}
                    for field, reducer in spec.items():
                        if field == "_id":
                            continue
                        assert reducer == {"$first": "$$ROOT"}
                        row[field] = doc
                    grouped[marker] = row
                    order.append(marker)
                docs = [grouped[marker] for marker in order]
            elif "$project" in stage:
                for doc in docs:
                    for key, include in stage["$project"].items():
                        if include != 0 or not key.startswith("doc."):
                            continue
                        parent, child = key.split(".", 1)
                        if isinstance(doc.get(parent), dict):
                            doc[parent].pop(child, None)
            elif "$addFields" in stage:
                continue
            else:
                raise AssertionError(f"unhandled aggregation stage: {stage}")
        return docs


class _Database:
    def __init__(self, collections: dict[str, list[dict]]):
        self._collections = {
            name: _Collection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))


def _stored_flight(xml: str):
    doc, expected, identity = _convert(xml)
    stored = copy.deepcopy(doc)
    airline, number, origin, departure = identity
    snapshot = {
        "flightNumber": number,
        "origin": origin,
        "departureDate": departure,
        "snapshotType": "flight_status",
        "sequenceNumber": 2,
        "capturedAt": stored["fetchedAt"],
        "data": copy.deepcopy(stored),
    }
    database = _Database({
        "flight_status": [stored],
        "snapshots": [snapshot],
    })
    return database, expected, identity


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    flights._dashboard_cache.clear()
    yield
    flights._dashboard_cache.clear()


@pytest.mark.parametrize("sample_name", sorted(_SAMPLE_KIOSK))
def test_convert_flight_status_keeps_each_cabin_kiosk_count(sample_name: str):
    xml = _with_kiosk_values(_load_flight_detail_xml(sample_name), _SAMPLE_KIOSK[sample_name])
    doc, expected, _identity = _convert(xml)

    assert expected == _SAMPLE_KIOSK[sample_name]
    _assert_kiosk(doc["passengerCounts"], expected)

    # The status route drops ``_raw`` before responding, so the count has to
    # live on the cabin object itself.
    published = flights._strip_id(copy.deepcopy(doc))
    assert "_raw" not in published
    _assert_kiosk(published["passengerCounts"], expected)


def test_single_passenger_counts_block_keeps_kiosk_count():
    xml = _load_flight_detail_xml("ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml")
    xml = re.sub(
        r'\s*<PassengerCounts classOfService="J">.*?</PassengerCounts>',
        "",
        xml,
        count=1,
        flags=re.DOTALL,
    )
    xml = _with_kiosk_values(xml, {"Y": 9})
    body = _parse_flight_detail(xml)
    assert isinstance(body["PassengerCounts"], dict)
    assert body["PassengerCounts"]["Kiosk"] == "9"

    doc, expected, _identity = _convert(xml)
    assert expected == {"Y": 9}
    _assert_kiosk(doc["passengerCounts"], expected)


def test_status_history_dashboard_and_list_return_kiosk_count(monkeypatch):
    xml = _with_kiosk_values(
        _load_flight_detail_xml("ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"),
        _SAMPLE_KIOSK["ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"],
    )
    database, expected, (_airline, number, origin, departure) = _stored_flight(xml)
    monkeypatch.setattr(flights, "get_db", lambda: database)

    with TestClient(app) as client:
        status = client.get(
            f"/flights/{number}/status",
            params={"origin": origin, "date": departure},
        )
        history = client.get(
            f"/flights/{number}/status/history",
            params={"origin": origin, "date": departure},
        )
        dashboard = client.get(
            f"/flights/{number}/dashboard",
            params={"origin": origin, "date": departure},
        )
        as_of = client.get(
            f"/flights/{number}/status",
            params={"origin": origin, "date": departure, "snapshot_sequence": 2},
        )
        listing = client.get("/flights", params={"date": departure})

    assert status.status_code == 200
    assert "_raw" not in status.json()
    _assert_kiosk(status.json()["passengerCounts"], expected)

    assert history.status_code == 200
    assert len(history.json()) == 1
    assert "_raw" not in history.json()[0]
    _assert_kiosk(history.json()[0]["passengerCounts"], expected)

    assert dashboard.status_code == 200
    assert "_raw" not in dashboard.json()["flightStatus"]
    _assert_kiosk(dashboard.json()["flightStatus"]["passengerCounts"], expected)

    assert as_of.status_code == 200
    assert as_of.json()["snapshotSequenceNumber"] == 2
    assert "_raw" not in as_of.json()
    _assert_kiosk(as_of.json()["passengerCounts"], expected)

    assert listing.status_code == 200
    rows = [
        row for row in listing.json()
        if row["flightNumber"] == number and row["origin"] == origin
    ]
    assert len(rows) == 1
    _assert_kiosk(rows[0]["passengerCounts"], expected)


def test_status_and_dashboard_routes_do_not_require_a_credential():
    """The omitted count is readable without a credential because these routes declare none."""
    openapi = app.openapi()
    for path in (
        "/flights/{flight_number}/status",
        "/flights/{flight_number}/status/history",
        "/flights/{flight_number}/dashboard",
        "/flights",
    ):
        for operation in openapi["paths"][path].values():
            assert "security" not in operation
    assert not app.router.dependencies
