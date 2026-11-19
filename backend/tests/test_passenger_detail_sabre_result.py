"""Live passenger detail must surface Sabre Result errors.

GetPassengerDataRS puts status, completion, and the error text on Result,
not on root attributes or ErrorInfo. These tests fail while the converter
ignores Result and the live detail route answers HTTP 200 with an empty
profile.

The only product operation on this path is
GET /flights/{flight_number}/passengers/{pnr}/detail
(backend.api.routes.passengers.get_passenger_detail). Ingestion does not
call GetPassengerData.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "sabre_docs" / "GetPassengerDataRQ_v4-0-4_Samples"

NOT_FOUND_SAMPLE = SAMPLES / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_3_2.xml"
ANCILLARY_SAMPLE = SAMPLES / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml"
SUCCESS_SAMPLE = SAMPLES / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_1.xml"

NOT_FOUND_MESSAGE = "One or More Passengers Data Not Found"
ANCILLARY_MESSAGE = (
    "Did not find any ancillary data that matched group code and/or category"
)

# Placeholders only. SabreClient reads these names; the tests never contact Sabre.
SABRE_ENV = {
    "SABRE_BASE_URL": "http://sabre.invalid",
    "SABRE_CPAID": "TEST",
    "SABRE_USERNAME": "placeholder-user",
    "SABRE_PASSWORD": "placeholder-password",
    "SABRE_PSEUDO_CITY_CODE": "TEST",
    "SABRE_ORGANIZATION": "TEST",
    "SABRE_DOMAIN": "TEST",
}

SESSION_RS = (
    "<soap-env:Envelope xmlns:soap-env=\"http://schemas.xmlsoap.org/soap/envelope/\">"
    "<soap-env:Header><wsse:Security xmlns:wsse=\"http://schemas.xmlsoap.org/ws/2002/12/secext\">"
    "<wsse:BinarySecurityToken>PLACEHOLDER_SESSION_TOKEN</wsse:BinarySecurityToken>"
    "</wsse:Security></soap-env:Header></soap-env:Envelope>"
)


class _HttpResponse:
    """Stand-in for the fields SabreClient._post reads off the HTTP response."""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def vendor_response_xml(path: Path) -> str:
    """Return the SOAP response envelope from a Sabre sample payload file."""
    text = path.read_text()
    marker = "<!--Response-->"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"{path} has no response envelope")
    xml = text[start + len(marker):]
    end = xml.rfind("</soap-env:Envelope>")
    if end < 0:
        raise AssertionError(f"{path} response envelope is truncated")
    return xml[: end + len("</soap-env:Envelope>")].strip()


def envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4"'
        ' xmlns:ns2="http://services.sabre.com/STL/v3">'
        f"{inner}"
        "</ns3:GetPassengerDataRS>"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def result_block(status, completion, messages):
    """Build a Result element. messages is a list of (code, text)."""
    specifics = []
    for code, text in messages:
        specifics.append(
            "<ns2:SystemSpecificResults>"
            f'<ns2:ErrorMessage code="{code}">{text}</ns2:ErrorMessage>'
            "</ns2:SystemSpecificResults>"
        )
    return (
        '<Result messageId="result-1" timeStamp="2016-03-18T06:42:56.370Z">'
        f"<ns2:Status>{status}</ns2:Status>"
        f"<ns2:CompletionStatus>{completion}</ns2:CompletionStatus>"
        "<ns2:System>ACS-BSO</ns2:System>"
        f"{''.join(specifics)}"
        "</Result>"
    )


def parsed_body(xml_text: str):
    """Run the same parse and body extract GetPassengerData uses."""
    client = SabreClient.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    return client._extract_body(parsed, "GetPassengerDataRS")


def converted(xml_text: str, airline="GF", flight="316", date="2016-03-18", origin="FCO"):
    return convert_passenger_data(
        parsed_body(xml_text), airline, flight, date, origin,
    )


def assert_error(errors, code, message):
    assert any(
        str(item.get("code")) == str(code) and item.get("message") == message
        for item in errors
    ), errors


@pytest.fixture
def live_detail(monkeypatch):
    """Call the live detail route with a scripted Sabre SOAP body.

    Mongo holds the passenger the route looks up before the Sabre call.
    requests.post is the HTTP boundary. Session open, GetPassengerData, and
    session close then run through SabreClient.
    """
    for key, value in SABRE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)

    state = {"xml": "", "actions": []}

    def post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        state["actions"].append(action)
        if action == "SessionCreateRQ":
            return _HttpResponse(200, SESSION_RS)
        if action == "GetPassengerDataRQ":
            return _HttpResponse(200, state["xml"])
        if action == "SessionCloseRQ":
            return _HttpResponse(200, "<ok/>")
        raise AssertionError(f"unexpected SOAP action {action}")

    monkeypatch.setattr("backend.sabre.client.requests.post", post)

    stored = {"doc": None}

    class _DB:
        def __getitem__(self, name):
            assert name == "passenger_list"
            return self

        def find_one(self, query, sort=None):
            doc = stored["doc"]
            if doc is None:
                return None
            if query.get("flightNumber") != doc["flightNumber"]:
                return None
            if query.get("departureDate") != doc["departureDate"]:
                return None
            if query.get("passengers.pnr") != doc["passengers"][0]["pnr"]:
                return None
            return doc

    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: _DB())

    app = FastAPI()
    app.include_router(passengers.router)
    client = TestClient(app, raise_server_exceptions=False)

    def request_detail(*, xml, flight_number, pnr, origin, date, airline,
                        last_name, first_name, stored_doc=True):
        state["xml"] = xml
        state["actions"].clear()
        if stored_doc:
            stored["doc"] = {
                "flightNumber": flight_number,
                "departureDate": date,
                "origin": origin,
                "passengers": [{
                    "pnr": pnr,
                    "lastName": last_name,
                    "firstName": first_name,
                }],
            }
        else:
            stored["doc"] = None
        return client.get(
            f"/flights/{flight_number}/passengers/{pnr}/detail",
            params={"origin": origin, "date": date, "airline": airline},
        )

    return request_detail, state


def test_convert_vendor_not_found_reports_business_logic_error():
    doc = converted(vendor_response_xml(NOT_FOUND_SAMPLE))
    assert doc["status"] == "BusinessLogicError"
    assert doc["completionStatus"] == "Incomplete"
    assert doc["passengers"] == []
    assert doc["totalPassengers"] == 0
    assert_error(doc["errors"], "1211", NOT_FOUND_MESSAGE)


def test_convert_ancillary_warning_keeps_passenger_and_records_error():
    doc = converted(
        vendor_response_xml(ANCILLARY_SAMPLE),
        airline="AZ", flight="317", date="2016-03-17", origin="CDG",
    )
    assert doc["status"] == "BusinessLogicError"
    assert doc["completionStatus"] == "Complete"
    assert doc["totalPassengers"] == 1
    passenger = doc["passengers"][0]
    assert passenger["lastName"] == "JOHN"
    assert passenger["firstName"] == "M"
    assert passenger["pnr"] == "OLRKTG"
    assert passenger["passengerId"] == "B7FCF1EE0001"
    assert_error(doc["errors"], "41007", ANCILLARY_MESSAGE)


def test_convert_vendor_success_reports_success_and_passenger():
    doc = converted(
        vendor_response_xml(SUCCESS_SAMPLE),
        airline="AZ", flight="4", date="2016-03-16", origin="FCO",
    )
    assert doc["status"] == "Success"
    assert doc["completionStatus"] == "Complete"
    assert doc["errors"] == []
    assert doc["totalPassengers"] == 1
    assert doc["passengers"][0]["lastName"] == "STEVE"
    assert doc["passengers"][0]["pnr"] == "OJLCOK"


def test_convert_success_without_passengers_is_an_empty_profile():
    xml = envelope(result_block("Success", "Complete", []))
    doc = converted(xml)
    assert doc["status"] == "Success"
    assert doc["completionStatus"] == "Complete"
    assert doc["errors"] == []
    assert doc["passengers"] == []
    assert doc["totalPassengers"] == 0


def test_convert_repeated_result_messages_are_all_kept():
    xml = envelope(result_block(
        "BusinessLogicError",
        "Incomplete",
        [("1001", "First passenger record was not found"),
         ("1002", "Second passenger record was not found")],
    ))
    doc = converted(xml)
    assert doc["status"] == "BusinessLogicError"
    assert doc["completionStatus"] == "Incomplete"
    assert doc["passengers"] == []
    assert_error(doc["errors"], "1001", "First passenger record was not found")
    assert_error(doc["errors"], "1002", "Second passenger record was not found")


def test_live_detail_vendor_not_found_is_an_http_error(live_detail):
    request_detail, state = live_detail
    response = request_detail(
        xml=vendor_response_xml(NOT_FOUND_SAMPLE),
        flight_number="316",
        pnr="JJNQHX",
        origin="FCO",
        date="2016-03-18",
        airline="AZ",
        last_name="BOLT",
        first_name="",
    )
    assert "GetPassengerDataRQ" in state["actions"]
    assert response.status_code >= 400, response.text
    assert NOT_FOUND_MESSAGE in response.text


def test_live_detail_ancillary_warning_returns_the_passenger(live_detail):
    request_detail, state = live_detail
    response = request_detail(
        xml=vendor_response_xml(ANCILLARY_SAMPLE),
        flight_number="317",
        pnr="OLRKTG",
        origin="CDG",
        date="2016-03-17",
        airline="AZ",
        last_name="JOHN",
        first_name="M",
    )
    assert "GetPassengerDataRQ" in state["actions"]
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "BusinessLogicError"
    assert body["completionStatus"] == "Complete"
    assert body["totalPassengers"] == 1
    assert body["passengers"][0]["lastName"] == "JOHN"
    assert body["passengers"][0]["pnr"] == "OLRKTG"
    assert body["passengers"][0]["passengerId"] == "B7FCF1EE0001"
    assert_error(body["errors"], "41007", ANCILLARY_MESSAGE)


def test_live_detail_success_returns_the_passenger(live_detail):
    request_detail, _state = live_detail
    response = request_detail(
        xml=vendor_response_xml(SUCCESS_SAMPLE),
        flight_number="4",
        pnr="OJLCOK",
        origin="FCO",
        date="2016-03-16",
        airline="AZ",
        last_name="STEVE",
        first_name="J",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Success"
    assert body["completionStatus"] == "Complete"
    assert body["errors"] == []
    assert body["passengers"][0]["lastName"] == "STEVE"
    assert body["passengers"][0]["pnr"] == "OJLCOK"


def test_live_detail_success_without_passengers_stays_http_200(live_detail):
    request_detail, _state = live_detail
    response = request_detail(
        xml=envelope(result_block("Success", "Complete", [])),
        flight_number="316",
        pnr="EMPTY1",
        origin="FCO",
        date="2016-03-18",
        airline="AZ",
        last_name="BOLT",
        first_name="",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Success"
    assert body["completionStatus"] == "Complete"
    assert body["errors"] == []
    assert body["passengers"] == []
    assert body["totalPassengers"] == 0


def test_live_detail_repeated_errors_include_every_message(live_detail):
    request_detail, _state = live_detail
    first = "First passenger record was not found"
    second = "Second passenger record was not found"
    response = request_detail(
        xml=envelope(result_block(
            "BusinessLogicError",
            "Incomplete",
            [("1001", first), ("1002", second)],
        )),
        flight_number="316",
        pnr="JJNQHX",
        origin="FCO",
        date="2016-03-18",
        airline="AZ",
        last_name="BOLT",
        first_name="",
    )
    assert response.status_code >= 400, response.text
    assert first in response.text
    assert second in response.text


def test_unknown_pnr_is_not_found_before_sabre(live_detail):
    request_detail, state = live_detail
    response = request_detail(
        xml=vendor_response_xml(NOT_FOUND_SAMPLE),
        flight_number="316",
        pnr="MISSING",
        origin="FCO",
        date="2016-03-18",
        airline="AZ",
        last_name="BOLT",
        first_name="",
        stored_doc=False,
    )
    assert response.status_code == 404
    assert state["actions"] == []
