"""Flight-status ingestion must keep Sabre paper and electronic ticket counts.

These tests follow the live path: a SOAP ACS_FlightDetailRS body is parsed and
extracted the same way SabreClient.get_flight_status does, converted by
convert_flight_status, then shaped by the flight-status API's _strip_id.
They fail until each cabin count keeps paperTicket and electronicTicket and
change detection reports when those counts move between paper and electronic.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _strip_id
from backend.feeder.converter import convert_flight_status
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient


def _flight_detail_xml(y_paper, y_electronic, j_paper, j_electronic):
    """SOAP envelope shaped like a captured ACS_FlightDetailRS.

    The security header is omitted. Counts match the GF2274 DMM economy and
    business blocks, with the ticket split supplied by the caller.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2274</Flight>
          <Origin>DMM</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>04:15PM</ScheduledDepartureTime>
          <FlightStatus>FINAL</FlightStatus>
          <DepartureGate>20</DepartureGate>
          <AircraftType>321</AircraftType>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Authorized>180</Authorized>
        <Booked>180</Booked>
        <Available>0</Available>
        <Thru>0</Thru>
        <Local>180</Local>
        <NonRevenueLocal>0</NonRevenueLocal>
        <NonRevenueThru>0</NonRevenueThru>
        <PaperTicket>{y_paper}</PaperTicket>
        <ElectronicTicket>{y_electronic}</ElectronicTicket>
        <Kiosk>0</Kiosk>
        <StandbyRestriction>B10</StandbyRestriction>
        <LocalOnBoard>180</LocalOnBoard>
        <TotalOnBoard>180</TotalOnBoard>
        <TotalBoardingPassIssued>180</TotalBoardingPassIssued>
      </PassengerCounts>
      <PassengerCounts classOfService="J">
        <Authorized>12</Authorized>
        <Booked>12</Booked>
        <Available>0</Available>
        <Thru>0</Thru>
        <Local>12</Local>
        <NonRevenueLocal>0</NonRevenueLocal>
        <NonRevenueThru>0</NonRevenueThru>
        <PaperTicket>{j_paper}</PaperTicket>
        <ElectronicTicket>{j_electronic}</ElectronicTicket>
        <Kiosk>0</Kiosk>
        <StandbyRestriction>B10</StandbyRestriction>
        <LocalOnBoard>12</LocalOnBoard>
        <TotalOnBoard>12</TotalOnBoard>
        <TotalBoardingPassIssued>12</TotalBoardingPassIssued>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _ingest(xml_text):
    """Parse, extract, and convert the way get_flight_status feeds the runner."""
    parsed = SabreClient._parse_xml(xml_text)
    raw = SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")
    return convert_flight_status(raw, "GF", "2274", "DMM", "2026-03-19")


def _public_status(doc):
    """Same shaping as GET /flights/{flight}/status and /status/history."""
    return _strip_id(copy.deepcopy(doc))


class FlightStatusTicketCountTests(unittest.TestCase):
    def test_all_paper_cabin_is_kept_on_the_public_status_document(self):
        doc = _public_status(_ingest(_flight_detail_xml(180, 0, 12, 0)))

        self.assertNotIn("_raw", doc)
        economy = doc["passengerCounts"]["Y"]
        business = doc["passengerCounts"]["J"]

        self.assertEqual(economy["booked"], 180)
        self.assertEqual(economy["onBoard"], 180)
        self.assertEqual(economy["boardingPasses"], 180)
        self.assertEqual(economy.get("paperTicket"), 180)
        self.assertEqual(economy.get("electronicTicket"), 0)

        self.assertEqual(business["booked"], 12)
        self.assertEqual(business["onBoard"], 12)
        self.assertEqual(business["boardingPasses"], 12)
        self.assertEqual(business.get("paperTicket"), 12)
        self.assertEqual(business.get("electronicTicket"), 0)

    def test_paper_and_electronic_cabins_with_the_same_booked_count_stay_distinct(self):
        paper = _public_status(_ingest(_flight_detail_xml(180, 0, 12, 0)))
        electronic = _public_status(_ingest(_flight_detail_xml(0, 180, 0, 12)))

        self.assertEqual(paper["passengerCounts"]["Y"]["booked"], 180)
        self.assertEqual(electronic["passengerCounts"]["Y"]["booked"], 180)
        self.assertEqual(
            paper["passengerCounts"]["Y"]["onBoard"],
            electronic["passengerCounts"]["Y"]["onBoard"],
        )
        self.assertEqual(
            paper["passengerCounts"]["Y"]["boardingPasses"],
            electronic["passengerCounts"]["Y"]["boardingPasses"],
        )
        self.assertNotEqual(
            paper["passengerCounts"]["Y"],
            electronic["passengerCounts"]["Y"],
        )
        self.assertEqual(electronic["passengerCounts"]["Y"].get("paperTicket"), 0)
        self.assertEqual(electronic["passengerCounts"]["Y"].get("electronicTicket"), 180)
        self.assertEqual(electronic["passengerCounts"]["J"].get("paperTicket"), 0)
        self.assertEqual(electronic["passengerCounts"]["J"].get("electronicTicket"), 12)

    def test_ticket_stock_change_is_detected_when_booked_count_does_not_change(self):
        flight_info = {
            "flightNumber": "2274",
            "origin": "DMM",
            "departureDate": "2026-03-19",
        }
        before = {
            "snapshotId": "snap-paper",
            "data": _ingest(_flight_detail_xml(180, 0, 12, 0)),
        }
        after = {
            "snapshotId": "snap-electronic",
            "data": _ingest(_flight_detail_xml(0, 180, 0, 12)),
        }
        changes = detect_changes(before, after, "flight_status", flight_info)
        by_field = {change["field"]: change for change in changes}

        self.assertNotIn("passengerCounts.Y.booked", by_field)
        self.assertNotIn("passengerCounts.J.booked", by_field)
        for field, old, new in (
            ("passengerCounts.Y.paperTicket", "180", "0"),
            ("passengerCounts.Y.electronicTicket", "0", "180"),
            ("passengerCounts.J.paperTicket", "12", "0"),
            ("passengerCounts.J.electronicTicket", "0", "12"),
        ):
            self.assertIn(field, by_field)
            self.assertEqual(by_field[field]["oldValue"], old)
            self.assertEqual(by_field[field]["newValue"], new)


if __name__ == "__main__":
    unittest.main()
