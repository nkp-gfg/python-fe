"""Live GetPassengerData itinerary segments must keep Sabre's clock and gate.

Sabre places EstimatedDepartureTime and DepartureGate on each
PassengerItinerary. The live detail route parses that SOAP body and returns
convert_passenger_data. These tests fail while those two values are left off
the itinerary segment the route returns.

Expected segment keys, matching the converter's other camelCase fields:
estimatedDepartureTime (Sabre EstimatedDepartureTime, for example 03:00PM)
and departureGate (Sabre DepartureGate). When Sabre omits an element the
value is an empty string, same as departureDate.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.sabre.client import SabreClient

SAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_2.xml"
)

CONNECTION_SEGMENT = """
                  <PassengerItinerary>
                     <Airline>AZ</Airline>
                     <Flight>2088</Flight>
                     <BookingClass>Y</BookingClass>
                     <Origin>FCO</Origin>
                     <Destination>JFK</Destination>
                     <DepartureDate>2016-03-16</DepartureDate>
                     <EstimatedDepartureTime>06:40PM</EstimatedDepartureTime>
                     <DepartureGate>4B</DepartureGate>
                     <AircraftType>E90</AircraftType>
                     <PassengerType>F</PassengerType>
                     <BagCount>0</BagCount>
                  </PassengerItinerary>
