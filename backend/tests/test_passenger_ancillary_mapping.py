"""Live passenger detail must keep Sabre ancillary purchases.

GetPassengerData AEDetails.ACS records the purchased count in QuantityBought
and the money in PriceDetails/TotalPrice (currencyCode attribute). These tests
parse vendor response envelopes with SabreClient._parse_xml and _extract_body,
the same two steps get_passenger_data uses after the SOAP post, then convert
that body with convert_passenger_data. The detail route test returns that
converted document with no extra rewrite.

They fail while quantity is read from the absent Quantity element and price
is read from the absent PriceDetails Amount and Currency elements.

Not covered here, because they do not share this field mismatch:
- convert_passenger_list never copies AEDetails onto the passenger document.
- convert_reservations reads AncillaryServices.PNRB, which has its own Quantity
  element, not AEDetails.ACS.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

_REPO = os.path.join(os.path.dirname(__file__), "..", "..")
_BAGGAGE_SAMPLE = os.path.join(
    _REPO,
    "sabre_docs",
    "GetPassengerDataRQ_v4-0-4_Samples",
    "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml",
)
_SEAT_SAMPLE = os.path.join(
    _REPO,
    "sabre_docs",
    "GetPassengerDataRQ_v4-0-4_Samples",
    "GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml",
)

# Placeholder ticket number. The vendor samples omit EMDTicketNumber.
_EMD_TICKET = "0720000000001"
_EMD_COUPON = "1"


def _response_envelope(path):
    """Return the GetPassengerDataRS SOAP envelope from a vendor sample file.

    The sample files also contain the matching request. Only the response
    envelope is what get_passenger_data parses.
    """
    text = open(path, encoding="utf-8").read()
    marker = "<!--Response-->"
    start = text.index(marker) + len(marker)
    end_tag = "</soap-env:Envelope>"
    end = text.index(end_tag, start) + len(end_tag)
    return text[start:end].strip()


def _body_from_response_xml(xml_text):
    """Run the product's SOAP parse and body extract, skipping the HTTP post."""
    client = SabreClient.__new__(SabreClient)
    parsed = client._parse_xml(xml_text)
    return client._extract_body(parsed, "GetPassengerDataRS")


def _ancillaries(xml_text, airline, flight_number, departure_date, origin):
    raw = _body_from_response_xml(xml_text)
    doc = convert_passenger_data(
        raw, airline, flight_number, departure_date, origin)
    items = []
    for passenger in doc["passengers"]:
        for segment in passenger["itinerary"]:
            items.extend(segment["aeDetails"])
    return items


def _with_emd_ticket(xml_text, ticket_number, coupon_number):
    """Insert an EMDTicketNumber element as defined by EMDTicketNumber.ACS."""
    needle = "<ns2:PriceDetails>"
    element = (
        f'<ns2:EMDTicketNumber couponNumber="{coupon_number}">'
        f"{ticket_number}</ns2:EMDTicketNumber>"
    )
    if needle not in xml_text:
        raise AssertionError("vendor response has no PriceDetails element to anchor")
    return xml_text.replace(needle, element + needle, 1)


def test_baggage_sample_keeps_bought_quantity_and_total_price():
    """Vendor baggage item: QuantityBought 1, TotalPrice 30.00 USD."""
    items = _ancillaries(
        _response_envelope(_BAGGAGE_SAMPLE),
        airline="AZ",
        flight_number="317",
        departure_date="2016-03-17",
        origin="CDG",
    )
    assert len(items) == 1
    item = items[0]
    assert item["itemId"] == "5"
    assert item["groupCode"] == "BG"
    assert item["statusCode"] == "HD"
    assert item["usedEMD"] == "false"
    assert item["quantity"] == 1
    assert item["quantityUsed"] == 0
    assert item["price"] == "30.00"
    assert item["currency"] == "USD"
    assert item["emdTicketNumber"] == ""


def test_seat_sample_keeps_bought_quantity_when_total_price_is_zero():
    """Vendor seat item: QuantityBought 1, TotalPrice 0 USD. Zero is a price."""
    items = _ancillaries(
        _response_envelope(_SEAT_SAMPLE),
        airline="EY",
        flight_number="1",
        departure_date="2018-05-04",
        origin="AUH",
    )
    assert len(items) == 1
    item = items[0]
    assert item["itemId"] == "F"
    assert item["groupCode"] == "SA"
    assert item["statusCode"] == "HK"
    assert item["usedEMD"] == "true"
    assert item["quantity"] == 1
    assert item["quantityUsed"] == 0
    assert item["price"] == "0"
    assert item["currency"] == "USD"
    assert item["emdTicketNumber"] == ""


def test_emd_ticket_number_is_kept_when_it_carries_a_coupon_attribute():
    """EMDTicketNumber.ACS is text plus an optional couponNumber attribute."""
    xml_text = _with_emd_ticket(
        _response_envelope(_BAGGAGE_SAMPLE), _EMD_TICKET, _EMD_COUPON)
    items = _ancillaries(
        xml_text,
        airline="AZ",
        flight_number="317",
        departure_date="2016-03-17",
        origin="CDG",
    )
    assert len(items) == 1
    item = items[0]
    assert item["quantity"] == 1
    assert item["price"] == "30.00"
    assert item["currency"] == "USD"
    assert item["emdTicketNumber"] == _EMD_TICKET
    assert item["emdCoupon"] == _EMD_COUPON


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "317"
        assert query["passengers.pnr"] == "OLRKTG"
        assert query["departureDate"] == "2016-03-17"
        return {
            "passengers": [
                {"pnr": "OLRKTG", "lastName": "JOHN", "firstName": "M"},
            ]
        }


class _Database:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def test_passenger_detail_route_returns_priced_ancillary(monkeypatch):
    """GET detail returns convert_passenger_data output for the Sabre body."""
    xml_text = _with_emd_ticket(
        _response_envelope(_BAGGAGE_SAMPLE), _EMD_TICKET, _EMD_COUPON)
    sabre_body = _body_from_response_xml(xml_text)

    class _SabreSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_passenger_data(self, **kwargs):
            assert kwargs["airline"] == "AZ"
            assert kwargs["flight_number"] == "317"
            assert kwargs["departure_date"] == "2016-03-17"
            assert kwargs["origin"] == "CDG"
            assert kwargs["last_name"] == "JOHN"
            assert kwargs["first_name"] == "M"
            assert kwargs["pnr"] == "OLRKTG"
            return sabre_body, "", {}

    monkeypatch.setattr(
        "backend.api.routes.passengers.SabreClient", _SabreSession)
    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db", lambda: _Database())

    result = get_passenger_detail(
        "317",
        "OLRKTG",
        origin="CDG",
        date="2016-03-17",
        airline="AZ",
    )
    items = result["passengers"][0]["itinerary"][0]["aeDetails"]
    assert len(items) == 1
    item = items[0]
    assert item["quantity"] == 1
    assert item["quantityUsed"] == 0
    assert item["price"] == "30.00"
    assert item["currency"] == "USD"
    assert item["emdTicketNumber"] == _EMD_TICKET
    assert item["emdCoupon"] == _EMD_COUPON
