"""Standby passengers must not be flagged as missing a boarding pass.

Sabre GetPassengerList leaves BoardingPassFlag false for a standby passenger
who already holds a standby boarding pass. Ingestion stores that as
boardingPassIssued=false and PassengerType S as isStandby=true. The process
audit and the dashboard readiness count then treat the false flag as a
missing pass.

These tests follow the product path: SOAP XML parsed by SabreClient, converted
by convert_passenger_list, stored as a passenger_list document, then read by
GET /flights/{flight_number}/audit and GET /flights/{flight_number}/dashboard.
They fail while those rules still alert on standby passengers, and pass once
a false BoardingPassFlag is not treated as a missing pass for passenger type
S / isStandby. Revenue and employee passengers with a false flag must still
alert.
"""

import copy
import unittest
from unittest.mock import patch

from backend.api.routes.audit import get_flight_audit
from backend.api.routes.flights import get_flight_dashboard
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient


FLIGHT = "4242"
ORIGIN = "BAH"
DATE = "2026-04-02"
AIRLINE = "GF"

# Distinct locators so each alert can be attributed to one passenger.
BOARDED_STANDBY = "SBYBD1"
CHECKEDIN_STANDBY = "SBYCI1"
BOARDED_STANDBY_FLAG_TRUE = "SBYOK1"
BOARDED_REVENUE = "REVBD1"
CHECKEDIN_REVENUE = "REVCI1"
BOARDED_EMPLOYEE = "EMPBD1"

MISSING_PASS_RULES = {"BOARDED_WITHOUT_BP", "CHECKEDIN_NO_BP"}


