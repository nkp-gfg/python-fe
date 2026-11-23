"""Purchase clock on live passenger detail.

Sabre GetPassengerData responses include PurchaseDate and PurchaseTime on
each ancillary (AEDetails). The live detail route must keep both values on
the ancillary object it returns. These tests fail until that copy exists.

Run from the repository root:

    PYTHONPATH=. python -m pytest backend/tests/test_ancillary_purchase_time.py -q
"""

import os
import re
import sys
from pathlib import Path

# Placeholders only. SabreClient reads these names at import; the test never
# sends them to a network because requests.post is replaced below.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "placeholder"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.feeder.converter import _strip_ns
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
BAGGAGE_SAMPLE = (
    ROOT
    / "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml"
)
SEAT_SAMPLE = (
    ROOT
    / "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml"
)

_SESSION_RESPONSE = (
    "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
)


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None):
        passengers_in_doc = self._document.get("passengers", [])
        wanted_pnr = query.get("passengers.pnr")
        if wanted_pnr and not any(p.get("pnr") == wanted_pnr for p in passengers_in_doc):
            return None
        if query.get("flightNumber") not in (None, self._document.get("flightNumber")):
            return None
        if query.get("departureDate") not in (None, self._document.get("departureDate")):
            return None
        return self._document


class _Database:
    def __init__(self, document):
        self._document = document

    def __getitem__(self, name):
        if name != "passenger_list":
            raise KeyError(name)
        return _Collection(self._document)


def _response_envelope(path: Path) -> str:
    """Return the sample's response SOAP envelope with the security header removed."""
    text = path.read_text(encoding="utf-8")
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    xml = text[start:end]
    xml = re.sub(r"<wsse:Security\b[\s\S]*?</wsse:Security>", "", xml)
    if "BinarySecurityToken" in xml:
        raise AssertionError(f"{path.name} still contains a security token after stripping")
    return xml


def _with_second_ancillary(xml: str) -> str:
    """Duplicate the baggage AEDetails element so two sale times share one response."""
    start = xml.index("<ns2:AEDetails>")
    end = xml.index("</ns2:AEDetails>") + len("</ns2:AEDetails>")
    block = xml[start:end]
    second = (
        block.replace("<ns2:ItemID>5</ns2:ItemID>", "<ns2:ItemID>6</ns2:ItemID>")
        .replace(
            "<ns2:ATPCOGroupCode>BG</ns2:ATPCOGroupCode>",
            "<ns2:ATPCOGroupCode>SA</ns2:ATPCOGroupCode>",
        )
        .replace(
            "<ns2:PurchaseDate>2016-03-17</ns2:PurchaseDate>",
            "<ns2:PurchaseDate>2016-03-16</ns2:PurchaseDate>",
        )
        .replace(
            "<ns2:PurchaseTime>11:59PM</ns2:PurchaseTime>",
            "<ns2:PurchaseTime>01:15AM</ns2:PurchaseTime>",
        )
    )
    return xml[:end] + second + xml[end:]


def _parsed_ancillaries(xml: str) -> list[dict]:
    """Run the same parse and namespace strip the Sabre client and converter use."""
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient()._extract_body(parsed, "GetPassengerDataRS")
    data = _strip_ns(body)
    responses = data["PassengerDataResponseList"]["PassengerDataResponse"]
    if isinstance(responses, dict):
        responses = [responses]
    found = []
    for pax in responses:
        segments = pax["PassengerItineraryList"]["PassengerItinerary"]
        if isinstance(segments, dict):
            segments = [segments]
        for seg in segments:
            details = seg["AEDetailsList"]["AEDetails"]
            if isinstance(details, dict):
                details = [details]
            found.extend(details)
    return found


def _install_sabre_stub(monkeypatch, xml: str) -> None:
    def _post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        if action == "SessionCreateRQ":
            return _Response(_SESSION_RESPONSE)
        if action == "SessionCloseRQ":
            return _Response("<closed/>")
        if action == "GetPassengerDataRQ":
            return _Response(xml)
        raise AssertionError(f"unexpected Sabre action {action}")

    monkeypatch.setattr("backend.sabre.client.requests.post", _post)


