"""Class I sold in cabin J must count as business on the revenue surfaces.

Sabre sends booking class I with cabin J. The dashboard passenger analysis
already counts that cabin as business. The revenue tab, the commercial
insights cabin bars, and the decision-briefing business total split the
same passengers by a letter set that omits I, so those passengers are
shown as economy or dropped from the business total.

These tests fail while that split is wrong. They follow the ingested
passenger document through the dashboard payload and the rendered tabs.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_MANIFEST = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2006_LHR_2026-03-18.xml"
)
RENDERER = REPO_ROOT / "frontend" / "tests" / "render_revenue_cabin.mjs"

# A short passenger list in the same SOAP shape as a GetPassengerListRS.
# Class I is cabin J, which is how the committed GF2006 manifest records it.
CLASS_I_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>LHR</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details><LastName>ONE</LastName><FirstName>A</FirstName></Name_Details>
          <PNRLocator>AAAAAA</PNRLocator>
          <PassengerID>P1</PassengerID>
          <BookingClass>I</BookingClass>
          <Cabin>J</Cabin>
          <PriorityCode>UPG</PriorityCode>
          <PassengerType>F</PassengerType>
          <Seat>1A</Seat>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>TWO</LastName><FirstName>B</FirstName></Name_Details>
          <PNRLocator>BBBBBB</PNRLocator>
          <PassengerID>P2</PassengerID>
          <BookingClass>I</BookingClass>
          <Cabin>J</Cabin>
          <PassengerType>F</PassengerType>
          <Seat>1K</Seat>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>THREE</LastName><FirstName>C</FirstName></Name_Details>
          <PNRLocator>CCCCCC</PNRLocator>
          <PassengerID>P3</PassengerID>
          <BookingClass>J</BookingClass>
          <Cabin>J</Cabin>
          <PassengerType>F</PassengerType>
          <Seat>2A</Seat>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>FOUR</LastName><FirstName>D</FirstName></Name_Details>
          <PNRLocator>DDDDDD</PNRLocator>
          <PassengerID>P4</PassengerID>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>F</PassengerType>
          <Seat>12A</Seat>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_list_from_soap(xml_text: str):
    """Run the same parse and convert steps the feeder uses on a Sabre response."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(
        SabreClient.__new__(SabreClient), parsed, "GetPassengerListRS"
    )
    return convert_passenger_list(body, "GF", "2006", "2026-03-18", "LHR")


def _dashboard_for(passenger_list: dict) -> dict:
    return _build_dashboard_payload(None, passenger_list, "LHR", "2026-03-18", {})


def _render(dashboard: dict) -> dict:
    encoded = json.dumps(dashboard)
    if "BinarySecurityToken" in encoded or "ICESMS" in encoded:
        raise AssertionError("dashboard payload unexpectedly contains a Sabre session token")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write(encoded)
        payload_path = handle.name
    completed = subprocess.run(
        ["node", str(RENDERER), payload_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "revenue surface render failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[start_at:end_at]


def _cabin_counts(passengers: list[dict]) -> tuple[int, int]:
    business = sum(1 for passenger in passengers if passenger.get("cabin") == "J")
    economy = sum(1 for passenger in passengers if passenger.get("cabin") != "J")
    return business, economy


class RevenueClassITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample_xml = SAMPLE_MANIFEST.read_text(encoding="utf-8")
        cls.sample_passengers = _passenger_list_from_soap(sample_xml)
        cls.sample_dashboard = _dashboard_for(cls.sample_passengers)
        cls.sample_html = _render(cls.sample_dashboard)

        fixture_passengers = _passenger_list_from_soap(CLASS_I_FIXTURE)
        cls.fixture_passengers = fixture_passengers
        cls.fixture_dashboard = _dashboard_for(fixture_passengers)
        cls.fixture_html = _render(cls.fixture_dashboard)

    def test_manifest_keeps_class_i_in_cabin_j_and_the_revenue_mix(self):
        passengers = self.sample_passengers["passengers"]
        class_i = [
            passenger
            for passenger in passengers
            if passenger.get("bookingClass") == "I" and passenger.get("cabin") == "J"
        ]
        self.assertGreater(len(class_i), 0)
        mix = self.sample_dashboard["insights"]["revenueClassMix"]
        self.assertEqual(mix.get("I"), len(class_i))
        business, _economy = _cabin_counts(passengers)
        self.assertEqual(self.sample_dashboard["analysis"]["business"]["total"], business)

    def test_revenue_tab_counts_class_i_in_the_j_cabin(self):
        passengers = self.sample_passengers["passengers"]
        business, economy = _cabin_counts(passengers)
        text = _text(self.sample_html["revenue"])
        j_cabin = _between(text, "J Cabin", "Y Cabin")
        y_cabin = _between(text, "Y Cabin", "Passenger Value Segmentation")
        premium = _between(text, "Premium", "Corporate")

        self.assertIn(f"{business} pax", j_cabin)
        self.assertIn("I:", j_cabin)
        self.assertIn("D:", j_cabin)
        self.assertNotIn("I:", y_cabin)
        self.assertIn(f"{economy} pax", y_cabin)
        self.assertIn(f"J:{round(business / (business + economy) * 100)}%", text)
        self.assertIn(f"{business} business class passengers", premium)
        self.assertIn(f"{business} ({round(business / len(passengers) * 100)}%)", premium)

    def test_commercial_tab_counts_class_i_in_the_j_bar(self):
        passengers = self.sample_passengers["passengers"]
        business, economy = _cabin_counts(passengers)
        text = _text(self.sample_html["commercial"])
        y_class = _between(text, "Y Class", "J Class")
        j_class = _between(text, "J Class", "Booking Window")

        self.assertIn("Class I", j_class)
        self.assertIn("Class D", j_class)
        self.assertRegex(j_class, rf"J Class {business}\b")
        self.assertNotIn("Class I", y_class)
        self.assertRegex(y_class, rf"Y Class {economy}\b")

    def test_revenue_tab_fixture_does_not_put_class_i_in_economy(self):
        passengers = self.fixture_passengers["passengers"]
        business, economy = _cabin_counts(passengers)
        self.assertEqual(business, 3)
        self.assertEqual(economy, 1)
        text = _text(self.fixture_html["revenue"])
        j_cabin = _between(text, "J Cabin", "Y Cabin")
        y_cabin = _between(text, "Y Cabin", "Passenger Value Segmentation")

        self.assertIn("3 pax", j_cabin)
        self.assertIn("I:", j_cabin)
        self.assertIn("J:", j_cabin)
        self.assertNotIn("I:", y_cabin)
        self.assertIn("1 pax", y_cabin)
        self.assertIn("Y:", y_cabin)

    def test_decision_briefing_business_total_includes_class_i(self):
        text = _text(self.fixture_html["briefing"])
        self.assertIn("Business cabin has 3 passengers", text)
        self.assertNotIn("Business cabin has 1 passengers", text)


if __name__ == "__main__":
    unittest.main()
