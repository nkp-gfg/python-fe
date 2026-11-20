"""Standby passengers must not inflate missing-boarding-pass counts.

Sabre GetPassengerList keeps BoardingPassFlag false for a standby passenger
even when that passenger already has a standby boarding pass. The dashboard
and the process audit treat every false flag as a missing pass.

These tests fail while that counting is in place. They pass once standby rows
(passenger type S / isStandby) are left out of the issued-pass and
checked-in-without-pass tallies, while a false flag still counts as a missing
pass for every other passenger type.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import run_audit
from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient


def _passenger_xml(pnr, last_name, passenger_type, boarding_pass, checked_in, boarded):
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>TEST</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{pnr}</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>12A</Seat>
          <Destination>BOM</Destination>
          <BoardingPassFlag>{"true" if boarding_pass else "false"}</BoardingPassFlag>
          <PassengerType>{passenger_type}</PassengerType>
          <BagCount>0</BagCount>
          <CheckIn_Info>
            <CheckInStatus>{"true" if checked_in else "false"}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{"true" if boarded else "false"}</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>"""


def _response_xml():
    """SOAP body shaped like a captured GetPassengerListRS, with no credentials."""
    rows = [
        # Full-fare passenger with a pass. Counts as issued.
        _passenger_xml("FTRUE", "FARE", "F", True, True, True),
        # Full-fare, checked in, no pass.
        _passenger_xml("FCI", "GAP", "F", False, True, False),
        # Full-fare, boarded without a check-in flag, no pass.
        _passenger_xml("FBD", "GATE", "F", False, False, True),
        # Full-fare, not checked in, no pass. Pending, but not checked-in-without-pass.
        _passenger_xml("FNONE", "BOOK", "F", False, False, False),
        # Employee, checked in, no pass. Not standby, so still a missing pass.
        _passenger_xml("ECI", "STAFF", "E", False, True, False),
        # Standby, checked in, flag false. Guide: standby pass already issued.
        _passenger_xml("SCI", "CLEAR", "S", False, True, False),
        # Standby, boarded, flag false. Same rule, boarded branch.
        _passenger_xml("SBD", "BOARD", "S", False, False, True),
        # Standby, not checked in, flag false. Still not a missing revenue pass.
        _passenger_xml("SNONE", "WAIT", "S", False, False, False),
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2056</Flight>
          <DepartureDate>2026-03-16</DepartureDate>
          <Origin>DMM</Origin>
          <Destination>BOM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(rows)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _ingest_manifest():
    """Parse and convert the way SabreClient.get_passenger_list feeds the feeder."""
    parsed = SabreClient._parse_xml(_response_xml())
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", "2056", "2026-03-16", "DMM")


def _by_pnr(passengers):
    return {p["pnr"]: p for p in passengers}


def test_converter_keeps_standby_flag_false_and_marks_type_s():
    passengers = _by_pnr(_ingest_manifest()["passengers"])
    assert len(passengers) == 8

    for pnr in ("SCI", "SBD", "SNONE"):
        row = passengers[pnr]
        assert row["passengerType"] == "S"
        assert row["isStandby"] is True
        assert row["boardingPassIssued"] is False

    assert passengers["SCI"]["isCheckedIn"] is True
    assert passengers["SCI"]["isBoarded"] is False
    assert passengers["SBD"]["isCheckedIn"] is False
    assert passengers["SBD"]["isBoarded"] is True
    assert passengers["FCI"]["isStandby"] is False
    assert passengers["FCI"]["boardingPassIssued"] is False
    assert passengers["ECI"]["isStandby"] is False
    assert passengers["FTRUE"]["boardingPassIssued"] is True


def test_dashboard_does_not_count_standby_as_missing_boarding_passes():
    manifest = _ingest_manifest()
    payload = _build_dashboard_payload(
        None, manifest, "DMM", "2026-03-16", {},
    )
    insights = payload["insights"]
    passengers = manifest["passengers"]
    standby = [p for p in passengers if p["isStandby"] or p["passengerType"] == "S"]
    regular = [p for p in passengers if p not in standby]
    assert standby and regular

    missing_regular = [p for p in regular if not p["boardingPassIssued"]]
    checked_in_missing = [
        p for p in missing_regular
        if p["isCheckedIn"] or p["isBoarded"]
    ]
    issued_regular = sum(1 for p in regular if p["boardingPassIssued"])

    boarding = insights["boardingPasses"]
    readiness = insights["operationalReadiness"]

    # Pending and checked-in-without-pass ignore standby. A false flag on a
    # non-standby passenger still counts, including the boarded-only case.
    assert boarding["notIssued"] == len(missing_regular)
    assert readiness["checkedInNoBP"] == len(checked_in_missing)
    assert any(p["pnr"] == "FBD" for p in checked_in_missing)
    assert any(p["pnr"] == "FCI" for p in checked_in_missing)
    assert any(p["pnr"] == "ECI" for p in checked_in_missing)

    # issuedPct is the figure the departure briefing weights. Standby rows
    # whose flag is false must not pull it below the rate for passengers the
    # flag actually describes. Counting those standby rows as issued, or
    # dropping them from the population, both satisfy this.
    floor = round(issued_regular / len(regular) * 100, 1)
    assert boarding["issuedPct"] + 1e-9 >= floor

    # The same payload is what GET /flights/{flight_number}/dashboard returns
    # as insights, and what the risk tab reads as checkedInNoBP.
    assert payload["passengerSummary"]["totalPassengers"] == len(passengers)


def test_audit_does_not_alert_standby_for_missing_boarding_pass():
    manifest = _ingest_manifest()
    result = run_audit(manifest, None, [])
    by_rule = {}
    for alert in result["alerts"]:
        by_rule.setdefault(alert["ruleId"], set()).add(alert.get("pnr"))

    assert by_rule.get("BOARDED_WITHOUT_BP") == {"FBD"}
    assert by_rule.get("CHECKEDIN_NO_BP") == {"FCI", "ECI"}
    assert "SCI" not in by_rule.get("CHECKEDIN_NO_BP", set())
    assert "SBD" not in by_rule.get("BOARDED_WITHOUT_BP", set())
    assert "SNONE" not in by_rule.get("CHECKEDIN_NO_BP", set())
    assert "SNONE" not in by_rule.get("BOARDED_WITHOUT_BP", set())
