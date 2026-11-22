"""Live passenger detail must keep the ticket and coupon on each itinerary VCR.

GetPassengerData puts VCRNumber and its couponNumber attribute on each
itinerary VCRInfo element (and, when sent, CreateDate and FareCalculation).
convert_passenger_data currently keeps only the fare basis and bag allowance
on that record, and the passenger detail route returns the converted document.

These tests fail while that omission is present. They drive the route the
product exposes: a stored name lookup, SabreClient parsing of the SOAP body,
then convert_passenger_data. The network call and the database are stood in
for; the parser, extractor, converter, and route return are the real ones.
"""

import os
import re
from pathlib import Path

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1")
os.environ["REDIS_URL"] = (
    "redis://127.0.0.1:1/0?socket_connect_timeout=0.2&socket_timeout=0.2"
)

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import passengers
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_3.xml"
)

_SECURITY = re.compile(
    r"<wsse:Security\b[^>]*>.*?</wsse:Security>",
    re.DOTALL,
)

# Distinct from the single passenger-level coupon so a fix cannot copy that
# one field onto every segment and still pass.
_SEGMENT_TICKET = "1000000000001"
_SECOND_TICKET = "1000000000002"
_SEGMENT_FARE = "FCO GF BAH100.00NUC100.00END"
_SECOND_FARE = "BAH GF MCT50.00NUC50.00END"

_MULTI_COUPON_RS = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>STEVE</LastName>
          <FirstName>J</FirstName>
          <PassengerID>PAX1</PassengerID>
          <PNRLocator nameAssociationID="1">OJLCOK</PNRLocator>
          <VCRNumber couponNumber="1">{_SEGMENT_TICKET}</VCRNumber>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <Airline>GF</Airline>
              <Flight>4</Flight>
              <Origin>FCO</Origin>
              <Destination>BAH</Destination>
              <VCRInfoList>
                <VCRInfo>
                  <VCRNumber couponNumber="1">{_SEGMENT_TICKET}</VCRNumber>
                  <CreateDate>2016-03-01</CreateDate>
                  <FareBasisCode>YOWFLN1</FareBasisCode>
                  <FareCalculation>{_SEGMENT_FARE}</FareCalculation>
                  <BagAllowance unit="PC">2</BagAllowance>
                </VCRInfo>
                <VCRInfo>
                  <VCRNumber couponNumber="3">{_SECOND_TICKET}</VCRNumber>
                  <CreateDate>2016-03-02</CreateDate>
                  <FareBasisCode>YFLEX</FareBasisCode>
                  <FareCalculation>{_SECOND_FARE}</FareCalculation>
                  <BagAllowance>1</BagAllowance>
                </VCRInfo>
              </VCRInfoList>
            </PassengerItinerary>
            <PassengerItinerary>
              <SegmentID>2</SegmentID>
              <Airline>GF</Airline>
              <Flight>5</Flight>
              <Origin>BAH</Origin>
              <Destination>MCT</Destination>
              <VCRInfoList>
                <VCRInfo>
                  <VCRNumber couponNumber="2">{_SEGMENT_TICKET}</VCRNumber>
                  <CreateDate>2016-03-01</CreateDate>
                  <FareBasisCode>YOWFLN1</FareBasisCode>
                  <FareCalculation>{_SEGMENT_FARE}</FareCalculation>
                  <BagAllowance unit="KG">20</BagAllowance>
                </VCRInfo>
              </VCRInfoList>
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _vendor_response_envelope() -> str:
    """Return the sample's response envelope with the security header removed.

    The published sample file also contains a request and a header token.
    SabreClient only reads the SOAP body, so dropping the header leaves the
    same GetPassengerDataRS the route converts.
    """
    text = _SAMPLE.read_text(encoding="utf-8")
    marker = "<!--Response-->"
    start = text.find(marker)
    assert start != -1, "vendor sample has no response section"
    envelope = text[start + len(marker):]
    end = envelope.find("</soap-env:Envelope>")
    assert end != -1, "vendor sample response is not a soap-env envelope"
    envelope = envelope[: end + len("</soap-env:Envelope>")]
    return _SECURITY.sub("", envelope)


class _PassengerList:
    def __init__(self, pnr, last_name, first_name, flight_number, departure_date):
        self._doc = {
            "flightNumber": flight_number,
            "departureDate": departure_date,
            "passengers": [
                {
                    "pnr": "OTHER1",
                    "lastName": "NOTTHIS",
                    "firstName": "NO",
                },
                {
                    "pnr": pnr,
                    "lastName": last_name,
                    "firstName": first_name,
                },
            ],
        }

    def find_one(self, query, sort=None):
        if query.get("passengers.pnr") != self._doc["passengers"][1]["pnr"]:
            return None
        if query.get("flightNumber") != self._doc["flightNumber"]:
            return None
        if query.get("departureDate") != self._doc["departureDate"]:
            return None
        return self._doc


class _DB:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, name):
        assert name == "passenger_list"
        return self._collection


def _offline():
    raise RuntimeError("offline")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("backend.api.main.get_db", _offline)
    monkeypatch.setattr("backend.api.main.get_redis", _offline)
    with TestClient(app) as test_client:
        yield test_client


