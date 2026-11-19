"""CorpID value T is Sabre's electronic-ticket marker, not a company code.

GetPassengerList documents ``CorpID`` of ``T`` as the marker shown when a
passenger holds an electronic ticket, and blank otherwise. Ingestion copies
that element onto ``passengers[].corpId``. The flight dashboard then counts
every non-empty ``corpId`` as a corporate account and publishes
``insights.corporateTravel``. The commercial tab, the corporate chart, and
the briefing all read that object.

These tests follow a GetPassengerListRS SOAP body through the client parser,
the passenger-list converter, and ``_build_dashboard_payload`` — the same
object ``get_flight_dashboard`` returns. They fail while ``T`` is still
counted as a company, and they keep a real company code in the total so a
fix cannot zero corporate travel entirely.

``store_passenger_list`` and ``store_snapshot`` persist the converted
document without rewriting ``corpId``. ``fetch_passenger_list`` returns that
document. Those steps do not change the value the tests already pass into
the dashboard builder.

Not covered here, because they do not recompute the corporate percentage:

- ``frontend/components/dashboard/briefing-tab.tsx``
- ``frontend/components/dashboard/decision-briefing.tsx``
- ``frontend/components/dashboard/revenue-tab.tsx``
- ``frontend/components/dashboard/flight-insight-tabs.tsx``
- ``frontend/components/dashboard/flight-insights.tsx``

Those views render ``insights.corporateTravel`` from this payload. The
briefing adds its high-corporate action when ``corporatePct >= 50``, and
the revenue view does the same at 30 percent.

Also not covered: the passenger-row badges in ``passenger-table.tsx`` and
``bottom-detail-panel.tsx``, which label any non-empty ``corpId`` as a
corporate id, and ``rule_staff_in_premium_no_upgrade``, which treats ``T``
as a staff passenger. Neither of those is the corporate-mix total.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import _ensure_list, _strip_ns, convert_passenger_list
from backend.sabre.client import SabreClient

# Sabre GetPassengerList guide, priority-list response element CorpID:
# "T" is displayed when the passenger has an electronic ticket; otherwise blank.
ELECTRONIC_TICKET_MARKER = "T"

GF2057_RESPONSE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2057_BOM_2026-03-19.xml",
)

# Briefing surfaces that treat the departure as a corporate-account flight.
BRIEFING_CORPORATE_THRESHOLD = 50
REVENUE_CORPORATE_THRESHOLD = 30


def _passenger_list_body(xml_text):
    """Run the SOAP body through the same parser the Sabre client uses."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not read client credentials or session state.
    return SabreClient._extract_body(object(), parsed, "GetPassengerListRS")


def _corp_facts(body):
    """Count CorpID the way convert_passenger_list reads PassengerInfo."""
    data = _strip_ns(body)
    passengers = _ensure_list(
        (data.get("PassengerInfoList") or {}).get("PassengerInfo", [])
    )
    facts = []
    for passenger in passengers:
        corp_id = passenger.get("CorpID") or ""
        if not isinstance(corp_id, str):
            corp_id = str(corp_id)
        facts.append({
            "corp_id": corp_id,
            "cabin": passenger.get("Cabin") or "",
            "is_ticket_marker": corp_id == ELECTRONIC_TICKET_MARKER,
            "is_company": bool(corp_id) and corp_id != ELECTRONIC_TICKET_MARKER,
        })
    return facts


def _dashboard_insights(xml_text, flight_number, origin, departure_date):
    body = _passenger_list_body(xml_text)
    facts = _corp_facts(body)
    document = convert_passenger_list(
        body, "GF", flight_number, departure_date, origin,
    )
    payload = _build_dashboard_payload(
        None, document, origin, departure_date, {},
    )
    insights = payload["insights"]
    assert insights is not None
    return facts, insights


def _assert_corporate_travel(facts, insights):
    expected = {}
    for fact in facts:
        if fact["is_company"]:
            expected[fact["corp_id"]] = expected.get(fact["corp_id"], 0) + 1
    corporate = insights["corporateTravel"]
    assert ELECTRONIC_TICKET_MARKER not in corporate["companies"]
    assert corporate["companies"] == expected
    assert corporate["totalCorporate"] == sum(expected.values())
    total = len(facts)
    expected_pct = round(sum(expected.values()) / total * 100, 1) if total else 0
    assert corporate["corporatePct"] == expected_pct
    return corporate


