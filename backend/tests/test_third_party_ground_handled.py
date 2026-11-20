"""Passenger detail must keep Sabre's third-party ground-handling flag.

These tests fail while GetPassengerData itinerary conversion omits
ThirdPartyGroundHandled. They pass once each converted segment carries
the flag as a boolean and the unauthenticated passenger-detail route
returns that value.

Sabre marks the element true when another company handles the flight on
the ground. The vendor samples place it on both a normal segment and a
codeshare. xmltodict 0.13 (the version resolved for xmltodict>=0.13,<1.0)
leaves the element text as the string "true" or "false"; the converted
document must still expose a real boolean.
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:9")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:9/0")

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder.converter import _strip_ns, convert_passenger_data
from backend.sabre.client import SabreClient


FLIGHT = "100"
ORIGIN = "BAH"
DATE = "2026-05-01"
PNR = "QKZOBC"
LAST_NAME = "HORN"
FIRST_NAME = "YVETTE"

SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body><SessionCreateRS/></soap-env:Body>
</soap-env:Envelope>"""

CLOSE_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body><SessionCloseRS/></soap-env:Body>
</soap-env:Envelope>"""


def _segment(segment_id, airline, flight, operating_airline, operating_flight,
             marketing_airline, marketing_flight, origin, destination,
             ground_handled):
    """One PassengerItinerary, shaped like the GetPassengerData samples.

    ground_handled is True, False, or None. None omits the element, which
    the schema allows (minOccurs 0) and which must convert to false.
    """
    flag = ""
    if ground_handled is True:
        flag = "<ThirdPartyGroundHandled>true</ThirdPartyGroundHandled>"
    elif ground_handled is False:
        flag = "<ThirdPartyGroundHandled>false</ThirdPartyGroundHandled>"
    return f"""
            <PassengerItinerary>
              <SegmentID>{segment_id}</SegmentID>
              <Airline>{airline}</Airline>
              <Flight>{flight}</Flight>
              <OperatingAirline>{operating_airline}</OperatingAirline>
              <OperatingFlight>{operating_flight}</OperatingFlight>
              <MarketingAirline>{marketing_airline}</MarketingAirline>
              <MarketingFlight>{marketing_flight}</MarketingFlight>
              {flag}
              <BookingClass>Y</BookingClass>
              <Origin>{origin}</Origin>
              <Destination>{destination}</Destination>
              <DepartureDate>{DATE}</DepartureDate>
              <AircraftType>789</AircraftType>
              <Cabin>Y</Cabin>
              <Seat>17D</Seat>
              <PassengerType>F</PassengerType>
              <BagCount>0</BagCount>
              <TotalBagWeightAndUnit>0</TotalBagWeightAndUnit>
              <CheckInNumber>1</CheckInNumber>
            </PassengerItinerary>"""


def passenger_data_response():
    """SOAP body in the shape SabreClient._extract_body reads.

    Segment 1 is a codeshare another company ground-handles (sample
    4_2_2_3_3b, with the flag set true). Segment 2 is an ordinary
    carrier segment explicitly not third-party handled (sample 4_2_7).
    Segment 3 omits the element.
    """
    segments = "\n".join([
        _segment("1", "EY", "6418", "VA", "880", "EY", "6418", "SYD", "MEL", True),
        _segment("2", "EY", "1", "EY", "1", "EY", "1", "AUH", "FRA", False),
        _segment("3", "GF", "100", "GF", "100", "GF", "100", "BAH", "LHR", None),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"/>
  </soap-env:Header>
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>{LAST_NAME}</LastName>
          <FirstName>{FIRST_NAME}</FirstName>
          <PassengerID>F335D0A70001</PassengerID>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <PassengerItineraryList>
            {segments}
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _parsed_passenger_data():
    """Run the product's SOAP parser and body extractor. No stand-in parser."""
    xml_text = passenger_data_response()
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerDataRS")


def _parsed_segments(raw):
    passenger = _strip_ns(raw)["PassengerDataResponseList"]["PassengerDataResponse"]
    return passenger["PassengerItineraryList"]["PassengerItinerary"]


