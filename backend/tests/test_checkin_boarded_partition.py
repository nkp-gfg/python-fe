"""Check-in and boarded flags are independent. Live counts must match the lists they open.

These tests fail while the live operations screen subtracts the boarded total from
the check-in total, and while Booked / Not-checked-in reuse the raw not-checked-in
counter. They pass once those figures use the same sets as the passenger table:
checked in and not boarded, boarded, and neither.
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import rule_boarded_without_checkin
from backend.api.routes.flights import (
    _build_dashboard_payload,
    _build_insights,
    _summarize_flight_for_list_fast,
)
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-11-23"
UI_RUNNER = os.path.join(os.path.dirname(__file__), "live_ops_status_ui.mjs")


def _soap(passengers):
    """SOAP envelope in the shape SabreClient.get_passenger_list parses."""
    blocks = []
    for index, pax in enumerate(passengers, start=1):
        blocks.append(
            f"""
            <PassengerInfo>
              <Name_Details>
                <LastName>{pax["last"]}</LastName>
                <FirstName>{pax["first"]}</FirstName>
              </Name_Details>
              <PNRLocator>{pax["pnr"]}</PNRLocator>
              <PassengerID>{index}</PassengerID>
              <LineNumber>{index}</LineNumber>
              <BookingClass>Y</BookingClass>
              <Cabin>Y</Cabin>
              <Seat>{index}A</Seat>
              <Destination>LHR</Destination>
              <PassengerType>F</PassengerType>
              <BoardingPassFlag>false</BoardingPassFlag>
              <BagCount>0</BagCount>
              <Indicators><Indicator>Revenue</Indicator></Indicators>
              <CheckIn_Info>
                <CheckInStatus>{"true" if pax["checked_in"] else "false"}</CheckInStatus>
              </CheckIn_Info>
              <Boarding_Info>
                <BoardStatus>{"true" if pax["boarded"] else "false"}</BoardStatus>
              </Boarding_Info>
            </PassengerInfo>
            """
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
      <soap-env:Body>
        <GetPassengerListRS>
          <ItineraryInfo>
            <Itinerary>
              <Airline>GF</Airline>
              <Flight>{FLIGHT}</Flight>
              <DepartureDate>{DATE}</DepartureDate>
              <Origin>{ORIGIN}</Origin>
              <Destination>LHR</Destination>
            </Itinerary>
            <CabinInfoList>
              <CabinInfo>
                <Cabin>Y</Cabin>
                <Count>{len(passengers)}</Count>
                <Authorized>180</Authorized>
                <Destination>LHR</Destination>
              </CabinInfo>
            </CabinInfoList>
          </ItineraryInfo>
          <PassengerInfoList>
            {"".join(blocks)}
          </PassengerInfoList>
        </GetPassengerListRS>
      </soap-env:Body>
    </soap-env:Envelope>
    """
    return xml


def _manifest(passengers):
    """Run the product parse path: SOAP XML -> body extract -> passenger list."""
    parsed = SabreClient._parse_xml(_soap(passengers))
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", FLIGHT, DATE, ORIGIN)


def _sets(passengers):
    checked_in_not_boarded = [
        p for p in passengers if p.get("isCheckedIn") and not p.get("isBoarded")
    ]
    boarded = [p for p in passengers if p.get("isBoarded")]
    neither = [
        p for p in passengers if not p.get("isCheckedIn") and not p.get("isBoarded")
    ]
    return checked_in_not_boarded, boarded, neither


OPEN_BOARDING = {
    "status": "OPENCI",
    "gate": "A12",
    "origin": ORIGIN,
    "boarding": {"indicator": "BDG"},
    "aircraft": {"type": "32N", "registration": "A9C-FE"},
    "schedule": {},
    "jumpSeat": {"cockpit": 0, "cabin": 0},
}

FINAL = {
    "status": "FINAL",
    "gate": "A12",
    "origin": ORIGIN,
    "boarding": {"indicator": ""},
    "aircraft": {"type": "32N", "registration": "A9C-FE"},
    "schedule": {},
    "jumpSeat": {"cockpit": 0, "cabin": 0},
}


# One of each combination, plus a second boarded-without-check-in passenger so
# the boarded total is larger than the check-in total.
INDEPENDENT = [
    {"last": "ALI", "first": "AMINA", "pnr": "AAA111", "checked_in": True, "boarded": False},
    {"last": "BAKER", "first": "BEN", "pnr": "BBB222", "checked_in": True, "boarded": True},
    {"last": "CHO", "first": "CHRIS", "pnr": "CCC333", "checked_in": False, "boarded": True},
    {"last": "CHO", "first": "CASEY", "pnr": "CCC444", "checked_in": False, "boarded": True},
    {"last": "DIAZ", "first": "DANA", "pnr": "DDD555", "checked_in": False, "boarded": False},
]

