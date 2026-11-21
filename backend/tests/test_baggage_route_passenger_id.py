"""Connection baggage-route passenger ids must survive live passenger detail.

Sabre puts a PassengerID on every baggage route. On a connection those ids
differ by segment. Live detail and the stored passenger list both copy the
route and leave that id behind, so a caller only has the parent id.

These tests fail until each route object includes its own passengerId, using
the same camelCase field as the parent passenger record, beside segmentId
where that field already exists. The parent passengerId stays as well.
"""

import os
import re
import sys
from pathlib import Path

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "GF")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

SabreClient.API_CALL_DELAY = 0

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_SAMPLE = (
    REPO_ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-1_SamplePayload_4-2-4-1.xml"
)

# Ids published in the vendor connection sample. The first matches the parent
# passenger. Each downline segment uses a different id.
PARENT_PASSENGER_ID = "43E2BE8D0001"
CONNECTION_ROUTE_IDS = [
    "43E2BE8D0001",
    "43E2BE8D0101",
    "43E2BE8D0201",
    "43E2BE8D0301",
]
PNR = "DXXRYZ"
LAST_NAME = "HDGLAX"
FIRST_NAME = "VRDJ"

SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""

# Same element shape as a captured GetPassengerListRS baggage route
# (ns2 prefix, PassengerID on the route), with a second segment whose id
# differs the way the GetPassengerData connection sample differs.
PASSENGER_LIST_RESPONSE = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2274</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>DMM</Origin>
          <Destination>BAH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>{LAST_NAME}</LastName>
            <FirstName>{FIRST_NAME}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <PassengerID>{PARENT_PASSENGER_ID}</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>12A</Seat>
          <Destination>BAH</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>1</BagCount>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:SegmentID>1</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>2274</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>DMM</ns2:Origin>
              <ns2:Destination>BAH</ns2:Destination>
              <ns2:DepartureDate>2026-03-19</ns2:DepartureDate>
              <ns2:DepartureTime>16:15</ns2:DepartureTime>
              <ns2:ArrivalDate>2026-03-19</ns2:ArrivalDate>
              <ns2:ArrivalTime>17:05</ns2:ArrivalTime>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>{CONNECTION_ROUTE_IDS[0]}</ns2:PassengerID>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>2</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>632</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>LHR</ns2:Destination>
              <ns2:DepartureDate>2026-03-19</ns2:DepartureDate>
              <ns2:DepartureTime>19:10</ns2:DepartureTime>
              <ns2:ArrivalDate>2026-03-19</ns2:ArrivalDate>
              <ns2:ArrivalTime>23:40</ns2:ArrivalTime>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>{CONNECTION_ROUTE_IDS[1]}</ns2:PassengerID>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _HttpResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _Collection:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, query, sort=None):
        return self._doc


class _Database:
    def __init__(self, docs):
        self._docs = docs

    def __getitem__(self, name):
        return _Collection(self._docs.get(name))


def _app():
    app = FastAPI()
    app.include_router(passengers.router)
    return TestClient(app)


def _vendor_passenger_data_response():
    """Return the vendor GetPassengerDataRS envelope with the sample token removed."""
    text = VENDOR_SAMPLE.read_text(encoding="utf-8")
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    xml = text[start:end]
    return re.sub(
        r"<wsse:BinarySecurityToken[^>]*>[^<]*</wsse:BinarySecurityToken>",
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>",
        xml,
        count=1,
    )


def _parsed_body(xml_text, response_key):
    """Run the same parse and SOAP-body extract the Sabre client uses."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient()
    return client._extract_body(parsed, response_key)


def _route_passenger_ids(routes):
    return [route.get("passengerId") for route in routes]


def test_parsed_vendor_sample_has_distinct_connection_passenger_ids():
    """The ids are present after the client's XML parse, before conversion."""
    body = _parsed_body(_vendor_passenger_data_response(), "GetPassengerDataRS")
    passenger = body["PassengerDataResponseList"]["PassengerDataResponse"]
    assert passenger["PassengerID"] == PARENT_PASSENGER_ID
    routes = passenger["BaggageRouteList"]["ns2:BaggageRoute"]
    assert [route["ns2:PassengerID"] for route in routes] == CONNECTION_ROUTE_IDS
    assert len(set(CONNECTION_ROUTE_IDS)) == 4


