"""Reservation DOCS entries must keep the travel document Sabre already returned.

Trip Search ingestion parses the SOAP body with SabreClient and stores the
result of convert_reservations. The DOCS entry on that body carries document
type, country of issue, document number, and expiration next to gender, date
of birth, and nationality. The stored passenger and both reservation routes
must surface those document fields. A later APISRequest sibling is still part
of the same passenger: a DOCS entry there is not skipped, and a DOCA entry
there still fills docaAddress.

These tests fail while that drop is present.

Not covered here:
- convert_passenger_data reads a different GetPassengerData free-text DOCS
  string and already keeps documentNumber and expiryDate.
- passengers._build_nationality_lookup copies nationality and service codes
  onto the passenger-list response and does not copy travel-document fields.
- The reservation table only renders gender, date of birth, and nationality.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.reservations import router as reservations_router
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE_TRIP_SEARCH = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-18.xml",
)

DOCUMENT_FIELDS = (
    ("documentType", "DocumentType"),
    ("countryOfIssue", "CountryOfIssue"),
    ("documentNumber", "DocumentNumber"),
    ("documentExpirationDate", "DocumentExpirationDate"),
)


def _parse_trip_search(xml_text):
    """Run the same parse SabreClient.get_reservations uses after the SOAP call."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "Trip_SearchRS")


def _apis_requests(passenger):
    special_requests = passenger.get("SpecialRequests") or {}
    apis = special_requests.get("APISRequest")
    if isinstance(apis, list):
        return [item for item in apis if isinstance(item, dict)]
    if isinstance(apis, dict):
        return [apis]
    return []


def _docs_entries(passenger):
    entries = []
    for apis in _apis_requests(passenger):
        for entry in _ensure_list(apis.get("DOCSEntry", [])):
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _doca_text(passenger):
    for apis in _apis_requests(passenger):
        doca = apis.get("DOCAEntry")
        if isinstance(doca, list):
            doca_entries = doca
        elif isinstance(doca, dict):
            doca_entries = [doca]
        else:
            doca_entries = []
        for entry in doca_entries:
            if isinstance(entry, dict) and entry.get("FreeText"):
                return entry["FreeText"]
    return ""