def _passenger_xml(
    last_name,
    first_name,
    pnr,
    passenger_id,
    line,
    passenger_type,
    boarding_pass_flag,
    checked_in,
    boarded,
    seat,
):
    """One PassengerInfo element in the shape of a GetPassengerListRS sample."""
    check_in = "true" if checked_in else "false"
    board = "true" if boarded else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <ThruIndicator>false</ThruIndicator>
          <LineNumber>{line}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <Destination>DXB</Destination>
          <BoardingPassFlag>{boarding_pass_flag}</BoardingPassFlag>
          <PassengerType>{passenger_type}</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>
            <Indicator>CheckedIn</Indicator>
          </Indicators>
          <CheckIn_Info>
            <CheckInNumber>{line}</CheckInNumber>
            <CheckInDate>{DATE}</CheckInDate>
            <CheckInTime>08:00</CheckInTime>
            <CheckInStatus>{check_in}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{board}</BoardStatus>
          </Boarding_Info>
          <EditCodeList>
            <ns2:EditCode>DOCS</ns2:EditCode>
          </EditCodeList>
        </PassengerInfo>"""


def standby_manifest_xml():
    """SOAP GetPassengerListRS whose standby rows match the vendor flag rule.

    BoardingPassFlag is false for the standby passengers. One standby row
    keeps the flag true so a real issued-pass record is not treated as the
    bug. Revenue (F) and employee (E) rows with a false flag stay in the
    same manifest.
    """
    passengers = "\n".join([
        _passenger_xml("STANDBY", "BOARDED", BOARDED_STANDBY, "P001", 1,
                       "S", "false", True, True, "1A"),
        _passenger_xml("STANDBY", "CHECKEDIN", CHECKEDIN_STANDBY, "P002", 2,
                       "S", "false", True, False, "1B"),
        _passenger_xml("STANDBY", "HASPASS", BOARDED_STANDBY_FLAG_TRUE, "P003", 3,
                       "S", "true", True, True, "1C"),
        _passenger_xml("REVENUE", "BOARDED", BOARDED_REVENUE, "P004", 4,
                       "F", "false", True, True, "2A"),
        _passenger_xml("REVENUE", "CHECKEDIN", CHECKEDIN_REVENUE, "P005", 5,
                       "F", "false", True, False, "2B"),
        _passenger_xml("EMPLOYEE", "BOARDED", BOARDED_EMPLOYEE, "P006", 6,
                       "E", "false", True, True, "3A"),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
        <CabinInfoList>
          <ns2:CabinInfo>
            <ns2:Destination>DXB</ns2:Destination>
            <ns2:Cabin>Y</ns2:Cabin>
            <ns2:Count>6</ns2:Count>
            <ns2:Authorized>180</ns2:Authorized>
            <ns2:Available>174</ns2:Available>
          </ns2:CabinInfo>
        </CabinInfoList>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def parse_passenger_list_body(xml_text):
    """Run the same parse and body extract the Sabre client uses."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def converted_manifest():
    raw = parse_passenger_list_body(standby_manifest_xml())
    return convert_passenger_list(raw, AIRLINE, FLIGHT, DATE, ORIGIN)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Just enough of a Mongo collection for the audit and dashboard reads.

    find_one/find apply the equality query and sort the route passes.
    Documents are copied on read so response shaping cannot change what
    was stored.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return self

    def find_one(self, query, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return matches[0] if matches else None

    def find(self, query, projection=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        return _Cursor(matches)


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _matches(doc, query):
    return all(doc.get(key) == value for key, value in query.items())


def _rules_for(result, pnr):
    return [
        alert for alert in result["alerts"]
        if alert.get("pnr") == pnr and alert.get("ruleId") in MISSING_PASS_RULES
    ]


class StandbyBoardingPassAuditTests(unittest.TestCase):
    def setUp(self):
        self.doc = converted_manifest()
        self.db = _Database()
        # Same write the feeder uses: the converted document is inserted whole.
        self.db["passenger_list"].insert_one(self.doc)
        self.by_pnr = {p["pnr"]: p for p in self.doc["passengers"]}

    def test_ingestion_keeps_false_flag_and_marks_type_s_standby(self):
        boarded = self.by_pnr[BOARDED_STANDBY]
        checked_in = self.by_pnr[CHECKEDIN_STANDBY]
        issued = self.by_pnr[BOARDED_STANDBY_FLAG_TRUE]
        revenue = self.by_pnr[BOARDED_REVENUE]
        employee = self.by_pnr[BOARDED_EMPLOYEE]

        self.assertTrue(boarded["isStandby"])
        self.assertEqual(boarded["passengerType"], "S")
        self.assertTrue(boarded["isBoarded"])
        self.assertTrue(boarded["isCheckedIn"])
        self.assertFalse(boarded["boardingPassIssued"])

        self.assertTrue(checked_in["isStandby"])
        self.assertTrue(checked_in["isCheckedIn"])
        self.assertFalse(checked_in["isBoarded"])
        self.assertFalse(checked_in["boardingPassIssued"])

        self.assertTrue(issued["isStandby"])
        self.assertTrue(issued["boardingPassIssued"])

        self.assertFalse(revenue["isStandby"])
        self.assertEqual(revenue["passengerType"], "F")
        self.assertFalse(revenue["boardingPassIssued"])
        self.assertFalse(employee["isStandby"])
        self.assertEqual(employee["passengerType"], "E")
        self.assertFalse(employee["boardingPassIssued"])

    def test_audit_route_skips_standby_and_keeps_revenue_missing_pass_alerts(self):
        with patch("backend.api.routes.audit.get_db", return_value=self.db):
            result = get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)

        self.assertEqual(result["flightNumber"], FLIGHT)
        self.assertEqual(result["origin"], ORIGIN)
        self.assertEqual(result["departureDate"], DATE)

        self.assertEqual(_rules_for(result, BOARDED_STANDBY), [])
        self.assertEqual(_rules_for(result, CHECKEDIN_STANDBY), [])
        self.assertEqual(_rules_for(result, BOARDED_STANDBY_FLAG_TRUE), [])
        self.assertNotIn(BOARDED_STANDBY, result["passengerAlerts"])
        self.assertNotIn(CHECKEDIN_STANDBY, result["passengerAlerts"])

        boarded_revenue = _rules_for(result, BOARDED_REVENUE)
        self.assertEqual(
            [(a["ruleId"], a["severity"]) for a in boarded_revenue],
            [("BOARDED_WITHOUT_BP", "critical")],
        )
        checked_in_revenue = _rules_for(result, CHECKEDIN_REVENUE)
        self.assertEqual(
            [(a["ruleId"], a["severity"]) for a in checked_in_revenue],
            [("CHECKEDIN_NO_BP", "warning")],
        )
        boarded_employee = _rules_for(result, BOARDED_EMPLOYEE)
        self.assertEqual(
            [(a["ruleId"], a["severity"]) for a in boarded_employee],
            [("BOARDED_WITHOUT_BP", "critical")],
        )

        # The false standby alerts must not share the list with the real ones.
        missing_pass = [
            alert for alert in result["alerts"]
            if alert.get("ruleId") in MISSING_PASS_RULES
        ]
        self.assertEqual(
            sorted(alert["pnr"] for alert in missing_pass),
            sorted([BOARDED_REVENUE, CHECKEDIN_REVENUE, BOARDED_EMPLOYEE]),
        )
        self.assertEqual(result["summary"]["critical"], 2)
        self.assertEqual(result["summary"]["warning"], 1)

    def test_dashboard_readiness_does_not_count_standby_false_flag_as_missing_pass(self):
        with patch("backend.api.routes.flights.get_db", return_value=self.db):
            # Call the route the way the framework does: unresolved Query
            # defaults are not left in place. snapshot_sequence stays unset.
            dashboard = get_flight_dashboard(
                FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None,
            )

        readiness = dashboard["insights"]["operationalReadiness"]
        # Three non-standby passengers are checked in or boarded with a false flag.
        # The two standby passengers whose flag is false are not missing a pass.
        self.assertEqual(readiness["checkedInNoBP"], 3)


if __name__ == "__main__":
    unittest.main()
