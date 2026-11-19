"""Late check-in on the live passenger detail path.

Sabre's baggage-route LateCheckin element is a string. The GetPassengerData
sample sends Y when the bag will not make the flight. GET
/flights/{flight}/passengers/{pnr}/detail must tell the caller that bag is
a late check-in, and it must keep the Sabre text on the route.

These tests fail while convert_passenger_data sets lateCheckin only for the
exact text "true". They pass when, for each baggage route:

* lateCheckin is true for the Sabre texts Y, true, and 1
* lateCheckin is false when the element is absent, empty, N, false, or 0
* lateCheckinCode is the text the SOAP parser handed the converter
  (empty when the element was absent or empty)

The passenger-list converter is not covered. It drops LateCheckin instead of
coercing it, so it does not share this comparison.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
AIRLINE = "GF"
PNR = "ABC123"
LAST_NAME = "CHARLES"
FIRST_NAME = "JANE"

# Placeholder only. The session parser looks for this element; it is not a credential.
_SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope>
  <soap-env:Header>
    <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""


class _HttpResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _baggage_route_xml(segment_id, late_text, include_element, namespaced):
    """One BaggageRoute. late_text is the raw element text, before parsing."""
    tag = "ns2:LateCheckin" if namespaced else "LateCheckin"
    if include_element:
        late_xml = f"<{tag}>{late_text}</{tag}>"
    else:
        late_xml = ""
    prefix = "ns2:" if namespaced else ""
    return f"""
            <ns2:BaggageRoute>
                <ns2:SegmentID>{segment_id}</ns2:SegmentID>
                <ns2:Airline>GF</ns2:Airline>
                <ns2:Flight>2057</ns2:Flight>
                <ns2:Origin>BAH</ns2:Origin>
                <ns2:Destination>LHR</ns2:Destination>
                <ns2:DepartureDate>2026-03-19</ns2:DepartureDate>
                <{prefix}SegmentStatus>HK</{prefix}SegmentStatus>
                {late_xml}
            </ns2:BaggageRoute>"""


def _passenger_data_soap(routes, namespaced=True):
    """SOAP envelope shaped like the GetPassengerData sample, without its header token."""
    body = "".join(
        _baggage_route_xml(
            segment_id=index,
            late_text=spec["text"],
            include_element=spec["include"],
            namespaced=namespaced,
        )
        for index, spec in enumerate(routes, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4"
                            xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>{LAST_NAME}</LastName>
          <FirstName>{FIRST_NAME}</FirstName>
          <PassengerID>P1</PassengerID>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <BaggageRouteList>
            {body}
          </BaggageRouteList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert_soap(xml):
    """Run the product's SOAP parse, body extract, and passenger-data converter."""
    parsed = SabreClient._parse_xml(xml)
    data = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    return convert_passenger_data(data, AIRLINE, FLIGHT, DATE, ORIGIN)


def _routes_for(late_text, include_element, namespaced=True):
    xml = _passenger_data_soap(
        [{"text": late_text, "include": include_element}],
        namespaced=namespaced,
    )
    doc = _convert_soap(xml)
    passenger = doc["passengers"][0]
    assert passenger["pnr"] == PNR
    assert passenger["lastName"] == LAST_NAME
    assert len(passenger["baggageRoutes"]) == 1
    return passenger["baggageRoutes"][0]


# (element text in the SOAP body, include the element, expected flag, expected code)
_LATE_CASES = [
    ("Y", True, True, "Y"),
    ("true", True, True, "true"),
    ("1", True, True, "1"),
    (" Y ", True, True, "Y"),
    (" true ", True, True, "true"),
    ("N", True, False, "N"),
    ("false", True, False, "false"),
    ("0", True, False, "0"),
    ("", True, False, ""),
    ("", False, False, ""),
]


@pytest.mark.parametrize(
    "late_text,include_element,expected_flag,expected_code",
    _LATE_CASES,
    ids=[
        "Y",
        "true",
        "1",
        "padded-Y",
        "padded-true",
        "N",
        "false",
        "0",
        "empty-element",
        "absent",
    ],
)
def test_parsed_late_checkin_flag(late_text, include_element, expected_flag, expected_code):
    route = _routes_for(late_text, include_element, namespaced=True)
    assert route["segmentId"] == "1"
    assert route["origin"] == "BAH"
    assert route["destination"] == "LHR"
    assert route["lateCheckin"] is expected_flag
    assert route["lateCheckinCode"] == expected_code


