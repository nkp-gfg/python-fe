"""Contact fields from a Trip Search reservation must survive ingest.

Sabre places EmailAddress on each passenger and EmailAddresses, PhoneNumbers,
and Remarks on the reservation (Reservation.PNRB). convert_reservations reads
the email and phone lists from the passenger-reservation wrapper, so a normal
Trip Search response is stored with those lists empty. The reservations API
then returns the stored document.

These tests fail while that conversion drops the contacts. They parse the
committed Gulf Air Trip Search sample with SabreClient._parse_xml and
_extract_body, the same functions get_reservations uses after the SOAP call,
then run convert_reservations and the reservation read routes.
"""

import copy
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_TRIP_SEARCH = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

# One reservation shaped like Reservation.PNRB: a passenger email, a
# reservation email list, and reservation phone numbers. Values are
# placeholders. This is not a captured Sabre credential.
SCHEMA_SHAPED_TRIP_SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="TESTPN">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-19T01:54:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01" nameType="S">
                      <stl19:LastName>DOE</stl19:LastName>
                      <stl19:FirstName>JANE</stl19:FirstName>
                      <stl19:EmailAddress id="6">
                        <stl19:Address>traveler@example.com</stl19:Address>
                      </stl19:EmailAddress>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
                <stl19:PhoneNumbers>
                  <stl19:PhoneNumber id="7">
                    <stl19:Number>973-00000000-H</stl19:Number>
                  </stl19:PhoneNumber>
                </stl19:PhoneNumbers>
                <stl19:EmailAddresses>
                  <stl19:EmailAddress id="8">
                    <stl19:Address>reservation@example.com</stl19:Address>
                  </stl19:EmailAddress>
                </stl19:EmailAddresses>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "departureAirport": "BOM",
    "departureDate": "2026-03-19",
}


def _local(tag):
    if tag is None:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _child_text(element, name):
    for child in list(element):
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _phone_numbers(phone_numbers_element):
    numbers = []
    for phone in list(phone_numbers_element):
        if _local(phone.tag) != "PhoneNumber":
            continue
        number = _child_text(phone, "Number")
        if number:
            numbers.append(number)
    return numbers


def _email_addresses(email_address_element):
    address = _child_text(email_address_element, "Address")
    return [address] if address else []


def contacts_from_trip_search_xml(xml_text):
    """Read contact fields from the schema locations, not from the converter.

    Passenger.PNRB allows EmailAddress on the passenger. Reservation.PNRB
    places EmailAddresses and PhoneNumbers on the reservation itself.
    """
    root = ET.fromstring(xml_text)
    expected = {}
    for element in root.iter():
        if _local(element.tag) != "Reservation":
            continue
        locator = element.attrib.get("Locator")
        if not locator:
            continue
        inner = None
        for child in list(element):
            if _local(child.tag) != "GetReservationRS":
                continue
            for grandchild in list(child):
                if _local(grandchild.tag) == "Reservation":
                    inner = grandchild
                    break
        if inner is None:
            continue
        emails = []
        phones = []
        for child in list(inner):
            name = _local(child.tag)
            if name == "PhoneNumbers":
                phones.extend(_phone_numbers(child))
            elif name == "EmailAddresses":
                for email in list(child):
                    if _local(email.tag) == "EmailAddress":
                        emails.extend(_email_addresses(email))
            elif name == "PassengerReservation":
                for node in child.iter():
                    if _local(node.tag) != "Passenger":
                        continue
                    for passenger_child in list(node):
                        child_name = _local(passenger_child.tag)
                        if child_name == "EmailAddress":
                            emails.extend(_email_addresses(passenger_child))
                        elif child_name == "PhoneNumbers":
                            phones.extend(_phone_numbers(passenger_child))
        expected[locator] = {"emails": emails, "phones": phones}
    return expected


