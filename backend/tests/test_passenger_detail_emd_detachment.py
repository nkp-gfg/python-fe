"""Live passenger detail must keep EMD detachment separate from the in-use flag.

Sabre GetPassengerData puts ancillary charges on each itinerary segment in
AEDetails. UsedEMD means the charge is in use. Disassociated means the
electronic miscellaneous document was detached from the ticket. EMDTicketNumber
is that document number and may carry a couponNumber attribute.

get_passenger_detail asks Sabre for one stored passenger and returns
convert_passenger_data with no further field filter. These tests drive that
route with a SOAP response and fail until every ancillary item includes:

- usedEMD: the UsedEMD in-use flag, unchanged
- disassociated: the Disassociated flag; empty string when Sabre omits it
- emdTicketNumber: the EMDTicketNumber text; empty string when Sabre omits it
- emdCouponNumber: the couponNumber attribute; empty string when absent

A present flag may be the raw XML string ("true" / "false") or a bool.
usedEMD and disassociated stay independent: a detached charge that is not in
use must not be reported as in use, and an in-use charge must not be reported
as detached.

Not covered here:
- convert_passenger_list never reads AEDetailsList on GetPassengerListRS.
- convert_reservations maps Trip_Search AncillaryService / EMDNumber, a
  different document shape.
- The passenger detail sheet's ancillary table only renders the fields the
  API returns today. It is not part of this contract.
"""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import passengers as passengers_routes
from backend.feeder.converter import _ensure_list, _strip_ns
from backend.sabre.client import SabreClient


FLIGHT = "0317"
ORIGIN = "CDG"
DATE = "2016-03-17"
AIRLINE = "GF"
PNR = "OLRKTG"
LAST_NAME = "JOHN"
FIRST_NAME = "M"

# Document numbers exist only on EMDTicketNumber so a passing test cannot
# pick them up from another copied element.
DETACHED_BAG = {
    "item_id": "5",
    "group": "BG",
    "used_emd": False,
    "disassociated": True,
    "ticket": "1764400111111",
    "coupon": "1",
}
IN_USE_SEAT = {
    "item_id": "8",
    "group": "SA",
    "used_emd": True,
    "disassociated": False,
    "ticket": "1764400222222",
    "coupon": "2",
}
DETACHED_NO_COUPON = {
    "item_id": "9",
    "group": "BG",
    "used_emd": False,
    "disassociated": True,
    "ticket": "1764400333333",
    "coupon": "",
}
NO_EMD_FIELDS = {
    "item_id": "10",
    "group": "ML",
    "used_emd": False,
    "disassociated": None,
    "ticket": "",
    "coupon": "",
}

_SABRE_ENV = {
    "SABRE_BASE_URL": "https://placeholder.invalid/sabre",
    "SABRE_CPAID": "PLACEHOLDER",
    "SABRE_USERNAME": "placeholder-user",
    "SABRE_PASSWORD": "placeholder-password",
    "SABRE_PSEUDO_CITY_CODE": "PLACEHOLDER",
    "SABRE_ORGANIZATION": "PLACEHOLDER",
    "SABRE_DOMAIN": "PLACEHOLDER",
}


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _emd_ticket_xml(ticket: str, coupon: str) -> str:
    if not ticket:
        return ""
    if coupon:
        return (
            f"<ns2:EMDTicketNumber couponNumber=\"{coupon}\">"
            f"{ticket}</ns2:EMDTicketNumber>"
        )
    return f"<ns2:EMDTicketNumber>{ticket}</ns2:EMDTicketNumber>"


def _ae_xml(spec: dict) -> str:
    disassociated = ""
    if spec["disassociated"] is not None:
        disassociated = f"<ns2:Disassociated>{_flag(spec['disassociated'])}</ns2:Disassociated>"
    return f"""<ns2:AEDetails>
        <ns2:ItemID>{spec['item_id']}</ns2:ItemID>
        <ns2:ATPCOGroupCode>{spec['group']}</ns2:ATPCOGroupCode>
        <ns2:StatusCode>HD</ns2:StatusCode>
        <ns2:UsedEMD>{_flag(spec['used_emd'])}</ns2:UsedEMD>
        <ns2:Quantity>1</ns2:Quantity>
        {disassociated}
        {_emd_ticket_xml(spec['ticket'], spec['coupon'])}
    </ns2:AEDetails>"""


