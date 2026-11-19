"""Boarding funnel counts must be a partition of the manifest.

Sabre leaves CheckInStatus true after BoardStatus becomes true. The flight
dashboard insights panel labels ``insights.boardingRate.checkedIn`` as
"Checked in (not boarded)", and the decision briefing and briefing tab print
``insights.boardingRate.checkedInPct`` as the check-in rate. Those fields are
built by ``_build_dashboard_payload`` (the handler behind
``GET /flights/{flight}/dashboard``) from the passenger list produced by
``SabreClient`` parsing plus ``convert_passenger_list``.

The three stages are disjoint:

* boarded: BoardStatus true
* checked in and not boarded: CheckInStatus true and BoardStatus false
* neither: both flags false

The check-in percentage is that checked-in stage plus the boarded stage,
divided by the passenger total once. The not-checked-in count is the neither
stage, so it cannot go negative and the rate cannot exceed 100 percent.

These tests fail while boarded passengers are added into the check-in total
and then added again for the percentage.

Not covered here, because they do not publish that funnel:

* ``groupBookings`` checked-in and boarded badges are overlapping tallies.
* ``analysis.checkedIn`` includes boarded passengers, and the workbench
  subtracts ``analysis.boarded`` before labeling the remainder "Checked-In".
* ``_derive_flight_phase`` adds those two analysis counts only on the
  check-in branch, which returns earlier once anyone has boarded.
* The analytics check-in rate divides the inclusive check-in count once.
* State-panel check-in rate adds the already disjoint state buckets.
"""

from pathlib import Path
import unittest

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
GF2057_SAMPLE = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)


def _soap_passenger_list(passengers):
    """Build a GetPassengerListRS envelope with the same status elements Sabre sends."""
    rows = []
    for index, (checked_in, boarded) in enumerate(passengers, start=1):
        rows.append(
            f"""
            <PassengerInfo>
              <Name_Details>
                <LastName>PAX{index}</LastName>
                <FirstName>TEST</FirstName>
              </Name_Details>
              <PNRLocator nameAssociationID="{index}">TST{index:03d}</PNRLocator>
              <PassengerID>P{index:04d}</PassengerID>
              <LineNumber>{index}</LineNumber>
              <BookingClass>Y</BookingClass>
              <Cabin>Y</Cabin>
              <Destination>DMM</Destination>
              <PassengerType>F</PassengerType>
              <CheckIn_Info>
                <CheckInStatus>{"true" if checked_in else "false"}</CheckInStatus>
              </CheckIn_Info>
              <Boarding_Info>
                <BoardStatus>{"true" if boarded else "false"}</BoardStatus>
              </Boarding_Info>
            </PassengerInfo>
            """
        )
    body = "\n".join(rows)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _dashboard_from_sabre_xml(xml_text):
    """Run the ingest parse and the dashboard payload the workbench reads."""
    parsed = SabreClient._parse_xml(xml_text)
    raw = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    passenger_list = convert_passenger_list(raw, "GF", "2057", "2026-03-19", "BOM")
    payload = _build_dashboard_payload(
        None, passenger_list, "BOM", "2026-03-19", {}
    )
    return passenger_list["passengers"], payload["insights"]["boardingRate"]


def _expected_boarding_rate(passengers):
    total = len(passengers)
    boarded = sum(1 for pax in passengers if pax.get("isBoarded"))
    checked_in_not_boarded = sum(
        1
        for pax in passengers
        if pax.get("isCheckedIn") and not pax.get("isBoarded")
    )
    neither = sum(
        1
        for pax in passengers
        if not pax.get("isCheckedIn") and not pax.get("isBoarded")
    )
    checked_in_or_boarded = sum(
        1
        for pax in passengers
        if pax.get("isCheckedIn") or pax.get("isBoarded")
    )
    return {
        "boarded": boarded,
        "checkedIn": checked_in_not_boarded,
        "notCheckedIn": neither,
        "boardedPct": round(boarded / total * 100, 1) if total else 0,
        "checkedInPct": round(checked_in_or_boarded / total * 100, 1) if total else 0,
    }


class BoardingFunnelTests(unittest.TestCase):
    def test_gf2057_sample_does_not_count_boarded_passengers_in_check_in(self):
        xml_text = GF2057_SAMPLE.read_text(encoding="utf-8")
        passengers, boarding_rate = _dashboard_from_sabre_xml(xml_text)
        overlap = sum(
            1
            for pax in passengers
            if pax.get("isCheckedIn") and pax.get("isBoarded")
        )
        self.assertGreater(
            overlap,
            0,
            "GF2057 sample must keep CheckInStatus true on boarded passengers",
        )
        expected = _expected_boarding_rate(passengers)
        self.assertEqual(boarding_rate, expected)
        self.assertEqual(
            boarding_rate["boarded"]
            + boarding_rate["checkedIn"]
            + boarding_rate["notCheckedIn"],
            len(passengers),
        )
        self.assertGreaterEqual(boarding_rate["notCheckedIn"], 0)
        self.assertLessEqual(boarding_rate["checkedInPct"], 100)

    def test_funnel_stages_stay_disjoint_for_every_flag_combination(self):
        scenarios = {
            "every boarded passenger still checked in": [
                (True, True),
                (True, True),
                (True, True),
            ],
            "boarded with and without check-in, plus unchecked": [
                (True, True),
                (True, True),
                (True, True),
                (False, True),
                (True, False),
                (False, False),
            ],
            "check-in open and nobody boarded": [
                (True, False),
                (True, False),
                (False, False),
            ],
        }
        for name, flags in scenarios.items():
            with self.subTest(name=name):
                passengers, boarding_rate = _dashboard_from_sabre_xml(
                    _soap_passenger_list(flags)
                )
                self.assertEqual(len(passengers), len(flags))
                expected = _expected_boarding_rate(passengers)
                self.assertEqual(boarding_rate, expected)
                self.assertEqual(
                    boarding_rate["boarded"]
                    + boarding_rate["checkedIn"]
                    + boarding_rate["notCheckedIn"],
                    len(passengers),
                )
                self.assertGreaterEqual(boarding_rate["notCheckedIn"], 0)
                self.assertLessEqual(boarding_rate["checkedInPct"], 100)


if __name__ == "__main__":
    unittest.main()
