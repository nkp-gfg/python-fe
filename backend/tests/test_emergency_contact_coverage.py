"""Emergency-contact coverage must follow Trip Search reservations.

Passenger-list conversion does not receive Sabre EmergencyContactRequest.
Reservation conversion stores hasEmergencyContact from PassengerRefusal.
The dashboard insight is what the safety checklist and the emergency-contact
card display, so it has to count those stored reservation flags for the
travelers on the manifest.

These tests fail while the insight only reads the missing manifest field.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "sample_data" / "sample_xmlfiles"
TRIP_SEARCH_SAMPLE = SAMPLE_DIR / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
PASSENGER_LIST_SAMPLE = SAMPLE_DIR / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"

# risk-tab.tsx treats a checklist row as passed when pctVal >= 80.
CHECKLIST_PASS_PERCENT = 80

STL19 = "http://webservices.sabre.com/pnrbuilder/v1_19"


def _soap_body(xml_text, response_key):
    """Parse a Sabre SOAP payload the way SabreClient.get_* does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _envelope(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body_xml}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _emergency_request_xml(kind):
    """kind: 'false', 'true', 'list-false-then-true', or None to omit."""
    if kind is None:
        return ""
    if kind == "list-false-then-true":
        requests = """
          <stl19:EmergencyContactRequest>
            <stl19:PassengerRefusal>false</stl19:PassengerRefusal>
          </stl19:EmergencyContactRequest>
          <stl19:EmergencyContactRequest>
            <stl19:PassengerRefusal>true</stl19:PassengerRefusal>
          </stl19:EmergencyContactRequest>
        """
    else:
        requests = f"""
          <stl19:EmergencyContactRequest>
            <stl19:PassengerRefusal>{kind}</stl19:PassengerRefusal>
          </stl19:EmergencyContactRequest>
        """
    return f"<stl19:SpecialRequests>{requests}</stl19:SpecialRequests>"


def _trip_search_xml(pnr, passengers):
    """passengers: list of (last_name, first_name, refusal_kind)."""
    pax_xml = []
    for last_name, first_name, kind in passengers:
        pax_xml.append(
            "<stl19:Passenger>"
            f"<stl19:LastName>{last_name}</stl19:LastName>"
            f"<stl19:FirstName>{first_name}</stl19:FirstName>"
            f"{_emergency_request_xml(kind)}"
            "</stl19:Passenger>"
        )
    body = f"""
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="{pnr}">
            <stl19:GetReservationRS xmlns:stl19="{STL19}">
              <stl19:Reservation numberInParty="{len(passengers)}">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{pnr}</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    {''.join(pax_xml)}
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
    """
    return _envelope(body)


def _passenger_list_xml(pnr, passengers):
    """passengers: list of (last_name, first_name). No emergency-contact fields."""
    rows = []
    for last_name, first_name in passengers:
        rows.append(
            "<PassengerInfo>"
            "<Name_Details>"
            f"<LastName>{last_name}</LastName>"
            f"<FirstName>{first_name}</FirstName>"
            "</Name_Details>"
            f"<PNRLocator>{pnr}</PNRLocator>"
            "<PassengerType>F</PassengerType>"
            "<Cabin>Y</Cabin>"
            "</PassengerInfo>"
        )
    body = f"""
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary><Destination>DMM</Destination></Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(rows)}
      </PassengerInfoList>
    </GetPassengerListRS>
    """
    return _envelope(body)


def _convert_pair(trip_xml, list_xml, flight_number="2057"):
    reservations = convert_reservations(
        _soap_body(trip_xml, "Trip_SearchRS"),
        "GF",
        flight_number,
        "BOM",
        "2026-03-19",
    )
    passenger_list = convert_passenger_list(
        _soap_body(list_xml, "GetPassengerListRS"),
        "GF",
        flight_number,
        "2026-03-19",
        "BOM",
    )
    return reservations, passenger_list


def _dashboard(passenger_list, reservations):
    return _build_dashboard_payload(
        {"status": "OPEN", "origin": "BOM"},
        passenger_list,
        "BOM",
        "2026-03-19",
        {},
        reservations,
        None,
        None,
    )


def _reservation_contact_count(reservation_doc):
    count = 0
    for reservation in reservation_doc.get("reservations", []):
        for passenger in reservation.get("passengers", []):
            if passenger.get("hasEmergencyContact"):
                count += 1
    return count


def _manifest_travelers_with_stored_contact(passenger_list, reservation_doc):
    """Manifest rows whose PNR + last name has a stored reservation contact.

    This is the same identity the passenger endpoint uses when it copies
    hasEmergencyContact onto a manifest row. Every reservation contact in
    these fixtures belongs to a manifest traveler, so this count is also
    the special-requests total.
    """
    flagged = set()
    for reservation in (reservation_doc or {}).get("reservations", []):
        pnr = reservation.get("pnr") or ""
        for passenger in reservation.get("passengers", []):
            if passenger.get("hasEmergencyContact"):
                last_name = (passenger.get("lastName") or "").upper()
                flagged.add((pnr, last_name))
    matched = 0
    for passenger in passenger_list.get("passengers", []):
        key = (
            passenger.get("pnr") or "",
            (passenger.get("lastName") or "").upper(),
        )
        if key in flagged:
            matched += 1
    return matched