def _itinerary_xml(segment_id: str, details: list[dict]) -> str:
    items = "\n".join(_ae_xml(spec) for spec in details)
    return f"""<PassengerItinerary>
        <SegmentID>{segment_id}</SegmentID>
        <Airline>GF</Airline>
        <Flight>{FLIGHT}</Flight>
        <Origin>{ORIGIN}</Origin>
        <Destination>BAH</Destination>
        <DepartureDate>{DATE}</DepartureDate>
        <AEDetailsList>
            {items}
        </AEDetailsList>
    </PassengerItinerary>"""


def passenger_data_response_xml() -> str:
    """SOAP body shaped like the GetPassengerData baggage sample.

    Segment 1 has one AEDetails element (xmltodict yields a dict).
    Segment 2 has three (xmltodict yields a list). Both shapes go through
    the same ancillary mapping.
    """
    segment_one = _itinerary_xml("1", [DETACHED_BAG])
    segment_two = _itinerary_xml(
        "2", [IN_USE_SEAT, DETACHED_NO_COUPON, NO_EMD_FIELDS]
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>{LAST_NAME}</LastName>
          <FirstName>{FIRST_NAME}</FirstName>
          <PassengerID>B7FCF1EE0001</PassengerID>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <PassengerItineraryList>
            {segment_one}
            {segment_two}
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == FLIGHT
        assert query["departureDate"] == DATE
        assert query["passengers.pnr"] == PNR
        return {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "passengers": [
                {
                    "pnr": PNR,
                    "lastName": LAST_NAME,
                    "firstName": FIRST_NAME,
                },
                {
                    "pnr": "OTHER1",
                    "lastName": "OTHER",
                    "firstName": "X",
                },
            ],
        }


class _Db:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def _parsed_ancillaries(xml: str) -> dict[str, dict]:
    """Run the same parse, body extract, and namespace strip the route uses."""
    client = object.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(client, parsed, "GetPassengerDataRS")
    data = _strip_ns(body)
    responses = _ensure_list(
        (data.get("PassengerDataResponseList") or {}).get("PassengerDataResponse", [])
    )
    found = {}
    for pax in responses:
        segments = _ensure_list(
            (pax.get("PassengerItineraryList") or {}).get("PassengerItinerary", [])
        )
        for seg in segments:
            for ae in _ensure_list((seg.get("AEDetailsList") or {}).get("AEDetails", [])):
                item_id = ae.get("ItemID", "")
                found[item_id] = ae
    return found


def _ticket_text_and_coupon(emd) -> tuple[str, str]:
    if isinstance(emd, dict):
        return str(emd.get("#text", "")), str(emd.get("@couponNumber", ""))
    if emd is None:
        return "", ""
    return str(emd), ""


def _assert_source_kept_detachment(xml: str) -> None:
    parsed = _parsed_ancillaries(xml)
    expected = {
        spec["item_id"]: spec
        for spec in (DETACHED_BAG, IN_USE_SEAT, DETACHED_NO_COUPON, NO_EMD_FIELDS)
    }
    assert set(parsed) == set(expected)
    for item_id, spec in expected.items():
        ae = parsed[item_id]
        assert ae.get("UsedEMD") == _flag(spec["used_emd"])
        if spec["disassociated"] is None:
            assert "Disassociated" not in ae
        else:
            assert ae.get("Disassociated") == _flag(spec["disassociated"])
        text, coupon = _ticket_text_and_coupon(ae.get("EMDTicketNumber"))
        assert text == spec["ticket"]
        assert coupon == spec["coupon"]


def _assert_flag(actual, expected: bool | None) -> None:
    if expected is None:
        assert actual == ""
        return
    if isinstance(actual, bool):
        assert actual is expected
        return
    assert actual == _flag(expected)


def _ancillaries_from_response(payload: dict) -> dict[str, dict]:
    found = {}
    for pax in payload["passengers"]:
        for seg in pax["itinerary"]:
            for ae in seg["aeDetails"]:
                found[ae["itemId"]] = ae
    return found


def _assert_returned_ancillary(ae: dict, spec: dict) -> None:
    assert "disassociated" in ae
    assert "emdTicketNumber" in ae
    assert "emdCouponNumber" in ae
    _assert_flag(ae["usedEMD"], spec["used_emd"])
    _assert_flag(ae["disassociated"], spec["disassociated"])
    assert str(ae["emdTicketNumber"]) == spec["ticket"]
    assert str(ae["emdCouponNumber"]) == spec["coupon"]
    # The in-use flag must not be overwritten with the detachment flag.
    if spec["used_emd"] != spec["disassociated"] and spec["disassociated"] is not None:
        _assert_flag(ae["usedEMD"], spec["used_emd"])
        _assert_flag(ae["disassociated"], spec["disassociated"])


def test_live_passenger_detail_drops_detached_emd(monkeypatch):
    """A caller of live passenger detail receives ancillary rows without detachment.

    The SOAP response carries Disassociated and EMDTicketNumber through parsing.
    The detail response still omits both, while UsedEMD is copied.
    """
    xml = passenger_data_response_xml()
    _assert_source_kept_detachment(xml)

    for name, value in _SABRE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)
    monkeypatch.setattr(passengers_routes, "get_db", lambda: _Db())

    seen = []

    def fake_post(url, data=None, headers=None, timeout=None):
        body = data.decode() if isinstance(data, (bytes, bytearray)) else data
        if "SessionCreateRQ" in body:
            seen.append("session")
            text = (
                "<soap-env:Envelope><soap-env:Body>"
                "<wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>"
                "</soap-env:Body></soap-env:Envelope>"
            )
        elif "GetPassengerDataRQ" in body:
            seen.append("passenger-data")
            required = [
                f"<LastName>{LAST_NAME}</LastName>",
                f"<FirstName>{FIRST_NAME}</FirstName>",
                f"<PNRLocator>{PNR}</PNRLocator>",
                f"<Airline>{AIRLINE}</Airline>",
                f"<Flight>{FLIGHT}</Flight>",
                f"<DepartureDate>{DATE}</DepartureDate>",
                f"<Origin>{ORIGIN}</Origin>",
            ]
            missing = [needle for needle in required if needle not in body]
            assert not missing, (
                "GetPassengerData request did not target the stored passenger: "
                + ", ".join(missing)
            )
            assert "<LastName>OTHER</LastName>" not in body
            text = xml
        elif "SessionCloseRQ" in body:
            seen.append("close")
            text = "<soap-env:Envelope><soap-env:Body/></soap-env:Envelope>"
        else:
            raise AssertionError("unexpected Sabre SOAP request")

        class _Response:
            def __init__(self, response_text):
                self.status_code = 200
                self.text = response_text

        return _Response(text)

    monkeypatch.setattr("backend.sabre.client.requests.post", fake_post)

    app = FastAPI()
    app.include_router(passengers_routes.router)
    client = TestClient(app)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": AIRLINE},
    )

    assert response.status_code == 200, response.text
    assert seen == ["session", "passenger-data", "close"]
    payload = response.json()
    assert "_raw" not in payload
    returned = _ancillaries_from_response(payload)
    assert set(returned) == {
        DETACHED_BAG["item_id"],
        IN_USE_SEAT["item_id"],
        DETACHED_NO_COUPON["item_id"],
        NO_EMD_FIELDS["item_id"],
    }
    _assert_returned_ancillary(returned[DETACHED_BAG["item_id"]], DETACHED_BAG)
    _assert_returned_ancillary(returned[IN_USE_SEAT["item_id"]], IN_USE_SEAT)
    _assert_returned_ancillary(returned[DETACHED_NO_COUPON["item_id"]], DETACHED_NO_COUPON)
    _assert_returned_ancillary(returned[NO_EMD_FIELDS["item_id"]], NO_EMD_FIELDS)


def test_converter_keeps_detachment_for_single_and_list_shapes():
    """The same mapping is what the route returns, for one item and for many.

    Calling convert_passenger_data on the extracted body checks the mapper
    without a second lookup between the parsed document and the rows.
    """
    from backend.feeder.converter import convert_passenger_data

    xml = passenger_data_response_xml()
    _assert_source_kept_detachment(xml)
    client = object.__new__(SabreClient)
    body = SabreClient._extract_body(
        client, SabreClient._parse_xml(xml), "GetPassengerDataRS"
    )
    doc = convert_passenger_data(body, AIRLINE, FLIGHT, DATE, ORIGIN)
    returned = _ancillaries_from_response(doc)
    for spec in (DETACHED_BAG, IN_USE_SEAT, DETACHED_NO_COUPON, NO_EMD_FIELDS):
        _assert_returned_ancillary(returned[spec["item_id"]], spec)
