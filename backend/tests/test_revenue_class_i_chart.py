"""Class I on a Sabre passenger list must survive into the commercial charts.

These tests fail while booking class I is left out of the business-cabin
grouping. The dashboard route already counts that passenger, and the manifest
cabin is J. The chart tests call the rendered Commercial, Revenue, and
briefing views and expect the same count.

Run from the repository root:

    python -m pytest backend/tests/test_revenue_class_i_chart.py -q
"""

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE = os.path.join(
    ROOT,
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
)
UI_TEST = os.path.join(ROOT, "frontend", "tests", "revenue-class-i-chart.test.ts")

# Placeholders only. The test never opens a Sabre session; it parses a saved
# response with SabreClient._parse_xml and _extract_body.
for _name, _value in {
    "SABRE_BASE_URL": "https://placeholder.invalid",
    "SABRE_CPAID": "XX",
    "SABRE_USERNAME": "placeholder",
    "SABRE_PASSWORD": "placeholder",
    "SABRE_PSEUDO_CITY_CODE": "XXX",
    "SABRE_ORGANIZATION": "XX",
    "SABRE_DOMAIN": "XX",
}.items():
    os.environ.setdefault(_name, _value)

sys.path.insert(0, ROOT)

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

# Letters the charts must treat as business. I is the Sabre business fare
# that the current sets omit; the manifest cabin for that passenger is J.
BUSINESS_CLASSES = {"J", "C", "D", "Z", "R", "F", "I"}


def _booking_class(passenger):
    return str(passenger.get("bookingClass", "")).strip().upper()


def load_gf2006_case():
    """Replay the product parse used for GetPassengerListRS."""
    with open(SAMPLE, encoding="utf-8") as handle:
        xml_text = handle.read()
    client = SabreClient()
    parsed = client._parse_xml(xml_text)
    body = client._extract_body(parsed, "GetPassengerListRS")
    passenger_list = convert_passenger_list(body, "GF", "2006", "2026-03-18", "LHR")
    dashboard = _build_dashboard_payload(
        None, passenger_list, "LHR", "2026-03-18", {}
    )
    passengers = passenger_list["passengers"]
    insights = dashboard["insights"]
    return {
        "revenueClassMix": insights["revenueClassMix"],
        "classICount": sum(1 for passenger in passengers if _booking_class(passenger) == "I"),
        "manifestBusiness": dashboard["analysis"]["business"]["total"],
        "passengerCount": len(passengers),
        "passengers": [
            {
                "bookingClass": passenger.get("bookingClass", ""),
                "cabin": passenger.get("cabin", ""),
            }
            for passenger in passengers
        ],
    }


def test_gf2006_class_i_reaches_dashboard_insights():
    """The ingested manifest counts the class I passenger in cabin J."""
    case = load_gf2006_case()
    passengers = case["passengers"]
    assert case["classICount"] >= 1
    assert case["revenueClassMix"]["I"] == case["classICount"]
    assert sum(case["revenueClassMix"].values()) == case["passengerCount"]
    class_i = [passenger for passenger in passengers if _booking_class(passenger) == "I"]
    assert class_i
    assert {passenger["cabin"] for passenger in class_i} == {"J"}
    cabin_j = [passenger for passenger in passengers if passenger["cabin"] == "J"]
    assert cabin_j
    assert all(_booking_class(passenger) in BUSINESS_CLASSES for passenger in cabin_j)
    assert case["manifestBusiness"] == len(cabin_j)
    assert case["manifestBusiness"] == sum(
        count
        for letter, count in case["revenueClassMix"].items()
        if letter.strip().upper() in BUSINESS_CLASSES
    )


def test_rendered_charts_keep_class_i_in_the_business_total():
    """Commercial, Revenue, and briefing views must show the class I count."""
    completed = subprocess.run(
        ["npx", "--yes", "tsx", "--tsconfig", "tsconfig.json", "tests/revenue-class-i-chart.test.ts"],
        cwd=os.path.join(ROOT, "frontend"),
        text=True,
        capture_output=True,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stderr or completed.stdout


if __name__ == "__main__":
    if "--json" in sys.argv:
        payload = load_gf2006_case()
        payload.pop("passengers", None)
        print("GF2006_JSON:" + json.dumps(payload))
