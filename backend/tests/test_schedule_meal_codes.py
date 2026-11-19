"""Published schedule meal codes from VerifyFlightDetailsRS.

Sabre allows FlightSegment/Meal to repeat. xmltodict turns two or more Meal
elements into a list. convert_schedule must keep every Code, in document
order, joined with "/" on that segment. The flight summary mealCode is the
first segment's joined codes, which is the value Live Ops shows as Meal
Service.

These tests fail while a repeated Meal is stored as an empty string.
"""

from backend.api.routes.flights import _build_insights, _strip_id
from backend.feeder.converter import convert_schedule
from backend.sabre.client import SabreClient

# Same separator the schedule document and the Meal Service line must show.
MEAL_JOIN = "/"


def _verify_flight_details_body(segments_xml):
    """SOAP body shaped like the responses SabreClient.verify_flight_details parses.

    Parsing stops where the live call stops: SabreClient._parse_xml then
    SabreClient._extract_body(..., "VerifyFlightDetailsRS"). The HTTP post
    returns the response text unchanged.
    """
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader" eb:version="1.0" soap-env:mustUnderstand="1">
      <eb:Action>VerifyFlightDetailsLLSRS</eb:Action>
    </eb:MessageHeader>
  </soap-env:Header>
  <soap-env:Body>
    <VerifyFlightDetailsRS xmlns="http://webservices.sabre.com/sabreXML/2011/10" Version="2.0.1">
      <stl:ApplicationResults xmlns:stl="http://services.sabre.com/STL/v01" status="Complete">
        <stl:Success timeStamp="2026-03-19T09:17:19Z"/>
      </stl:ApplicationResults>
      <OriginDestinationOptions>
        <OriginDestinationOption OriginTimeZone="AST" DestinationTimeZone="GMT">
          {segments_xml}
        </OriginDestinationOption>
      </OriginDestinationOptions>
    </VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(object(), parsed, "VerifyFlightDetailsRS")


def _segment(origin, destination, meals, flight_number="2057"):
    meal_xml = "".join(f'<Meal Code="{code}"/>' for code in meals)
    return f"""
          <FlightSegment AirMilesFlown="3200" ArrivalDateTime="03-19T18:30"
                         DepartureDateTime="03-19T14:05" ElapsedTime="07.25"
                         AccumulatedElapsedTime="07.25" FlightNumber="{flight_number}">
            <DestinationLocation LocationCode="{destination}" Terminal="4"/>
            <Equipment AirEquipType="789"/>
            <MarketingAirline Code="GF" FlightNumber="{flight_number}"/>
            {meal_xml}
            <OriginLocation LocationCode="{origin}" Terminal="1"/>
          </FlightSegment>
"""


def _published_schedule(segments_xml, flight_number="2057"):
    """Run the body through convert_schedule the way lookup and ingestion do."""
    raw = _verify_flight_details_body(segments_xml)
    doc = convert_schedule(raw, "GF", flight_number, "2026-03-19")
    assert doc["success"] is True
    # Lookup drops _raw before returning. Storage keeps the same meal fields.
    returned = dict(doc)
    returned.pop("_raw", None)
    return returned


def _joined(codes):
    return MEAL_JOIN.join(codes)


def test_schedule_lookup_keeps_every_meal_code_on_the_segment_and_summary():
    """POST /flights/schedule/lookup stores each Meal/@Code, not an empty string."""
    doc = _published_schedule(_segment("BAH", "LHR", ["B", "L"]))

    assert doc["segments"][0]["mealCode"] == _joined(["B", "L"])
    assert doc["mealCode"] == _joined(["B", "L"])


def test_schedule_ingestion_keeps_meal_codes_on_every_segment():
    """Feeder schedule ingestion uses the same converter for every segment.

    The summary follows the first segment only. A later segment that is the
    one carrying repeated Meal elements must still keep its own codes.
    """
    doc = _published_schedule(
        _segment("BAH", "LHR", []) + _segment("LHR", "CDG", ["S", "D"], "2058"),
        flight_number="2057",
    )

    assert [seg["mealCode"] for seg in doc["segments"]] == ["", _joined(["S", "D"])]
    assert doc["mealCode"] == ""


def test_three_meal_elements_keep_document_order():
    doc = _published_schedule(_segment("BAH", "LHR", ["B", "L", "D"]))

    assert doc["segments"][0]["mealCode"] == _joined(["B", "L", "D"])
    assert doc["mealCode"] == _joined(["B", "L", "D"])


def test_flight_insights_meal_service_shows_every_meal_code():
    """Dashboard Meal Service prints insights.flightInfo.mealCode.

    The workbench Meal line prints schedule.mealCode from the same document.
    """
    doc = _published_schedule(
        _segment("BAH", "LHR", ["B", "L"]) + _segment("LHR", "CDG", ["S"], "2058")
    )
    schedule = _strip_id(dict(doc))
    insights = _build_insights(
        [{"pnr": "ABC123"}],
        None,
        schedule,
        {"passengers": [{}]},
        {},
    )

    assert schedule["mealCode"] == _joined(["B", "L"])
    assert insights["flightInfo"]["mealCode"] == _joined(["B", "L"])
    assert schedule["segments"][1]["mealCode"] == "S"


def test_single_meal_element_stays_a_single_code():
    doc = _published_schedule(
        _segment("BAH", "LHR", ["B"]) + _segment("LHR", "CDG", ["S"], "2058")
    )

    assert doc["segments"][0]["mealCode"] == "B"
    assert doc["segments"][1]["mealCode"] == "S"
    assert doc["mealCode"] == "B"