"""


def _vendor_response_xml() -> str:
    """Return the GetPassengerDataRS envelope from the published Sabre sample.

    The sample file also contains a request envelope and a token. Only the
    response envelope is returned, and this helper does not copy that token
    into the test source.
    """
    text = SAMPLE_PATH.read_text(encoding="utf-8")
    start = text.find("<soap-env:Envelope")
    end = text.find("</soap-env:Envelope>")
    if start < 0 or end < 0:
        raise AssertionError("vendor sample has no soap-env response envelope")
    return text[start:end + len("</soap-env:Envelope>")]


def _with_gates_and_connection(xml: str) -> str:
    """Add the schema's DepartureGate and a same-day connecting segment.

    The published sample sends EstimatedDepartureTime and omits DepartureGate.
    The GetPassengerData schema allows both on PassengerItinerary, so the
    connecting segment carries a different clock and gate through the same parser.
    """
    gated = xml.replace(
        "<EstimatedDepartureTime>03:00PM</EstimatedDepartureTime>",
        "<EstimatedDepartureTime>03:00PM</EstimatedDepartureTime>"
        "<DepartureGate>12A</DepartureGate>",
    )
    if gated == xml:
        raise AssertionError("vendor sample no longer contains 03:00PM")
    needle = "</PassengerItineraryList>"
    first = gated.find(needle)
    if first < 0:
        raise AssertionError("vendor sample has no PassengerItineraryList")
    return gated[:first] + CONNECTION_SEGMENT + gated[first:]


def _without_clock_or_gate(xml: str) -> str:
    stripped = xml.replace(
        "<EstimatedDepartureTime>03:00PM</EstimatedDepartureTime>", ""
    )
    if "EstimatedDepartureTime" in stripped or "DepartureGate" in stripped:
        raise AssertionError("clock or gate element still present")
    return stripped


def _parse_passenger_data(xml: str) -> dict:
    """Run the same SOAP parse the Sabre client uses after a 200 response."""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "GetPassengerDataRS")


def _convert(raw: dict) -> dict:
    from backend.feeder.converter import convert_passenger_data

    return convert_passenger_data(raw, "AZ", "2071", "2016-03-16", "LIN")


def _segment(doc: dict, passenger_index: int, segment_index: int) -> dict:
    return doc["passengers"][passenger_index]["itinerary"][segment_index]


def test_parsed_vendor_sample_still_contains_estimated_departure_time():
    """Precondition: the client parser delivers the sample clock to the converter."""
    raw = _parse_passenger_data(_vendor_response_xml())
    responses = raw["PassengerDataResponseList"]["PassengerDataResponse"]
    assert isinstance(responses, list) and len(responses) == 2
    for response in responses:
        segment = response["PassengerItineraryList"]["PassengerItinerary"]
        assert segment["Flight"] == "2071"
        assert segment["DepartureDate"] == "2016-03-16"
        assert segment["EstimatedDepartureTime"] == "03:00PM"
        assert "DepartureGate" not in segment


def test_vendor_sample_itinerary_keeps_estimated_departure_time():
    raw = _parse_passenger_data(_vendor_response_xml())
    doc = _convert(raw)
    assert len(doc["passengers"]) == 2
    for passenger in doc["passengers"]:
        assert len(passenger["itinerary"]) == 1
        segment = passenger["itinerary"][0]
        assert segment["airline"] == "AZ"
        assert segment["flight"] == "2071"
        assert segment["origin"] == "LIN"
        assert segment["destination"] == "FCO"
        assert segment["departureDate"] == "2016-03-16"
        assert segment["estimatedDepartureTime"] == "03:00PM"
        assert segment["departureGate"] == ""


def test_each_segment_keeps_its_own_clock_and_gate():
    raw = _parse_passenger_data(_with_gates_and_connection(_vendor_response_xml()))
    first_segments = raw["PassengerDataResponseList"]["PassengerDataResponse"][0][
        "PassengerItineraryList"
    ]["PassengerItinerary"]
    assert isinstance(first_segments, list) and len(first_segments) == 2
    assert first_segments[0]["EstimatedDepartureTime"] == "03:00PM"
    assert first_segments[0]["DepartureGate"] == "12A"
    assert first_segments[1]["EstimatedDepartureTime"] == "06:40PM"
    assert first_segments[1]["DepartureGate"] == "4B"

    doc = _convert(raw)
    outbound = _segment(doc, 0, 0)
    connection = _segment(doc, 0, 1)
    companion = _segment(doc, 1, 0)

    assert outbound["departureDate"] == "2016-03-16"
    assert outbound["estimatedDepartureTime"] == "03:00PM"
    assert outbound["departureGate"] == "12A"
    assert connection["flight"] == "2088"
    assert connection["origin"] == "FCO"
    assert connection["destination"] == "JFK"
    assert connection["departureDate"] == "2016-03-16"
    assert connection["estimatedDepartureTime"] == "06:40PM"
    assert connection["departureGate"] == "4B"
    assert companion["estimatedDepartureTime"] == "03:00PM"
    assert companion["departureGate"] == "12A"
    assert outbound["estimatedDepartureTime"] != connection["estimatedDepartureTime"]
    assert outbound["departureGate"] != connection["departureGate"]


def test_omitted_clock_and_gate_are_empty_strings():
    raw = _parse_passenger_data(_without_clock_or_gate(_vendor_response_xml()))
    segment = _convert(raw)["passengers"][0]["itinerary"][0]
    assert segment["departureDate"] == "2016-03-16"
    assert segment["estimatedDepartureTime"] == ""
    assert segment["departureGate"] == ""


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "2071"
        assert query["passengers.pnr"] == "GFLARH"
        assert query["departureDate"] == "2016-03-16"
        return {
            "flightNumber": "2071",
            "origin": "LIN",
            "departureDate": "2016-03-16",
            "passengers": [
                {"pnr": "GFLARH", "lastName": "JOSE", "firstName": "P"},
            ],
        }


class _Db(dict):
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


class _SabreSession:
    def __init__(self, xml_text):
        self.xml_text = xml_text
        self.calls = []

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
        parsed = SabreClient._parse_xml(self.xml_text)
        data = SabreClient._extract_body(self, parsed, "GetPassengerDataRS")
        return data, self.xml_text, {}


def test_live_detail_route_returns_each_segment_clock_and_gate(monkeypatch):
    xml = _with_gates_and_connection(_vendor_response_xml())
    session = _SabreSession(xml)
    monkeypatch.setattr(passengers, "get_db", lambda: _Db())
    monkeypatch.setattr(passengers, "SabreClient", lambda: session)

    app = FastAPI()
    app.include_router(passengers.router)
    response = TestClient(app).get(
        "/flights/2071/passengers/GFLARH/detail",
        params={"origin": "LIN", "date": "2016-03-16", "airline": "AZ"},
    )
    assert response.status_code == 200, response.text
    assert session.calls == [{
        "airline": "AZ",
        "flight_number": "2071",
        "departure_date": "2016-03-16",
        "origin": "LIN",
        "last_name": "JOSE",
        "first_name": "P",
        "pnr": "GFLARH",
    }]
    body = response.json()
    outbound = body["passengers"][0]["itinerary"][0]
    connection = body["passengers"][0]["itinerary"][1]
    assert outbound["departureDate"] == "2016-03-16"
    assert outbound["estimatedDepartureTime"] == "03:00PM"
    assert outbound["departureGate"] == "12A"
    assert connection["departureDate"] == "2016-03-16"
    assert connection["estimatedDepartureTime"] == "06:40PM"
    assert connection["departureGate"] == "4B"
