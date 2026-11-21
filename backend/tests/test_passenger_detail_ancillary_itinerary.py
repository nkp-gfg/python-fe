"""Passenger detail must keep Sabre AncillaryItinInfoList baggage products.

GetPassengerDataRS places applicable ancillaries on AncillaryItinInfoList, a
sibling of PassengerDataResponseList. A Gulf Air sector can return the
hard-matched free baggage allowance and priced excess-baggage products there
while the passenger itinerary segment has no AEDetailsList.

These tests parse a SOAP envelope with SabreClient._parse_xml and
_extract_body (the same functions get_passenger_data uses) and then run
convert_passenger_data and the passenger detail route. They fail until each
passenger in that document carries ancillaryItinerary.

Contract for every object in passenger["ancillaryItinerary"]:
  origin, destination: str
  commercialName, groupCode, subCode: str
  hardMatch: bool          # XML "true"/"false", not bool("false")
  freeAllowance: bool      # true only for the free allowance, never for a priced excess product
  baggageWeightLimits: list[{"weight": str, "unit": str}]
                           # every WeightInfo/BagWeightLimit, KG and LB alike; [] when absent
  totalPrice: {"amount": str, "currency": str} | None
                           # TotalPrice text and currencyCode; None when Sabre sent no TotalPrice

Segment AEDetails, when present, stay on itinerary[].aeDetails. The free
allowance still has to appear in ancillaryItinerary when the segment list is empty.

Not covered here: the React passenger-detail sheet (it only reads
itinerary[].aeDetails and vcrInfo today), and a GetPassengerDataRS that
returns several PassengerDataResponse entries sharing one AncillaryItinInfoList.
The live detail route looks up one PNR and returns that conversion unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient


def _envelope(passenger_xml, ancillary_xml):
    """SOAP body in the shape GetPassengerData returns.

    Ancillary elements use the ns2 prefix from the captured response.
    AncillaryItinInfoList itself is unprefixed. One AncillaryItinInfo element
    stays a dict after xmltodict, which is the captured shape.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>SAMPLE</LastName>
          <FirstName>PAX</FirstName>
          <PassengerID>PAX1</PassengerID>
          <PNRLocator nameAssociationID="1">ABC123</PNRLocator>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <Airline>GF</Airline>
              <Flight>2068</Flight>
              <Origin>DMM</Origin>
              <Destination>MAA</Destination>
              <DepartureDate>2026-03-20</DepartureDate>
              <BookingClass>Y</BookingClass>
              <Cabin>Y</Cabin>
              {passenger_xml}
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
      {ancillary_xml}
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


