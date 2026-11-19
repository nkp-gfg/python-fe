"""Live passenger detail must keep Timatic edit attributes from Sabre.

GetPassengerData puts visa and residency values under EditCodeAttributeList.
These tests drive the real client parse and the detail route with that XML.
They fail while convert_passenger_data keeps only attributes of the Edit
element itself and leaves the attribute map empty.
"""

from pathlib import Path

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.passengers import get_passenger_detail, router
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_SAMPLES = [
    REPO_ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-2_SamplePayload_4_2_5a.xml.xml",
    REPO_ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-2_SamplePayload_4_2_5b.xml.xml",
]

# Values from the version 4.0.2 GetPassengerData samples, Edit name="TIM".
TIMATIC_ATTRIBUTES = {
    "StayType": "VACATION",
    "DocumentType": "PASSPORT NORMAL",
    "ResidencyDocumentType": "RESIDENCEPERMIT",
    "ResidencyCountry": "GB",
    "ReturnDate": "29APR17",
    "VisaVerified": "VISA VERIFIED",
}

# No credentials. infant is an attribute of Edit; the values are child elements.
MULTI_EDIT_ENVELOPE = """\
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>CHARLES</LastName>
          <FirstName>JANE</FirstName>
          <PassengerID>1</PassengerID>
          <PNRLocator>OLBJTY</PNRLocator>
          <PassengerEditList>
            <ns2:Edit name="TIM" infant="true">
              <ns2:EditCodeAttributeList>
                <ns2:EditCodeAttribute name="VisaVerified">
                  <ns2:Values><ns2:Value>VISA VERIFIED</ns2:Value></ns2:Values>
                </ns2:EditCodeAttribute>
                <ns2:EditCodeAttribute name="ResidencyCountry">
                  <ns2:Values><ns2:Value>GB</ns2:Value></ns2:Values>
                </ns2:EditCodeAttribute>
              </ns2:EditCodeAttributeList>
            </ns2:Edit>
            <ns2:Edit name="DOCS" infant="false">
              <ns2:EditCodeAttributeList>
                <ns2:EditCodeAttribute name="DocumentType">
                  <ns2:Values><ns2:Value>PASSPORT NORMAL</ns2:Value></ns2:Values>
                </ns2:EditCodeAttribute>
              </ns2:EditCodeAttributeList>
            </ns2:Edit>
            <ns2:Edit name="APP"/>
          </PassengerEditList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _response_envelope(path: Path) -> str:
    """Return the response SOAP envelope from a vendor sample file.

    The sample files wrap a request and a response. The product receives only
    the response envelope, which is the soap-env document that contains
    GetPassengerDataRS.
    """
    text = path.read_text(encoding="utf-8")
    rs_at = text.find("<ns3:GetPassengerDataRS")
    assert rs_at != -1, path.name
    start = text.rfind("<soap-env:Envelope", 0, rs_at)
    end = text.find("</soap-env:Envelope>", rs_at)
    assert start != -1 and end != -1, path.name
    return text[start : end + len("</soap-env:Envelope>")]


def _install_sabre_placeholders(monkeypatch):
    monkeypatch.setenv("SABRE_BASE_URL", "http://127.0.0.1")
    monkeypatch.setenv("SABRE_CPAID", "TEST")
    monkeypatch.setenv("SABRE_USERNAME", "placeholder-user")
    monkeypatch.setenv("SABRE_PASSWORD", "placeholder-password")
    monkeypatch.setenv("SABRE_PSEUDO_CITY_CODE", "TEST")
    monkeypatch.setenv("SABRE_ORGANIZATION", "TEST")
    monkeypatch.setenv("SABRE_DOMAIN", "TEST")


def _client_returning(xml_text: str):
    """SabreClient whose HTTP transport returns xml_text and nothing else.

    Session open and close do not send the sample document. get_passenger_data
    still runs the real request build, response decode, parse, and body extract.
    """

    def fake_post(url, data=None, headers=None, timeout=None):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response._content = xml_text.encode("utf-8")
        return response

    class PassengerDataClient(SabreClient):
        API_CALL_DELAY = 0.0

        def create_session(self):
            self._conversation_id = "placeholder-conversation"
            self._token = "placeholder-token"
            return self._token

        def close_session(self):
            self._token = None

    return fake_post, PassengerDataClient


def _fetch_passenger_data(monkeypatch, xml_text: str):
    _install_sabre_placeholders(monkeypatch)
    fake_post, client_cls = _client_returning(xml_text)
    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)
    with client_cls() as client:
        raw_data, raw_xml, _meta = client.get_passenger_data(
            airline="EY",
            flight_number="11",
            departure_date="2017-01-04",
            origin="AUH",
            last_name="CHARLES",
            first_name="JANE",
            pnr="OLBJTY",
        )
    assert raw_xml == xml_text
    return raw_data


def _tim_edit(document):
    edits = document["passengers"][0]["passengerEdits"]
    named = [edit for edit in edits if edit.get("name") == "TIM"]
    assert len(named) == 1
    return named[0]


def _infant_flag(edit):
    """Infant may sit on the edit or inside its attribute map."""
    if "infant" in edit and not isinstance(edit["infant"], dict):
        raw = edit["infant"]
    else:
        raw = edit.get("attributes", {}).get("infant")
    if isinstance(raw, str):
        return raw.strip().lower() == "true"
    return raw


@pytest.mark.parametrize("sample_path", VENDOR_SAMPLES, ids=lambda path: path.name)
def test_vendor_sample_timatic_attributes_survive_get_passenger_data(
    monkeypatch, sample_path
):
    raw_data = _fetch_passenger_data(monkeypatch, _response_envelope(sample_path))
    document = convert_passenger_data(raw_data, "EY", "11", "2017-01-04", "AUH")

    tim = _tim_edit(document)
    assert tim["attributes"] == TIMATIC_ATTRIBUTES


def test_each_edit_keeps_its_own_values_and_infant_flag(monkeypatch):
    raw_data = _fetch_passenger_data(monkeypatch, MULTI_EDIT_ENVELOPE)
    document = convert_passenger_data(raw_data, "EY", "11", "2017-01-04", "AUH")
    edits = {edit["name"]: edit for edit in document["passengers"][0]["passengerEdits"]}

    assert set(edits) == {"TIM", "DOCS", "APP"}
    assert edits["TIM"]["attributes"]["VisaVerified"] == "VISA VERIFIED"
    assert edits["TIM"]["attributes"]["ResidencyCountry"] == "GB"
    assert "DocumentType" not in edits["TIM"]["attributes"]
    assert _infant_flag(edits["TIM"]) is True

    assert edits["DOCS"]["attributes"]["DocumentType"] == "PASSPORT NORMAL"
    assert "VisaVerified" not in edits["DOCS"]["attributes"]
    assert _infant_flag(edits["DOCS"]) is False

    assert edits["APP"]["attributes"] == {}


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "11"
        assert query["passengers.pnr"] == "OLBJTY"
        assert query["departureDate"] == "2017-01-04"
        return {
            "passengers": [
                {"pnr": "OLBJTY", "lastName": "CHARLES", "firstName": "JANE"}
            ]
        }


class _Database:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def test_passenger_detail_route_returns_timatic_attributes(monkeypatch):
    xml_text = _response_envelope(VENDOR_SAMPLES[0])
    _install_sabre_placeholders(monkeypatch)
    fake_post, client_cls = _client_returning(xml_text)
    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)
    monkeypatch.setattr("backend.api.routes.passengers.SabreClient", client_cls)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: _Database())

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/flights/11/passengers/OLBJTY/detail",
        params={"origin": "AUH", "date": "2017-01-04", "airline": "EY"},
    )
    assert response.status_code == 200
    body = response.json()

    direct = get_passenger_detail(
        flight_number="11",
        pnr="OLBJTY",
        origin="AUH",
        date="2017-01-04",
        airline="EY",
    )
    assert body["passengers"][0]["passengerEdits"] == direct["passengers"][0]["passengerEdits"]
    assert _tim_edit(body)["attributes"] == TIMATIC_ATTRIBUTES
