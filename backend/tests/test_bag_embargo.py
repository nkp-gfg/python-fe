"""Passenger detail must treat a non-zero Sabre BagEmbargo short as embargoed.

Sabre types BagEmbargo as xsd:short. GetPassengerData responses put that
number in the element (the vendor sample uses 2 on connecting segments).
The detail route is supposed to report bagEmbargo true for any non-zero
code and to keep the original number on bagEmbargoCode. A missing element
is not embargoed and bagEmbargoCode is null. Zero stays clear, with the
code preserved as 0.

These tests call get_passenger_detail. The SOAP body is parsed by
SabreClient and mapped by convert_passenger_data. They fail while a
numeric code is compared with the text "true".

convert_passenger_list is not covered. It copies a shorter baggage route
and never reads BagEmbargo, so it does not share this comparison.
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

_SAMPLE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sabre_docs",
    "GetPassengerDataRQ_v4-0-4_Samples",
    "GetPassengerDataRQ_v4-0-1_SamplePayload_4-2-4-1.xml",
)

# XML text of BagEmbargo, and the detail the route must return.
# None means the element is omitted.
_CODES = (
    (None, False, None),
    ("0", False, 0),
    ("1", True, 1),
    ("2", True, 2),
    ("-1", True, -1),
    ("32767", True, 32767),
)


def _vendor_response_xml():
    """SOAP response envelope from the vendor sample, not the request half."""
    with open(_SAMPLE, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("<!--Response:-->")
    start = text.index("<soap-env:Envelope", start)
    end = text.index("</soap-env:Envelope>", start) + len("</soap-env:Envelope>")
    return text[start:end]


def _response_xml(embargo_texts):
    routes = []
    for index, embargo in enumerate(embargo_texts, start=1):
        embargo_xml = ""
        if embargo is not None:
            embargo_xml = f"<ns2:BagEmbargo>{embargo}</ns2:BagEmbargo>"
        routes.append(
            f"""
            <ns2:BaggageRoute>
                <ns2:SegmentID>{index}</ns2:SegmentID>
                <ns2:Airline>GF</ns2:Airline>
                <ns2:Flight>100</ns2:Flight>
                <ns2:Origin>BAH</ns2:Origin>
                <ns2:Destination>DXB</ns2:Destination>
                <ns2:DepartureDate>2026-03-19</ns2:DepartureDate>
                <ns2:SegmentStatus>HK</ns2:SegmentStatus>
                {embargo_xml}
            </ns2:BaggageRoute>"""
        )
    body = "\n".join(routes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>SMITH</LastName>
          <FirstName>JANE</FirstName>
          <PassengerID>P1</PassengerID>
          <PNRLocator nameAssociationID="1">ABC123</PNRLocator>
          <BaggageRouteList>
            {body}
          </BaggageRouteList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _bag_embargo_values(node):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "BagEmbargo" or key.endswith(":BagEmbargo"):
                found.append(value)
            else:
                found.extend(_bag_embargo_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_bag_embargo_values(item))
    return found


class _SoapResponse:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class _PassengerList:
    def __init__(self, passenger):
        self._passenger = passenger

    def find_one(self, query, sort=None):
        wanted = query.get("passengers.pnr")
        if wanted != self._passenger["pnr"]:
            return None
        if query.get("flightNumber") != self._passenger["flightNumber"]:
            return None
        return {
            "flightNumber": self._passenger["flightNumber"],
            "departureDate": self._passenger["date"],
            "origin": self._passenger["origin"],
            "passengers": [
                {
                    "pnr": self._passenger["pnr"],
                    "lastName": self._passenger["lastName"],
                    "firstName": self._passenger["firstName"],
                }
            ],
        }


class _Db:
    def __init__(self, passenger):
        self._lists = _PassengerList(passenger)

    def __getitem__(self, name):
        if name != "passenger_list":
            raise KeyError(name)
        return self._lists


def _detail(monkeypatch, response_xml, passenger):
    """Run the detail route against response_xml parsed by SabreClient."""
    monkeypatch.setenv("SABRE_BASE_URL", "http://sabre.invalid/soap")
    monkeypatch.setenv("SABRE_CPAID", "TEST")
    monkeypatch.setenv("SABRE_USERNAME", "placeholder-user")
    monkeypatch.setenv("SABRE_PASSWORD", "placeholder")
    monkeypatch.setenv("SABRE_PSEUDO_CITY_CODE", "TEST")
    monkeypatch.setenv("SABRE_ORGANIZATION", "TEST")
    monkeypatch.setenv("SABRE_DOMAIN", "TEST")
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0)

    seen = []
    real_convert = convert_passenger_data

    def _record(raw_data, airline, flight_number, departure_date, origin):
        seen.append(raw_data)
        return real_convert(raw_data, airline, flight_number, departure_date, origin)

    monkeypatch.setattr("backend.api.routes.passengers.convert_passenger_data", _record)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: _Db(passenger))

    def _post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        if action == "GetPassengerDataRQ":
            text = response_xml
        elif action == "SessionCreateRQ":
            text = (
                "<wsse:BinarySecurityToken>"
                "placeholder-session-token"
                "</wsse:BinarySecurityToken>"
            )
        else:
            text = "<ok/>"
        return _SoapResponse(text)

    monkeypatch.setattr(requests, "post", _post)
    result = get_passenger_detail(
        passenger["flightNumber"],
        passenger["pnr"],
        origin=passenger["origin"],
        date=passenger["date"],
        airline=passenger["airline"],
    )
    assert seen, "detail route did not hand the Sabre body to convert_passenger_data"
    parsed_codes = _bag_embargo_values(seen[0])
    wire = json.loads(json.dumps(result))
    return wire, parsed_codes


def _assert_routes(routes, expected):
    assert len(routes) == len(expected)
    for route, (embargoed, _code) in zip(routes, expected):
        assert route["bagEmbargo"] is embargoed
    for route, (_embargoed, code) in zip(routes, expected):
        assert "bagEmbargoCode" in route
        if code is None:
            assert route["bagEmbargoCode"] is None
        else:
            assert route["bagEmbargoCode"] == code
            assert type(route["bagEmbargoCode"]) is int


def test_detail_route_reports_nonzero_bag_embargo_short(monkeypatch):
    xml = _response_xml([code for code, _, _ in _CODES])
    passenger = {
        "flightNumber": "100",
        "pnr": "ABC123",
        "origin": "BAH",
        "date": "2026-03-19",
        "airline": "GF",
        "lastName": "SMITH",
        "firstName": "JANE",
    }
    wire, parsed_codes = _detail(monkeypatch, xml, passenger)
    assert parsed_codes == [code for code, _, _ in _CODES if code is not None]
    routes = wire["passengers"][0]["baggageRoutes"]
    _assert_routes(routes, [(flag, number) for _, flag, number in _CODES])


def test_vendor_sample_connecting_bag_embargo_reaches_detail(monkeypatch):
    xml = _vendor_response_xml()
    passenger = {
        "flightNumber": "129",
        "pnr": "DXXRYZ",
        "origin": "ANF",
        "date": "2016-07-04",
        "airline": "LA",
        "lastName": "HDGLAX",
        "firstName": "VRDJ",
    }
    wire, parsed_codes = _detail(monkeypatch, xml, passenger)
    assert parsed_codes == ["2", "2", "2"]
    passenger_doc = wire["passengers"][0]
    assert passenger_doc["pnr"] == "DXXRYZ"
    assert passenger_doc["lastName"] == "HDGLAX"
    routes = passenger_doc["baggageRoutes"]
    assert [(r["origin"], r["destination"]) for r in routes] == [
        ("ANF", "SCL"),
        ("SCL", "LIM"),
        ("LIM", "JFK"),
        ("JFK", "MIA"),
    ]
    _assert_routes(
        routes,
        [
            (False, None),
            (True, 2),
            (True, 2),
            (True, 2),
        ],
    )