_FREE_AND_EXCESS = """
<AncillaryItinInfoList>
  <ns2:AncillaryItinInfo>
    <ns2:OwningCarrier>GF</ns2:OwningCarrier>
    <ns2:Origin>DMM</ns2:Origin>
    <ns2:Destination>MAA</ns2:Destination>
    <ns2:AncillaryInfo>
      <ns2:HardMatch>false</ns2:HardMatch>
      <ns2:GroupCode>SA</ns2:GroupCode>
      <ns2:SubCode>0B5</ns2:SubCode>
      <ns2:Descriptions>
        <ns2:CommercialName>STANDARD SEAT</ns2:CommercialName>
      </ns2:Descriptions>
      <ns2:PriceDetails>
        <ns2:TotalPrice currencyCode="OMR">0.000</ns2:TotalPrice>
      </ns2:PriceDetails>
    </ns2:AncillaryInfo>
    <ns2:AncillaryInfo>
      <ns2:HardMatch>true</ns2:HardMatch>
      <ns2:GroupCode>BG</ns2:GroupCode>
      <ns2:SubCode>0DF</ns2:SubCode>
      <ns2:Descriptions>
        <ns2:CommercialName>FREE BAGGAGE ALLOWANCE</ns2:CommercialName>
      </ns2:Descriptions>
      <ns2:BaggageWeightAndSize>
        <ns2:WeightInfo>
          <ns2:BagWeightLimit unit="KG">35</ns2:BagWeightLimit>
        </ns2:WeightInfo>
      </ns2:BaggageWeightAndSize>
    </ns2:AncillaryInfo>
    <ns2:AncillaryInfo>
      <ns2:HardMatch>true</ns2:HardMatch>
      <ns2:GroupCode>BG</ns2:GroupCode>
      <ns2:SubCode>0CW</ns2:SubCode>
      <ns2:Descriptions>
        <ns2:CommercialName>EXCESS BAGGAGE PER 5KG</ns2:CommercialName>
      </ns2:Descriptions>
      <ns2:BaggageWeightAndSize>
        <ns2:WeightInfo>
          <ns2:BagWeightLimit unit="LB">11</ns2:BagWeightLimit>
        </ns2:WeightInfo>
        <ns2:WeightInfo>
          <ns2:BagWeightLimit unit="KG">5</ns2:BagWeightLimit>
        </ns2:WeightInfo>
      </ns2:BaggageWeightAndSize>
      <ns2:PriceDetails>
        <ns2:TotalPrice currencyCode="OMR">27.000</ns2:TotalPrice>
      </ns2:PriceDetails>
    </ns2:AncillaryInfo>
    <ns2:AncillaryInfo>
      <ns2:HardMatch>true</ns2:HardMatch>
      <ns2:GroupCode>BG</ns2:GroupCode>
      <ns2:SubCode>0CW</ns2:SubCode>
      <ns2:Descriptions>
        <ns2:CommercialName>EXCESS BAGGAGE PER 5KG</ns2:CommercialName>
      </ns2:Descriptions>
      <ns2:BaggageWeightAndSize>
        <ns2:WeightInfo>
          <ns2:BagWeightLimit unit="KG">5</ns2:BagWeightLimit>
        </ns2:WeightInfo>
      </ns2:BaggageWeightAndSize>
      <ns2:PriceDetails>
        <ns2:TotalPrice currencyCode="OMR">31.000</ns2:TotalPrice>
      </ns2:PriceDetails>
    </ns2:AncillaryInfo>
  </ns2:AncillaryItinInfo>
</AncillaryItinInfoList>
"""

_FREE_ONLY = """
<AncillaryItinInfoList>
  <ns2:AncillaryItinInfo>
    <ns2:OwningCarrier>GF</ns2:OwningCarrier>
    <ns2:Origin>DMM</ns2:Origin>
    <ns2:Destination>MAA</ns2:Destination>
    <ns2:AncillaryInfo>
      <ns2:HardMatch>true</ns2:HardMatch>
      <ns2:GroupCode>BG</ns2:GroupCode>
      <ns2:SubCode>0DF</ns2:SubCode>
      <ns2:Descriptions>
        <ns2:CommercialName>FREE BAGGAGE ALLOWANCE</ns2:CommercialName>
      </ns2:Descriptions>
      <ns2:BaggageWeightAndSize>
        <ns2:WeightInfo>
          <ns2:BagWeightLimit unit="KG">35</ns2:BagWeightLimit>
        </ns2:WeightInfo>
      </ns2:BaggageWeightAndSize>
    </ns2:AncillaryInfo>
  </ns2:AncillaryItinInfo>
</AncillaryItinInfoList>
"""

_SEGMENT_AE = """
<AEDetailsList>
  <ns2:AEDetails>
    <ns2:ItemID>ITEM1</ns2:ItemID>
    <ns2:ATPCOGroupCode>SA</ns2:ATPCOGroupCode>
    <ns2:StatusCode>HK</ns2:StatusCode>
    <ns2:UsedEMD>true</ns2:UsedEMD>
    <ns2:Quantity>1</ns2:Quantity>
    <ns2:PriceDetails>
      <ns2:Amount>10.00</ns2:Amount>
      <ns2:Currency>OMR</ns2:Currency>
    </ns2:PriceDetails>
  </ns2:AEDetails>
</AEDetailsList>
"""