def _assert_manifest_has_no_contact_flag(passenger_list):
    """Passenger-list conversion never stores the reservation contact flag."""
    manifest = passenger_list["passengers"]
    assert manifest
    assert not any(passenger.get("hasEmergencyContact") for passenger in manifest)


def _assert_coverage(payload, passenger_list, reservations):
    manifest = passenger_list["passengers"]
    expected = _manifest_travelers_with_stored_contact(passenger_list, reservations)
    stored = _reservation_contact_count(reservations)
    assert expected == stored
    assert expected > 0

    summary = payload["specialRequestsSummary"]
    assert summary is not None
    assert summary["emergencyContacts"] == stored

    insights = payload["insights"]
    assert insights is not None
    coverage = insights["emergencyContacts"]
    total = len(manifest)
    assert coverage["withContact"] == expected, (
        f"insight counted {coverage['withContact']} contacts; "
        f"{expected} manifest travelers have a stored reservation contact"
    )
    assert coverage["withoutContact"] == total - expected
    assert coverage["coveragePct"] == round(expected / total * 100, 1)
    return coverage


def test_full_manifest_contact_coverage_is_not_reported_as_zero():
    """Every traveler accepted a contact. The checklist must be able to pass."""
    passengers = [
        ("ALI", "AMINA", "false"),
        ("BAKER", "NOOR", "list-false-then-true"),
        ("CHEN", "LI", "false"),
        ("DAS", "RAJ", "false"),
    ]
    pnr = "ABC123"
    reservations, passenger_list = _convert_pair(
        _trip_search_xml(pnr, passengers),
        _passenger_list_xml(pnr, [(last, first) for last, first, _ in passengers]),
    )
    stored_flags = [
        passenger.get("hasEmergencyContact")
        for reservation in reservations["reservations"]
        for passenger in reservation["passengers"]
    ]
    assert stored_flags == [True, True, True, True]
    _assert_manifest_has_no_contact_flag(passenger_list)

    coverage = _assert_coverage(
        _dashboard(passenger_list, reservations),
        passenger_list,
        reservations,
    )
    assert coverage["coveragePct"] >= CHECKLIST_PASS_PERCENT
    assert coverage["withContact"] == 4
    assert coverage["withoutContact"] == 0


def test_refusal_and_missing_request_are_not_treated_as_contacts():
    """A missing manifest field is not a contact, and a refusal is not one either."""
    passengers = [
        ("ALI", "AMINA", "false"),
        ("BAKER", "NOOR", "true"),
        ("CHEN", "LI", None),
    ]
    pnr = "ABC123"
    reservations, passenger_list = _convert_pair(
        _trip_search_xml(pnr, passengers),
        _passenger_list_xml(pnr, [(last, first) for last, first, _ in passengers]),
    )
    flags = {
        passenger["lastName"]: passenger.get("hasEmergencyContact")
        for reservation in reservations["reservations"]
        for passenger in reservation["passengers"]
    }
    assert flags == {"ALI": True, "BAKER": False, "CHEN": False}
    _assert_manifest_has_no_contact_flag(passenger_list)

    coverage = _assert_coverage(
        _dashboard(passenger_list, reservations),
        passenger_list,
        reservations,
    )
    assert coverage["withContact"] == 1
    assert coverage["withoutContact"] == 2
    assert coverage["coveragePct"] == 33.3
    assert coverage["coveragePct"] < CHECKLIST_PASS_PERCENT


def test_gf2057_trip_search_contacts_reach_the_dashboard_insight():
    """Committed GF2057 BOM Trip Search for 2026-03-19, through the live converters."""
    reservations, passenger_list = _convert_pair(
        TRIP_SEARCH_SAMPLE.read_text(encoding="utf-8"),
        PASSENGER_LIST_SAMPLE.read_text(encoding="utf-8"),
    )
    _assert_manifest_has_no_contact_flag(passenger_list)
    coverage = _assert_coverage(
        _dashboard(passenger_list, reservations),
        passenger_list,
        reservations,
    )
    # Six EmergencyContactRequest rows with PassengerRefusal false, all matched
    # to manifest travelers. The other manifest travelers have no request.
    assert coverage["withContact"] == 6
    assert coverage["withoutContact"] == len(passenger_list["passengers"]) - 6
    assert coverage["coveragePct"] == round(6 / len(passenger_list["passengers"]) * 100, 1)


def test_dashboard_without_reservations_does_not_invent_contacts():
    passengers = [("ALI", "AMINA")]
    _, passenger_list = _convert_pair(
        _trip_search_xml("ABC123", [("ALI", "AMINA", "false")]),
        _passenger_list_xml("ABC123", passengers),
    )
    payload = _dashboard(passenger_list, None)
    assert payload["specialRequestsSummary"] is None
    coverage = payload["insights"]["emergencyContacts"]
    assert coverage["withContact"] == 0
    assert coverage["withoutContact"] == 1
    assert coverage["coveragePct"] == 0
