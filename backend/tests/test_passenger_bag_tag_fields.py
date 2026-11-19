"""Live passenger detail must keep bag weight and bag routing.

GetPassengerData places each checked bag on PassengerItinerary/BagTagInfoList/
BagTagInfo (BagTagDetailsList.ACS → BagTagInfo.ACS). Weight is
WeightAndSize/WeightAndUnit, with the unit in the unit attribute and the
amount in the element text. Cities are BagOrigin and BagFinalDestination.

These tests parse vendor sample responses with SabreClient._parse_xml and
_extract_body, then run convert_passenger_data and the
GET /flights/{flight}/passengers/{pnr}/detail route. They fail while the
converter still reads Weight, Unit, Origin, and Destination.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers as passengers_routes
from backend.feeder.converter import _strip_ns, convert_passenger_data
from backend.sabre.client import SabreClient

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_SAMPLES = os.path.join(_ROOT, "sabre_docs", "GetPassengerDataRQ_v4-0-4_Samples")
_SECURITY = re.compile(r"<wsse:Security\b.*?</wsse:Security>", re.DOTALL)


def _response_envelope(sample_name):
    """Return the response SOAP envelope from a vendor sample file.

    The security header is removed before the XML is parsed. It is not part
    of GetPassengerDataRS, and the client never feeds it to the converter.
    """
    path = os.path.join(_SAMPLES, sample_name)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    marker = "<!--Response-->"
    start = text.find(marker)
    chunk = text[start + len(marker):] if start != -1 else text
    begin = chunk.find("<soap-env:Envelope")
    end = chunk.find("</soap-env:Envelope>")
    assert begin != -1 and end != -1, sample_name
    envelope = chunk[begin:end + len("</soap-env:Envelope>")]
    return _SECURITY.sub("", envelope)


def parse_passenger_data(xml_text):
    """Same parse the Sabre client applies to a GetPassengerDataRS body.

    _extract_body does not use instance state. Calling it on an uninitialized
    client avoids opening a Sabre session.
    """
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerDataRS")


def load_sample(sample_name):
    return parse_passenger_data(_response_envelope(sample_name))


def _bag_tags(doc):
    tags = []
    for passenger in doc["passengers"]:
        for segment in passenger["itinerary"]:
            tags.extend(segment["bagTags"])
    return tags


def _first_bag(raw_body):
    stripped = _strip_ns(raw_body)
    passenger = stripped["PassengerDataResponseList"]["PassengerDataResponse"]
    if isinstance(passenger, list):
        passenger = passenger[0]
    segment = passenger["PassengerItineraryList"]["PassengerItinerary"]
    if isinstance(segment, list):
        segment = segment[0]
    bag = segment["BagTagInfoList"]["BagTagInfo"]
    if isinstance(bag, list):
        bag = bag[0]
    return bag


def _envelope(bag_xml):
    return f"""<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LastName>STEVE</LastName>
          <FirstName>J</FirstName>
          <PNRLocator>OJLCOK</PNRLocator>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <BagTagInfoList>
                {bag_xml}
              </BagTagInfoList>
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _convert_bags(xml_text):
    body = parse_passenger_data(xml_text)
    doc = convert_passenger_data(body, "AZ", "4", "2016-03-16", "FCO")
    return _bag_tags(doc)


def test_vendor_sample_keeps_weight_unit_and_cities():
    body = load_sample("GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_3.xml")
    bag = _first_bag(body)
    weight = bag["WeightAndSize"]["WeightAndUnit"]
    assert weight["#text"] == "10"
    assert weight["@unit"] == "KG"
    assert bag["BagTagNumber"] == "9055300200"
    assert bag["BagOrigin"] == "FCO"
    assert bag["BagFinalDestination"] == "TRN"
    assert "Weight" not in bag
    assert "Origin" not in bag
    assert "Destination" not in bag

    doc = convert_passenger_data(body, "AZ", "4", "2016-03-16", "FCO")
    assert _bag_tags(doc) == [{
        "bagTagNumber": "9055300200",
        "weight": "10",
        "unit": "KG",
        "origin": "FCO",
        "destination": "TRN",
    }]


def test_second_vendor_sample_keeps_its_own_route():
    body = load_sample("GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml")
    bag = _first_bag(body)
    assert bag["WeightAndSize"]["WeightAndUnit"]["#text"] == "10"
    assert bag["WeightAndSize"]["WeightAndUnit"]["@unit"] == "KG"
    assert bag["BagOrigin"] == "CDG"
    assert bag["BagFinalDestination"] == "FCO"

    doc = convert_passenger_data(body, "AZ", "4", "2016-03-16", "CDG")
    assert _bag_tags(doc) == [{
        "bagTagNumber": "9055456456",
        "weight": "10",
        "unit": "KG",
        "origin": "CDG",
        "destination": "FCO",
    }]


def test_cities_are_kept_when_the_sample_omits_weight():
    body = load_sample("GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml")
    bag = _first_bag(body)
    assert "WeightAndSize" not in bag
    assert bag["BagTagNumber"] == "0061464375"
    assert bag["BagOrigin"] == "AUH"
    assert bag["BagFinalDestination"] == "FRA"

    doc = convert_passenger_data(body, "HM", "1", "2018-05-05", "AUH")
    assert _bag_tags(doc) == [{
        "bagTagNumber": "0061464375",
        "weight": "",
        "unit": "",
        "origin": "AUH",
        "destination": "FRA",
    }]


