"""Ancillary product identity from Sabre GetPassengerData.

The purchases record must keep ATPCOSubCode and the schema Description
element. Group code alone is a product family: two services in group BG
are different products when their subcodes differ.

These tests fail while convert_passenger_data drops those fields and the
detail route returns that record. Quantity and price field names are a
separate mapping and are not asserted.

Contract for each itinerary aeDetails item:
  subCode      — text of ATPCOSubCode ("" when the element is absent)
  description  — text of Description ("" when the element is absent)
"""

import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import passengers  # noqa: E402
from backend.feeder.converter import convert_passenger_data  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLES = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
)
BAGGAGE_SAMPLE = SAMPLES / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml"
SEAT_SAMPLE = SAMPLES / "GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml"

# Fields the purchases table renders today. Two products that match on
# every one of these cannot be told apart without the subcode.
_DISPLAYED = ("groupCode", "statusCode", "quantity", "price", "currency", "usedEMD")


def _response_xml(path: Path) -> str:
    """SOAP response envelope from a vendor sample, without the security header."""
    text = path.read_text(encoding="utf-8")
    marker = "<!--Response-->"
    start = text.find(marker)
    assert start != -1, f"{path.name} has no response envelope"
    xml = text[start + len(marker):]
    end = xml.rfind("</soap-env:Envelope>")
    assert end != -1, f"{path.name} response envelope is truncated"
    xml = xml[: end + len("</soap-env:Envelope>")]
    xml = re.sub(
        r"<wsse:Security\b[^>]*>.*?</wsse:Security>",
        "",
        xml,
        count=1,
        flags=re.DOTALL,
    )
    return xml


def _parse_passenger_data(xml: str) -> dict:
    """Same parse the Sabre client uses before convert_passenger_data."""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "GetPassengerDataRS")


def _ancillaries(doc: dict) -> list[dict]:
    items = []
    for passenger in doc.get("passengers", []):
        for segment in passenger.get("itinerary", []):
            items.extend(segment.get("aeDetails", []))
    return items


def _with_second_baggage_product(xml: str) -> str:
    """Two BG purchases that share every field the table currently shows.

    The vendor baggage item keeps subcode 123. The added item is subcode
    0C3 with a different Description. Status, EMD flag, and the price block
    are copied unchanged so quantity and price stay identical after the
    existing mapping.
    """
    start = xml.find("<ns2:AEDetails>")
    end = xml.find("</ns2:AEDetails>")
    assert start != -1 and end != -1
    block_end = end + len("</ns2:AEDetails>")
    original = xml[start:block_end]
    first = original.replace(
        "<ns2:ATPCOSubCode>123</ns2:ATPCOSubCode>",
        "<ns2:ATPCOSubCode>123</ns2:ATPCOSubCode>"
        "<ns2:Description>UPTO50LB 23KG BAGGAGE</ns2:Description>",
        1,
    )
    second = (
        original
        .replace("<ns2:ItemID>5</ns2:ItemID>", "<ns2:ItemID>6</ns2:ItemID>", 1)
        .replace(
            "<ns2:ATPCOSubCode>123</ns2:ATPCOSubCode>",
            "<ns2:ATPCOSubCode>0C3</ns2:ATPCOSubCode>"
            "<ns2:Description>PREPAID BAG 23KG</ns2:Description>",
            1,
        )
    )
    return xml[:start] + first + second + xml[block_end:]


def test_baggage_sample_keeps_subcode_123():
    body = _parse_passenger_data(_response_xml(BAGGAGE_SAMPLE))
    doc = convert_passenger_data(body, "AZ", "317", "2016-03-17", "CDG")
    items = _ancillaries(doc)
    assert len(items) == 1
    assert items[0]["groupCode"] == "BG"
    assert items[0]["subCode"] == "123"
    assert items[0]["description"] == ""


def test_seat_sample_keeps_subcode_0b5():
    body = _parse_passenger_data(_response_xml(SEAT_SAMPLE))
    doc = convert_passenger_data(body, "EY", "1", "2018-05-05", "AUH")
    items = _ancillaries(doc)
    assert len(items) == 1
    assert items[0]["groupCode"] == "SA"
    assert items[0]["subCode"] == "0B5"
    assert items[0]["description"] == ""


def test_same_group_products_stay_distinct():
    xml = _with_second_baggage_product(_response_xml(BAGGAGE_SAMPLE))
    body = _parse_passenger_data(xml)
    doc = convert_passenger_data(body, "AZ", "317", "2016-03-17", "CDG")
    items = _ancillaries(doc)
    by_sub = {item["subCode"]: item for item in items}
    assert set(by_sub) == {"123", "0C3"}
    assert by_sub["123"]["description"] == "UPTO50LB 23KG BAGGAGE"
    assert by_sub["0C3"]["description"] == "PREPAID BAG 23KG"
    assert by_sub["123"]["groupCode"] == by_sub["0C3"]["groupCode"] == "BG"
    displayed = tuple(tuple(item[field] for field in _DISPLAYED) for item in items)
    assert displayed[0] == displayed[1]


def test_detail_route_returns_subcode_for_the_requested_pnr(monkeypatch):
    xml = _with_second_baggage_product(_response_xml(BAGGAGE_SAMPLE))
    body = _parse_passenger_data(xml)
    stored = {
        "flightNumber": "317",
        "origin": "CDG",
        "departureDate": "2016-03-17",
        "passengers": [
            {"pnr": "OLRKTG", "lastName": "JOHN", "firstName": "M"},
            {"pnr": "OTHER1", "lastName": "OTHER", "firstName": "NAME"},
        ],
    }

    class _Collection:
        def find_one(self, query, sort=None):
            assert query["flightNumber"] == "317"
            assert query["passengers.pnr"] == "OLRKTG"
            assert query["departureDate"] == "2016-03-17"
            return stored

    class _DB:
        def __getitem__(self, name):
            assert name == "passenger_list"
            return _Collection()

    seen = {}

    class _Sabre:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_passenger_data(self, **kwargs):
            seen.update(kwargs)
            return body, "", {}

    monkeypatch.setattr(passengers, "get_db", lambda: _DB())
    monkeypatch.setattr(passengers, "SabreClient", _Sabre)

    app = FastAPI()
    app.include_router(passengers.router)
    response = TestClient(app).get(
        "/flights/317/passengers/OLRKTG/detail",
        params={"origin": "CDG", "date": "2016-03-17"},
    )
    assert response.status_code == 200, response.text
    assert seen["pnr"] == "OLRKTG"
    assert seen["last_name"] == "JOHN"
    assert seen["first_name"] == "M"
    assert seen["flight_number"] == "317"
    assert seen["origin"] == "CDG"
    assert seen["departure_date"] == "2016-03-17"

    payload = response.json()
    items = _ancillaries(payload)
    by_sub = {item["subCode"]: item for item in items}
    assert set(by_sub) == {"123", "0C3"}
    assert by_sub["123"]["groupCode"] == "BG"
    assert by_sub["123"]["description"] == "UPTO50LB 23KG BAGGAGE"
    assert by_sub["0C3"]["groupCode"] == "BG"
    assert by_sub["0C3"]["description"] == "PREPAID BAG 23KG"
    assert payload["passengers"][0]["pnr"] == "OLRKTG"