def _source_passengers(raw_body):
    data = _strip_ns(raw_body)
    reservations = _ensure_list(
        ((data.get("ReservationsList") or {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )
    found = []
    for reservation in reservations:
        inner = (reservation.get("GetReservationRS") or {}).get("Reservation") or {}
        passengers = _ensure_list(
            ((inner.get("PassengerReservation") or {}).get("Passengers") or {}).get(
                "Passenger", []
            )
        )
        for passenger in passengers:
            if isinstance(passenger, dict):
                found.append((reservation.get("@Locator", ""), passenger))
    return found


def _stored_passenger(converted, pnr, source_passenger):
    name_id = source_passenger.get("@nameId", "")
    last_name = source_passenger.get("LastName", "")
    first_name = source_passenger.get("FirstName", "")
    for reservation in converted.get("reservations", []):
        if reservation.get("pnr") != pnr:
            continue
        for passenger in reservation.get("passengers", []):
            if (
                passenger.get("nameId") == name_id
                and passenger.get("lastName") == last_name
                and passenger.get("firstName") == first_name
            ):
                return passenger
    return None


def _assert_document_kept(stored, source_passenger):
    assert stored is not None
    entries = _docs_entries(source_passenger)
    numbered = [entry for entry in entries if entry.get("DocumentNumber")]
    travel_document = numbered[0] if numbered else (entries[0] if entries else None)
    gendered = next((entry for entry in entries if entry.get("Gender")), None)

    if gendered is not None:
        assert stored.get("gender") == gendered.get("Gender", "")
        assert stored.get("dateOfBirth") == gendered.get("DateOfBirth", "")
        assert stored.get("nationality") == gendered.get(
            "DocumentNationalityCountry", ""
        )

    if travel_document is not None:
        for stored_key, source_key in DOCUMENT_FIELDS:
            assert stored.get(stored_key) == (travel_document.get(source_key) or "")

    doca = _doca_text(source_passenger)
    if doca:
        assert stored.get("docaAddress") == doca


def _convert(xml_text, airline="GF", flight_number="2057", origin="BOM", departure_date="2026-03-18"):
    raw = _parse_trip_search(xml_text)
    converted = convert_reservations(raw, airline, flight_number, origin, departure_date)
    return raw, converted


def _trip_search_envelope(passenger_xml, locator="ABC123"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord">
      <Success>Success</Success>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-01T00:00:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    {passenger_xml}
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _passenger_xml(apis_blocks, last_name="TRAVELER", first_name="TEST", name_id="01.01"):
    apis = "\n".join(apis_blocks)
    return f"""
                    <stl19:Passenger nameId="{name_id}" nameType="S">
                      <stl19:LastName>{last_name}</stl19:LastName>
                      <stl19:FirstName>{first_name}</stl19:FirstName>
                      <stl19:SpecialRequests>
                        {apis}
                      </stl19:SpecialRequests>
                    </stl19:Passenger>"""


def _docs_block(
    document_type="P",
    country="XX",
    number="TESTDOC1",
    nationality="XX",
    date_of_birth="1980-01-02",
    gender="F",
    expiration="2031-06-01",
):
    return f"""
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:DocumentType>{document_type}</stl19:DocumentType>
                            <stl19:CountryOfIssue>{country}</stl19:CountryOfIssue>
                            <stl19:DocumentNumber>{number}</stl19:DocumentNumber>
                            <stl19:DocumentNationalityCountry>{nationality}</stl19:DocumentNationalityCountry>
                            <stl19:DateOfBirth>{date_of_birth}</stl19:DateOfBirth>
                            <stl19:Gender>{gender}</stl19:Gender>
                            <stl19:DocumentExpirationDate>{expiration}</stl19:DocumentExpirationDate>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>"""


def _doca_block(free_text="DOCA GF HK1/R/XX"):
    return f"""
                        <stl19:APISRequest>
                          <stl19:DOCAEntry>
                            <stl19:FreeText>{free_text}</stl19:FreeText>
                          </stl19:DOCAEntry>
                        </stl19:APISRequest>"""


class _FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def find_one(self, query, sort=None):
        matches = [doc for doc in self.documents if _query_matches(doc, query)]
        if not matches:
            return None
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda doc: doc.get(key) or 0, reverse=direction < 0)
        return copy.deepcopy(matches[0])


class _FakeDB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


def _query_matches(document, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and "$lte" in expected:
            if document.get(key) is None or document.get(key) > expected["$lte"]:
                return False
            continue
        if "." in key:
            parent_key, child_key = key.split(".", 1)
            parent = document.get(parent_key)
            if not isinstance(parent, list):
                return False
            if not any(
                isinstance(item, dict) and item.get(child_key) == expected
                for item in parent
            ):
                return False
            continue
        if document.get(key) != expected:
            return False
    return True


def test_sample_trip_search_keeps_docs_document_fields():
    """The committed Gulf Air Trip Search response is the ingestion input."""
    with open(SAMPLE_TRIP_SEARCH, encoding="utf-8") as handle:
        xml_text = handle.read()
    raw, converted = _convert(xml_text)

    numbered = 0
    later_doca = 0
    for pnr, source in _source_passengers(raw):
        entries = _docs_entries(source)
        if not any(entry.get("DocumentNumber") for entry in entries):
            continue
        numbered += 1
        stored = _stored_passenger(converted, pnr, source)
        _assert_document_kept(stored, source)
        apis = _apis_requests(source)
        if len(apis) > 1 and _doca_text(source) and not (
            isinstance(apis[0].get("DOCAEntry"), (dict, list)) and apis[0].get("DOCAEntry")
        ):
            later_doca += 1

    assert numbered >= 1
    assert later_doca >= 1


def test_docs_on_later_apis_sibling_is_not_skipped():
    """DOCA arrives first and the passport DOCS entry is the next APISRequest."""
    xml_text = _trip_search_envelope(
        _passenger_xml(
            [
                _doca_block("DOCA GF HK1/R/XX"),
                _docs_block(number="TESTDOC2", expiration="2032-07-04", gender="M"),
            ]
        )
    )
    raw, converted = _convert(xml_text)
    pnr, source = _source_passengers(raw)[0]
    stored = _stored_passenger(converted, pnr, source)
    _assert_document_kept(stored, source)
    assert stored["documentNumber"] == "TESTDOC2"
    assert stored["documentExpirationDate"] == "2032-07-04"
    assert stored["docaAddress"] == "DOCA GF HK1/R/XX"
    assert stored["gender"] == "M"


def test_single_apis_docs_entry_keeps_document_fields():
    """xmltodict leaves a lone APISRequest as a dict, not a one-item list."""
    xml_text = _trip_search_envelope(_passenger_xml([_docs_block()]))
    raw, converted = _convert(xml_text)
    pnr, source = _source_passengers(raw)[0]
    assert isinstance(
        (source.get("SpecialRequests") or {}).get("APISRequest"), dict
    )
    stored = _stored_passenger(converted, pnr, source)
    _assert_document_kept(stored, source)
    assert stored["documentType"] == "P"
    assert stored["countryOfIssue"] == "XX"
    assert stored["documentNumber"] == "TESTDOC1"
    assert stored["documentExpirationDate"] == "2031-06-01"
    assert stored["dateOfBirth"] == "1980-01-02"
    assert stored["nationality"] == "XX"


def test_reservation_routes_expose_document_fields(monkeypatch):
    """List, historical list, and PNR routes return the stored travel document."""
    xml_text = _trip_search_envelope(
        _passenger_xml(
            [
                _docs_block(number="TESTDOC3", expiration="2033-08-09"),
                _doca_block("DOCA GF HK1/R/YY"),
            ]
        ),
        locator="PNR123",
    )
    _raw, converted = _convert(xml_text)
    snapshot = {
        "flightNumber": converted["flightNumber"],
        "snapshotType": "reservations",
        "sequenceNumber": 4,
        "origin": converted["departureAirport"],
        "departureDate": converted["departureDate"],
        "data": converted,
    }
    database = _FakeDB(
        {
            "reservations": _FakeCollection([converted]),
            "snapshots": _FakeCollection([snapshot]),
        }
    )
    monkeypatch.setattr("backend.api.routes.reservations.get_db", lambda: database)

    app = FastAPI()
    app.include_router(reservations_router)
    client = TestClient(app)

    latest = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert latest.status_code == 200
    latest_passenger = latest.json()["reservations"][0]["passengers"][0]
    assert latest_passenger["documentType"] == "P"
    assert latest_passenger["countryOfIssue"] == "XX"
    assert latest_passenger["documentNumber"] == "TESTDOC3"
    assert latest_passenger["documentExpirationDate"] == "2033-08-09"
    assert latest_passenger["dateOfBirth"] == "1980-01-02"
    assert latest_passenger["nationality"] == "XX"
    assert latest_passenger["gender"] == "F"

    historical = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 4},
    )
    assert historical.status_code == 200
    historical_passenger = historical.json()["reservations"][0]["passengers"][0]
    assert historical_passenger["documentNumber"] == "TESTDOC3"
    assert historical_passenger["documentExpirationDate"] == "2033-08-09"

    by_pnr = client.get(
        "/flights/2057/reservations/PNR123",
        params={"date": "2026-03-18"},
    )
    assert by_pnr.status_code == 200
    pnr_passenger = by_pnr.json()["reservations"][0]["passengers"][0]
    assert pnr_passenger["documentNumber"] == "TESTDOC3"
    assert pnr_passenger["documentExpirationDate"] == "2033-08-09"
    assert pnr_passenger["countryOfIssue"] == "XX"
    assert pnr_passenger["documentType"] == "P"
