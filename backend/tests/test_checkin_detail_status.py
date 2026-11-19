"""Sabre DetailStatus values must reach the passenger detail API unchanged.

The live sheet asks Sabre for GetPassengerData with validateCheckInRequirements
enabled. These tests run that response through the same parser and converter the
route uses. They do not call Sabre.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "get_passenger_data_checkin_statuses.xml"
VENDOR_SAMPLE = (
    ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_1.xml"
)

# DetailStatus.ACS in GetPassengerDataRQRS_v4-0-0.xsd. The sheet's OK /
# Satisfied / Required strings are not in this set.
SABRE_DETAIL_STATUSES = ("ValidationRequired", "ValidationFailed", "WithDOCRequired")


def _response_envelope(xml_text: str) -> str:
    start = xml_text.find("<soap-env:Envelope")
    end = xml_text.find("</soap-env:Envelope>")
    if start < 0 or end < 0:
        raise AssertionError("sample has no soap-env response envelope")
    return xml_text[start : end + len("</soap-env:Envelope>")]


def convert_get_passenger_data(xml_text: str, airline: str, flight: str, date: str, origin: str):
    """Same steps as SabreClient.get_passenger_data after the HTTP body arrives."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    return convert_passenger_data(body, airline, flight, date, origin)


class CheckInDetailStatusPassthroughTest(unittest.TestCase):
    def test_vendor_sample_keeps_validation_required(self):
        """The published sample uses ValidationRequired for a missing passport and Timatic."""
        xml = _response_envelope(VENDOR_SAMPLE.read_text())
        doc = convert_get_passenger_data(xml, "EY", "655", "2017-01-11", "AUH")
        passenger = doc["passengers"][0]
        segment_statuses = [item["detailStatus"] for item in passenger["itinerary"][0]["requiredInfo"]]
        summary_statuses = [item["detailStatus"] for item in passenger["requiredInfo"]]
        self.assertIn("ValidationRequired", segment_statuses)
        self.assertTrue(all(status == "ValidationRequired" for status in segment_statuses))
        self.assertTrue(all(status == "ValidationRequired" for status in summary_statuses))
        codes = {item["code"] for item in passenger["itinerary"][0]["requiredInfo"]}
        self.assertIn("DOCS", codes)
        self.assertIn("TIM", codes)
        # The strings the sheet treats as success or as the only explicit failure
        # are not what this response contains.
        for status in segment_statuses + summary_statuses:
            self.assertNotIn(status, ("OK", "Satisfied", "Required"))

    def test_each_schema_status_is_stored_unchanged_on_segment_and_summary(self):
        xml = FIXTURE.read_text()
        doc = convert_get_passenger_data(xml, "GF", "2057", "2026-04-02", "BAH")
        passenger = doc["passengers"][0]
        for collection in (
            passenger["itinerary"][0]["requiredInfo"],
            passenger["requiredInfo"],
        ):
            by_code = {item["code"]: item["detailStatus"] for item in collection}
            self.assertEqual(by_code["DOCS"], "ValidationRequired")
            self.assertEqual(by_code["TIM"], "ValidationFailed")
            self.assertEqual(by_code["DOCV"], "WithDOCRequired")
            self.assertEqual(set(by_code.values()), set(SABRE_DETAIL_STATUSES))

    def test_json_response_keeps_validation_failed(self):
        """FastAPI returns this dict as JSON. Encoding does not rename DetailStatus."""
        doc = convert_get_passenger_data(
            FIXTURE.read_text(), "GF", "2057", "2026-04-02", "BAH"
        )
        decoded = json.loads(json.dumps(doc))
        self.assertEqual(
            decoded["passengers"][0]["requiredInfo"][1]["detailStatus"],
            "ValidationFailed",
        )
        self.assertEqual(
            decoded["passengers"][0]["itinerary"][0]["requiredInfo"][0]["detailStatus"],
            "ValidationRequired",
        )


if __name__ == "__main__":
    unittest.main()
