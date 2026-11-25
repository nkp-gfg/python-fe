"""Passenger detail must return purchased and consumed ancillary counts.

GetPassengerData AEDetails defines QuantityBought (units purchased) and
QuantityUsed (units already used). It does not define Quantity. The detail
route must copy both counts onto each ancillary item:

- quantity: integer purchased count from QuantityBought
- quantityUsed: integer consumed count from QuantityUsed

These tests call GET /flights/{flight}/passengers/{pnr}/detail with no
credential. The stored passenger list is stubbed. The Sabre HTTP exchange is
stubbed at SabreClient._post so the response XML still runs through
get_passenger_data, xmltodict parsing, namespace extraction, and
convert_passenger_data.
"""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
for _name in (
    "SABRE_BASE_URL",
    "SABRE_CPAID",
    "SABRE_USERNAME",
    "SABRE_PASSWORD",
    "SABRE_PSEUDO_CITY_CODE",
    "SABRE_ORGANIZATION",
    "SABRE_DOMAIN",
):
    os.environ.setdefault(_name, "placeholder")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.passengers import router
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml"
)

_SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""

# Same envelope and AEDetails shape as the vendor sample. Quantity is not
# part of AEDetails; it is present here only as a decoy that must be ignored.
_MULTI_ITEM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>TEST</LastName>
          <FirstName>PAX</FirstName>
          <PassengerID>DB15B93A0101</PassengerID>
          <PNRLocator nameAssociationID="1">MULTI1</PNRLocator>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>2</SegmentID>
              <Airline>EY</Airline>
              <Flight>1</Flight>
              <Origin>AUH</Origin>
              <Destination>FRA</Destination>
              <AEDetailsList>
                <ns2:AEDetails>
                  <ns2:ItemID>SEAT</ns2:ItemID>
                  <ns2:ATPCOGroupCode>SA</ns2:ATPCOGroupCode>
                  <ns2:StatusCode>HK</ns2:StatusCode>
                  <ns2:UsedEMD>true</ns2:UsedEMD>
                  <ns2:Quantity>9</ns2:Quantity>
                  <ns2:QuantityUsed>2</ns2:QuantityUsed>
                  <ns2:QuantityBought>3</ns2:QuantityBought>
                </ns2:AEDetails>
                <ns2:AEDetails>
                  <ns2:ItemID>BAG</ns2:ItemID>
                  <ns2:ATPCOGroupCode>BG</ns2:ATPCOGroupCode>
                  <ns2:StatusCode>HI</ns2:StatusCode>
                  <ns2:UsedEMD>false</ns2:UsedEMD>
                  <ns2:QuantityUsed>1</ns2:QuantityUsed>
                  <ns2:QuantityBought>4</ns2:QuantityBought>
                </ns2:AEDetails>
              </AEDetailsList>
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _vendor_response_xml():
    """Response envelope from the vendor sample, with the session token redacted."""
    text = _SAMPLE.read_text(encoding="utf-8")
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    xml = text[start:end]
    xml, replaced = re.subn(
        r"(<wsse:BinarySecurityToken[^>]*>)[^<]*(</wsse:BinarySecurityToken>)",
        r"\1placeholder-token\2",
        xml,
        count=1,
    )
    if replaced != 1:
        raise AssertionError("vendor sample session token was not redacted")
    return xml


class _PassengerList:
    def __init__(self, pnr):
        self._pnr = pnr

    def find_one(self, query, sort=None):
        if query.get("passengers.pnr") != self._pnr:
            return None
        return {
            "flightNumber": query.get("flightNumber"),
            "origin": "AUH",
            "departureDate": query.get("departureDate"),
            "passengers": [
                {"pnr": self._pnr, "lastName": "TEST", "firstName": "PAX"},
            ],
        }


class _DB:
    def __init__(self, pnr):
        self._lists = _PassengerList(pnr)

    def __getitem__(self, name):
        if name != "passenger_list":
            raise KeyError(name)
        return self._lists


def _sabre_post(response_xml):
    """Stand in for the HTTP POST. Instance methods bind this function."""
    actions = []

    def _post(client, action, body, timeout=30, endpoint=None):
        actions.append(action)
        if action == "SessionCreateRQ":
            return _SESSION_XML, 200, 1, body
        if action == "GetPassengerDataRQ":
            return response_xml, 200, 1, body
        if action == "SessionCloseRQ":
            return "<ok/>", 200, 1, body
        raise AssertionError(f"unexpected Sabre action {action}")

    _post.actions = actions
    return _post


def _ancillaries(payload):
    items = []
    for passenger in payload["passengers"]:
        for segment in passenger["itinerary"]:
            items.extend(segment["aeDetails"])
    return items


def _assert_counts(test, ae, item_id, bought, used):
    test.assertEqual(ae["itemId"], item_id)
    actual_used = ae.get("quantityUsed", "<missing>")
    quantity_ok = type(ae.get("quantity")) is int and ae.get("quantity") == bought
    used_ok = type(ae.get("quantityUsed")) is int and ae.get("quantityUsed") == used
    test.assertTrue(
        quantity_ok and used_ok,
        f"{item_id}: quantity={ae.get('quantity')!r} expected QuantityBought {bought}; "
        f"quantityUsed={actual_used!r} expected QuantityUsed {used}",
    )


class PassengerDetailQuantityUsedTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _get_detail(self, response_xml, pnr):
        transport = _sabre_post(response_xml)
        with patch.object(SabreClient, "_post", transport), patch(
            "backend.api.routes.passengers.get_db", return_value=_DB(pnr)
        ):
            response = self.client.get(
                f"/flights/1/passengers/{pnr}/detail",
                params={"origin": "AUH", "date": "2018-05-05", "airline": "EY"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("authorization", {k.lower() for k in response.request.headers})
        self.assertIn("GetPassengerDataRQ", transport.actions)
        return response.json()

    def test_vendor_seat_sample_reports_bought_and_used_counts(self):
        payload = self._get_detail(_vendor_response_xml(), "VWLGVM")
        items = _ancillaries(payload)
        self.assertEqual(len(items), 1)
        seat = items[0]
        self.assertEqual(seat["groupCode"], "SA")
        # UsedEMD is an EMD-type flag, not the consumed count. The sample
        # sets that flag while QuantityUsed is 0 and QuantityBought is 1.
        self.assertEqual(seat["usedEMD"], "true")
        _assert_counts(self, seat, "F", bought=1, used=0)

    def test_each_ancillary_keeps_its_own_consumed_count(self):
        payload = self._get_detail(_MULTI_ITEM_XML, "MULTI1")
        items = _ancillaries(payload)
        self.assertEqual(len(items), 2)
        by_id = {item["itemId"]: item for item in items}
        # Purchased count comes from QuantityBought (3), not the decoy Quantity (9).
        _assert_counts(self, by_id["SEAT"], "SEAT", bought=3, used=2)
        _assert_counts(self, by_id["BAG"], "BAG", bought=4, used=1)
        self.assertNotEqual(by_id["SEAT"]["quantity"], by_id["SEAT"]["quantityUsed"])


if __name__ == "__main__":
    unittest.main()
