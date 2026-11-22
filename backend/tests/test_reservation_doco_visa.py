"""Visa records from Sabre Trip Search DOCO entries must survive conversion.

POST /flights/ingest and the batch worker both call run_feeder, which parses
the Trip_SearchRS SOAP body with SabreClient._parse_xml / _extract_body and
passes that dict to convert_reservations. These tests drive that same parse
and conversion.

They fail while conversion keeps only the first APISRequest and never copies
DOCOEntry. They pass once every APIS request is read and each DOCO visa is
stored on the passenger, including through the reservations response model
and the dashboard nationality / gender readers that use that passenger.

Not covered here:
- convert_passenger_data keeps a GetPassengerData DOCO free-text row under
  freeText. That response is not a Trip Search DOCOEntry, and the text is
  not discarded, so it is a different shape.
- GetPassengerList edit code DOCV is a presence flag on the passenger list.
  It is not the visa number, place of issue, or applicable country.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    _build_dashboard_payload,
    _build_gender_lookup,
)
from backend.api.routes.passengers import _build_nationality_lookup  # noqa: E402
from backend.feeder.converter import _strip_ns, convert_reservations  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402
from backend.sabre.models import ReservationsResponse  # noqa: E402

_SAMPLE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sabre_docs",
    "Trip_Search",
    "Trip_SearchRQ-4.5.0-Samples",
    "Trip_SearchRQ-4.5.0-Samples",
    "Samples",
    "TripSearch-4.5.0-TN-TripSearchBLOBTN-NameCriteriaWithGender-STL.txt",
)

_CRACOW_VISA = "DOCO AF HK1/CRACOW PL/V/789789/CRACOW PL/14JUN1976/CH"
_PARIS_VISA = (
    "DOCO AF HK1 WAWCDG1247Y20NOV/LYON FR/V/789789/PARIS FR/14JUN2005/CH"
)


def _trip_search_body(trip_search_rs_xml):
    """Parse a Trip_SearchRS the way SabreClient.get_reservations does."""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header/>"
        "<soap-env:Body>"
        f"{trip_search_rs_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )
    parsed = SabreClient._parse_xml(envelope)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _bundled_sample_xml():
    text = open(_SAMPLE, encoding="utf-8").read()
    start = text.index("<Trip_SearchRS")
    end = text.index("</Trip_SearchRS>") + len("</Trip_SearchRS>")
    return text[start:end]


def _convert(trip_search_rs_xml, airline="GF", flight="2057", origin="BAH", date="2026-03-19"):
    raw = _trip_search_body(trip_search_rs_xml)
    return convert_reservations(raw, airline, flight, origin, date)


def _passenger(doc, last_name):
    matches = [
        pax
        for reservation in doc["reservations"]
        for pax in reservation["passengers"]
        if pax.get("lastName") == last_name
    ]
    assert len(matches) == 1, last_name
    return matches[0]


def _parsed_doco_free_texts(trip_search_rs_xml, last_name):
    """DOCO FreeText values the parser hands to convert_reservations."""
    data = _strip_ns(_trip_search_body(trip_search_rs_xml))
    reservations = data["ReservationsList"]["Reservations"]["Reservation"]
    if isinstance(reservations, dict):
        reservations = [reservations]
    texts = []
    for reservation in reservations:
        inner = reservation["GetReservationRS"]["Reservation"]
        passengers = inner["PassengerReservation"]["Passengers"]["Passenger"]
        if isinstance(passengers, dict):
            passengers = [passengers]
        for passenger in passengers:
            if passenger.get("LastName") != last_name:
                continue
            requests = passenger.get("SpecialRequests", {}).get("APISRequest", [])
            if isinstance(requests, dict):
                requests = [requests]
            for request in requests:
                entries = request.get("DOCOEntry", [])
                if isinstance(entries, dict):
                    entries = [entries]
                elif not isinstance(entries, list):
                    entries = []
                for entry in entries:
                    if isinstance(entry, dict):
                        texts.append(entry.get("FreeText", ""))
    return texts


def _assert_visa(entry, document_number, place, issue_date, country, free_text):
    assert entry["documentNumber"] == document_number
    assert entry["visaPlaceOfIssue"] == place
    assert entry["visaIssueDate"] == issue_date
    assert entry["visaApplicableCountry"] == country
    assert entry["freeText"] == free_text


def _reservation_xml(locator, passengers_xml):
    return f"""<Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="1" TotalResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19" Version="1.19.0">
              <stl19:Reservation numberInParty="2" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    {passengers_xml}
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>"""


def _passenger_xml(last_name, first_name, name_id, apis_xml):
    return f"""<stl19:Passenger nameId="{name_id}" nameType="S">
      <stl19:LastName>{last_name}</stl19:LastName>
      <stl19:FirstName>{first_name}</stl19:FirstName>
      <stl19:SpecialRequests>
        {apis_xml}
      </stl19:SpecialRequests>
    </stl19:Passenger>"""


class _ReservationsCollection:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, query, sort=None):
        return self._doc


class _ReservationsDb(dict):
    def __init__(self, doc):
        super().__init__()
        self._doc = doc

    def __getitem__(self, name):
        assert name == "reservations"
        return _ReservationsCollection(self._doc)


def test_bundled_sample_parser_delivers_doco_before_conversion():
    """The bundled Trip Search sample's first APIS requests are DOCO visas."""
    xml = _bundled_sample_xml()
    texts = _parsed_doco_free_texts(xml, "NOWAK")
    assert texts == [_CRACOW_VISA, _CRACOW_VISA, _CRACOW_VISA, _PARIS_VISA]