def _parse_passenger_data(xml_text):
    """Run the SOAP parse path get_passenger_data uses, without a Sabre session."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(object(), parsed, "GetPassengerDataRS")


def _convert(xml_text):
    raw = _parse_passenger_data(xml_text)
    assert raw.get("AncillaryItinInfoList") or "<AncillaryItinInfoList>" not in xml_text, (
        "parser dropped AncillaryItinInfoList before conversion"
    )
    if "<AncillaryItinInfoList>" in xml_text:
        assert raw.get("AncillaryItinInfoList"), (
            "parser dropped AncillaryItinInfoList before conversion"
        )
    return convert_passenger_data(raw, "GF", "2068", "2026-03-20", "DMM")


def _products(doc):
    assert doc["passengers"], "converter returned no passengers"
    passenger = doc["passengers"][0]
    assert "ancillaryItinerary" in passenger, (
        "AncillaryItinInfoList was not copied onto the passenger. "
        f"segment aeDetails={passenger['itinerary'][0]['aeDetails']!r}"
    )
    products = passenger["ancillaryItinerary"]
    assert isinstance(products, list)
    return passenger, products


def _limits(item):
    limits = item["baggageWeightLimits"]
    assert isinstance(limits, list)
    return {(entry["weight"], entry["unit"]) for entry in limits}


def _assert_gulf_air_products(doc):
    passenger, products = _products(doc)
    assert passenger["itinerary"][0]["aeDetails"] == []

    allowance = [p for p in products if p["commercialName"] == "FREE BAGGAGE ALLOWANCE"]
    excess = [p for p in products if p["commercialName"] == "EXCESS BAGGAGE PER 5KG"]
    seats = [p for p in products if p["commercialName"] == "STANDARD SEAT"]
    assert len(allowance) == 1
    assert len(excess) == 2
    assert len(seats) == 1

    free = allowance[0]
    assert free["freeAllowance"] is True
    assert free["groupCode"] == "BG"
    assert free["subCode"] == "0DF"
    assert free["hardMatch"] is True
    assert free["origin"] == "DMM"
    assert free["destination"] == "MAA"
    assert _limits(free) == {("35", "KG")}
    assert free["totalPrice"] is None

    priced = {(p["totalPrice"]["amount"], p["totalPrice"]["currency"]) for p in excess}
    assert priced == {("27.000", "OMR"), ("31.000", "OMR")}
    for item in excess:
        assert item["freeAllowance"] is False
        assert item is not free
        assert item["groupCode"] == "BG"
        assert item["subCode"] == "0CW"
        assert item["hardMatch"] is True
        assert item["origin"] == "DMM"
        assert item["destination"] == "MAA"
        assert ("5", "KG") in _limits(item)
    heavier = next(p for p in excess if p["totalPrice"]["amount"] == "27.000")
    assert _limits(heavier) == {("11", "LB"), ("5", "KG")}

    seat = seats[0]
    assert seat["freeAllowance"] is False
    assert seat["groupCode"] == "SA"
    assert seat["subCode"] == "0B5"
    assert seat["hardMatch"] is False
    assert _limits(seat) == set()
    assert seat["totalPrice"] == {"amount": "0.000", "currency": "OMR"}


def test_ancillary_itinerary_allowance_survives_conversion():
    xml = _envelope("", _FREE_AND_EXCESS)
    doc = _convert(xml)
    _assert_gulf_air_products(doc)
    print("  PASS: test_ancillary_itinerary_allowance_survives_conversion")


def test_single_ancillary_info_element_is_not_skipped():
    """xmltodict leaves a single AncillaryInfo as a dict, not a one-item list."""
    xml = _envelope("", _FREE_ONLY)
    raw = _parse_passenger_data(xml)
    info = raw["AncillaryItinInfoList"]["ns2:AncillaryItinInfo"]["ns2:AncillaryInfo"]
    assert isinstance(info, dict), info

    doc = _convert(xml)
    _, products = _products(doc)
    assert len(products) == 1
    free = products[0]
    assert free["commercialName"] == "FREE BAGGAGE ALLOWANCE"
    assert free["freeAllowance"] is True
    assert free["hardMatch"] is True
    assert free["groupCode"] == "BG"
    assert free["subCode"] == "0DF"
    assert _limits(free) == {("35", "KG")}
    assert free["totalPrice"] is None
    print("  PASS: test_single_ancillary_info_element_is_not_skipped")


def test_segment_ae_details_kept_beside_itinerary_allowance():
    xml = _envelope(_SEGMENT_AE, _FREE_ONLY)
    doc = _convert(xml)
    passenger, products = _products(doc)
    ae = passenger["itinerary"][0]["aeDetails"]
    assert len(ae) == 1
    assert ae[0]["itemId"] == "ITEM1"
    assert ae[0]["groupCode"] == "SA"
    assert ae[0]["statusCode"] == "HK"
    assert len(products) == 1
    assert products[0]["commercialName"] == "FREE BAGGAGE ALLOWANCE"
    assert products[0]["freeAllowance"] is True
    assert _limits(products[0]) == {("35", "KG")}
    print("  PASS: test_segment_ae_details_kept_beside_itinerary_allowance")


def test_missing_ancillary_itinerary_is_an_empty_list():
    xml = _envelope("", "")
    doc = _convert(xml)
    _, products = _products(doc)
    assert products == []
    print("  PASS: test_missing_ancillary_itinerary_is_an_empty_list")


class _PassengerList:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "2068"
        assert query["passengers.pnr"] == "ABC123"
        assert query["departureDate"] == "2026-03-20"
        return self._doc


class _DB:
    def __init__(self, doc):
        self._lists = _PassengerList(doc)

    def __getitem__(self, name):
        assert name == "passenger_list"
        return self._lists


class _Sabre:
    def __init__(self, raw):
        self._raw = raw
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, **kwargs):
        self.calls.append(kwargs)
        return self._raw, "<GetPassengerDataRS/>", {}


def test_detail_route_returns_ancillary_itinerary_for_requested_pnr():
    xml = _envelope("", _FREE_AND_EXCESS)
    raw = _parse_passenger_data(xml)
    stored = {
        "passengers": [
            {"pnr": "OTHER1", "lastName": "OTHER", "firstName": "PERSON"},
            {"pnr": "ABC123", "lastName": "SAMPLE", "firstName": "PAX"},
        ]
    }
    client = _Sabre(raw)

    import backend.api.routes.passengers as passengers

    original_db = passengers.get_db
    original_client = passengers.SabreClient
    passengers.get_db = lambda: _DB(stored)
    passengers.SabreClient = lambda: client
    try:
        result = get_passenger_detail(
            "2068",
            "ABC123",
            origin="DMM",
            date="2026-03-20",
            airline="GF",
        )
    finally:
        passengers.get_db = original_db
        passengers.SabreClient = original_client

    assert client.calls == [{
        "airline": "GF",
        "flight_number": "2068",
        "departure_date": "2026-03-20",
        "origin": "DMM",
        "last_name": "SAMPLE",
        "first_name": "PAX",
        "pnr": "ABC123",
    }]
    _assert_gulf_air_products(result)
    print("  PASS: test_detail_route_returns_ancillary_itinerary_for_requested_pnr")


if __name__ == "__main__":
    print("Running passenger detail ancillary itinerary tests...")
    failed = 0
    tests = [
        test_ancillary_itinerary_allowance_survives_conversion,
        test_single_ancillary_info_element_is_not_skipped,
        test_segment_ae_details_kept_beside_itinerary_allowance,
        test_missing_ancillary_itinerary_is_an_empty_list,
        test_detail_route_returns_ancillary_itinerary_for_requested_pnr,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {test.__name__}: {exc}")
    if failed:
        print(f"{failed} TEST(S) FAILED")
        sys.exit(1)
    print("ALL TESTS PASS")
