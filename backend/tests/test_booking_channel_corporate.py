"""Channel-mix tests for corporate point-of-sale sales.

The commercial dashboard publishes four booking-channel buckets
(online, agent, corporate, other) and draws a Corporate segment from
``categories.corporate``. Ingestion stores each Trip Search reservation's
POS agent sine. The classifier then adds that reservation's party size to
online, agent, or other, and no branch adds it to corporate.

These tests are the expected behavior. They fail while every corporate
desk sine is swallowed by the ``len(sine) <= 4`` agent branch.

Corporate desk sines are the exact codes CRP and CORP (any letter case).
They are ordinary Sabre agent sines of 3 and 4 characters, the same width
as agency sines such as ZAK. Online sines stay online, STX* and other
short agency sines stay agent, and a sine longer than 4 that is not an
online code and not a corporate desk sine stays other.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload, _build_insights
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient


ONLINE_SINES = ("WEB", "MOB", "APP", "ND1", "ND2", "NDC")
CORPORATE_SINES = ("CRP", "CORP")


def _trip_search_xml(reservations):
    """Build a Trip_SearchRS envelope shaped like the Sabre payload the feeder parses.

    ``reservations`` is a list of (locator, agent_sine, number_in_party).
    ``number_in_party`` may be None to omit the attribute.
    """
    blocks = []
    for locator, sine, party in reservations:
        party_attr = "" if party is None else f' numberInParty="{party}"'
        blocks.append(
            f"""
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation{party_attr} numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                  <stl19:CreationTimestamp>2026-03-19T01:54:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:POS AirExtras="false">
                  <stl19:Source AgentDutyCode="4" AgentSine="{sine}" AirlineVendorID="GF" BookingSource="A0A0" HomePseudoCityCode="BAH" ISOCountry="BH" PrimeHostID="GF" PseudoCityCode="BAH" />
                </stl19:POS>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01">
                      <stl19:LastName>TEST</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>"""
        )
    count = len(reservations)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" TimeStamp="2026-03-19T08:55:50" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="{count}" TotalResults="{count}">
        <Reservations>
          {''.join(blocks)}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _reservation_doc_from_trip_search(reservations):
    """Run the feeder's SOAP parse and reservation converter. No network call."""
    parsed = SabreClient._parse_xml(_trip_search_xml(reservations))
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", "2057", "BOM", "2026-03-19")


def _dashboard_categories(reservation_doc):
    """Build the dashboard payload the commercial tab reads."""
    passengers = []
    for reservation in reservation_doc.get("reservations", []):
        passengers.append({
            "lastName": "TEST",
            "firstName": "PAX",
            "pnr": reservation.get("pnr", ""),
            "cabin": "Y",
            "isRevenue": True,
            "editCodes": [],
        })
    if not passengers:
        passengers = [{
            "lastName": "TEST",
            "firstName": "PAX",
            "pnr": "EMPTY1",
            "cabin": "Y",
            "isRevenue": True,
            "editCodes": [],
        }]
    payload = _build_dashboard_payload(
        fs=None,
        pl={
            "passengers": passengers,
            "departureDate": "2026-03-19",
            "origin": "BOM",
            "destination": "DMM",
            "totalPassengers": len(passengers),
        },
        origin="BOM",
        date="2026-03-19",
        change_summary={},
        reservation_doc=reservation_doc,
    )
    insights = payload["insights"]
    assert insights is not None
    return insights["bookingChannels"]


def _categories_from_stored(reservations):
    """Classify reservations already stored in the reservations collection shape."""
    insights = _build_insights(
        passengers=[{"pnr": "X", "lastName": "TEST", "editCodes": []}],
        reservation_doc={"reservations": reservations},
        schedule_doc=None,
        pl={"departureDate": "2026-03-19"},
        change_summary={},
    )
    return insights["bookingChannels"]


