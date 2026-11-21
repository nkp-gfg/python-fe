"""Issuing carrier on a Sabre bag tag must survive live passenger detail.

GetPassengerData BagTagInfo.Airline is the carrier that checked the bag.
The vendor sample for a ground-handled itinerary sets that carrier to a
different airline than the itinerary flight. These tests fail while
convert_passenger_data omits that element and the detail route returns
the converted document.
"""

import os
import re
from pathlib import Path

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "XX")
os.environ.setdefault("SABRE_USERNAME", "placeholder")
os.environ.setdefault("SABRE_PASSWORD", "placeholder")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XX")
os.environ.setdefault("SABRE_ORGANIZATION", "XX")
os.environ.setdefault("SABRE_DOMAIN", "XX")

from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml"
)

# Query airline on the detail route. Distinct from both the itinerary
# carrier (EY) and the bag-tag carriers in the sample response (HM, LH).
REQUEST_AIRLINE = "GF"
ITINERARY_AIRLINE = "EY"
FLIGHT_NUMBER = "1"
ORIGIN = "AUH"
DEPARTURE_DATE = "2018-05-05"
PNR = "VWLGVM"
LAST_NAME = "TEST"
FIRST_NAME = "PAX"

# Tag numbers and issuing carriers. The first pair is the vendor sample.
# The second is inserted beside it so a list of BagTagInfo records each
# keep their own carrier.
TAG_HM = "0061464375"
TAG_LH = "0061999001"


def _passenger_data_response_xml():
    """SOAP response body from the vendor sample, plus a second bag tag.

    The sample file also contains a request envelope and a sample session
    token. Only the response envelope is parsed, and that token is replaced
    before the client sees the document.
    """
    text = SAMPLE.read_text(encoding="utf-8")
    response = text.split("<!--Response-->", 1)[1].strip()
    response = re.sub(
        r"(<wsse:BinarySecurityToken[^>]*>)(.*?)(</wsse:BinarySecurityToken>)",
        r"\1placeholder-token\3",
        response,
        count=1,
        flags=re.DOTALL,
    )
    second_tag = """
                        <BagTagInfo>
                           <ns2:BagTagNumber>0061999001</ns2:BagTagNumber>
                           <ns2:Airline>LH</ns2:Airline>
                           <ns2:BagOrigin>FRA</ns2:BagOrigin>
                           <ns2:BagFinalDestination>LHR</ns2:BagFinalDestination>
                        </BagTagInfo>
"""
    return response.replace("</BagTagInfoList>", second_tag + "</BagTagInfoList>", 1)


def _parsed_passenger_data():
    """Run the response through the same parser the SOAP client uses."""
    client = SabreClient()
    parsed = client._parse_xml(_passenger_data_response_xml())
    return client._extract_body(parsed, "GetPassengerDataRS")


def _bag_tags_by_number(doc):
    itinerary = doc["passengers"][0]["itinerary"]
    assert len(itinerary) == 1
    assert itinerary[0]["airline"] == ITINERARY_AIRLINE
    tags = itinerary[0]["bagTags"]
    return {tag["bagTagNumber"]: tag for tag in tags}


def test_convert_passenger_data_keeps_bag_tag_issuing_airline():
    raw = _parsed_passenger_data()
    doc = convert_passenger_data(
        raw, REQUEST_AIRLINE, FLIGHT_NUMBER, DEPARTURE_DATE, ORIGIN
    )

    tags = _bag_tags_by_number(doc)
    assert set(tags) == {TAG_HM, TAG_LH}
    # Issuing carrier comes from BagTagInfo.Airline, not the flight the
    # caller asked about and not the marketing carrier on the itinerary.
    assert tags[TAG_HM]["airline"] == "HM"
    assert tags[TAG_LH]["airline"] == "LH"
    assert tags[TAG_HM]["airline"] != ITINERARY_AIRLINE
    assert tags[TAG_HM]["airline"] != REQUEST_AIRLINE
    assert doc["airline"] == REQUEST_AIRLINE


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == FLIGHT_NUMBER
        assert query["passengers.pnr"] == PNR
        assert query["departureDate"] == DEPARTURE_DATE
        return {
            "flightNumber": FLIGHT_NUMBER,
            "departureDate": DEPARTURE_DATE,
            "passengers": [
                {
                    "pnr": PNR,
                    "lastName": LAST_NAME,
                    "firstName": FIRST_NAME,
                }
            ],
        }

    def index_information(self):
        return {}


class _Database:
    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def test_passenger_detail_route_returns_bag_tag_airline_without_credentials(monkeypatch):
    """GET the live detail route with no credential and a stored passenger.

    Sabre is not contacted. requests.post is replaced with the vendor SOAP
    response after the real client has built the GetPassengerData request
    from the stored last name and PNR.
    """
    response_xml = _passenger_data_response_xml()
    posted = []

    def fake_post(url, data=None, headers=None, timeout=None):
        import requests

        action = (headers or {}).get("SOAPAction")
        posted.append({"action": action, "body": data or "", "url": url})
        resp = requests.Response()
        resp.status_code = 200
        resp.url = url
        if action == "SessionCreateRQ":
            body = (
                "<soap-env:Envelope xmlns:soap-env=\"http://schemas.xmlsoap.org/soap/envelope/\">"
                "<soap-env:Body><wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>"
                "</soap-env:Body></soap-env:Envelope>"
            )
        elif action == "GetPassengerDataRQ":
            body = response_xml
        else:
            body = "<ok/>"
        resp._content = body.encode("utf-8")
        return resp

    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)
    monkeypatch.setattr("backend.api.main.get_db", lambda: _Database())
    monkeypatch.setattr("backend.api.main.get_redis", lambda: type("Redis", (), {"ping": lambda self: True})())
    monkeypatch.setattr("backend.feeder.storage.ensure_indexes", lambda: None)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: _Database())

    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app) as client:
        response = client.get(
            f"/flights/{FLIGHT_NUMBER}/passengers/{PNR}/detail",
            params={
                "origin": ORIGIN,
                "date": DEPARTURE_DATE,
                "airline": REQUEST_AIRLINE,
            },
        )

    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}

    actions = [call["action"] for call in posted]
    assert "GetPassengerDataRQ" in actions
    passenger_call = next(call for call in posted if call["action"] == "GetPassengerDataRQ")
    assert f"<LastName>{LAST_NAME}</LastName>" in passenger_call["body"]
    assert f"<PNRLocator>{PNR}</PNRLocator>" in passenger_call["body"]
    assert f"<Airline>{REQUEST_AIRLINE}</Airline>" in passenger_call["body"]

    tags = _bag_tags_by_number(response.json())
    assert tags[TAG_HM]["airline"] == "HM"
    assert tags[TAG_LH]["airline"] == "LH"
    assert tags[TAG_HM]["airline"] != ITINERARY_AIRLINE
    assert tags[TAG_HM]["airline"] != REQUEST_AIRLINE