# Boarded passengers are also checked in. The usual boarding overlap.
NESTED = [
    {"last": "ALI", "first": "AMINA", "pnr": "AAA111", "checked_in": True, "boarded": False},
    {"last": "BAKER", "first": "BEN", "pnr": "BBB222", "checked_in": True, "boarded": True},
    {"last": "BAKER", "first": "BETH", "pnr": "BBB223", "checked_in": True, "boarded": True},
    {"last": "DIAZ", "first": "DANA", "pnr": "DDD555", "checked_in": False, "boarded": False},
]


def _dashboard(passengers, flight_status):
    manifest = _manifest(passengers)
    payload = _build_dashboard_payload(
        flight_status, manifest, ORIGIN, DATE, {}, None, None, None
    )
    return manifest, payload


def test_sabre_flags_stay_independent_and_audit_sees_boarded_without_checkin():
    manifest = _manifest(INDEPENDENT)
    flags = {
        (p["pnr"], p["isCheckedIn"], p["isBoarded"]) for p in manifest["passengers"]
    }
    assert ("CCC333", False, True) in flags
    assert ("CCC444", False, True) in flags
    assert ("AAA111", True, False) in flags
    assert ("BBB222", True, True) in flags
    alerts = rule_boarded_without_checkin(manifest["passengers"])
    alerted = {a["pnr"] for a in alerts}
    assert alerted == {"CCC333", "CCC444"}


def test_boarding_funnel_uses_the_passenger_table_sets():
    """Readiness funnel labels checked-in as not yet boarded."""
    manifest = _manifest(NESTED)
    exclusive, boarded, neither = _sets(manifest["passengers"])
    insights = _build_insights(manifest["passengers"], None, None, manifest, {})
    rate = insights["boardingRate"]
    assert rate["checkedIn"] == len(exclusive)
    assert rate["boarded"] == len(boarded)
    assert rate["notCheckedIn"] == len(neither)
    assert rate["notCheckedIn"] >= 0
    assert rate["checkedIn"] + rate["boarded"] + rate["notCheckedIn"] == len(
        manifest["passengers"]
    )
    assert 0 <= rate["checkedInPct"] <= 100

    manifest = _manifest(INDEPENDENT)
    exclusive, boarded, neither = _sets(manifest["passengers"])
    insights = _build_insights(manifest["passengers"], None, None, manifest, {})
    rate = insights["boardingRate"]
    assert rate["checkedIn"] == len(exclusive)
    assert rate["notCheckedIn"] == len(neither)
    assert rate["notCheckedIn"] >= 0
    assert 0 <= rate["checkedInPct"] <= 100


def test_closed_flight_list_phase_does_not_treat_boarded_as_no_show():
    manifest = _manifest(INDEPENDENT)
    exclusive, boarded, neither = _sets(manifest["passengers"])
    # Production list aggregation drops the passenger array after counting.
    # This fallback still has the rows, which is enough to count the sets.
    assert "_checkedIn" not in manifest
    summary = _summarize_flight_for_list_fast(FINAL, manifest)
    description = summary["flightPhase"]["description"]
    assert f"{len(neither)} no-show" in description
    assert f"{len(exclusive)} offload" in description
    assert f"{len(neither) + 1} no-show" not in description


def test_tree_not_checked_in_card_excludes_boarded_passengers():
    _manifest_doc, payload = _dashboard(INDEPENDENT, OPEN_BOARDING)
    cards = {card["id"]: card for card in payload["tree"]["statusCards"]}
    _, _, neither = _sets(_manifest(INDEPENDENT)["passengers"])
    assert cards["notCheckedIn"]["value"] == len(neither)
    if cards["notCheckedIn"]["value"] == 0:
        assert cards["notCheckedIn"]["subLabel"] == ""


def test_live_operations_tiles_match_the_lists_they_open():
    scenarios = []
    for name, rows in (("nested", NESTED), ("boarded_without_checkin", INDEPENDENT)):
        manifest, payload = _dashboard(rows, OPEN_BOARDING)
        scenarios.append(
            {
                "name": name,
                "dashboard": payload,
                "passengers": {"passengers": manifest["passengers"]},
                "tree": payload.get("tree") or {},
                "audit": {"alerts": [], "passengerAlerts": {}},
            }
        )
    fixture = tempfile.NamedTemporaryFile(
        prefix="live-ops-status-", suffix=".json", delete=False
    )
    fixture.close()
    with open(fixture.name, "w", encoding="utf-8") as handle:
        json.dump({"scenarios": scenarios}, handle)
    repo = os.path.join(os.path.dirname(__file__), "..", "..")
    completed = subprocess.run(
        ["node", UI_RUNNER, fixture.name],
        cwd=os.path.abspath(repo),
        text=True,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Live operations status UI\n"
            + completed.stdout
            + "\n"
            + completed.stderr
        )
