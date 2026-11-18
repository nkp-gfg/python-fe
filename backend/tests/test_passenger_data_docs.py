"""Live passenger detail must keep Sabre free-text lines and the DOCS document.

GetPassengerData places each free-text string under FreeTextInfo/TextLine/Text
(see SabreTypeLibrary FreeTextInfo.ACS and the committed v4 sample). These
tests fail while convert_passenger_data reads a direct Text child that the
schema does not send, so docsData and freeText come back empty.

The only product caller is
GET /flights/{flight_number}/passengers/{pnr}/detail
(backend.api.routes.passengers.get_passenger_detail). Passenger-list
conversion, reservation APIS DOCSEntry parsing, flight-status remarks, and
data_audit.compare_passenger_data do not read this FreeTextInfo shape.
"""

import os
import sys
from pathlib import Path

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.api.routes import passengers as passengers_route
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

SabreClient.API_CALL_DELAY = 0

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_1.xml"
)

_DOCS = "P/US/123456789/US/01JAN70/F/01JAN20/ALPHA/PAX"
_EXPECTED_DOCS = {
    "documentType": "P",
    "documentCountry": "US",
    "documentNumber": "123456789",
    "nationality": "US",
    "dateOfBirth": "01JAN70",
    "gender": "F",
    "expiryDate": "01JAN20",
    "lastName": "ALPHA",
    "firstName": "PAX",
}
_SAMPLE_FREE_TEXT = [
    ("BT", "AZ TRN    9055300200.10"),
    ("BT", "1 / 10 KG"),
    ("DOCS", _DOCS),
    ("ET", "0552120002468  C01 16MAR Y FCOTRN CKIN"),
]


def _sample_response_envelope():
    """Return the response SOAP envelope from the committed Sabre sample.

    The file also contains the request envelope. Only the response is what
    SabreClient._post receives.
    """
    text = _SAMPLE.read_text(encoding="utf-8")
    marker = text.find("<!--Response-->")
    start = text.find("<soap-env:Envelope", marker)
    end = text.find("</soap-env:Envelope>", start)
    assert start != -1 and end != -1
    return text[start:end + len("</soap-env:Envelope>")]


def _envelope(response_body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3">'
        "<PassengerDataResponseList><PassengerDataResponse>"
        "<LineNumber>1</LineNumber>"
        "<LastName>ALPHA</LastName><FirstName>PAX</FirstName>"
        "<PNRLocator>ABC123</PNRLocator>"
        f"{response_body}"
        "</PassengerDataResponse></PassengerDataResponseList>"
        "</ns3:GetPassengerDataRS>"
        "</soap-env:Body></soap-env:Envelope>"
    )


def _parsed_body(xml_text):
    """Run the same parse and body extract GetPassengerData uses after HTTP."""
    client = SabreClient()
    parsed = client._parse_xml(xml_text)
    return client._extract_body(parsed, "GetPassengerDataRS")


def _convert(xml_text):
    raw = _parsed_body(xml_text)
    return convert_passenger_data(raw, "AZ", "4", "2016-03-16", "FCO")


def _assert_docs(passenger, docs_text=_DOCS):
    assert passenger["docsData"] == _EXPECTED_DOCS
    assert passenger["gender"] == "F"
    docs_lines = [
        entry["text"]
        for entry in passenger["freeText"]
        if entry["editCode"] == "DOCS"
    ]
    assert docs_lines == [docs_text]


def test_sample_payload_fills_document_and_other_free_text():
    """Schema-valid sample: DOCS, bag-tag, and ticket lines sit under TextLine."""
    doc = _convert(_sample_response_envelope())
    assert doc["totalPassengers"] == 1
    passenger = doc["passengers"][0]
    _assert_docs(passenger)
    assert [
        (entry["editCode"], entry["text"]) for entry in passenger["freeText"]
    ] == _SAMPLE_FREE_TEXT


