"""Revenue tab loyalty headcount must count each passenger once.

Gulf Air manifests put the FalconFlyer marker FF on the same passenger as a
tier code (BLU, SLV, GLD, or BLK). The dashboard increments one loyalty
bucket per matching code, and the Revenue tab adds those buckets into a
single Loyalty row. These tests drive a captured Sabre GetPassengerListRS
through the parser, the passenger-list converter, the dashboard payload,
and the Revenue tab component. They fail while that row reports the sum of
the buckets.

The phase-summary helper copies the same per-code buckets and does not add
them up. The workbench chips show each code on its own. Network analytics
group reservation frequent-flyer tier levels, which is a different field.
Those surfaces are not a second summed loyalty total.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SAMPLE_DIR = os.path.join(REPO_ROOT, "sample_data", "sample_xmlfiles")
LOYALTY_CODES = ("FF", "BLU", "SLV", "GLD", "BLK")

GF2057 = os.path.join(
    SAMPLE_DIR, "GetPassengerListRS_GF2057_BOM_2026-03-19.xml")
GF2006 = os.path.join(
    SAMPLE_DIR, "GetPassengerListRS_GF2006_LHR_2026-03-18.xml")


def _js_round(value):
    """Match the Revenue tab pct() helper, which uses Math.round."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _loyalty_label(distinct, total):
    pct = _js_round((distinct / total) * 100) if total else 0
    return f"{distinct} ({pct}%) width={pct}"


def _distinct_loyalty(passengers):
    codes = set(LOYALTY_CODES)
    return sum(
        1 for passenger in passengers
        if codes.intersection(passenger.get("editCodes") or [])
    )


