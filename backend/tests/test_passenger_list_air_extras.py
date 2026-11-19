"""Air-extra EMD numbers and prices must survive passenger-list ingestion.

Sabre GetPassengerListRS may put AEDetailsList on each PassengerInfo
(status, quantity bought, EMD ticket number, priced total). Ingestion
requests that body with display code AE, then stores the manifest
convert_passenger_list writes. These tests fail until that manifest row
keeps the paid-extra fields beside bagCount, including when the AE
display-code call is merged onto a passenger already returned by an
earlier display code.

Expected aeDetails item on a passenger-list row:
    statusCode: StatusCode text
    quantityBought: QuantityBought as int
    emdTicketNumber: EMDTicketNumber text (not the coupon attribute)
    currency: TotalPrice @currencyCode
    totalPrice: TotalPrice text, unchanged

GetPassengerData shares the same AEDetails.ACS payload on each
itinerary segment. The detail document must fill the fields the
passenger detail view already reads (quantity, price, currency) from
QuantityBought and TotalPrice, and must also keep emdTicketNumber.
"""

import copy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passenger_by_pnr, get_passenger_detail, get_passengers
from backend.feeder.converter import convert_passenger_data, convert_passenger_list
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError
from backend.sabre import templates


def _list_response(passengers_xml):
    """SOAP body shaped like a captured GetPassengerListRS, plus AE details."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                        xmlns:ns2="http://services.sabre.com/STL/v3"
                        version="4.0.0" timeStamp="2026-03-18T10:17:20.892Z" messageID="MSG-000001">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>LHR</Origin>
          <Destination>BAH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _passenger_xml(last, first, pnr, passenger_id, bag_count, ae_blocks):
    extras = ""
    if ae_blocks:
        extras = "<AEDetailsList>" + "".join(ae_blocks) + "</AEDetailsList>"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <BagCount>{bag_count}</BagCount>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
          {extras}
        </PassengerInfo>"""


def _ae(status, qty, emd, currency, total, coupon="1"):
    """One STL AEDetails element, prefixed the way captured responses are."""
    return f"""
            <ns2:AEDetails>
              <ns2:StatusCode>{status}</ns2:StatusCode>
              <ns2:QuantityBought>{qty}</ns2:QuantityBought>
              <ns2:EMDTicketNumber couponNumber="{coupon}">{emd}</ns2:EMDTicketNumber>
              <ns2:PriceDetails>
                <ns2:TotalPrice currencyCode="{currency}">{total}</ns2:TotalPrice>
              </ns2:PriceDetails>
            </ns2:AEDetails>"""


BAG_EMD = "0724000000001"
SEAT_EMD = "0724000000002"
ONLY_AE_EMD = "0724000000003"

LIST_FIXTURE = _list_response(
    _passenger_xml(
        "SMITH", "JOHN", "ABC123", "D381F7B70001", 2,
        [
            _ae("HI", 1, BAG_EMD, "BHD", "25.500", coupon="1"),
            _ae("HD", 2, SEAT_EMD, "USD", "40.00", coupon="2"),
        ],
    )
    + _passenger_xml(
        "JONES", "MARY", "XYZ789", "D381F7B70002", 1,
        [_ae("HI", 1, ONLY_AE_EMD, "BHD", "15.000")],
    )
    + _passenger_xml("NOOR", "ALI", "QWE456", "D381F7B70003", 0, [])
)


def _parser():
    """The real SOAP parser, without opening a Sabre session."""
    return SabreClient.__new__(SabreClient)


def _parse_list(xml_text):
    client = _parser()
    parsed = client._parse_xml(xml_text)
    return client._extract_body(parsed, "GetPassengerListRS")


def _ae_item(status, qty, emd, currency, total):
    return {
        "statusCode": status,
        "quantityBought": qty,
        "emdTicketNumber": emd,
        "currency": currency,
        "totalPrice": total,
    }


def _assert_air_extras(test, passenger, expected, bag_count):
    test.assertEqual(passenger["bagCount"], bag_count)
    test.assertEqual(passenger.get("aeDetails"), expected)


