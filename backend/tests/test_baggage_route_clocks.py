"""The passenger-detail baggage card must show connection clocks.

GetPassengerData returns each bag route with a departure date, a departure
clock, an arrival date, and an arrival clock. convert_passenger_data keeps
all four, and get_passenger_detail returns that document with no credentials.
The baggage table on the live detail sheet is the only card that paints those
routes. These tests fail while that card shows the departure date alone.

Not covered: convert_passenger_list drops the same four fields before any
card can read them. That is a different loss, on the passenger-list document,
and no list view renders those routes.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.api.routes.passengers import get_passenger_detail  # noqa: E402
from backend.feeder.converter import convert_passenger_data  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE = (
    REPO
    / "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/GetPassengerDataRQ_v4-0-1_SamplePayload_4-2-4-1.xml"
)
RENDERER = REPO / "frontend/tests/render-baggage-card.mts"

# The published sample's last bag route arrives the same calendar day it
# leaves, so a date-only cell hides that arrival date. Move only that
# arrival onto the next day. The clocks in the sample are unchanged.
OVERNIGHT_ARRIVAL = "2016-07-07"


def _response_envelope():
    text = SAMPLE.read_text()
    start = text.find("<soap-env:Envelope")
    end = text.find("</soap-env:Envelope>", start)
    if start < 0 or end < 0:
        raise AssertionError("sample response envelope was not found")
    xml = text[start:end + len("</soap-env:Envelope>")]
    marker = "<ns2:Flight>942</ns2:Flight>"
    at = xml.find(marker)
    if at < 0:
        raise AssertionError("sample bag route 942 was not found")
    head, tail = xml[:at], xml[at:]
    old = "<ns2:ArrivalDate>2016-07-06</ns2:ArrivalDate>"
    if old not in tail:
        raise AssertionError("sample arrival date for route 942 was not found")
    tail = tail.replace(old, f"<ns2:ArrivalDate>{OVERNIGHT_ARRIVAL}</ns2:ArrivalDate>", 1)
    return head + tail


class _PassengerList:
    def find_one(self, query, sort=None):
        return {
            "flightNumber": "129",
            "origin": "ANF",
            "departureDate": "2016-07-04",
            "passengers": [
                {"pnr": "DXXRYZ", "lastName": "HDGLAX", "firstName": "VRDJ"},
            ],
        }


class _Db(dict):
    def __getitem__(self, name):
        if name == "passenger_list":
            return _PassengerList()
        raise KeyError(name)


class _Sabre:
    """Return the sample body parsed by the real SOAP helpers. No network."""

    def __init__(self):
        self.xml = _response_envelope()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, **kwargs):
        parsed = SabreClient._parse_xml(self.xml)
        data = SabreClient._extract_body(self, parsed, "GetPassengerDataRS")
        return data, "", {}


def _row_for(rows, route):
    flight = f"{route['airline']}{route['flight']}"
    matches = [row for row in rows if flight in row and route["origin"] in row]
    if len(matches) != 1:
        raise AssertionError(f"expected one row for {flight}, saw {rows}")
    return matches[0]


class BaggageRouteClockTests(unittest.TestCase):
    def test_detail_card_shows_stored_connection_clocks(self):
        # The published sample, before the one-field arrival edit, keeps the
        # clocks the schema defines. This is the parser the detail route uses.
        raw = SAMPLE.read_text()
        start = raw.find("<soap-env:Envelope")
        end = raw.find("</soap-env:Envelope>", start)
        published = raw[start:end + len("</soap-env:Envelope>")]
        published_body = SabreClient._extract_body(
            None, SabreClient._parse_xml(published), "GetPassengerDataRS")
        published_doc = convert_passenger_data(
            published_body, "LA", "129", "2016-07-04", "ANF")
        published_routes = {
            (r["airline"], r["flight"]): r
            for r in published_doc["passengers"][0]["baggageRoutes"]
        }
        self.assertEqual(published_routes[("LA", "129")]["departureTime"], "07:45AM")
        self.assertEqual(published_routes[("LA", "129")]["arrivalTime"], "09:40AM")
        self.assertEqual(published_routes[("LA", "129")]["arrivalDate"], "2016-07-04")
        self.assertEqual(published_routes[("LA", "632")]["departureTime"], "03:10PM")
        self.assertEqual(published_routes[("LA", "632")]["departureDate"], "2016-07-05")

        # No credential is supplied. The detail route looks up the stored
        # surname, parses the Sabre body, and returns the converter document.
        import backend.api.routes.passengers as passengers

        passengers.SabreClient = _Sabre
        passengers.get_db = lambda: _Db()
        detail = get_passenger_detail(
            "129", "DXXRYZ", origin="ANF", date="2016-07-04", airline="LA")

        routes = detail["passengers"][0]["baggageRoutes"]
        by_flight = {(r["airline"], r["flight"]): r for r in routes}
        self.assertEqual(by_flight[("LA", "129")]["departureTime"], "07:45AM")
        self.assertEqual(by_flight[("LA", "129")]["arrivalTime"], "09:40AM")
        self.assertEqual(by_flight[("LA", "530")]["departureTime"], "12:20AM")
        self.assertEqual(by_flight[("LA", "530")]["arrivalTime"], "08:55AM")
        self.assertEqual(by_flight[("AA", "942")]["departureDate"], "2016-07-06")
        self.assertEqual(by_flight[("AA", "942")]["departureTime"], "11:10AM")
        self.assertEqual(by_flight[("AA", "942")]["arrivalDate"], OVERNIGHT_ARRIVAL)
        self.assertEqual(by_flight[("AA", "942")]["arrivalTime"], "02:10PM")
        for route in routes:
            self.assertTrue(route["departureTime"])
            self.assertTrue(route["arrivalTime"])
            self.assertTrue(route["arrivalDate"])

        proc = subprocess.run(
            ["npx", "tsx", "tests/render-baggage-card.mts"],
            input=json.dumps(detail),
            text=True,
            cwd=REPO / "frontend",
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"renderer failed\nstdout={proc.stdout}\nstderr={proc.stderr}",
        )
        rows = json.loads(proc.stdout)["rows"]
        self.assertEqual(len(rows), len(routes))

        for route in routes:
            row = _row_for(rows, route)
            self.assertIn(
                route["departureTime"],
                row,
                f"row {row} hid departure clock {route['departureTime']}",
            )
            self.assertIn(
                route["arrivalTime"],
                row,
                f"row {row} hid arrival clock {route['arrivalTime']}",
            )
            if route["arrivalDate"] == route["departureDate"]:
                self.assertGreaterEqual(
                    row.count(route["arrivalDate"]),
                    2,
                    f"row {row} shows the calendar date once and hides the arrival date",
                )
            else:
                self.assertIn(
                    route["arrivalDate"],
                    row,
                    f"row {row} hid arrival date {route['arrivalDate']}",
                )


if __name__ == "__main__":
    unittest.main()