def _install_sabre_response(monkeypatch, response_xml, pnr, last_name, first_name,
                            flight_number, departure_date):
    """Serve response_xml from SabreClient._post and the matching stored name."""

    def _init(self):
        self._token = "placeholder-token"
        self._conversation_id = "placeholder-conversation"
        self._endpoint = "http://sabre.invalid/placeholder"
        self._cpaid = "IPCC"
        self._last_call_time = 0.0

    def _create_session(self):
        self._token = "placeholder-token"
        self._conversation_id = "placeholder-conversation"

    def _close_session(self):
        return None

    def _post(self, action, body, timeout=30, endpoint=None):
        assert action == "GetPassengerDataRQ"
        assert f"<LastName>{last_name}</LastName>" in body
        assert f"<PNRLocator>{pnr}</PNRLocator>" in body
        assert f"<Flight>{flight_number}</Flight>" in body
        return response_xml, 200, 1, body

    monkeypatch.setattr(passengers.SabreClient, "__init__", _init)
    monkeypatch.setattr(passengers.SabreClient, "create_session", _create_session)
    monkeypatch.setattr(passengers.SabreClient, "close_session", _close_session)
    monkeypatch.setattr(passengers.SabreClient, "_post", _post)
    monkeypatch.setattr(
        passengers,
        "get_db",
        lambda: _DB(_PassengerList(
            pnr, last_name, first_name, flight_number, departure_date)),
    )


def _get_detail(client, monkeypatch, response_xml, *, flight_number, pnr, origin,
                date, airline, last_name, first_name):
    _install_sabre_response(
        monkeypatch, response_xml, pnr, last_name, first_name,
        flight_number, date,
    )
    response = client.get(
        f"/flights/{flight_number}/passengers/{pnr}/detail",
        params={"origin": origin, "date": date, "airline": airline},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_vcr(info, *, ticket, coupon, fare_basis, create_date=None,
                fare_calculation=None):
    assert info.get("fareBasisCode") == fare_basis
    assert info.get("vcrNumber") == ticket
    assert info.get("couponNumber") == coupon
    if create_date is not None:
        assert info.get("createDate") == create_date
    if fare_calculation is not None:
        assert info.get("fareCalculation") == fare_calculation


def test_parse_and_extract_keep_itinerary_vcr_number():
    """The SOAP parser the client uses does see the itinerary ticket and coupon."""
    parsed = SabreClient._parse_xml(_vendor_response_envelope())
    data = SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, "GetPassengerDataRS")
    pax = data["PassengerDataResponseList"]["PassengerDataResponse"]
    vcr = pax["PassengerItineraryList"]["PassengerItinerary"]["VCRInfoList"]["VCRInfo"]
    assert vcr["VCRNumber"]["#text"] == "0552120002468"
    assert vcr["VCRNumber"]["@couponNumber"] == "1"
    assert vcr["FareBasisCode"] == "YOWFLN1"


def test_live_detail_returns_vendor_itinerary_ticket_and_coupon(client, monkeypatch):
    """Vendor sample coupon 1 / ticket on VCRInfo must survive the detail route."""
    body = _get_detail(
        client,
        monkeypatch,
        _vendor_response_envelope(),
        flight_number="4",
        pnr="OJLCOK",
        origin="FCO",
        date="2016-03-16",
        airline="AZ",
        last_name="STEVE",
        first_name="J",
    )
    passenger = body["passengers"][0]
    # The passenger-level element is a different field and is already copied.
    # It does not replace the itinerary record the sample also sends.
    assert passenger["vcrNumber"] == "0552120002468"
    assert passenger["couponNumber"] == "1"
    assert passenger["pnr"] == "OJLCOK"
    info = passenger["itinerary"][0]["vcrInfo"][0]
    _assert_vcr(
        info,
        ticket="0552120002468",
        coupon="1",
        fare_basis="YOWFLN1",
    )


def test_live_detail_returns_each_segment_coupon(client, monkeypatch):
    """Each VCRInfo keeps its own ticket, coupon, create date, and fare calc.

    The passenger-level VCRNumber is only coupon 1 of the first ticket. Segment
    2 uses coupon 2 of that ticket, and the first segment also carries a second
    virtual coupon with a different ticket number.
    """
    body = _get_detail(
        client,
        monkeypatch,
        _MULTI_COUPON_RS,
        flight_number="4",
        pnr="OJLCOK",
        origin="FCO",
        date="2016-03-16",
        airline="GF",
        last_name="STEVE",
        first_name="J",
    )
    passenger = body["passengers"][0]
    assert passenger["vcrNumber"] == _SEGMENT_TICKET
    assert passenger["couponNumber"] == "1"
    first, second = passenger["itinerary"]
    _assert_vcr(
        first["vcrInfo"][0],
        ticket=_SEGMENT_TICKET,
        coupon="1",
        fare_basis="YOWFLN1",
        create_date="2016-03-01",
        fare_calculation=_SEGMENT_FARE,
    )
    _assert_vcr(
        first["vcrInfo"][1],
        ticket=_SECOND_TICKET,
        coupon="3",
        fare_basis="YFLEX",
        create_date="2016-03-02",
        fare_calculation=_SECOND_FARE,
    )
    _assert_vcr(
        second["vcrInfo"][0],
        ticket=_SEGMENT_TICKET,
        coupon="2",
        fare_basis="YOWFLN1",
        create_date="2016-03-01",
        fare_calculation=_SEGMENT_FARE,
    )