class PassengerListAirExtraTests(unittest.TestCase):
    def test_convert_passenger_list_keeps_emd_price_and_quantity(self):
        raw = _parse_list(LIST_FIXTURE)
        info = raw["PassengerInfoList"]["PassengerInfo"]
        self.assertIn("AEDetailsList", info[0])
        self.assertIn("AEDetailsList", info[1])
        self.assertNotIn("AEDetailsList", info[2])

        doc = convert_passenger_list(raw, "GF", "2006", "2026-03-18", "LHR")
        by_pnr = {p["pnr"]: p for p in doc["passengers"]}

        _assert_air_extras(self, by_pnr["ABC123"], [
            _ae_item("HI", 1, BAG_EMD, "BHD", "25.500"),
            _ae_item("HD", 2, SEAT_EMD, "USD", "40.00"),
        ], bag_count=2)
        # A single AEDetails child is a dict after xmltodict, not a list.
        _assert_air_extras(self, by_pnr["XYZ789"], [
            _ae_item("HI", 1, ONLY_AE_EMD, "BHD", "15.000"),
        ], bag_count=1)
        _assert_air_extras(self, by_pnr["QWE456"], [], bag_count=0)

    def test_ae_display_call_survives_merge_into_stored_manifest(self):
        """Booked list returns the passenger first. The later AE call carries the extras.

        Display-code order in the feeder is Booked, CheckedIn, NoShowOFL, then AE.
        A passenger already stored from the booked call must still pick up
        AEDetailsList from the AE response, and a passenger who appears only
        on that AE response must be stored with the same fields.
        """
        booked = _list_response(
            _passenger_xml("SMITH", "JOHN", "ABC123", "D381F7B70001", 2, [])
        )
        empty = _list_response("")
        ae_call = _list_response(
            _passenger_xml(
                "SMITH", "JOHN", "ABC123", "D381F7B70001", 2,
                [_ae("HI", 1, BAG_EMD, "BHD", "25.500")],
            )
            + _passenger_xml(
                "JONES", "MARY", "XYZ789", "D381F7B70002", 1,
                [_ae("HD", 1, ONLY_AE_EMD, "USD", "15.000")],
            )
        )
        bodies = {
            templates.DISPLAY_CODES_BOOKED: booked,
            templates.DISPLAY_CODES_CHECKEDIN: empty,
            templates.DISPLAY_CODES_NOSHOW_OFL: empty,
            templates.DISPLAY_CODES_ALL: ae_call,
        }
        seen_codes = []

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_flight_status(self, *args, **kwargs):
                raise SabreError("not under test")

            def get_reservations(self, *args, **kwargs):
                raise SabreError("not under test")

            def get_trip_report(self, *args, **kwargs):
                raise SabreError("not under test")

            def verify_flight_details(self, *args, **kwargs):
                raise SabreError("not under test")

            def get_passenger_list(self, airline, flight_number, departure_date, origin,
                                   display_codes=None):
                seen_codes.append(display_codes)
                xml_text = bodies[display_codes]
                meta = {
                    "requestXml": templates.PASSENGER_LIST.format(
                        cpaid="placeholder",
                        conversation_id="placeholder",
                        message_id="placeholder",
                        timestamp="2026-03-18T00:00:00",
                        token="placeholder",
                        airline=airline,
                        flight_number=flight_number,
                        departure_date=departure_date,
                        origin=origin,
                        display_codes=display_codes,
                    ),
                    "httpStatus": 200,
                    "durationMs": 1,
                    "sessionToken": None,
                    "conversationId": "placeholder",
                }
                return _parse_list(xml_text), xml_text, meta

        captured = {}

        def _store_passenger_list(doc):
            captured["passenger_list"] = doc
            return "id"

        def _store_snapshot(**kwargs):
            captured["snapshot"] = kwargs["data"]
            return "snap", "chk", True

        def _store_raw_request(**kwargs):
            return "req"

        with patch("backend.feeder.runner.SabreClient", _Client), \
                patch("backend.feeder.storage.store_passenger_list", _store_passenger_list), \
                patch("backend.feeder.storage.store_snapshot", _store_snapshot), \
                patch("backend.feeder.storage.store_raw_request", _store_raw_request), \
                patch("backend.feeder.storage.update_flight_state", lambda *args, **kwargs: None), \
                patch("backend.feeder.storage.store_flight_status", lambda doc: None):
            run_feeder([{
                "airline": "GF",
                "flightNumber": "2006",
                "origin": "LHR",
                "departureDate": "2026-03-18",
                "departureDateTime": "2026-03-18T21:50:00",
                "flightSequenceNumber": 1,
            }])

        self.assertIn(templates.DISPLAY_CODES_ALL, seen_codes)
        ae_request = [
            code for code in seen_codes if code == templates.DISPLAY_CODES_ALL
        ]
        self.assertIn("<v4:DisplayCode>AE</v4:DisplayCode>", ae_request[0])

        stored = captured["passenger_list"]
        self.assertIs(stored, captured["snapshot"])
        by_pnr = {p["pnr"]: p for p in stored["passengers"]}
        _assert_air_extras(self, by_pnr["ABC123"], [
            _ae_item("HI", 1, BAG_EMD, "BHD", "25.500"),
        ], bag_count=2)
        _assert_air_extras(self, by_pnr["XYZ789"], [
            _ae_item("HD", 1, ONLY_AE_EMD, "USD", "15.000"),
        ], bag_count=1)

    def test_passenger_api_returns_stored_air_extras_not_raw(self):
        raw = _parse_list(LIST_FIXTURE)
        doc = convert_passenger_list(raw, "GF", "2006", "2026-03-18", "LHR")
        db = _MemoryDb({"passenger_list": [doc], "reservations": []})

        with patch("backend.api.routes.passengers.get_db", return_value=db):
            listed = get_passengers(
                "2006", origin="LHR", date="2026-03-18", snapshot_sequence=None,
            )
            by_pnr_route = get_passenger_by_pnr("2006", "ABC123", date="2026-03-18")

        self.assertNotIn("_raw", listed)
        by_pnr = {p["pnr"]: p for p in listed["passengers"]}
        _assert_air_extras(self, by_pnr["ABC123"], [
            _ae_item("HI", 1, BAG_EMD, "BHD", "25.500"),
            _ae_item("HD", 2, SEAT_EMD, "USD", "40.00"),
        ], bag_count=2)
        _assert_air_extras(self, by_pnr["QWE456"], [], bag_count=0)
        _assert_air_extras(self, by_pnr_route["passengers"][0], [
            _ae_item("HI", 1, BAG_EMD, "BHD", "25.500"),
            _ae_item("HD", 2, SEAT_EMD, "USD", "40.00"),
        ], bag_count=2)