def test_unprefixed_late_checkin_y_is_late():
    route = _routes_for("Y", True, namespaced=False)
    assert route["lateCheckin"] is True
    assert route["lateCheckinCode"] == "Y"


def test_sibling_baggage_routes_do_not_share_late_checkin():
    xml = _passenger_data_soap(
        [
            {"text": "Y", "include": True},
            {"text": "N", "include": True},
            {"text": "", "include": False},
        ]
    )
    routes = _convert_soap(xml)["passengers"][0]["baggageRoutes"]
    assert [r["segmentId"] for r in routes] == ["1", "2", "3"]
    assert routes[0]["lateCheckin"] is True
    assert routes[0]["lateCheckinCode"] == "Y"
    assert routes[1]["lateCheckin"] is False
    assert routes[1]["lateCheckinCode"] == "N"
    assert routes[2]["lateCheckin"] is False
    assert routes[2]["lateCheckinCode"] == ""


class _PassengerList:
    def __init__(self, document):
        self.document = document
        self.queries = []

    def find_one(self, query, sort=None):
        self.queries.append(query)
        return self.document


class _Database:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        assert name == "passenger_list"
        return self.collection


def _install_sabre_env(monkeypatch):
    monkeypatch.setenv("SABRE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SABRE_CPAID", "TEST")
    monkeypatch.setenv("SABRE_USERNAME", "placeholder-user")
    monkeypatch.setenv("SABRE_PASSWORD", "placeholder-not-a-secret")
    monkeypatch.setenv("SABRE_PSEUDO_CITY_CODE", "TEST")
    monkeypatch.setenv("SABRE_ORGANIZATION", "TEST")
    monkeypatch.setenv("SABRE_DOMAIN", "TEST")
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)


def test_detail_route_returns_late_checkin_from_sabre_y(monkeypatch):
    """GET detail: stored PNR lookup, live GetPassengerData, then the converter.

    The SOAP body is the sample shape: a namespaced LateCheckin of Y on the
    first bag route, and N on the second. The handler is called with no
    credentials, which is how the route is registered.
    """
    _install_sabre_env(monkeypatch)
    soap = _passenger_data_soap(
        [
            {"text": "Y", "include": True},
            {"text": "N", "include": True},
        ]
    )
    posted = []

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        posted.append(action)
        if action == "SessionCreateRQ":
            return _HttpResponse(_SESSION_XML)
        if action == "GetPassengerDataRQ":
            posted.append(data)
            return _HttpResponse(soap)
        if action == "SessionCloseRQ":
            return _HttpResponse("<ok/>")
        raise AssertionError(f"unexpected SOAP action {action}")

    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)
    stored = _PassengerList(
        {
            "flightNumber": FLIGHT,
            "departureDate": DATE,
            "origin": ORIGIN,
            "passengers": [
                {
                    "pnr": PNR,
                    "lastName": LAST_NAME,
                    "firstName": FIRST_NAME,
                }
            ],
        }
    )
    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db",
        lambda: _Database(stored),
    )

    result = get_passenger_detail(
        flight_number=FLIGHT,
        pnr=PNR,
        origin=ORIGIN,
        date=DATE,
        airline=AIRLINE,
    )

    assert stored.queries[0]["flightNumber"] == FLIGHT
    assert stored.queries[0]["passengers.pnr"] == PNR
    assert stored.queries[0]["departureDate"] == DATE
    request_bodies = [
        item for item in posted if isinstance(item, str) and item.lstrip().startswith("<?xml")
    ]
    assert len(request_bodies) == 1
    request_xml = request_bodies[0]
    assert f"<LastName>{LAST_NAME}</LastName>" in request_xml
    assert f"<PNRLocator>{PNR}</PNRLocator>" in request_xml
    assert f"<Flight>{FLIGHT}</Flight>" in request_xml
    assert "GetPassengerDataRQ" in posted

    assert result["flightNumber"] == FLIGHT
    assert result["origin"] == ORIGIN
    passenger = result["passengers"][0]
    assert passenger["pnr"] == PNR
    late, on_time = passenger["baggageRoutes"]
    assert late["segmentId"] == "1"
    assert late["lateCheckin"] is True
    assert late["lateCheckinCode"] == "Y"
    assert on_time["segmentId"] == "2"
    assert on_time["lateCheckin"] is False
    assert on_time["lateCheckinCode"] == "N"