def _client_for(monkeypatch, xml: str, stored_passenger: dict) -> TestClient:
    _install_sabre_stub(monkeypatch, xml)
    monkeypatch.setattr(passengers, "get_db", lambda: _Database({
        "flightNumber": stored_passenger["flightNumber"],
        "departureDate": stored_passenger["departureDate"],
        "origin": stored_passenger["origin"],
        "passengers": [stored_passenger],
    }))
    app = FastAPI()
    app.include_router(passengers.router)
    return TestClient(app)


def _detail(client: TestClient, flight: str, pnr: str, origin: str, date: str) -> dict:
    response = client.get(
        f"/flights/{flight}/passengers/{pnr}/detail",
        params={"origin": origin, "date": date, "airline": "GF"},
        headers={},
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    return response.json()


def _returned_ancillaries(payload: dict) -> list[dict]:
    rows = []
    for pax in payload["passengers"]:
        for seg in pax["itinerary"]:
            rows.extend(seg["aeDetails"])
    return rows


def _assert_purchase_clock(parsed_item: dict, returned_item: dict) -> None:
    purchase_date = parsed_item["PurchaseDate"]
    purchase_time = parsed_item["PurchaseTime"]
    assert purchase_date and purchase_time
    assert returned_item.get("itemId") == parsed_item.get("ItemID")
    assert returned_item.get("groupCode") == parsed_item.get("ATPCOGroupCode")
    assert returned_item.get("statusCode") == parsed_item.get("StatusCode")
    assert returned_item.get("purchaseDate") == purchase_date, returned_item
    assert returned_item.get("purchaseTime") == purchase_time, returned_item


def test_baggage_purchase_clock_survives_live_detail(monkeypatch):
    xml = _response_envelope(BAGGAGE_SAMPLE)
    parsed = _parsed_ancillaries(xml)
    assert len(parsed) == 1
    assert parsed[0]["ATPCOGroupCode"] == "BG"
    assert parsed[0]["PurchaseDate"] == "2016-03-17"
    assert parsed[0]["PurchaseTime"] == "11:59PM"

    client = _client_for(monkeypatch, xml, {
        "flightNumber": "317",
        "departureDate": "2016-03-17",
        "origin": "CDG",
        "pnr": "OLRKTG",
        "lastName": "JOHN",
        "firstName": "M",
    })
    payload = _detail(client, "317", "OLRKTG", "CDG", "2016-03-17")
    returned = _returned_ancillaries(payload)
    assert len(returned) == 1
    _assert_purchase_clock(parsed[0], returned[0])


def test_seat_purchase_clock_survives_live_detail(monkeypatch):
    xml = _response_envelope(SEAT_SAMPLE)
    parsed = _parsed_ancillaries(xml)
    assert len(parsed) == 1
    assert parsed[0]["ATPCOGroupCode"] == "SA"
    assert parsed[0]["PurchaseDate"] == "2018-05-02"
    assert parsed[0]["PurchaseTime"] == "12:00AM"

    client = _client_for(monkeypatch, xml, {
        "flightNumber": "1",
        "departureDate": "2018-05-05",
        "origin": "AUH",
        "pnr": "VWLGVM",
        "lastName": "TEST",
        "firstName": "PAX",
    })
    payload = _detail(client, "1", "VWLGVM", "AUH", "2018-05-05")
    returned = _returned_ancillaries(payload)
    assert len(returned) == 1
    _assert_purchase_clock(parsed[0], returned[0])


def test_each_ancillary_keeps_its_own_purchase_clock(monkeypatch):
    xml = _with_second_ancillary(_response_envelope(BAGGAGE_SAMPLE))
    parsed = _parsed_ancillaries(xml)
    assert [(item["ItemID"], item["PurchaseDate"], item["PurchaseTime"]) for item in parsed] == [
        ("5", "2016-03-17", "11:59PM"),
        ("6", "2016-03-16", "01:15AM"),
    ]

    client = _client_for(monkeypatch, xml, {
        "flightNumber": "317",
        "departureDate": "2016-03-17",
        "origin": "CDG",
        "pnr": "OLRKTG",
        "lastName": "JOHN",
        "firstName": "M",
    })
    payload = _detail(client, "317", "OLRKTG", "CDG", "2016-03-17")
    returned = {row["itemId"]: row for row in _returned_ancillaries(payload)}
    assert set(returned) == {"5", "6"}
    for item in parsed:
        _assert_purchase_clock(item, returned[item["ItemID"]])