def _passenger_xml(corp_id, cabin, line_number):
    corp_element = f"<CorpID>{corp_id}</CorpID>" if corp_id is not None else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>PAX{line_number}</LastName>
            <FirstName>TEST</FirstName>
          </Name_Details>
          <PNRLocator>T{line_number:05d}</PNRLocator>
          <PassengerID>ID{line_number:04d}</PassengerID>
          <LineNumber>{line_number}</LineNumber>
          {corp_element}
          <BookingClass>Y</BookingClass>
          <Cabin>{cabin}</Cabin>
          <PassengerType>F</PassengerType>
        </PassengerInfo>
    """


def _soap_envelope(passenger_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>1001</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {passenger_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def test_gf2057_ticket_marker_is_not_reported_as_corporate():
    """A captured ticketed departure must not brief as a corporate flight.

    The committed GF2057 list has 59 passengers. 58 carry CorpID T, including
    both business-cabin rows, and one CorpID is blank. No other company code
    is present.
    """
    xml_text = open(GF2057_RESPONSE, encoding="utf-8").read()
    # Count the element in the response text so a parser that drops CorpID
    # cannot make the insight assertion pass by seeing an empty field.
    assert xml_text.count("<CorpID>T</CorpID>") == 58
    assert xml_text.count("<PassengerInfo>") == 59

    facts, insights = _dashboard_insights(
        xml_text, "2057", "BOM", "2026-03-19",
    )
    assert len(facts) == 59
    assert sum(1 for fact in facts if fact["is_ticket_marker"]) == 58
    assert sum(1 for fact in facts if fact["corp_id"] == "") == 1
    assert sum(1 for fact in facts if fact["is_company"]) == 0
    assert sum(
        1 for fact in facts
        if fact["is_ticket_marker"] and fact["cabin"] == "J"
    ) == 2

    corporate = _assert_corporate_travel(facts, insights)
    assert corporate["totalCorporate"] == 0
    assert corporate["companies"] == {}
    assert corporate["corporatePct"] == 0.0
    assert corporate["corporatePct"] < BRIEFING_CORPORATE_THRESHOLD
    assert corporate["corporatePct"] < REVENUE_CORPORATE_THRESHOLD


def test_ticket_marker_does_not_inflate_a_real_company_code():
    """T sits beside a real company code and must not enter the company chart.

    Four electronic-ticket markers, one of them in cabin J, plus one blank
    CorpID, plus one real company. The corporate share of that flight is one
    passenger out of six. Counting T pushes the share through both the 30
    percent revenue warning and the 50 percent briefing action.
    """
    passengers = [
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "J", 1),
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "Y", 2),
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "Y", 3),
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "Y", 4),
        _passenger_xml(None, "Y", 5),
        _passenger_xml("GFCL", "Y", 6),
    ]
    facts, insights = _dashboard_insights(
        _soap_envelope("".join(passengers)), "1001", "BAH", "2026-03-19",
    )
    assert sum(1 for fact in facts if fact["is_ticket_marker"]) == 4
    assert any(fact["is_ticket_marker"] and fact["cabin"] == "J" for fact in facts)

    corporate = _assert_corporate_travel(facts, insights)
    assert corporate["totalCorporate"] == 1
    assert corporate["companies"] == {"GFCL": 1}
    assert corporate["corporatePct"] == 16.7
    assert corporate["corporatePct"] < REVENUE_CORPORATE_THRESHOLD
    assert corporate["corporatePct"] < BRIEFING_CORPORATE_THRESHOLD


def test_real_company_codes_still_count_when_a_ticket_marker_is_present():
    """Excluding T must leave an actual company identifier in the total.

    Two passengers share a company code and one only carries the electronic
    ticket marker. The departure is still a corporate-heavy flight because of
    the company code, and the chart must not list T.
    """
    passengers = [
        _passenger_xml("ACME", "J", 1),
        _passenger_xml("ACME", "Y", 2),
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "Y", 3),
    ]
    facts, insights = _dashboard_insights(
        _soap_envelope("".join(passengers)), "1002", "BAH", "2026-03-19",
    )
    corporate = _assert_corporate_travel(facts, insights)
    assert corporate["totalCorporate"] == 2
    assert corporate["companies"] == {"ACME": 2}
    assert corporate["corporatePct"] == 66.7
    assert corporate["corporatePct"] >= BRIEFING_CORPORATE_THRESHOLD


def test_several_company_codes_keep_their_own_counts():
    """Each non-marker CorpID is its own company, and blanks stay out."""
    passengers = [
        _passenger_xml("GFCL", "Y", 1),
        _passenger_xml("GFCL", "Y", 2),
        _passenger_xml("OMEGA", "J", 3),
        _passenger_xml(ELECTRONIC_TICKET_MARKER, "Y", 4),
        _passenger_xml(None, "Y", 5),
    ]
    facts, insights = _dashboard_insights(
        _soap_envelope("".join(passengers)), "1003", "BAH", "2026-03-19",
    )
    corporate = _assert_corporate_travel(facts, insights)
    assert corporate["companies"] == {"GFCL": 2, "OMEGA": 1}
    assert corporate["totalCorporate"] == 3
    assert corporate["corporatePct"] == 60.0