def trip_search_body(xml_text):
    """Parse a Trip_SearchRS the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state; skip SabreClient() so the
    # test does not read Sabre credentials from the environment.
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def convert_trip_search(xml_text):
    body = trip_search_body(xml_text)
    return convert_reservations(
        body,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureAirport"],
        FLIGHT["departureDate"],
    )


def _stored_contacts(reservation):
    emails = [
        email for email in (reservation.get("emails") or [])
        if isinstance(email, str) and email
    ]
    phones = []
    for phone in reservation.get("phones") or []:
        if isinstance(phone, dict):
            number = phone.get("number") or ""
        elif isinstance(phone, str):
            number = phone
        else:
            number = ""
        if number:
            phones.append(number)
    return emails, phones


def assert_contacts_preserved(reservations, expected):
    by_pnr = {}
    for reservation in reservations:
        by_pnr.setdefault(reservation.get("pnr"), reservation)

    dropped = []
    email_count = 0
    phone_count = 0
    for pnr, wanted in expected.items():
        wanted_emails = [email for email in wanted["emails"] if email]
        wanted_phones = [phone for phone in wanted["phones"] if phone]
        if not wanted_emails and not wanted_phones:
            continue
        email_count += len(wanted_emails)
        phone_count += len(wanted_phones)
        stored = by_pnr.get(pnr)
        if stored is None:
            dropped.append(pnr)
            continue
        got_emails, got_phones = _stored_contacts(stored)
        if (
            not set(wanted_emails).issubset(got_emails)
            or not set(wanted_phones).issubset(got_phones)
        ):
            dropped.append(pnr)

    assert email_count > 0, "fixture has no email addresses to check"
    assert phone_count > 0, "fixture has no phone numbers to check"
    assert dropped == [], (
        f"stored reservations dropped Sabre contact fields on {len(dropped)} "
        f"PNRs, including {dropped[:8]}"
    )


class _Collection:
    """In-memory stand-in for the queries the reservation routes issue.

    store_reservations and store_snapshot insert the converted document
    unchanged. This collection returns that document for the same filters.
    """

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if not matches:
            return None
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: doc.get(field) if doc.get(field) is not None else 0,
                    reverse=direction < 0,
                )
        return copy.deepcopy(matches[0])

    @staticmethod
    def _matches(doc, query):
        for key, expected in query.items():
            if key == "sequenceNumber" and isinstance(expected, dict):
                if doc.get("sequenceNumber", 0) > expected.get("$lte", 0):
                    return False
                continue
            if key == "reservations.pnr":
                pnrs = [
                    reservation.get("pnr")
                    for reservation in doc.get("reservations", [])
                ]
                if expected not in pnrs:
                    return False
                continue
            if doc.get(key) != expected:
                return False
        return True


class _Database:
    def __init__(self, reservations_doc):
        snapshot = {
            "flightNumber": reservations_doc["flightNumber"],
            "origin": reservations_doc["departureAirport"],
            "departureDate": reservations_doc["departureDate"],
            "snapshotType": "reservations",
            "sequenceNumber": 1,
            "capturedAt": reservations_doc.get("fetchedAt", ""),
            "data": reservations_doc,
        }
        self._collections = {
            "reservations": _Collection([reservations_doc]),
            "snapshots": _Collection([snapshot]),
        }

    def __getitem__(self, name):
        return self._collections[name]


@pytest.fixture(scope="module")
def sample_xml():
    return SAMPLE_TRIP_SEARCH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sample_expected(sample_xml):
    return contacts_from_trip_search_xml(sample_xml)


@pytest.fixture(scope="module")
def sample_document(sample_xml):
    return convert_trip_search(sample_xml)


@pytest.fixture
def reservations_client(monkeypatch, sample_document):
    database = _Database(sample_document)
    monkeypatch.setattr(reservations_route, "get_db", lambda: database)
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def test_sample_trip_search_keeps_emails_and_phones(sample_document, sample_expected):
    """The committed Gulf Air Trip Search sample keeps its contacts."""
    assert "DQGZWU" in sample_expected
    assert sample_expected["DQGZWU"]["emails"], "sample PNR has no passenger or reservation email"
    assert sample_expected["DQGZWU"]["phones"], "sample PNR has no reservation phone"
    assert_contacts_preserved(sample_document["reservations"], sample_expected)


def test_schema_locations_for_email_and_phone_are_stored():
    """Passenger email, reservation email list, and reservation phones are kept."""
    document = convert_trip_search(SCHEMA_SHAPED_TRIP_SEARCH)
    expected = contacts_from_trip_search_xml(SCHEMA_SHAPED_TRIP_SEARCH)
    assert expected["TESTPN"]["emails"] == [
        "traveler@example.com",
        "reservation@example.com",
    ]
    assert expected["TESTPN"]["phones"] == ["973-00000000-H"]
    assert_contacts_preserved(document["reservations"], expected)


def test_flight_reservations_route_returns_sample_contacts(
    reservations_client, sample_expected
):
    response = reservations_client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert_contacts_preserved(body["reservations"], sample_expected)


def test_reservation_by_pnr_route_returns_sample_contacts(
    reservations_client, sample_expected
):
    response = reservations_client.get(
        "/flights/2057/reservations/DQGZWU",
        params={"date": "2026-03-19"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert_contacts_preserved(body["reservations"], {"DQGZWU": sample_expected["DQGZWU"]})


def test_snapshot_reservations_route_returns_sample_contacts(
    reservations_client, sample_expected
):
    response = reservations_client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-19", "snapshot_sequence": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert_contacts_preserved(body["reservations"], sample_expected)