def _element_is_true(value):
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _element_is_false(value):
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _flags(document):
    itinerary = document["passengers"][0]["itinerary"]
    missing = [
        segment.get("segmentId", "")
        for segment in itinerary
        if "thirdPartyGroundHandled" not in segment
    ]
    if missing:
        raise AssertionError(
            "converted itinerary dropped thirdPartyGroundHandled on segments "
            + ", ".join(missing)
        )
    return [segment["thirdPartyGroundHandled"] for segment in itinerary]


class _StoredPassengerList:
    def find_one(self, query, sort=None, **kwargs):
        return {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "passengers": [{
                "pnr": PNR,
                "lastName": LAST_NAME,
                "firstName": FIRST_NAME,
            }],
        }

    def index_information(self):
        return {"flight_lookup": {}}

    def create_index(self, *args, **kwargs):
        return "unused"

    def drop_index(self, *args, **kwargs):
        return None


class _Database:
    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        return _StoredPassengerList()


class _Redis:
    def ping(self):
        return True


class _Response:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


def _fake_post(url, data=None, headers=None, timeout=None):
    action = (headers or {}).get("SOAPAction")
    if action == "SessionCreateRQ":
        return _Response(SESSION_RESPONSE)
    if action == "GetPassengerDataRQ":
        return _Response(passenger_data_response())
    if action == "SessionCloseRQ":
        return _Response(CLOSE_RESPONSE)
    raise AssertionError(f"unexpected Sabre action {action}")


class ThirdPartyGroundHandledTests(unittest.TestCase):
    def test_convert_passenger_data_copies_flag_from_parsed_sabre_xml(self):
        raw = _parsed_passenger_data()
        parsed_segments = _parsed_segments(raw)
        self.assertTrue(_element_is_true(parsed_segments[0]["ThirdPartyGroundHandled"]))
        self.assertTrue(_element_is_false(parsed_segments[1]["ThirdPartyGroundHandled"]))
        self.assertNotIn("ThirdPartyGroundHandled", parsed_segments[2])

        document = convert_passenger_data(raw, "GF", FLIGHT, DATE, ORIGIN)
        itinerary = document["passengers"][0]["itinerary"]

        self.assertEqual(
            [segment["segmentId"] for segment in itinerary],
            ["1", "2", "3"],
        )
        self.assertEqual(itinerary[0]["marketingAirline"], "EY")
        self.assertEqual(itinerary[0]["marketingFlight"], "6418")
        self.assertEqual(itinerary[0]["operatingAirline"], "VA")
        self.assertEqual(itinerary[0]["operatingFlight"], "880")
        # Codeshare that another company ground-handles, then an explicit
        # false, then an omitted element. Each segment keeps its own flag.
        self.assertEqual(_flags(document), [True, False, False])
        for segment in itinerary:
            self.assertIsInstance(segment["thirdPartyGroundHandled"], bool)

    def test_passenger_detail_route_returns_flag_without_credentials(self):
        database = _Database()
        with patch("backend.api.main.get_db", return_value=database), \
                patch("backend.api.main.get_redis", return_value=_Redis()), \
                patch("backend.api.routes.passengers.get_db", return_value=database), \
                patch("backend.sabre.client.requests.post", side_effect=_fake_post):
            client = TestClient(app)
            response = client.get(
                f"/flights/{FLIGHT}/passengers/{PNR}/detail",
                params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("authorization", {k.lower() for k in response.request.headers})
        body = response.json()
        itinerary = body["passengers"][0]["itinerary"]
        self.assertEqual(itinerary[0]["airline"], "EY")
        self.assertEqual(itinerary[0]["flight"], "6418")
        self.assertEqual(itinerary[0]["operatingAirline"], "VA")
        self.assertEqual(itinerary[0]["operatingFlight"], "880")
        self.assertEqual(_flags(body), [True, False, False])


if __name__ == "__main__":
    unittest.main()