def test_live_passenger_detail_returns_each_baggage_route_passenger_id(monkeypatch):
    """GET detail, with no credential, must keep every route passenger id."""
    vendor_xml = _vendor_passenger_data_response()
    parsed = _parsed_body(vendor_xml, "GetPassengerDataRS")
    parsed_routes = parsed["PassengerDataResponseList"]["PassengerDataResponse"][
        "BaggageRouteList"
    ]["ns2:BaggageRoute"]
    assert [route["ns2:PassengerID"] for route in parsed_routes] == CONNECTION_ROUTE_IDS

    def post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        if action == "GetPassengerDataRQ":
            return _HttpResponse(vendor_xml)
        return _HttpResponse(SESSION_RESPONSE)

    monkeypatch.setattr("backend.sabre.client.requests.post", post)
    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db",
        lambda: _Database(
            {
                "passenger_list": {
                    "flightNumber": "129",
                    "departureDate": "2016-07-04",
                    "passengers": [
                        {
                            "pnr": PNR,
                            "lastName": LAST_NAME,
                            "firstName": FIRST_NAME,
                        }
                    ],
                }
            }
        ),
    )

    response = _app().get(
        f"/flights/129/passengers/{PNR}/detail",
        params={"origin": "ANF", "date": "2016-07-04", "airline": "LA"},
    )
    assert response.status_code == 200, response.text
    passenger = response.json()["passengers"][0]
    assert passenger["passengerId"] == PARENT_PASSENGER_ID
    routes = passenger["baggageRoutes"]
    assert [route["segmentId"] for route in routes] == ["1", "2", "3", "4"]
    assert [route["flight"] for route in routes] == ["129", "632", "530", "942"]
    assert _route_passenger_ids(routes) == CONNECTION_ROUTE_IDS


def test_passenger_list_keeps_downline_baggage_route_passenger_id(monkeypatch):
    """Ingested passenger lists take the same route converter omission to both list reads."""
    def post(url, data=None, headers=None, timeout=None):
        return _HttpResponse(PASSENGER_LIST_RESPONSE)

    monkeypatch.setattr("backend.sabre.client.requests.post", post)
    client = SabreClient()
    client._token = "placeholder-session-token"
    client._conversation_id = "placeholder-conversation"
    raw, _xml, _meta = client.get_passenger_list(
        "GF", "2274", "2026-03-19", "DMM"
    )
    parsed_routes = raw["PassengerInfoList"]["PassengerInfo"]["BaggageRouteList"][
        "ns2:BaggageRoute"
    ]
    assert [route["ns2:PassengerID"] for route in parsed_routes] == CONNECTION_ROUTE_IDS[:2]

    stored = convert_passenger_list(raw, "GF", "2274", "2026-03-19", "DMM")
    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db",
        lambda: _Database(
            {
                "passenger_list": stored,
                "reservations": None,
            }
        ),
    )
    http = _app()
    listed = http.get(
        "/flights/2274/passengers",
        params={"origin": "DMM", "date": "2026-03-19"},
    )
    assert listed.status_code == 200, listed.text
    passenger = listed.json()["passengers"][0]
    assert passenger["passengerId"] == PARENT_PASSENGER_ID
    assert passenger["pnr"] == PNR
    routes = passenger["baggageRoutes"]
    assert [route["flight"] for route in routes] == ["2274", "632"]
    assert [route["destination"] for route in routes] == ["BAH", "LHR"]
    assert _route_passenger_ids(routes) == CONNECTION_ROUTE_IDS[:2]

    by_pnr = http.get(
        f"/flights/2274/passengers/{PNR}",
        params={"date": "2026-03-19"},
    )
    assert by_pnr.status_code == 200, by_pnr.text
    assert _route_passenger_ids(by_pnr.json()["passengers"][0]["baggageRoutes"]) == (
        CONNECTION_ROUTE_IDS[:2]
    )