def _passenger_list_from_soap(xml_text, airline, flight_number, departure_date, origin):
    """Follow the Sabre client parse and the feeder converter. No network call."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    body = client._extract_body(parsed, "GetPassengerListRS")
    return convert_passenger_list(
        body, airline, flight_number, departure_date, origin)


def _dashboard_for_list(passenger_list, origin, departure_date):
    return _build_dashboard_payload(
        None, passenger_list, origin, departure_date, {})


def _load_sample(path, airline, flight_number, departure_date, origin):
    with open(path, encoding="utf-8") as handle:
        xml_text = handle.read()
    passenger_list = _passenger_list_from_soap(
        xml_text, airline, flight_number, departure_date, origin)
    dashboard = _dashboard_for_list(passenger_list, origin, departure_date)
    return passenger_list, dashboard


def _soap_manifest(passengers):
    """Build a GetPassengerListRS envelope in the shape Sabre returns.

    passengers: sequence of (last_name, first_name, pnr, edit_codes).
    """
    blocks = []
    for last_name, first_name, pnr, edit_codes in passengers:
        edits = "\n".join(
            f"            <ns2:EditCode>{code}</ns2:EditCode>"
            for code in edit_codes
        )
        blocks.append(
            f"""        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          <PNRLocator>{pnr}</PNRLocator>
          <Cabin>Y</Cabin>
          <BookingClass>Y</BookingClass>
          <PassengerType>F</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <EditCodeList>
{edits}
          </EditCodeList>
        </PassengerInfo>"""
        )
    body = "\n".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>9999</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
{body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _case(name, passenger_list, dashboard):
    passengers = passenger_list["passengers"]
    total = dashboard["passengerSummary"]["totalPassengers"]
    distinct = _distinct_loyalty(passengers)
    return {
        "name": name,
        "dashboard": dashboard,
        "expected": _loyalty_label(distinct, total),
        "distinct": distinct,
        "total": total,
        "bucket_sum": sum(dashboard["analysis"]["loyaltyCounts"].values()),
    }


def _render_loyalty_labels(dashboards):
    frontend = os.path.join(REPO_ROOT, "frontend")
    renderer = os.path.join(frontend, "tests", "render-revenue-loyalty.tsx")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(dashboards, handle)
        payload_path = handle.name
    try:
        completed = subprocess.run(
            [
                "npx", "--yes", "tsx@4.20.6",
                renderer, payload_path,
            ],
            cwd=frontend,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        os.unlink(payload_path)
    if completed.returncode != 0:
        raise AssertionError(
            "Revenue tab renderer failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != len(dashboards):
        raise AssertionError(
            f"renderer returned {len(lines)} rows for {len(dashboards)} dashboards:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return lines


def test_revenue_tab_loyalty_total_counts_each_passenger_once():
    """The Loyalty row is the number of distinct loyalty passengers, not the sum of codes."""
    sample_2057 = _load_sample(GF2057, "GF", "2057", "2026-03-19", "BOM")
    sample_2006 = _load_sample(GF2006, "GF", "2006", "2026-03-18", "LHR")

    everyone = _soap_manifest([
        ("ALPHA", "A", "AAA111", ["FF", "BLU", "ET"]),
        ("BRAVO", "B", "BBB222", ["M", "FF", "SLV"]),
        ("CHARLIE", "C", "CCC333", ["FF", "GLD", "DOCS"]),
    ])
    everyone_list = _passenger_list_from_soap(
        everyone, "GF", "9999", "2026-03-19", "BAH")

    mixed = _soap_manifest([
        ("DELTA", "D", "DDD444", ["FF"]),
        ("ECHO", "E", "EEE555", ["FF", "DOCS"]),
        ("FOXTROT", "F", "FFF666", ["FF", "SLV", "GLD"]),
        ("GOLF", "G", "GGG777", ["BLK", "WB"]),
        ("HOTEL", "H", "HHH888", ["ET", "DOCS"]),
    ])
    mixed_list = _passenger_list_from_soap(
        mixed, "GF", "9999", "2026-03-19", "BAH")

    none = _soap_manifest([
        ("INDIA", "I", "III999", ["ET"]),
        ("JULIET", "J", "JJJ000", ["DOCS", "DOCA"]),
    ])
    none_list = _passenger_list_from_soap(
        none, "GF", "9998", "2026-03-19", "BAH")

    tier_only = _soap_manifest([
        ("KILO", "K", "KKK123", ["BLU"]),
    ])
    tier_only_list = _passenger_list_from_soap(
        tier_only, "GF", "9997", "2026-03-19", "BAH")

    cases = [
        _case("GF2057 Bombay manifest", *sample_2057),
        _case("GF2006 London manifest", *sample_2006),
        _case(
            "every passenger is a tiered member",
            everyone_list,
            _dashboard_for_list(everyone_list, "BAH", "2026-03-19"),
        ),
        _case(
            "mixed marker, tier, and multi-tier passengers",
            mixed_list,
            _dashboard_for_list(mixed_list, "BAH", "2026-03-19"),
        ),
        _case(
            "no loyalty codes",
            none_list,
            _dashboard_for_list(none_list, "BAH", "2026-03-19"),
        ),
        _case(
            "tier without the FalconFlyer marker",
            tier_only_list,
            _dashboard_for_list(tier_only_list, "BAH", "2026-03-19"),
        ),
    ]

    # Distinct counts come from the converted manifest, not from loyaltyCounts.
    # GF2057 has seven frequent-flyer passengers on a 59-passenger flight.
    # GF2006 has two. A fix may stop the buckets from overlapping; these
    # passenger counts stay the same.
    bombay = cases[0]
    assert bombay["distinct"] == 7
    assert bombay["total"] == 59
    london = cases[1]
    assert london["distinct"] == 2
    assert london["total"] == 46

    actuals = _render_loyalty_labels([case["dashboard"] for case in cases])
    mismatches = []
    for case, actual in zip(cases, actuals):
        if actual != case["expected"]:
            mismatches.append(
                f"{case['name']}: displayed {actual}, "
                f"distinct passengers {case['expected']} "
                f"(sum of FF/BLU/SLV/GLD/BLK buckets is {case['bucket_sum']})"
            )
    assert not mismatches, "\n".join(mismatches)


if __name__ == "__main__":
    test_revenue_tab_loyalty_total_counts_each_passenger_once()
    print("PASS: test_revenue_tab_loyalty_total_counts_each_passenger_once")
