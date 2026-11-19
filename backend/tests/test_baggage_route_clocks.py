"""Checked-bag routes on the stored passenger manifest must keep their clocks.

GetPassengerList baggage routes include a departure date and time, an arrival
date and time, a booking class, and a segment id. convert_passenger_list is
what the feeder stores, and the passenger-list API returns that document.
These tests fail while those fields are dropped.

The live GetPassengerData converter already keeps the same fields. That case
is pinned here so the manifest uses those names.
"""

import copy
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes.passengers import get_passengers
from backend.feeder.converter import (
    _ensure_list,
    _strip_ns,
    convert_passenger_data,
    convert_passenger_list,
)
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.feeder import storage
from backend.sabre.client import SabreClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GF2057_LIST = os.path.join(
    ROOT,
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2057_BOM_2026-03-19.xml",
)

# Same names convert_passenger_data already stores for a live lookup.
CLOCK_FIELDS = (
    "segmentId",
    "departureDate",
    "departureTime",
    "arrivalDate",
    "arrivalTime",
    "bookingClass",
)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _MemoryCollection:
    """Insert/find stand-in. store_passenger_list and store_snapshot persist
    the converted document unchanged; this collection does the same.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = f"mem-{len(self.docs) + 1}"
        self.docs.append(copy.deepcopy(doc))
        return _InsertResult(doc["_id"])

    def find_one(self, query=None, sort=None, projection=None):
        matches = [doc for doc in self.docs if _query_matches(doc, query or {})]
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=key: (
                        doc.get(field) is None,
                        "" if doc.get(field) is None else doc.get(field),
                    ),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


def _query_matches(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and not (value is not None and value <= expected["$lte"]):
                return False
            if any(op != "$lte" for op in expected):
                return False
        elif value != expected:
            return False
    return True


def _parse_passenger_list_response(xml_text):
    """Run the same parse and SOAP-body extract get_passenger_list uses."""
    parsed = SabreClient._parse_xml(xml_text)
    # SabreClient() reads live credentials. Extraction does not use them.
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerListRS")


def _merge_passenger_list_calls(first_raw, subsequent_raw):
    """Same merge the feeder runs before convert_passenger_list.

    Mirrors backend/feeder/runner.py: the first successful GetPassengerList
    body is the base, and later calls append passengers whose
    PNR + last name + passenger id are not already present.
    """
    merged = copy.deepcopy(first_raw)
    subsequent_pax = _extract_passenger_info_list(subsequent_raw)
    if not subsequent_pax:
        return merged
    base_pax_list = _extract_passenger_info_list(merged)
    existing_keys = {_pax_dedup_key(pax) for pax in base_pax_list}
    for pax in subsequent_pax:
        key = _pax_dedup_key(pax)
        if key not in existing_keys:
            base_pax_list.append(pax)
            existing_keys.add(key)
    _set_passenger_info_list(merged, base_pax_list)
    return merged


def _source_routes(raw):
    """Read bag-route clocks from a parsed GetPassengerList body."""
    data = _strip_ns(raw)
    passengers = []
    for pax in _ensure_list((data.get("PassengerInfoList") or {}).get("PassengerInfo")):
        if not isinstance(pax, dict):
            continue
        name = pax.get("Name_Details") or {}
        if not isinstance(name, dict):
            name = {}
        routes = []
        for route in _ensure_list((pax.get("BaggageRouteList") or {}).get("BaggageRoute")):
            if not isinstance(route, dict):
                continue
            routes.append({
                "segmentId": route.get("SegmentID", ""),
                "airline": route.get("Airline", ""),
                "flight": route.get("Flight", ""),
                "origin": route.get("Origin", ""),
                "destination": route.get("Destination", ""),
                "departureDate": route.get("DepartureDate", ""),
                "departureTime": route.get("DepartureTime", ""),
                "arrivalDate": route.get("ArrivalDate", ""),
                "arrivalTime": route.get("ArrivalTime", ""),
                "bookingClass": route.get("BookingClass", ""),
                "segmentStatus": route.get("SegmentStatus", ""),
            })
        passengers.append({
            "passengerId": pax.get("PassengerID", ""),
            "lastName": name.get("LastName", ""),
            "firstName": name.get("FirstName", ""),
            "routes": routes,
        })
    return passengers


def _stored_by_id(passengers):
    return {pax.get("passengerId", ""): pax for pax in passengers}


def _assert_routes_keep_clocks(source_passengers, stored_passengers, where):
    stored = _stored_by_id(stored_passengers)
    assert source_passengers, f"{where}: parsed list had no passengers"
    mismatches = []
    timed = 0
    for source in source_passengers:
        saved = stored.get(source["passengerId"])
        assert saved is not None, (
            f"{where}: passenger {source['passengerId']} missing from stored manifest"
        )
        saved_routes = saved.get("baggageRoutes") or []
        assert len(saved_routes) == len(source["routes"]), (
            f"{where}: {source['lastName']} stored {len(saved_routes)} bag routes, "
            f"source had {len(source['routes'])}"
        )
        for source_route, saved_route in zip(source["routes"], saved_routes):
            if source_route["departureTime"] or source_route["arrivalTime"]:
                timed += 1
            for field in CLOCK_FIELDS:
                if saved_route.get(field) != source_route[field]:
                    mismatches.append(
                        f"{where}: {source['lastName']} {source_route['airline']}"
                        f"{source_route['flight']} {source_route['origin']}-"
                        f"{source_route['destination']} {field} stored "
                        f"{saved_route.get(field)!r}, source {source_route[field]!r}"
                    )
            for field in ("airline", "flight", "origin", "destination", "segmentStatus"):
                if saved_route.get(field) != source_route[field]:
                    mismatches.append(
                        f"{where}: {source['lastName']} kept field {field} changed"
                    )
    assert timed > 0, f"{where}: source bag routes had no clocks to preserve"
    mismatches.sort(key=lambda line: ("5023" not in line, "21:40" not in line, line))
    assert not mismatches, (
        f"{len(mismatches)} bag-route fields dropped\n"
        + "\n".join(mismatches[:8])
    )


def _connection_pairs(passengers):
    """Inbound bag into DMM and the onward GF5023 leg on the same passenger."""
    pairs = []
    for pax in passengers:
        routes = pax.get("routes", pax.get("baggageRoutes")) or []
        inbound = next(
            (
                route for route in routes
                if route.get("flight") == "2057"
                and route.get("origin") == "BOM"
                and route.get("destination") == "DMM"
                and route.get("arrivalTime") == "20:10"
            ),
            None,
        )
        onward = next(
            (
                route for route in routes
                if route.get("flight") == "5023"
                and route.get("origin") == "DMM"
                and route.get("destination") == "BAH"
                and route.get("departureTime") == "21:40"
            ),
            None,
        )
        if inbound and onward:
            pairs.append((pax, inbound, onward))
    return pairs


def _layover_minutes(inbound, onward):
    arrived = datetime.strptime(
        f"{inbound['arrivalDate']} {inbound['arrivalTime']}",
        "%Y-%m-%d %H:%M",
    )
    departs = datetime.strptime(
        f"{onward['departureDate']} {onward['departureTime']}",
        "%Y-%m-%d %H:%M",
    )
    return int((departs - arrived).total_seconds() // 60)


def _load_gf2057():
    with open(GF2057_LIST, encoding="utf-8") as handle:
        xml_text = handle.read()
    parsed = _parse_passenger_list_response(xml_text)
    everyone = _extract_passenger_info_list(parsed)
    assert everyone, "GetPassengerList body had no passengers"
    # First display-code call returns one passenger. A later call returns the
    # rest, including the checked-bag connection, and the feeder merges them.
    first_call = copy.deepcopy(parsed)
    _set_passenger_info_list(first_call, [copy.deepcopy(everyone[0])])
    merged = _merge_passenger_list_calls(first_call, parsed)
    return parsed, merged


@pytest.fixture
def gf2057_manifest(monkeypatch):
    parsed, merged = _load_gf2057()
    direct = convert_passenger_list(parsed, "GF", "2057", "2026-03-19", "BOM")
    converted = convert_passenger_list(merged, "GF", "2057", "2026-03-19", "BOM")
    database = _MemoryDB()
    monkeypatch.setattr(storage, "get_db", lambda: database)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: database)
    storage.store_passenger_list(converted)
    storage.store_snapshot(
        request_id="req-gf2057",
        airline="GF",
        flight_number="2057",
        origin="BOM",
        departure_date="2026-03-19",
        snapshot_type="passenger_list",
        data=converted,
    )
    return {
        "parsed": parsed,
        "merged": merged,
        "direct": direct,
        "converted": converted,
        "database": database,
    }


def test_gf2057_list_conversion_keeps_every_checked_bag_clock(gf2057_manifest):
    parsed = gf2057_manifest["parsed"]
    direct = gf2057_manifest["direct"]
    source = _source_routes(parsed)
    connections = _connection_pairs(source)
    assert connections, (
        "committed GF2057 list no longer contains a bag that arrives DMM at "
        "20:10 and leaves on GF5023 at 21:40"
    )
    _assert_routes_keep_clocks(source, direct["passengers"], "convert_passenger_list")
    stored_pairs = _connection_pairs(direct["passengers"])
    assert len(stored_pairs) == len(connections)
    for _pax, inbound, onward in stored_pairs:
        assert _layover_minutes(inbound, onward) == 90


def test_feeder_merge_then_convert_keeps_connection_clocks(gf2057_manifest):
    merged = gf2057_manifest["merged"]
    converted = gf2057_manifest["converted"]
    source = _source_routes(merged)
    connections = _connection_pairs(source)
    assert connections, "feeder merge dropped the GF5023 checked-bag connection"
    assert len(source) == len(_source_routes(gf2057_manifest["parsed"]))
    _assert_routes_keep_clocks(
        source, converted["passengers"], "merged convert_passenger_list"
    )
    stored = _stored_by_id(converted["passengers"])
    for pax, inbound, onward in connections:
        saved = stored[pax["passengerId"]]
        saved_routes = saved["baggageRoutes"]
        saved_inbound = next(
            route for route in saved_routes
            if route.get("origin") == "BOM" and route.get("destination") == "DMM"
        )
        saved_onward = next(
            route for route in saved_routes
            if route.get("flight") == "5023"
        )
        assert saved_inbound.get("arrivalTime") == inbound["arrivalTime"]
        assert saved_inbound.get("arrivalDate") == inbound["arrivalDate"]
        assert saved_onward.get("departureTime") == onward["departureTime"]
        assert saved_onward.get("departureDate") == onward["departureDate"]
        assert saved_onward.get("arrivalDate") == onward["arrivalDate"]
        assert saved_onward.get("arrivalTime") == onward["arrivalTime"]
        assert saved_onward.get("bookingClass") == onward["bookingClass"]
        assert saved_onward.get("segmentId") == onward["segmentId"]
        assert _layover_minutes(saved_inbound, saved_onward) == 90


def test_passenger_list_api_returns_connection_clocks(gf2057_manifest):
    # Omitted snapshot_sequence is None once FastAPI has resolved Query().
    response = get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    assert "_raw" not in response
    source = _source_routes(gf2057_manifest["merged"])
    _assert_routes_keep_clocks(
        source, response["passengers"], "GET /flights/2057/passengers"
    )
    persisted = gf2057_manifest["database"]["passenger_list"].docs[0]
    _assert_routes_keep_clocks(
        source,
        persisted["passengers"],
        "passenger_list collection",
    )
    pairs = _connection_pairs(response["passengers"])
    assert pairs, "API manifest has no GF5023 connection whose clocks can be read"
    for _pax, inbound, onward in pairs:
        assert _layover_minutes(inbound, onward) == 90


def test_historical_passenger_list_returns_connection_clocks(gf2057_manifest):
    response = get_passengers(
        "2057",
        origin="BOM",
        date="2026-03-19",
        snapshot_sequence=1,
    )
    assert response.get("snapshotSequenceNumber") == 1
    assert "_raw" not in response
    source = _source_routes(gf2057_manifest["merged"])
    _assert_routes_keep_clocks(
        source,
        response["passengers"],
        "GET /flights/2057/passengers?snapshot_sequence=1",
    )
    pairs = _connection_pairs(response["passengers"])
    assert pairs
    for _pax, inbound, onward in pairs:
        assert onward["departureTime"] == "21:40"
        assert inbound["arrivalTime"] == "20:10"
        assert _layover_minutes(inbound, onward) == 90


def test_live_passenger_data_keeps_the_manifest_clock_fields():
    """Contract for the field names the stored manifest must copy."""
    raw = {
        "PassengerDataResponseList": {
            "PassengerDataResponse": {
                "LineNumber": "1",
                "LastName": "FATIMA",
                "FirstName": "NAZIA MRS",
                "PassengerID": "F32C40BE0001",
                "PNRLocator": {"#text": "EXAMPLE", "@nameAssociationID": "1"},
                "BaggageRouteList": {
                    "BaggageRoute": [
                        {
                            "SegmentID": "1",
                            "Airline": "GF",
                            "Flight": "2057",
                            "Origin": "BOM",
                            "Destination": "DMM",
                            "DepartureDate": "2026-03-19",
                            "DepartureTime": "17:40",
                            "ArrivalDate": "2026-03-19",
                            "ArrivalTime": "20:10",
                            "BookingClass": "Q",
                            "SegmentStatus": "HK",
                        },
                        {
                            "SegmentID": "2",
                            "Airline": "GF",
                            "Flight": "5023",
                            "Origin": "DMM",
                            "Destination": "BAH",
                            "DepartureDate": "2026-03-19",
                            "DepartureTime": "21:40",
                            "ArrivalDate": "2026-03-20",
                            "ArrivalTime": "1:40",
                            "BookingClass": "Q",
                            "SegmentStatus": "HK",
                        },
                    ]
                },
            }
        }
    }
    doc = convert_passenger_data(raw, "GF", "2057", "2026-03-19", "BOM")
    onward = doc["passengers"][0]["baggageRoutes"][1]
    assert onward["flight"] == "5023"
    assert onward["segmentId"] == "2"
    assert onward["departureDate"] == "2026-03-19"
    assert onward["departureTime"] == "21:40"
    assert onward["arrivalDate"] == "2026-03-20"
    assert onward["arrivalTime"] == "1:40"
    assert onward["bookingClass"] == "Q"
    inbound = doc["passengers"][0]["baggageRoutes"][0]
    assert _layover_minutes(inbound, onward) == 90
