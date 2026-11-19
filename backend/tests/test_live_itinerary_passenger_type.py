"""Live passenger detail must show each segment's Sabre passenger type.

GetPassengerData puts PassengerType on each itinerary segment. The schema
documents F as Full Fare, S as Standby Revenue, P as Positive Space Non
Revenue, and E as Non Revenue. The live detail route returns that code, and
the itinerary card the operator reads must label it.

These tests fail while the card omits the label. They pass once each segment
card shows the schema meaning for its own code.

Run from the repository root, after the frontend render tools are installed:

    cd frontend && npm ci && npm install --no-save --ignore-scripts jsdom@26.1.0 tsx@4.20.6
    cd .. && python3 backend/tests/test_live_itinerary_passenger_type.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SABRE_API_DELAY_SECONDS", "0")
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.encoders import jsonable_encoder

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
RENDERER = FRONTEND / "tests" / "render-live-itinerary.mts"

# Meanings from GetPassengerDataRQRS_v4-0-0.xsd PassengerType documentation.
SCHEMA_LABELS = {
    "F": "Full Fare",
    "S": "Standby Revenue",
    "P": "Positive Space Non Revenue",
    "E": "Non Revenue",
}

FLIGHT_NUMBER = "4111"
ORIGIN = "BAH"
DATE = "2026-04-02"
PNR = "ABC123"
AIRLINE = "GF"

SEGMENTS = [
    {
        "id": "1",
        "flight": "4111",
        "origin": "BAH",
        "destination": "DXB",
        "date": "2026-04-02",
        "booking_class": "Y",
        "cabin": "Y",
        "seat": "12A",
        "passenger_type": "F",
    },
    {
        "id": "2",
        "flight": "4222",
        "origin": "DXB",
        "destination": "LHR",
        "date": "2026-04-03",
        "booking_class": "J",
        "cabin": "J",
        "seat": "2C",
        "passenger_type": "S",
    },
    {
        "id": "3",
        "flight": "4333",
        "origin": "LHR",
        "destination": "BAH",
        "date": "2026-04-04",
        "booking_class": "W",
        "cabin": "W",
        "seat": "4D",
        "passenger_type": "P",
    },
    {
        "id": "4",
        "flight": "4444",
        "origin": "BAH",
        "destination": "MCT",
        "date": "2026-04-05",
        "booking_class": "Y",
        "cabin": "Y",
        "seat": "8B",
        "passenger_type": "E",
    },
    {
        "id": "5",
        "flight": "4555",
        "origin": "MCT",
        "destination": "DOH",
        "date": "2026-04-06",
        "booking_class": "Y",
        "cabin": "Y",
        "seat": "15F",
        "passenger_type": "",
    },
]


def _passenger_data_response(segments):
    """SOAP body in the shape of the published GetPassengerData sample.

    PassengerType is the un-namespaced element used by the sample payloads.
    The header carries no session token.
    """
    itinerary = []
    for seg in segments:
        type_xml = ""
        if seg["passenger_type"]:
            type_xml = f"<PassengerType>{seg['passenger_type']}</PassengerType>"
        itinerary.append(
            f"""<PassengerItinerary>
              <SegmentID>{seg['id']}</SegmentID>
              <Airline>{AIRLINE}</Airline>
              <Flight>{seg['flight']}</Flight>
              <BookingClass>{seg['booking_class']}</BookingClass>
              <Origin>{seg['origin']}</Origin>
              <Destination>{seg['destination']}</Destination>
              <DepartureDate>{seg['date']}</DepartureDate>
              <Cabin>{seg['cabin']}</Cabin>
              <Seat>{seg['seat']}</Seat>
              {type_xml}
              <BagCount>0</BagCount>
            </PassengerItinerary>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader">
      <eb:Action>GetPassengerDataRS</eb:Action>
    </eb:MessageHeader>
  </soap-env:Header>
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>ALMAHRI</LastName>
          <FirstName>NOOR</FirstName>
          <PassengerID>PAX0001</PassengerID>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <PassengerItineraryList>
            {''.join(itinerary)}
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
      <Result>
        <ns2:Status>Success</ns2:Status>
        <ns2:CompletionStatus>Complete</ns2:CompletionStatus>
      </Result>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


class _Response:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class _Collection:
    def __init__(self, name, doc):
        self.name = name
        self.doc = doc

    def find_one(self, query, sort=None):
        if self.name != "passenger_list":
            return None
        return self.doc


class _DB:
    def __init__(self, doc):
        self.doc = doc

    def __getitem__(self, name):
        return _Collection(name, self.doc)


def _stored_passenger_list():
    """List record used only to resolve the surname for the Sabre lookup.

    It deliberately has no segment passenger type. The type under test comes
    from the GetPassengerData response.
    """
    return {
        "flightNumber": FLIGHT_NUMBER,
        "departureDate": DATE,
        "passengers": [
            {"pnr": PNR, "lastName": "ALMAHRI", "firstName": "NOOR"},
        ],
    }