def test_multiple_text_lines_are_joined_before_docs_parse():
    """TextList.ACS allows many Text children. Join them in order with no separator."""
    xml = _envelope(
        "<FreeTextInfoList>"
        "<ns2:FreeTextInfo>"
        "<ns2:EditCode>DOCS</ns2:EditCode>"
        "<ns2:TextLine>"
        "<ns2:Text>P/US/123456789/US/01JAN70/</ns2:Text>"
        "<ns2:Text>F/01JAN20/ALPHA/PAX</ns2:Text>"
        "</ns2:TextLine>"
        "</ns2:FreeTextInfo>"
        "</FreeTextInfoList>"
    )
    passenger = _convert(xml)["passengers"][0]
    _assert_docs(passenger)


def test_textline_is_preferred_over_a_direct_text_child():
    """When both are present, the schema location TextLine/Text is the document."""
    xml = _envelope(
        "<FreeTextInfoList>"
        "<ns2:FreeTextInfo>"
        "<ns2:EditCode>DOCS</ns2:EditCode>"
        "<ns2:Text>P/GB/000000000/GB/02FEB80/M/02FEB30/BETA/QAX</ns2:Text>"
        "<ns2:TextLine>"
        f"<ns2:Text>{_DOCS}</ns2:Text>"
        "</ns2:TextLine>"
        "</ns2:FreeTextInfo>"
        "</FreeTextInfoList>"
    )
    passenger = _convert(xml)["passengers"][0]
    _assert_docs(passenger)


def test_direct_text_child_is_used_only_when_textline_is_absent():
    xml = _envelope(
        "<FreeTextInfoList>"
        "<FreeTextInfo>"
        "<EditCode>DOCS</EditCode>"
        f"<Text>{_DOCS}</Text>"
        "</FreeTextInfo>"
        "</FreeTextInfoList>"
    )
    passenger = _convert(xml)["passengers"][0]
    _assert_docs(passenger)


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "4"
        assert query["passengers.pnr"] == "OJLCOK"
        assert query["departureDate"] == "2016-03-16"
        return {
            "flightNumber": "4",
            "departureDate": "2016-03-16",
            "origin": "FCO",
            "passengers": [
                {"pnr": "OTHER1", "lastName": "OTHER", "firstName": "X"},
                {"pnr": "OJLCOK", "lastName": "STEVE", "firstName": "J"},
            ],
        }


class _DB:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def test_live_detail_route_returns_sample_document_fields(monkeypatch):
    """Attack path: stored locator resolves the name, Sabre returns the sample, the route returns it unfiltered."""
    envelope = _sample_response_envelope()
    seen = {}

    def create_session(self):
        self._conversation_id = "placeholder-conversation"
        self._token = "placeholder-token"
        return self._token

    def close_session(self):
        self._token = None

    def post(self, action, body, timeout=30, endpoint=None):
        seen["action"] = action
        seen["body"] = body
        return envelope, 200, 1, body

    monkeypatch.setattr(passengers_route, "get_db", lambda: _DB())
    monkeypatch.setattr(SabreClient, "create_session", create_session)
    monkeypatch.setattr(SabreClient, "close_session", close_session)
    monkeypatch.setattr(SabreClient, "_post", post)

    result = passengers_route.get_passenger_detail(
        "4",
        "OJLCOK",
        origin="FCO",
        date="2016-03-16",
        airline="AZ",
    )

    assert seen["action"] == "GetPassengerDataRQ"
    assert "<LastName>STEVE</LastName>" in seen["body"]
    assert "<FirstName>J</FirstName>" in seen["body"]
    assert "<PNRLocator>OJLCOK</PNRLocator>" in seen["body"]
    assert result["airline"] == "AZ"
    assert result["flightNumber"] == "4"
    assert result["origin"] == "FCO"
    assert result["departureDate"] == "2016-03-16"
    passenger = result["passengers"][0]
    _assert_docs(passenger)
    assert [
        (entry["editCode"], entry["text"]) for entry in passenger["freeText"]
    ] == _SAMPLE_FREE_TEXT
