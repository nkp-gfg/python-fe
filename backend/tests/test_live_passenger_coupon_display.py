"""Coupon number stored with a ticket must be visible beside that ticket.

The live GetPassengerData response carries VCRNumber text plus the
couponNumber attribute. The flight-board list carries VCR_Data SerialNumber
plus CouponNumber. These tests drive the real SOAP parse, the real
converters, and the passenger routes, then render the sheet and the board
with that JSON.

They fail while the screens print the ticket and leave the coupon off.
Reservation tickets are not covered: convert_reservations stores Ticketing
@RPH as ticketNumber and does not keep a coupon field.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.api.routes import passengers as passengers_route
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

FRONTEND = REPO / "frontend"
VITEST_CONFIG = FRONTEND / "vitest.coupon.config.ts"
UI_TEST = FRONTEND / "tests" / "passenger-coupon-display.test.tsx"


def _passenger_data_envelope(passengers):
    blocks = []
    for index, pax in enumerate(passengers, start=1):
        coupon = pax.get("coupon")
        coupon_attr = f' couponNumber="{coupon}"' if coupon is not None else ""
        blocks.append(
            f"""
        <PassengerDataResponse>
          <LineNumber>{index}</LineNumber>
          <LastName>{pax["lastName"]}</LastName>
          <FirstName>{pax["firstName"]}</FirstName>
          <PassengerID>{pax["passengerId"]}</PassengerID>
          <PNRLocator nameAssociationID="{index}">{pax["pnr"]}</PNRLocator>
          <VCRNumber{coupon_attr}>{pax["ticket"]}</VCRNumber>
        </PassengerDataResponse>"""
        )
    body = "\n".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        {body}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_list_envelope(passengers):
    blocks = []
    for index, pax in enumerate(passengers, start=1):
        coupon = pax.get("coupon")
        coupon_xml = f"<CouponNumber>{coupon}</CouponNumber>" if coupon is not None else ""
        blocks.append(
            f"""
        <PassengerInfo>
          <LineNumber>{index}</LineNumber>
          <PassengerID>{pax["passengerId"]}</PassengerID>
          <Name_Details>
            <LastName>{pax["lastName"]}</LastName>
            <FirstName>{pax["firstName"]}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="{index}">{pax["pnr"]}</PNRLocator>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{pax.get("seat", "42C")}</Seat>
          <Destination>TRN</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <VCR_Info>
            <VCR_Data inUse="true" type="adult">
              <AirlineNumber>055</AirlineNumber>
              {coupon_xml}
              <SerialNumber>{pax["ticket"]}</SerialNumber>
            </VCR_Data>
          </VCR_Info>
        </PassengerInfo>"""
        )
    body = "\n".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _Collection:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, query, sort=None):
        return self._doc


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return _Collection(self._collections.get(name))


class _ParsingSabre:
    """Run the product SOAP parse and body extract. No live Sabre call."""

    xml = ""
    response_key = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _parsed_body(self):
        holder = object.__new__(SabreClient)
        parsed = SabreClient._parse_xml(self.xml)
        return SabreClient._extract_body(holder, parsed, self.response_key)

    def get_passenger_data(self, **kwargs):
        return self._parsed_body(), self.xml, {}


def _client(monkeypatch, database, xml, response_key):
    sabre = _ParsingSabre()
    sabre.xml = xml
    sabre.response_key = response_key
    monkeypatch.setattr(passengers_route, "get_db", lambda: database)
    monkeypatch.setattr(passengers_route, "SabreClient", lambda: sabre)
    app = FastAPI()
    app.include_router(passengers_route.router)
    return TestClient(app)


def _detail_response(monkeypatch, passengers, opened_pnr):
    xml = _passenger_data_envelope(passengers)
    opened = next(pax for pax in passengers if pax["pnr"] == opened_pnr)
    database = _Database(
        {
            "passenger_list": {
                "flightNumber": "4",
                "origin": "FCO",
                "departureDate": "2016-03-16",
                "passengers": [
                    {
                        "pnr": opened["pnr"],
                        "lastName": opened["lastName"],
                        "firstName": opened["firstName"],
                    }
                ],
            }
        }
    )
    client = _client(monkeypatch, database, xml, "GetPassengerDataRS")
    response = client.get(
        f"/flights/4/passengers/{opened_pnr}/detail",
        params={"origin": "FCO", "date": "2016-03-16", "airline": "GF"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _list_response(monkeypatch, passengers):
    xml = _passenger_list_envelope(passengers)
    holder = object.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(holder, parsed, "GetPassengerListRS")
    stored = convert_passenger_list(body, "GF", "4", "2016-03-16", "FCO")
    database = _Database({"passenger_list": stored, "reservations": None})
    client = _client(monkeypatch, database, xml, "GetPassengerListRS")
    response = client.get(
        "/flights/4/passengers",
        params={"origin": "FCO", "date": "2016-03-16"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_live_detail_keeps_sample_coupon(monkeypatch):
    payload = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "STEVE",
                "firstName": "J",
                "passengerId": "B784C60F0001",
                "pnr": "OJLCOK",
                "ticket": "0552120002468",
                "coupon": "1",
            },
            {
                "lastName": "OTHER",
                "firstName": "K",
                "passengerId": "B784C60F0002",
                "pnr": "OTHER1",
                "ticket": "1760000000004",
                "coupon": "4",
            },
        ],
        "OJLCOK",
    )
    first = payload["passengers"][0]
    second = payload["passengers"][1]
    assert first["vcrNumber"] == "0552120002468"
    assert first["couponNumber"] == "1"
    assert first["pnr"] == "OJLCOK"
    assert second["vcrNumber"] == "1760000000004"
    assert second["couponNumber"] == "4"


def test_live_detail_keeps_a_different_coupon(monkeypatch):
    payload = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "NASH",
                "firstName": "A",
                "passengerId": "PAX000000004",
                "pnr": "NASH01",
                "ticket": "1761234567890",
                "coupon": "4",
            }
        ],
        "NASH01",
    )
    passenger = payload["passengers"][0]
    assert passenger["vcrNumber"] == "1761234567890"
    assert passenger["couponNumber"] == "4"


def test_live_detail_blank_coupon_when_attribute_absent(monkeypatch):
    payload = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "BARE",
                "firstName": "T",
                "passengerId": "PAX000000000",
                "pnr": "BARE01",
                "ticket": "0552000000001",
                "coupon": None,
            }
        ],
        "BARE01",
    )
    passenger = payload["passengers"][0]
    assert passenger["vcrNumber"] == "0552000000001"
    assert passenger["couponNumber"] == ""


def test_passenger_list_keeps_coupon_beside_serial(monkeypatch):
    payload = _list_response(
        monkeypatch,
        [
            {
                "lastName": "STEVE",
                "firstName": "J",
                "passengerId": "B784C60F0001",
                "pnr": "OJLCOK",
                "ticket": "5438287621",
                "coupon": "2",
                "seat": "42C",
            },
            {
                "lastName": "NASH",
                "firstName": "A",
                "passengerId": "PAX000000007",
                "pnr": "NASH01",
                "ticket": "1761234567890",
                "coupon": "7",
                "seat": "12A",
            },
            {
                "lastName": "BARE",
                "firstName": "T",
                "passengerId": "PAX000000000",
                "pnr": "BARE01",
                "ticket": "0552000000001",
                "coupon": None,
                "seat": "1A",
            },
        ],
    )
    by_pnr = {pax["pnr"]: pax for pax in payload["passengers"]}
    assert by_pnr["OJLCOK"]["ticketNumber"] == "5438287621"
    assert by_pnr["OJLCOK"]["vcrCouponNumber"] == "2"
    assert by_pnr["NASH01"]["ticketNumber"] == "1761234567890"
    assert by_pnr["NASH01"]["vcrCouponNumber"] == "7"
    assert by_pnr["BARE01"]["ticketNumber"] == "0552000000001"
    assert by_pnr["BARE01"]["vcrCouponNumber"] == ""


def _ui_fixtures(monkeypatch):
    sample = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "STEVE",
                "firstName": "J",
                "passengerId": "B784C60F0001",
                "pnr": "OJLCOK",
                "ticket": "0552120002468",
                "coupon": "1",
            },
            {
                "lastName": "OTHER",
                "firstName": "K",
                "passengerId": "B784C60F0002",
                "pnr": "OTHER1",
                "ticket": "1760000000004",
                "coupon": "4",
            },
        ],
        "OJLCOK",
    )
    other = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "NASH",
                "firstName": "A",
                "passengerId": "PAX000000004",
                "pnr": "NASH01",
                "ticket": "1761234567890",
                "coupon": "4",
            }
        ],
        "NASH01",
    )
    bare = _detail_response(
        monkeypatch,
        [
            {
                "lastName": "BARE",
                "firstName": "T",
                "passengerId": "PAX000000000",
                "pnr": "BARE01",
                "ticket": "0552000000001",
                "coupon": None,
            }
        ],
        "BARE01",
    )
    listed = _list_response(
        monkeypatch,
        [
            {
                "lastName": "STEVE",
                "firstName": "J",
                "passengerId": "B784C60F0001",
                "pnr": "OJLCOK",
                "ticket": "5438287621",
                "coupon": "2",
                "seat": "42C",
            },
            {
                "lastName": "NASH",
                "firstName": "A",
                "passengerId": "PAX000000007",
                "pnr": "NASH01",
                "ticket": "1761234567890",
                "coupon": "7",
                "seat": "12A",
            },
            {
                "lastName": "BARE",
                "firstName": "T",
                "passengerId": "PAX000000000",
                "pnr": "BARE01",
                "ticket": "0552000000001",
                "coupon": None,
                "seat": "1A",
            },
        ],
    )
    return {
        "detailCases": [
            {
                "name": "sample ticket 0552120002468 shows coupon 1 and not the other traveler coupon 4",
                "flightNumber": "4",
                "origin": "FCO",
                "date": "2016-03-16",
                "pnr": "OJLCOK",
                "response": sample,
                "ticket": "0552120002468",
                "coupon": "1",
                "hiddenCoupons": ["4"],
            },
            {
                "name": "ticket 1761234567890 shows coupon 4",
                "flightNumber": "4",
                "origin": "FCO",
                "date": "2016-03-16",
                "pnr": "NASH01",
                "response": other,
                "ticket": "1761234567890",
                "coupon": "4",
                "hiddenCoupons": ["1"],
            },
            {
                "name": "ticket without a coupon attribute does not invent one",
                "flightNumber": "4",
                "origin": "FCO",
                "date": "2016-03-16",
                "pnr": "BARE01",
                "response": bare,
                "ticket": "0552000000001",
                "coupon": None,
                "hiddenCoupons": [],
            },
        ],
        "listCases": [
            {
                "name": "board ticket cells show each stored coupon",
                "flightNumber": "4",
                "origin": "FCO",
                "date": "2016-03-16",
                "response": listed,
                "rows": [
                    {"ticket": "5438287621", "coupon": "2"},
                    {"ticket": "1761234567890", "coupon": "7"},
                    {"ticket": "0552000000001", "coupon": None},
                ],
            }
        ],
    }


def test_screens_show_the_stored_coupon(monkeypatch, tmp_path):
    fixtures = _ui_fixtures(monkeypatch)
    fixture_path = tmp_path / "coupon-fixtures.json"
    fixture_path.write_text(json.dumps(fixtures))
    env = os.environ.copy()
    env["COUPON_FIXTURE"] = str(fixture_path)
    completed = subprocess.run(
        [
            "npx",
            "vitest",
            "run",
            "--config",
            str(VITEST_CONFIG),
            str(UI_TEST),
        ],
        cwd=str(FRONTEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stdout + completed.stderr