def _data_response():
    """GetPassengerDataRS segment AEDetails, same AEDetails.ACS shape as the list."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4"
                            xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>JOHN</LastName>
          <FirstName>M</FirstName>
          <PassengerID>B7FCF1EE0001</PassengerID>
          <PNRLocator nameAssociationID="1">OLRKTG</PNRLocator>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <Airline>GF</Airline>
              <Flight>2006</Flight>
              <Origin>LHR</Origin>
              <Destination>BAH</Destination>
              <BagCount>1</BagCount>
              <AEDetailsList>
                <ns2:AEDetails>
                  <ns2:ItemID>5</ns2:ItemID>
                  <ns2:ATPCOGroupCode>BG</ns2:ATPCOGroupCode>
                  <ns2:StatusCode>HD</ns2:StatusCode>
                  <ns2:UsedEMD>false</ns2:UsedEMD>
                  <ns2:QuantityBought>1</ns2:QuantityBought>
                  <ns2:EMDTicketNumber couponNumber="1">0724000000004</ns2:EMDTicketNumber>
                  <ns2:PriceDetails>
                    <ns2:TotalPrice currencyCode="USD">30.00</ns2:TotalPrice>
                  </ns2:PriceDetails>
                </ns2:AEDetails>
              </AEDetailsList>
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


class PassengerDataAirExtraTests(unittest.TestCase):
    def test_convert_passenger_data_keeps_bought_quantity_price_and_emd(self):
        client = _parser()
        parsed = client._parse_xml(_data_response())
        raw = client._extract_body(parsed, "GetPassengerDataRS")
        doc = convert_passenger_data(raw, "GF", "2006", "2026-03-18", "LHR")
        ae = doc["passengers"][0]["itinerary"][0]["aeDetails"][0]
        self.assertEqual(ae["statusCode"], "HD")
        self.assertEqual(ae["quantity"], 1)
        self.assertEqual(ae["price"], "30.00")
        self.assertEqual(ae["currency"], "USD")
        self.assertEqual(ae["emdTicketNumber"], "0724000000004")
        self.assertEqual(doc["passengers"][0]["itinerary"][0]["bagCount"], 1)

    def test_passenger_detail_route_returns_emd_and_price(self):
        client = _parser()
        parsed = client._parse_xml(_data_response())
        raw = client._extract_body(parsed, "GetPassengerDataRS")
        manifest = {
            "flightNumber": "2006",
            "departureDate": "2026-03-18",
            "passengers": [{
                "pnr": "OLRKTG",
                "lastName": "JOHN",
                "firstName": "M",
            }],
        }
        db = _MemoryDb({"passenger_list": [manifest], "reservations": []})

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_passenger_data(self, **kwargs):
                return raw, "<xml/>", {}

        with patch("backend.api.routes.passengers.get_db", return_value=db), \
                patch("backend.api.routes.passengers.SabreClient", _Client):
            result = get_passenger_detail(
                "2006", "OLRKTG", origin="LHR", date="2026-03-18", airline="GF",
            )

        ae = result["passengers"][0]["itinerary"][0]["aeDetails"][0]
        self.assertEqual(ae["quantity"], 1)
        self.assertEqual(ae["price"], "30.00")
        self.assertEqual(ae["currency"], "USD")
        self.assertEqual(ae["emdTicketNumber"], "0724000000004")


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None):
        for doc in self.docs:
            if not _query_matches(doc, query):
                continue
            return copy.deepcopy(doc)
        return None


class _MemoryDb:
    def __init__(self, collections):
        self._collections = {name: _Coll(docs) for name, docs in collections.items()}

    def __getitem__(self, name):
        return self._collections.get(name, _Coll([]))


def _query_matches(doc, query):
    for key, expected in query.items():
        if key == "passengers.pnr":
            if not any(p.get("pnr") == expected for p in doc.get("passengers", [])):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


if __name__ == "__main__":
    unittest.main()