def test_trip_search_corporate_desk_sine_is_corporate_on_the_dashboard():
    """A CRP sale ingested from Trip_SearchRS must show up as corporate volume.

    WEB and ND1 stay online. ZAK and the other 3-character agency sines seen
    on Trip Search POS stay agent. STX stays agent. A long non-corporate sine
    stays other. CRP is counted only in corporate, by party size.
    """
    reservation_doc = _reservation_doc_from_trip_search([
        ("WEB001", "WEB", 2),
        ("ND1001", "nd1", 1),
        ("ZAK001", "ZAK", 3),
        ("HHM001", "HHM", 4),
        ("CWS001", "CWS", 5),
        ("MIM001", "MIM", 6),
        ("STX001", "STX9", 7),
        ("CRP001", "CRP", 8),
        ("CRP002", "crp", 9),
        ("OTH001", "LONGCODE", 10),
    ])
    stored = {item["pnr"]: item for item in reservation_doc["reservations"]}
    assert stored["CRP001"]["pointOfSale"]["agentSine"] == "CRP"
    assert stored["CRP001"]["numberInParty"] == 8
    assert stored["CRP002"]["pointOfSale"]["agentSine"] == "crp"
    assert stored["WEB001"]["pointOfSale"]["agentSine"] == "WEB"

    booking = _dashboard_categories(reservation_doc)
    categories = booking["categories"]
    assert categories["corporate"] == 8 + 9
    assert categories["online"] == 3
    assert categories["agent"] == 3 + 4 + 5 + 6 + 7
    assert categories["other"] == 10
    assert sum(categories.values()) == 2 + 1 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10
    assert booking["channels"]["CRP"] == 8
    assert booking["channels"]["crp"] == 9
    assert booking["channels"]["ZAK"] == 3


def test_corporate_code_corp_is_not_swallowed_as_an_agency_sine():
    """CORP is four characters, so the agency-length branch would otherwise take it."""
    booking = _categories_from_stored([
        {"pnr": "C1", "numberInParty": 4, "pointOfSale": {"agentSine": "CORP"}},
        {"pnr": "C2", "numberInParty": 6, "pointOfSale": {"agentSine": "corp"}},
        {"pnr": "A1", "numberInParty": 2, "pointOfSale": {"agentSine": "ZAK"}},
    ])
    categories = booking["categories"]
    assert categories["corporate"] == 10
    assert categories["agent"] == 2
    assert categories["online"] == 0
    assert categories["other"] == 0


def test_missing_party_size_on_a_corporate_sine_counts_one_passenger():
    """Stored reservations omit numberInParty only when the key is absent.

    The classifier's default of 1 must land in corporate for a corporate sine,
    not in the agency bucket.
    """
    booking = _categories_from_stored([
        {"pnr": "C1", "pointOfSale": {"agentSine": "CRP"}},
    ])
    categories = booking["categories"]
    assert categories["corporate"] == 1
    assert categories["agent"] == 0
    assert categories["online"] == 0
    assert categories["other"] == 0
    assert booking["channels"]["CRP"] == 1


def test_online_stx_and_long_sines_do_not_count_as_corporate():
    reservations = []
    for sine in ONLINE_SINES:
        reservations.append({
            "pnr": sine,
            "numberInParty": 2,
            "pointOfSale": {"agentSine": sine.lower()},
        })
    reservations.extend([
        {"pnr": "STX", "numberInParty": 3, "pointOfSale": {"agentSine": "stxcorp"}},
        {"pnr": "LONG", "numberInParty": 4, "pointOfSale": {"agentSine": "CORPORATE"}},
        {"pnr": "BLANK", "numberInParty": 11, "pointOfSale": {"agentSine": ""}},
        {"pnr": "NONE", "numberInParty": 12, "pointOfSale": {}},
        {"pnr": "BAD", "numberInParty": 13, "pointOfSale": "CRP"},
    ])
    booking = _categories_from_stored(reservations)
    categories = booking["categories"]
    assert categories["online"] == 2 * len(ONLINE_SINES)
    assert categories["agent"] == 3
    assert categories["other"] == 4
    assert categories["corporate"] == 0
    assert "CRP" not in booking["channels"]


def test_empty_reservation_list_keeps_corporate_at_zero():
    booking = _categories_from_stored([])
    assert booking["categories"] == {
        "online": 0,
        "agent": 0,
        "corporate": 0,
        "other": 0,
    }
    assert booking["channels"] == {}
