"""Check-in hour buckets must be labeled as the dashboard hour key.

Sabre CheckInTime values such as 22:29 are stored on the passenger list and
grouped by the dashboard into hour keys such as 22:00. The readiness chart
must use that key on the axis and in the tooltip. These tests fail while the
chart appends another :00 and prints 22:00:00.

FlightInsightsPanel (frontend/components/dashboard/flight-insights.tsx) prints
the hour key unchanged and is not mounted by the flight workbench, so it does
not share this bug and is not covered here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.api.routes.flights import _build_insights  # noqa: E402
from backend.feeder.converter import convert_passenger_list  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_LHR = (
    REPO
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2006_LHR_2026-03-18.xml"
)
RENDERER = REPO / "frontend" / "tests" / "render_checkin_axis.mjs"
ESBUILD_PREFIX = Path("/tmp/python-fe-esbuild")
ESBUILD_MODULE = ESBUILD_PREFIX / "node_modules" / "esbuild" / "lib" / "main.js"


def _passenger_list_from_soap(xml_text: str, flight_number: str, origin: str, departure_date: str):
    """Run the product SOAP parse and passenger-list converter.

    SabreClient.__init__ reads credential environment variables and is not
    required: _parse_xml and _extract_body do not use them. get_passenger_list
    returns the same body _extract_body pulls out of the SOAP envelope.
    """
    client = object.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(client, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", flight_number, departure_date, origin)


def _insights_for_document(document: dict):
    return _build_insights(document["passengers"], None, None, document, {})


def _ensure_esbuild() -> str:
    if os.environ.get("ESBUILD_MODULE") and Path(os.environ["ESBUILD_MODULE"]).is_file():
        return os.environ["ESBUILD_MODULE"]
    if not ESBUILD_MODULE.is_file():
        subprocess.run(
            ["npm", "install", "--prefix", str(ESBUILD_PREFIX), "esbuild@0.25.12"],
            check=True,
            capture_output=True,
            text=True,
        )
    return str(ESBUILD_MODULE)


def _render_checkin_chart(insights: dict, total_passengers: int) -> dict:
    esbuild_module = _ensure_esbuild()
    completed = subprocess.run(
        ["node", str(RENDERER)],
        input=json.dumps({"insights": insights, "totalPassengers": total_passengers}),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "ESBUILD_MODULE": esbuild_module},
    )
    if completed.returncode != 0:
        raise AssertionError(
            "check-in chart renderer failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def _assert_axis_matches_hour_keys(test: unittest.TestCase, insights: dict, total_passengers: int):
    distribution = insights["checkInTimeline"]["hourDistribution"]
    expected_labels = list(distribution)
    expected_counts = [distribution[label] for label in expected_labels]
    rendered = _render_checkin_chart(insights, total_passengers)

    test.assertEqual(rendered["categories"], expected_labels)
    test.assertEqual(rendered["series"], expected_counts)
    test.assertEqual(rendered["svgCategoryLabels"], expected_labels)
    test.assertEqual(rendered["tooltipNames"], expected_labels)
    for label, count, tooltip in zip(expected_labels, expected_counts, rendered["tooltips"]):
        test.assertIn(label, tooltip)
        test.assertNotIn(f"{label}:00", tooltip)
        test.assertIn(str(count), tooltip)
        test.assertNotRegex(label, r"^\d{2}:\d{2}:\d{2}$")


class CheckInHourAxisTests(unittest.TestCase):
    def test_committed_lhr_list_labels_hour_buckets(self):
        xml_text = SAMPLE_LHR.read_text()
        self.assertIn("<CheckInTime>22:29</CheckInTime>", xml_text)
        document = _passenger_list_from_soap(xml_text, "2006", "LHR", "2026-03-18")
        times = [p.get("checkInTime") for p in document["passengers"] if p.get("checkInTime")]
        self.assertIn("22:29", times)
        self.assertIn("19:20", times)
        self.assertIn("06:18", times)

        insights = _insights_for_document(document)
        timeline = insights["checkInTimeline"]
        self.assertEqual(
            timeline["hourDistribution"],
            {"06:00": 1, "19:00": 1, "22:00": 1},
        )
        self.assertEqual(timeline["totalWithTime"], 3)
        _assert_axis_matches_hour_keys(self, insights, document["totalPassengers"])

    def test_unpadded_midnight_and_shared_hour_keep_bucket_label(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details><LastName>EARLY</LastName><FirstName>ANN</FirstName></Name_Details>
          <CheckIn_Info><CheckInTime>00:05</CheckInTime><CheckInStatus>true</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>NINE</LastName><FirstName>BEN</FirstName></Name_Details>
          <CheckIn_Info><CheckInTime>9:01</CheckInTime><CheckInStatus>true</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>PEAK</LastName><FirstName>CIA</FirstName></Name_Details>
          <CheckIn_Info><CheckInTime>22:10</CheckInTime><CheckInStatus>true</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>PEAK</LastName><FirstName>DAN</FirstName></Name_Details>
          <CheckIn_Info><CheckInTime>22:59</CheckInTime><CheckInStatus>true</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>BLANK</LastName><FirstName>EVE</FirstName></Name_Details>
          <CheckIn_Info><CheckInTime></CheckInTime><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        document = _passenger_list_from_soap(xml_text, "2754", "DMM", "2026-03-18")
        stored_times = [p["checkInTime"] for p in document["passengers"]]
        self.assertEqual(stored_times, ["00:05", "9:01", "22:10", "22:59", None])

        insights = _insights_for_document(document)
        self.assertEqual(
            insights["checkInTimeline"]["hourDistribution"],
            {"00:00": 1, "09:00": 1, "22:00": 2},
        )
        _assert_axis_matches_hour_keys(self, insights, document["totalPassengers"])


if __name__ == "__main__":
    unittest.main()