def test_bundled_sample_keeps_every_doco_visa_and_later_passport():
    """DOCO arrives before DOCS. Both the visas and the passport must be kept."""
    xml = _bundled_sample_xml()
    assert _parsed_doco_free_texts(xml, "NOWAK")[-1] == _PARIS_VISA
    doc = _convert(xml, airline="AF", flight="1247", origin="WAW", date="2019-11-20")
    passenger = _passenger(doc, "NOWAK")

    assert passenger["gender"] == "M"
    assert passenger["dateOfBirth"] == "1974-02-26"
    assert passenger["nationality"] == "US"
    visas = passenger["visas"]
    assert [visa["freeText"] for visa in visas] == [
        _CRACOW_VISA,
        _CRACOW_VISA,
        _CRACOW_VISA,
        _PARIS_VISA,
    ]
    for visa in visas[:3]:
        _assert_visa(visa, "", "", "", "", _CRACOW_VISA)
    _assert_visa(visas[3], "", "", "", "", _PARIS_VISA)
    assert "789789" in visas[3]["freeText"]
    assert "PARIS FR" in visas[3]["freeText"]


def test_structured_visa_on_a_later_apis_request_is_kept_beside_passport():
    """A passport on the first request must not hide a DOCO visa on a later one."""
    docs = """<stl19:APISRequest>
      <stl19:DOCSEntry>
        <stl19:DocumentType>P</stl19:DocumentType>
        <stl19:DocumentNumber>P123</stl19:DocumentNumber>
        <stl19:DocumentNationalityCountry>IN</stl19:DocumentNationalityCountry>
        <stl19:DateOfBirth>1985-01-09</stl19:DateOfBirth>
        <stl19:Gender>F</stl19:Gender>
      </stl19:DOCSEntry>
    </stl19:APISRequest>"""
    doca = """<stl19:APISRequest>
      <stl19:DOCAEntry>
        <stl19:FreeText>DOCA GF HK1/R/AE/DUBAI/123 PALM</stl19:FreeText>
      </stl19:DOCAEntry>
    </stl19:APISRequest>"""
    doco = """<stl19:APISRequest>
      <stl19:DOCOEntry>
        <stl19:DocumentType>V</stl19:DocumentType>
        <stl19:DocumentNumber>V998877</stl19:DocumentNumber>
        <stl19:VisaPlaceOfIssue>PARIS FR</stl19:VisaPlaceOfIssue>
        <stl19:VisaIssueDate>2005-06-14</stl19:VisaIssueDate>
        <stl19:VisaApplicableCountry>CH</stl19:VisaApplicableCountry>
        <stl19:FreeText>DOCO AF HK1/LYON FR/V/V998877/PARIS FR/14JUN2005/CH</stl19:FreeText>
      </stl19:DOCOEntry>
    </stl19:APISRequest>"""
    no_apis = """<stl19:Passenger nameId="02.01" nameType="S">
      <stl19:LastName>NONE</stl19:LastName>
      <stl19:FirstName>VISA</stl19:FirstName>
    </stl19:Passenger>"""
    xml = _reservation_xml(
        "DQGZWU",
        _passenger_xml("MATHEW", "SUMA", "01.01", docs + doca + doco) + no_apis,
    )
    assert _parsed_doco_free_texts(xml, "MATHEW") == [
        "DOCO AF HK1/LYON FR/V/V998877/PARIS FR/14JUN2005/CH"
    ]
    doc = _convert(xml)
    passenger = _passenger(doc, "MATHEW")
    assert passenger["gender"] == "F"
    assert passenger["dateOfBirth"] == "1985-01-09"
    assert passenger["nationality"] == "IN"
    assert passenger["docaAddress"] == "DOCA GF HK1/R/AE/DUBAI/123 PALM"
    assert len(passenger["visas"]) == 1
    _assert_visa(
        passenger["visas"][0],
        "V998877",
        "PARIS FR",
        "2005-06-14",
        "CH",
        "DOCO AF HK1/LYON FR/V/V998877/PARIS FR/14JUN2005/CH",
    )
    other = _passenger(doc, "NONE")
    assert other["visas"] == []
    assert other["nationality"] == ""
    assert "V998877" not in str(other["visas"])


