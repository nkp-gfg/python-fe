"""Live passenger detail must return Sabre's name item number.

GetPassengerData puts nameAssociationID and nameRefNumber on the same
PNRLocator element. Those values identify different name items on a shared
record. convert_passenger_data keeps the association id and drops the name
reference number, and GET /flights/{flight}/passengers/{pnr}/detail returns
that document with no authentication.

These tests fail until each passenger in that payload carries nameRefNumber
beside nameAssociationId, copied from the locator attribute.

The live detail sheet (frontend/components/dashboard/passenger-detail-sheet.tsx)
is not covered: this repository has no frontend test runner, and the sheet
does not read the locator attribute. GetPassengerList is not covered: its
PNRLocator schema has nameAssociationID only.
"""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://sabre.invalid"
os.environ["SABRE_CPAID"] = "placeholder-cpaid"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder-pcc"
os.environ["SABRE_ORGANIZATION"] = "placeholder-org"
os.environ["SABRE_DOMAIN"] = "placeholder-domain"

from unittest.mock import patch

import xmltodict
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.feeder.converter import _strip_ns, convert_passenger_data
from backend.sabre.client import SabreClient

# Same locator shape as the vendor GetPassengerData sample
# (nameAssociationID and nameRefNumber on one PNRLocator), with a second
# traveler on that locator whose numbers are swapped. Treating association
# id "1" as name item 01.01 selects BOB, not ADA.
SHARED_LOCATOR_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>JOHN</LastName>
          <FirstName>ADA</FirstName>
          <PassengerID>PAXADA</PassengerID>
          <PNRLocator nameAssociationID="1" nameRefNumber="02.01">OLRKTG</PNRLocator>
        </PassengerDataResponse>
        <PassengerDataResponse>
          <LineNumber>2</LineNumber>
          <LastName>JOHN</LastName>
          <FirstName>BOB</FirstName>
          <PassengerID>PAXBOB</PassengerID>
          <PNRLocator nameAssociationID="2" nameRefNumber="01.01">OLRKTG</PNRLocator>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

STRING_LOCATOR_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>JOHN</LastName>
          <FirstName>ADA</FirstName>
          <PassengerID>PAXADA</PassengerID>
          <PNRLocator>OLRKTG</PNRLocator>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _Response:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "2057"
        assert query["passengers.pnr"] == "OLRKTG"
        assert query["departureDate"] == "2026-03-19"
        assert sort == [("fetchedAt", -1)]
        return {
            "passengers": [
                {"pnr": "OLRKTG", "lastName": "JOHN", "firstName": "ADA"},
                {"pnr": "OLRKTG", "lastName": "JOHN", "firstName": "BOB"},
            ]
        }


class _DB:
    def __getitem__(self, key):
        assert key == "passenger_list"
        return _PassengerList()


def _texts(node, local_name):
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and key.split(":")[-1] == local_name and isinstance(child, str):
                    found.append(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def _parsed_passengers(xml_text):
    """Run the client's SOAP parse and the converter's namespace strip."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body is an instance method and does not use session state.
    data = SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, "GetPassengerDataRS")
    stripped = _strip_ns(data)
    passengers = stripped["PassengerDataResponseList"]["PassengerDataResponse"]
    if isinstance(passengers, dict):
        return data, [passengers]
    return data, passengers


def _assert_name_items(passengers):
    by_first = {pax["firstName"]: pax for pax in passengers}
    ada = by_first["ADA"]
    bob = by_first["BOB"]
    assert ada["pnr"] == "OLRKTG"
    assert bob["pnr"] == "OLRKTG"
    assert ada["nameAssociationId"] == "1"
    assert bob["nameAssociationId"] == "2"
    # Exact Sabre name item numbers, not the association ids rewritten as 01.01 / 02.01.
    assert ada["nameRefNumber"] == "02.01"
    assert bob["nameRefNumber"] == "01.01"
    assert ada["nameRefNumber"] != ada["nameAssociationId"]
    assert bob["nameRefNumber"] != bob["nameAssociationId"]
    owner_of_name_item = {pax["nameRefNumber"]: pax["firstName"] for pax in passengers}
    assert owner_of_name_item["01.01"] == "BOB"
    assert owner_of_name_item["02.01"] == "ADA"


def test_detail_route_returns_name_ref_number_for_shared_locator():
    """Unauthenticated detail call must return both travelers' name item numbers."""
    SabreClient.API_CALL_DELAY = 0
    raw_data, parsed_passengers = _parsed_passengers(SHARED_LOCATOR_RESPONSE)
    # The parser delivers the attribute the converter currently ignores.
    assert parsed_passengers[0]["PNRLocator"]["@nameAssociationID"] == "1"
    assert parsed_passengers[0]["PNRLocator"]["@nameRefNumber"] == "02.01"
    assert parsed_passengers[0]["PNRLocator"]["#text"] == "OLRKTG"
    assert parsed_passengers[1]["PNRLocator"]["@nameAssociationID"] == "2"
    assert parsed_passengers[1]["PNRLocator"]["@nameRefNumber"] == "01.01"

    seen = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        if action == "SessionCreateRQ":
            return _Response(
                "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
            )
        if action == "GetPassengerDataRQ":
            seen["request"] = data
            return _Response(SHARED_LOCATOR_RESPONSE)
        if action == "SessionCloseRQ":
            return _Response("<ok/>")
        raise AssertionError(action)

    app = FastAPI()
    app.include_router(passengers.router)
    detail = next(
        route for route in passengers.router.routes
        if getattr(route, "path", None) == "/flights/{flight_number}/passengers/{pnr}/detail"
    )
    assert detail.dependencies == []

    with patch.object(passengers, "get_db", return_value=_DB()), patch(
        "backend.sabre.client.requests.post", side_effect=fake_post
    ):
        client = TestClient(app)
        response = client.get(
            "/flights/2057/passengers/OLRKTG/detail",
            params={"origin": "BOM", "date": "2026-03-19", "airline": "GF"},
        )

    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    request_body = xmltodict.parse(seen["request"])
    assert _texts(request_body, "LastName") == ["JOHN"]
    assert _texts(request_body, "FirstName") == ["ADA"]
    assert _texts(request_body, "PNRLocator") == ["OLRKTG"]

    # JSON the unauthenticated caller receives, then the converter the route calls.
    _assert_name_items(response.json()["passengers"])
    converted = convert_passenger_data(raw_data, "GF", "2057", "2026-03-19", "BOM")
    _assert_name_items(converted["passengers"])


def test_string_locator_still_exposes_empty_name_ref_number():
    """A locator with no attributes still returns the nameRefNumber field."""
    raw_data, parsed_passengers = _parsed_passengers(STRING_LOCATOR_RESPONSE)
    assert parsed_passengers[0]["PNRLocator"] == "OLRKTG"
    converted = convert_passenger_data(raw_data, "GF", "2057", "2026-03-19", "BOM")
    passenger = converted["passengers"][0]
    assert passenger["pnr"] == "OLRKTG"
    assert passenger["nameAssociationId"] == ""
    assert passenger["nameRefNumber"] == ""
