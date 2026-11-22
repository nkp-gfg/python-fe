"""Codeshare fields on a Sabre bag route must survive ingestion and the APIs.

These tests fail while convert_passenger_list (and the passenger-data bag
route) drop OperatingAirline, OperatingFlight, MarketingAirline, and
MarketingFlight. They pass once those four values are copied onto every
stored bag route the passenger-list and passenger-detail APIs return.

Run:
    PYTHONPATH=. python3 -m unittest backend.tests.test_bag_route_codeshare -v
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import passengers as passengers_api
from backend.feeder.converter import _strip_ns, convert_passenger_data, convert_passenger_list
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient

FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-19"
AIRLINE = "GF"

# Vendor design sample: marketing flight differs from Airline/Flight.
# Second segment: operating carrier differs (BaggageRoute.ACS fields).
DESIGN_ROUTE = {
    "airline": "K0",
    "flight": "121",
    "origin": "AAA",
    "destination": "BBB",
    "segmentStatus": "HK",
    "operatingAirline": "K0",
    "operatingFlight": "121",
    "marketingAirline": "U0",
    "marketingFlight": "123",
}
OPERATING_ROUTE = {
    "airline": "GF",
    "flight": "2057",
    "origin": "BOM",
    "destination": "DMM",
    "segmentStatus": "HK",
    "operatingAirline": "BA",
    "operatingFlight": "999",
    "marketingAirline": "GF",
    "marketingFlight": "2057",
}
MERGED_ROUTE = {
    "airline": "GF",
    "flight": "2057",
    "origin": "BOM",
    "destination": "BAH",
    "segmentStatus": "HK",
    "operatingAirline": "BA",
    "operatingFlight": "888",
    "marketingAirline": "XX",
    "marketingFlight": "777",
}

_ROUTE_FIELDS = (
    "airline",
    "flight",
    "origin",
    "destination",
    "segmentStatus",
    "operatingAirline",
    "operatingFlight",
    "marketingAirline",
    "marketingFlight",
)


def _soap(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _bag_route_xml(route, segment_id):
    return (
        "<ns2:BaggageRoute>"
        f"<ns2:SegmentID>{segment_id}</ns2:SegmentID>"
        f"<ns2:Airline>{route['airline']}</ns2:Airline>"
        f"<ns2:Flight>{route['flight']}</ns2:Flight>"
        f"<ns2:OperatingAirline>{route['operatingAirline']}</ns2:OperatingAirline>"
        f"<ns2:OperatingFlight>{route['operatingFlight']}</ns2:OperatingFlight>"
        f"<ns2:MarketingAirline>{route['marketingAirline']}</ns2:MarketingAirline>"
        f"<ns2:MarketingFlight>{route['marketingFlight']}</ns2:MarketingFlight>"
        f"<ns2:Origin>{route['origin']}</ns2:Origin>"
        f"<ns2:Destination>{route['destination']}</ns2:Destination>"
        f"<ns2:SegmentStatus>{route['segmentStatus']}</ns2:SegmentStatus>"
        "</ns2:BaggageRoute>"
    )


def _passenger_list_xml(passengers):
    pax_xml = []
    for pax in passengers:
        routes = "".join(
            _bag_route_xml(route, index)
            for index, route in enumerate(pax["routes"], start=1)
        )
        pax_xml.append(
            "<PassengerInfo>"
            "<Name_Details>"
            f"<LastName>{pax['lastName']}</LastName>"
            f"<FirstName>{pax['firstName']}</FirstName>"
            "</Name_Details>"
            f'<PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>'
            f"<PassengerID>{pax['passengerId']}</PassengerID>"
            "<BaggageRouteList>"
            f"{routes}"
            "</BaggageRouteList>"
            "</PassengerInfo>"
        )
    body = (
        '<GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3">'
        "<ItineraryInfo><Itinerary>"
        f"<Airline>{AIRLINE}</Airline><Flight>{FLIGHT}</Flight>"
        f"<DepartureDate>{DATE}</DepartureDate><Origin>{ORIGIN}</Origin>"
        "<Destination>DMM</Destination>"
        "</Itinerary></ItineraryInfo>"
        "<PassengerInfoList>"
        f"{''.join(pax_xml)}"
        "</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _soap(body)


def _passenger_data_xml(route):
    body = (
        '<GetPassengerDataRS xmlns="http://services.sabre.com/checkin/getPassengerData/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3">'
        "<PassengerDataResponseList><PassengerDataResponse>"
        "<LastName>NIKE</LastName><FirstName>ALEX</FirstName>"
        "<PNRLocator nameAssociationID=\"1\">SDKBCH</PNRLocator>"
        "<PassengerID>C4FB6A8B0672</PassengerID>"
        "<BaggageRouteList>"
        f"{_bag_route_xml(route, 1)}"
        "</BaggageRouteList>"
        "</PassengerDataResponse></PassengerDataResponseList>"
        "</GetPassengerDataRS>"
    )
    return _soap(body)


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, response_key)


def _merge_like_runner(base_raw, subsequent_raw):
    """Same passenger-list merge the feeder uses for later display-code calls."""
    subsequent_pax = _extract_passenger_info_list(subsequent_raw)
    if not subsequent_pax:
        return base_raw
    base_pax_list = _extract_passenger_info_list(base_raw)
    existing_keys = {_pax_dedup_key(p) for p in base_pax_list}
    for pax in subsequent_pax:
        key = _pax_dedup_key(pax)
        if key not in existing_keys:
            base_pax_list.append(pax)
            existing_keys.add(key)
    _set_passenger_info_list(base_raw, base_pax_list)
    return base_raw


def _ingested_passenger_list():
    """Parse two Sabre responses, merge them, and convert as ingestion does."""
    first = _parse_body(
        _passenger_list_xml([
            {
                "lastName": "NIKE",
                "firstName": "ALEX",
                "pnr": "SDKBCH",
                "passengerId": "C4FB6A8B0672",
                "routes": [DESIGN_ROUTE, OPERATING_ROUTE],
            }
        ]),
        "GetPassengerListRS",
    )
    second = _parse_body(
        _passenger_list_xml([
            {
                "lastName": "KHAN",
                "firstName": "SARA",
                "pnr": "OTHER1",
                "passengerId": "P2",
                "routes": [MERGED_ROUTE],
            }
        ]),
        "GetPassengerListRS",
    )
    merged = _merge_like_runner(first, second)
    stripped = _strip_ns(merged)
    parsed_routes = _routes_from_stripped(stripped)
    return convert_passenger_list(merged, AIRLINE, FLIGHT, DATE, ORIGIN), parsed_routes


def _routes_from_stripped(data):
    passengers = data["PassengerInfoList"]["PassengerInfo"]
    if isinstance(passengers, dict):
        passengers = [passengers]
    found = {}
    for pax in passengers:
        pnr = pax["PNRLocator"]
        if isinstance(pnr, dict):
            pnr = pnr.get("#text", "")
        routes = pax["BaggageRouteList"]["BaggageRoute"]
        if isinstance(routes, dict):
            routes = [routes]
        found[pnr] = routes
    return found


def _assert_codeshare(route, expected):
    actual = {field: route.get(field) for field in _ROUTE_FIELDS}
    wanted = {field: expected[field] for field in _ROUTE_FIELDS}
    if actual != wanted:
        raise AssertionError(
            "bag route dropped codeshare carriers:\n"
            f"  expected {wanted}\n"
            f"  stored   {actual}"
        )
    if (
        expected["marketingAirline"] != expected["airline"]
        or expected["marketingFlight"] != expected["flight"]
    ):
        if actual["marketingAirline"] == actual["airline"] and actual["marketingFlight"] == actual["flight"]:
            raise AssertionError(
                "marketing flight collapsed into the stored airline and flight"
            )
    if (
        expected["operatingAirline"] != expected["airline"]
        or expected["operatingFlight"] != expected["flight"]
    ):
        if actual["operatingAirline"] == actual["airline"] and actual["operatingFlight"] == actual["flight"]:
            raise AssertionError(
                "operating flight collapsed into the stored airline and flight"
            )


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: doc.get(field) if doc.get(field) is not None else "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _DB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection([]))


def _matches(doc, query):
    for key, expected in query.items():
        if key == "sequenceNumber" and isinstance(expected, dict) and "$lte" in expected:
            if doc.get("sequenceNumber", 0) > expected["$lte"]:
                return False
            continue
        if "." in key:
            head, tail = key.split(".", 1)
            value = doc.get(head)
            if isinstance(value, list):
                if not any(isinstance(item, dict) and _field_eq(item, tail, expected) for item in value):
                    return False
            elif not (isinstance(value, dict) and _field_eq(value, tail, expected)):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _field_eq(doc, key, expected):
    if "." in key:
        head, tail = key.split(".", 1)
        value = doc.get(head)
        return isinstance(value, dict) and _field_eq(value, tail, expected)
    return doc.get(key) == expected


class BagRouteCodeshareTests(unittest.TestCase):
    def setUp(self):
        self.stored, self.parsed_routes = _ingested_passenger_list()
        self.db = _DB({
            "passenger_list": _Collection([self.stored]),
            "snapshots": _Collection([{
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DATE,
                "snapshotType": "passenger_list",
                "sequenceNumber": 1,
                "data": copy.deepcopy(self.stored),
            }]),
            "reservations": _Collection([]),
        })
        self._original_get_db = passengers_api.get_db
        passengers_api.get_db = lambda: self.db

    def tearDown(self):
        passengers_api.get_db = self._original_get_db

    def test_parser_delivered_codeshare_fields(self):
        """Sabre's parsed bag route still has the four elements before conversion."""
        nike = self.parsed_routes["SDKBCH"]
        self.assertEqual(nike[0]["MarketingAirline"], "U0")
        self.assertEqual(nike[0]["MarketingFlight"], "123")
        self.assertEqual(nike[0]["OperatingAirline"], "K0")
        self.assertEqual(nike[0]["OperatingFlight"], "121")
        self.assertEqual(nike[1]["OperatingAirline"], "BA")
        self.assertEqual(nike[1]["OperatingFlight"], "999")
        merged = self.parsed_routes["OTHER1"]
        self.assertEqual(merged[0]["MarketingAirline"], "XX")
        self.assertEqual(merged[0]["MarketingFlight"], "777")
        self.assertEqual(merged[0]["OperatingAirline"], "BA")
        self.assertEqual(merged[0]["OperatingFlight"], "888")

    def test_passenger_list_api_returns_codeshare_bag_route(self):
        response = passengers_api.get_passengers(
            FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None,
        )
        self.assertNotIn("_raw", response)
        by_pnr = {pax["pnr"]: pax for pax in response["passengers"]}
        nike_routes = by_pnr["SDKBCH"]["baggageRoutes"]
        self.assertEqual(len(nike_routes), 2)
        _assert_codeshare(nike_routes[0], DESIGN_ROUTE)
        _assert_codeshare(nike_routes[1], OPERATING_ROUTE)
        merged_routes = by_pnr["OTHER1"]["baggageRoutes"]
        self.assertEqual(len(merged_routes), 1)
        _assert_codeshare(merged_routes[0], MERGED_ROUTE)

    def test_passenger_list_snapshot_returns_codeshare_bag_route(self):
        response = passengers_api.get_passengers(
            FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=1,
        )
        self.assertNotIn("_raw", response)
        by_pnr = {pax["pnr"]: pax for pax in response["passengers"]}
        _assert_codeshare(by_pnr["SDKBCH"]["baggageRoutes"][0], DESIGN_ROUTE)
        _assert_codeshare(by_pnr["SDKBCH"]["baggageRoutes"][1], OPERATING_ROUTE)
        _assert_codeshare(by_pnr["OTHER1"]["baggageRoutes"][0], MERGED_ROUTE)

    def test_passenger_by_pnr_returns_codeshare_bag_route(self):
        response = passengers_api.get_passenger_by_pnr(FLIGHT, "SDKBCH", date=DATE)
        routes = response["passengers"][0]["baggageRoutes"]
        _assert_codeshare(routes[0], DESIGN_ROUTE)
        _assert_codeshare(routes[1], OPERATING_ROUTE)

    def test_passenger_detail_api_returns_codeshare_bag_route(self):
        xml_text = _passenger_data_xml(DESIGN_ROUTE)

        class _DetailClient:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_passenger_data(self, **kwargs):
                parsed = SabreClient._parse_xml(xml_text)
                body = SabreClient._extract_body(
                    SabreClient.__new__(SabreClient), parsed, "GetPassengerDataRS",
                )
                return body, xml_text, {}

        original = passengers_api.SabreClient
        passengers_api.SabreClient = _DetailClient
        try:
            response = passengers_api.get_passenger_detail(
                FLIGHT, "SDKBCH", origin=ORIGIN, date=DATE, airline=AIRLINE,
            )
        finally:
            passengers_api.SabreClient = original

        routes = response["passengers"][0]["baggageRoutes"]
        self.assertEqual(len(routes), 1)
        _assert_codeshare(routes[0], DESIGN_ROUTE)
        # The same response's itinerary copy is a different structure; the bag
        # route itself must carry the marketing flight, not only the itinerary.
        self.assertEqual(routes[0]["marketingFlight"], "123")
        self.assertNotEqual(routes[0]["marketingFlight"], routes[0]["flight"])


if __name__ == "__main__":
    unittest.main()
