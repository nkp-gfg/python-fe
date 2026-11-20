"""ACS LegApp must survive flight-status ingestion and the status APIs.

Sabre sends LegApp on each ACS_FlightDetailRS LegInfo element. These tests
parse the committed Gulf Air flight-detail responses with the product SOAP
parser, run the feeder conversion, and read the documents back through the
flight-status routes. They fail while conversion drops LegApp and reads the
wrong leg city fields.

Run from the repository root:

    python -m unittest backend.tests.test_leg_app_preserved
"""

import copy
import re
import unittest
from pathlib import Path
from unittest import mock

from backend.api.routes import flights as flights_routes
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient

_ROOT = Path(__file__).resolve().parents[2]
_SAMPLES = sorted(
    (_ROOT / "sample_data" / "sample_xmlfiles").glob("ACS_FlightDetailRS_*.xml")
)
_LEG_BLOCK = re.compile(r"<LegInfo>(.*?)</LegInfo>", re.DOTALL)
_RESPONSE_KEY = "ns3:ACS_FlightDetailRS"


def _tag(block, name):
    match = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return match.group(1) if match else ""


def _expected_legs(xml_text):
    """Leg fields as they appear in the ACS response body, in document order."""
    legs = []
    for block in _LEG_BLOCK.findall(xml_text):
        legs.append(
            {
                "city": _tag(block, "LegCity"),
                "status": _tag(block, "LegStatus"),
                "legApp": _tag(block, "LegApp"),
                "controllingCityInd": _tag(block, "ControllingCityInd"),
            }
        )
    return legs


def _itinerary_id(xml_text):
    return {
        "airline": _tag(xml_text, "Airline"),
        "flightNumber": _tag(xml_text, "Flight"),
        "origin": _tag(xml_text, "Origin"),
        "departureDate": _tag(xml_text, "DepartureDate"),
    }


def _parse_flight_detail_body(xml_text):
    """Same parse and body extraction SabreClient.get_flight_status uses."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, _RESPONSE_KEY)


def _parsed_leg_list(body):
    legs = body.get("LegInfoList", {}).get("LegInfo", [])
    if isinstance(legs, dict):
        return [legs]
    return list(legs)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        reverse = direction < 0
        self._docs = sorted(
            self._docs,
            key=lambda doc: (doc.get(key) is None, doc.get(key)),
            reverse=reverse,
        )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: (doc.get(key) is None, doc.get(key)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query, projection=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        return _Cursor(matches)


class _MemoryDb(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, _Collection())
        return super().__getitem__(name)


def _matches(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and (value is None or value > expected["$lte"]):
                return False
            if "$lte" not in expected:
                return False
        elif value != expected:
            return False
    return True


def _assert_public_legs(test, legs, expected):
    test.assertEqual(len(legs), len(expected))
    for actual, want in zip(legs, expected):
        test.assertEqual(
            actual.get("legApp"),
            want["legApp"],
            f"leg {want['city'] or actual.get('city')!r} dropped LegApp {want['legApp']!r}",
        )
        test.assertEqual(actual.get("city"), want["city"])
        test.assertEqual(actual.get("status"), want["status"])
        if want["controllingCityInd"] == "!":
            test.assertTrue(
                actual.get("controllingCity"),
                f"leg {want['city']} ignored ControllingCityInd '!'",
            )
        else:
            test.assertFalse(actual.get("controllingCity"))
        test.assertNotIn("_raw", actual)


class LegAppPreservedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _SAMPLES:
            raise AssertionError("no ACS_FlightDetailRS samples found")

    def test_samples_cover_both_leg_app_values(self):
        """The committed responses are the wire shape this bug drops."""
        seen = set()
        for path in _SAMPLES:
            for leg in _expected_legs(path.read_text(encoding="utf-8")):
                self.assertIn(leg["legApp"], {"Y", "N"})
                self.assertTrue(leg["city"])
                seen.add(leg["legApp"])
        self.assertEqual(seen, {"Y", "N"})

    def test_conversion_and_status_routes_keep_leg_app(self):
        for path in _SAMPLES:
            with self.subTest(sample=path.name):
                xml_text = path.read_text(encoding="utf-8")
                expected = _expected_legs(xml_text)
                ident = _itinerary_id(xml_text)
                self.assertGreaterEqual(len(expected), 2, path.name)
                self.assertTrue(ident["flightNumber"], path.name)

                body = _parse_flight_detail_body(xml_text)
                parsed_legs = _parsed_leg_list(body)
                self.assertEqual(
                    [leg.get("LegCity", "") for leg in parsed_legs],
                    [leg["city"] for leg in expected],
                    f"{path.name}: parser lost LegCity before conversion",
                )
                self.assertEqual(
                    [leg.get("LegApp", "") for leg in parsed_legs],
                    [leg["legApp"] for leg in expected],
                    f"{path.name}: parser lost LegApp before conversion",
                )

                converted = convert_flight_status(
                    body,
                    ident["airline"],
                    ident["flightNumber"],
                    ident["origin"],
                    ident["departureDate"],
                )

                db = _MemoryDb()
                db["flight_status"].docs.append(copy.deepcopy(converted))
                db["snapshots"].docs.append(
                    {
                        "flightNumber": ident["flightNumber"],
                        "origin": ident["origin"],
                        "departureDate": ident["departureDate"],
                        "snapshotType": "flight_status",
                        "sequenceNumber": 1,
                        "capturedAt": "2026-03-19T00:00:00Z",
                        "data": copy.deepcopy(converted),
                    }
                )

                flights_routes._dashboard_cache.clear()
                with mock.patch("backend.api.routes.flights.get_db", return_value=db):
                    current = flights_routes.get_flight_status(
                        ident["flightNumber"],
                        origin=ident["origin"],
                        date=ident["departureDate"],
                        snapshot_sequence=None,
                    )
                    history = flights_routes.get_flight_status_history(
                        ident["flightNumber"],
                        origin=ident["origin"],
                        date=ident["departureDate"],
                        limit=20,
                    )
                    historical = flights_routes.get_flight_status(
                        ident["flightNumber"],
                        origin=ident["origin"],
                        date=ident["departureDate"],
                        snapshot_sequence=1,
                    )
                    dashboard = flights_routes.get_flight_dashboard(
                        ident["flightNumber"],
                        origin=ident["origin"],
                        date=ident["departureDate"],
                        snapshot_sequence=None,
                    )

                # Dashboard historical loads call get_snapshot_data_as_of with
                # positional arguments, and that helper is keyword-only. The
                # worker swallows the TypeError and the route answers 404, so
                # that path cannot return legs until that call is corrected.
                for label, payload in (
                    ("status", current),
                    ("status history", history[0]),
                    ("status as-of snapshot", historical),
                    ("dashboard flightStatus", dashboard["flightStatus"]),
                    ("converted flight status", converted),
                ):
                    _assert_public_legs(self, payload["legs"], expected)
                    if label != "converted flight status":
                        self.assertNotIn("_raw", payload, label)


if __name__ == "__main__":
    unittest.main()