def _open_session(self):
    self._token = "PLACEHOLDER_TOKEN"
    self._conversation_id = "placeholder-conversation"
    return self._token


def _close_session(self):
    self._token = None


def _detail_from_sabre(segments):
    xml = _passenger_data_response(segments)
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction")
        calls.append(action)
        if action != "GetPassengerDataRQ":
            raise AssertionError(f"unexpected Sabre action: {action}")
        if not str(url).startswith("https://sabre.invalid/"):
            raise AssertionError("Sabre client posted outside the placeholder endpoint")
        return _Response(xml)

    with patch("backend.sabre.client.requests.post", fake_post), \
            patch.object(SabreClient, "create_session", _open_session), \
            patch.object(SabreClient, "close_session", _close_session), \
            patch("backend.api.routes.passengers.get_db", return_value=_DB(_stored_passenger_list())):
        result = get_passenger_detail(
            FLIGHT_NUMBER,
            PNR,
            origin=ORIGIN,
            date=DATE,
            airline=AIRLINE,
        )
    assert calls == ["GetPassengerDataRQ"]
    return jsonable_encoder(result)


def _render_sheet(detail):
    if not (FRONTEND / "node_modules" / "jsdom").is_dir() or not (FRONTEND / "node_modules" / "tsx").is_dir():
        raise AssertionError(
            "frontend render tools are missing. From frontend run: "
            "npm ci && npm install --no-save --ignore-scripts jsdom@26.1.0 tsx@4.20.6"
        )
    payload = FRONTEND / "tests" / "_live_itinerary_detail.json"
    payload.write_text(json.dumps(detail))
    try:
        completed = subprocess.run(
            [
                "npx",
                "tsx",
                str(RENDERER),
                str(payload),
                FLIGHT_NUMBER,
                ORIGIN,
                DATE,
                PNR,
            ],
            cwd=FRONTEND,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        payload.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise AssertionError(
            "live sheet render failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def test_single_segment_response_keeps_passenger_type():
    """xmltodict returns one PassengerItinerary as a dict, not a list."""
    detail = _detail_from_sabre([SEGMENTS[1]])
    itinerary = detail["passengers"][0]["itinerary"]
    assert len(itinerary) == 1
    assert itinerary[0]["passengerType"] == "S"
    assert itinerary[0]["flight"] == "4222"
    assert itinerary[0]["seat"] == "2C"


def test_live_itinerary_card_labels_segment_passenger_type():
    detail = _detail_from_sabre(SEGMENTS)
    itinerary = detail["passengers"][0]["itinerary"]
    assert [seg["passengerType"] for seg in itinerary] == ["F", "S", "P", "E", ""]

    # The same one-segment dict shape, parsed directly, still carries the code.
    single_xml = _passenger_data_response([SEGMENTS[2]])
    parsed = SabreClient._parse_xml(single_xml)
    body = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    converted = convert_passenger_data(body, AIRLINE, FLIGHT_NUMBER, DATE, ORIGIN)
    assert converted["passengers"][0]["itinerary"][0]["passengerType"] == "P"

    rendered = _render_sheet(detail)
    assert rendered["bodyHasItinerary"] is True
    by_flight = {seg["flight"]: seg for seg in rendered["segments"]}

    failures = []
    for seg in SEGMENTS:
        flight = f"{AIRLINE}{seg['flight']}"
        card = by_flight.get(flight)
        if card is None or card["text"] is None:
            failures.append(f"{flight}: itinerary card was not rendered")
            continue
        text = card["text"]
        if f"Class: {seg['booking_class']}" not in text or seg["seat"] not in text:
            failures.append(f"{flight}: card text is missing the segment identity: {text!r}")
            continue
        expected = SCHEMA_LABELS.get(seg["passenger_type"])
        if expected is None:
            for label in SCHEMA_LABELS.values():
                if label in text:
                    failures.append(
                        f"{flight}: blank passenger type was labeled {label!r}: {text!r}"
                    )
            continue
        if expected not in text:
            failures.append(
                f"{flight}: passengerType {seg['passenger_type']} is not labeled "
                f"{expected!r} on the itinerary card. Visible text: {text!r}"
            )
        for code, label in SCHEMA_LABELS.items():
            if code != seg["passenger_type"] and label in text and label not in expected:
                failures.append(
                    f"{flight}: card shows {label!r} for passengerType {seg['passenger_type']}: {text!r}"
                )
    if failures:
        raise AssertionError(
            "live itinerary card does not show the Sabre segment passenger type:\n"
            + "\n".join(failures)
        )


if __name__ == "__main__":
    test_single_segment_response_keeps_passenger_type()
    print("  PASS: test_single_segment_response_keeps_passenger_type")
    test_live_itinerary_card_labels_segment_passenger_type()
    print("  PASS: test_live_itinerary_card_labels_segment_passenger_type")
    print("ALL TESTS PASS")