def test_each_bag_on_a_segment_keeps_its_own_weight_and_route():
    xml = _envelope("""
        <BagTagInfo>
          <ns2:BagTagNumber>1000000001</ns2:BagTagNumber>
          <ns2:WeightAndSize>
            <ns2:WeightAndUnit unit="KG">12</ns2:WeightAndUnit>
          </ns2:WeightAndSize>
          <ns2:BagOrigin>BAH</ns2:BagOrigin>
          <ns2:BagFinalDestination>LHR</ns2:BagFinalDestination>
        </BagTagInfo>
        <BagTagInfo>
          <ns2:BagTagNumber>1000000002</ns2:BagTagNumber>
          <ns2:WeightAndSize>
            <ns2:WeightAndUnit unit="LB">40</ns2:WeightAndUnit>
          </ns2:WeightAndSize>
          <ns2:BagOrigin>BAH</ns2:BagOrigin>
          <ns2:BagFinalDestination>DXB</ns2:BagFinalDestination>
        </BagTagInfo>
    """)
    assert _convert_bags(xml) == [
        {
            "bagTagNumber": "1000000001",
            "weight": "12",
            "unit": "KG",
            "origin": "BAH",
            "destination": "LHR",
        },
        {
            "bagTagNumber": "1000000002",
            "weight": "40",
            "unit": "LB",
            "origin": "BAH",
            "destination": "DXB",
        },
    ]


def test_weight_text_without_a_unit_attribute_is_kept():
    xml = _envelope("""
        <BagTagInfo>
          <ns2:BagTagNumber>1000000003</ns2:BagTagNumber>
          <ns2:WeightAndSize>
            <ns2:WeightAndUnit>23</ns2:WeightAndUnit>
          </ns2:WeightAndSize>
          <ns2:BagOrigin>BAH</ns2:BagOrigin>
          <ns2:BagFinalDestination>MCT</ns2:BagFinalDestination>
        </BagTagInfo>
    """)
    assert _convert_bags(xml) == [{
        "bagTagNumber": "1000000003",
        "weight": "23",
        "unit": "",
        "origin": "BAH",
        "destination": "MCT",
    }]


def test_zero_weight_is_kept():
    xml = _envelope("""
        <BagTagInfo>
          <ns2:BagTagNumber>1000000004</ns2:BagTagNumber>
          <ns2:WeightAndSize>
            <ns2:WeightAndUnit unit="LB">0</ns2:WeightAndUnit>
          </ns2:WeightAndSize>
          <ns2:BagOrigin>BAH</ns2:BagOrigin>
          <ns2:BagFinalDestination>DOH</ns2:BagFinalDestination>
        </BagTagInfo>
    """)
    assert _convert_bags(xml) == [{
        "bagTagNumber": "1000000004",
        "weight": "0",
        "unit": "LB",
        "origin": "BAH",
        "destination": "DOH",
    }]


def test_legacy_element_names_remain_a_fallback():
    xml = _envelope("""
        <BagTagInfo>
          <BagTagNumber>1000000005</BagTagNumber>
          <Weight>15</Weight>
          <Unit>KG</Unit>
          <Origin>BAH</Origin>
          <Destination>LHR</Destination>
        </BagTagInfo>
    """)
    assert _convert_bags(xml) == [{
        "bagTagNumber": "1000000005",
        "weight": "15",
        "unit": "KG",
        "origin": "BAH",
        "destination": "LHR",
    }]


def test_schema_fields_win_when_legacy_names_are_also_present():
    xml = _envelope("""
        <BagTagInfo>
          <ns2:BagTagNumber>1000000006</ns2:BagTagNumber>
          <Weight>1</Weight>
          <Unit>LB</Unit>
          <Origin>XXX</Origin>
          <Destination>YYY</Destination>
          <ns2:WeightAndSize>
            <ns2:WeightAndUnit unit="KG">32</ns2:WeightAndUnit>
          </ns2:WeightAndSize>
          <ns2:BagOrigin>BAH</ns2:BagOrigin>
          <ns2:BagFinalDestination>DXB</ns2:BagFinalDestination>
        </BagTagInfo>
    """)
    assert _convert_bags(xml) == [{
        "bagTagNumber": "1000000006",
        "weight": "32",
        "unit": "KG",
        "origin": "BAH",
        "destination": "DXB",
    }]


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "4"
        assert query["departureDate"] == "2016-03-16"
        assert query["passengers.pnr"] == "OJLCOK"
        return {
            "passengers": [
                {"pnr": "OJLCOK", "lastName": "STEVE", "firstName": "J"},
            ],
        }


class _Database:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


class _SabreSession:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, airline, flight_number, departure_date, origin,
                           last_name, first_name=None, pnr=None):
        self.calls.append({
            "airline": airline,
            "flight_number": flight_number,
            "departure_date": departure_date,
            "origin": origin,
            "last_name": last_name,
            "first_name": first_name,
            "pnr": pnr,
        })
        body = load_sample("GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_3.xml")
        return body, "", {}


def test_live_detail_route_returns_bag_weight_and_routing(monkeypatch):
    _SabreSession.calls = []
    monkeypatch.setattr(passengers_routes, "get_db", lambda: _Database())
    monkeypatch.setattr(passengers_routes, "SabreClient", _SabreSession)

    app = FastAPI()
    app.include_router(passengers_routes.router)
    client = TestClient(app)
    response = client.get(
        "/flights/4/passengers/OJLCOK/detail",
        params={"origin": "FCO", "date": "2016-03-16", "airline": "AZ"},
    )
    assert response.status_code == 200, response.text
    assert _SabreSession.calls == [{
        "airline": "AZ",
        "flight_number": "4",
        "departure_date": "2016-03-16",
        "origin": "FCO",
        "last_name": "STEVE",
        "first_name": "J",
        "pnr": "OJLCOK",
    }]
    body = response.json()
    assert body["passengers"][0]["itinerary"][0]["bagTags"] == [{
        "bagTagNumber": "9055300200",
        "weight": "10",
        "unit": "KG",
        "origin": "FCO",
        "destination": "TRN",
    }]