def test_single_apis_request_and_repeated_doco_entries_are_kept():
    """xmltodict leaves one APISRequest as a dict, and repeated DOCOEntry as a list."""
    apis = """<stl19:APISRequest>
      <stl19:DOCOEntry>
        <stl19:DocumentType>V</stl19:DocumentType>
        <stl19:DocumentNumber>V111</stl19:DocumentNumber>
        <stl19:VisaPlaceOfIssue>BAHRAIN</stl19:VisaPlaceOfIssue>
        <stl19:VisaIssueDate>2024-01-02</stl19:VisaIssueDate>
        <stl19:VisaApplicableCountry>BH</stl19:VisaApplicableCountry>
        <stl19:FreeText>DOCO GF HK1//V/V111/BAHRAIN/02JAN2024/BH</stl19:FreeText>
      </stl19:DOCOEntry>
      <stl19:DOCOEntry>
        <stl19:DocumentType>V</stl19:DocumentType>
        <stl19:DocumentNumber>V222</stl19:DocumentNumber>
        <stl19:VisaPlaceOfIssue>DOHA</stl19:VisaPlaceOfIssue>
        <stl19:VisaIssueDate>2024-03-04</stl19:VisaIssueDate>
        <stl19:VisaApplicableCountry>QA</stl19:VisaApplicableCountry>
        <stl19:FreeText>DOCO GF HK1//V/V222/DOHA/04MAR2024/QA</stl19:FreeText>
      </stl19:DOCOEntry>
    </stl19:APISRequest>"""
    xml = _reservation_xml("SINGLE", _passenger_xml("ALI", "NOOR", "01.01", apis))
    raw = _strip_ns(_trip_search_body(xml))
    reservation = raw["ReservationsList"]["Reservations"]["Reservation"]
    passenger = reservation["GetReservationRS"]["Reservation"]["PassengerReservation"]["Passengers"]["Passenger"]
    assert isinstance(passenger["SpecialRequests"]["APISRequest"], dict)
    assert isinstance(passenger["SpecialRequests"]["APISRequest"]["DOCOEntry"], list)

    doc = _convert(xml)
    stored = _passenger(doc, "ALI")
    assert len(stored["visas"]) == 2
    _assert_visa(
        stored["visas"][0],
        "V111",
        "BAHRAIN",
        "2024-01-02",
        "BH",
        "DOCO GF HK1//V/V111/BAHRAIN/02JAN2024/BH",
    )
    _assert_visa(
        stored["visas"][1],
        "V222",
        "DOHA",
        "2024-03-04",
        "QA",
        "DOCO GF HK1//V/V222/DOHA/04MAR2024/QA",
    )


def test_reservations_response_keeps_visa_fields():
    """GET /flights/{flight}/reservations validates through ReservationsResponse."""
    apis = """<stl19:APISRequest>
      <stl19:DOCOEntry>
        <stl19:DocumentNumber>V555</stl19:DocumentNumber>
        <stl19:VisaPlaceOfIssue>MANAMA</stl19:VisaPlaceOfIssue>
        <stl19:VisaIssueDate>2020-05-06</stl19:VisaIssueDate>
        <stl19:VisaApplicableCountry>BH</stl19:VisaApplicableCountry>
        <stl19:FreeText>DOCO GF HK1//V/V555/MANAMA/06MAY2020/BH</stl19:FreeText>
      </stl19:DOCOEntry>
    </stl19:APISRequest>"""
    xml = _reservation_xml("APIRES", _passenger_xml("KHAN", "SARA", "01.01", apis))
    doc = _convert(xml)
    published = ReservationsResponse.model_validate(doc).model_dump()
    passenger = _passenger(published, "KHAN")
    assert len(passenger["visas"]) == 1
    _assert_visa(
        passenger["visas"][0],
        "V555",
        "MANAMA",
        "2020-05-06",
        "BH",
        "DOCO GF HK1//V/V555/MANAMA/06MAY2020/BH",
    )


def test_dashboard_and_enrichment_use_passport_stored_beside_a_leading_doco():
    """Nationality counts and passenger enrichment read the converted reservation."""
    xml = _bundled_sample_xml()
    doc = _convert(xml, airline="AF", flight="1247", origin="WAW", date="2019-11-20")
    passenger_list = {
        "passengers": [
            {
                "lastName": "NOWAK",
                "firstName": "ANNA MS",
                "pnr": "MZMWIO",
                "nationality": "XX",
                "cabin": "Y",
                "isRevenue": True,
                "isCheckedIn": False,
                "isBoarded": False,
                "editCodes": [],
            }
        ],
        "totalPassengers": 1,
        "origin": "WAW",
        "departureDate": "2019-11-20",
    }
    payload = _build_dashboard_payload(
        {"origin": "WAW", "departureDate": "2019-11-20", "status": "OPEN"},
        passenger_list,
        "WAW",
        "2019-11-20",
        {},
        reservation_doc=doc,
    )
    assert payload["analysis"]["nationalityCounts"] == {"US": 1}
    assert payload["analysis"]["totalMale"] == 1
    assert _build_gender_lookup(doc)[("MZMWIO", "NOWAK")] == "M"

    lookup = _build_nationality_lookup(
        _ReservationsDb(doc),
        "1247",
        "2019-11-20",
        origin="WAW",
    )
    assert lookup[("MZMWIO", "NOWAK")]["nationality"] == "US"
