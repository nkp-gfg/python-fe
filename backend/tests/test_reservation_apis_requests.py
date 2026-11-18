"""Reservation APIS requests must all be kept.

Sabre Trip_SearchRS sends passport DOCS data and residence DOCA data as
sibling APISRequest elements. convert_reservations currently keeps only
APISRequest[0], and inside that request it stops at the first DOCSEntry
that has a gender and the first DOCAEntry.

These tests parse SOAP with SabreClient._parse_xml and _extract_body, the
same functions get_reservations uses, then run convert_reservations. They
fail while that truncation is present.

Not covered here: convert_passenger_list reads DOCS from passenger-list
free-text edit codes (backend/feeder/converter.py around the gender_from_docs
loop). That path does not read APISRequest elements.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from backend.api.routes.flights import _build_dashboard_payload
from backend.api.routes.passengers import get_passengers
from backend.api.routes.reservations import get_reservation_by_pnr, get_reservations
from backend.feeder.converter import _strip_ns, convert_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE_DIR = os.path.join(REPO_ROOT, "sample_data", "sample_xmlfiles")

RESIDENCE = "DOCA GF HK1/R/GB"
DESTINATION = "DOCA GF HK1/D/BH"
FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-18"


def _trip_search(special_requests, locator="PNR001", last_name="DOE", first_name="JANE"):
    """SOAP Trip_SearchRS body shaped like the committed Gulf Air responses."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord">
      <Success>Success</Success>
      <ReservationsList NumberResults="1" TotalResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01" nameType="S">
                      <stl19:LastName>{last_name}</stl19:LastName>
                      <stl19:FirstName>{first_name}</stl19:FirstName>
                      <stl19:SpecialRequests>
                        {special_requests}
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _docs(gender="", dob="", nationality=""):
    return f"""<stl19:DOCSEntry>
      <stl19:DocumentType>P</stl19:DocumentType>
      <stl19:Gender>{gender}</stl19:Gender>
      <stl19:DateOfBirth>{dob}</stl19:DateOfBirth>
      <stl19:DocumentNationalityCountry>{nationality}</stl19:DocumentNationalityCountry>
    </stl19:DOCSEntry>"""


def _doca(text):
    return f"<stl19:DOCAEntry><stl19:FreeText>{text}</stl19:FreeText></stl19:DOCAEntry>"


def _apis(*entries):
    return "<stl19:APISRequest>" + "".join(entries) + "</stl19:APISRequest>"


def convert_trip_search(xml):
    """Run the Trip Search parse that SabreClient.get_reservations uses."""
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", FLIGHT, ORIGIN, DATE)


def _passenger(doc):
    assert len(doc["reservations"]) == 1
    reservation = doc["reservations"][0]
    assert len(reservation["passengers"]) == 1
    return reservation, reservation["passengers"][0]


def _assert_identity(pax, gender, dob, nationality):
    assert pax["gender"] == gender
    assert pax["dateOfBirth"] == dob
    assert pax["nationality"] == nationality


def _assert_contains(haystack, needle):
    assert needle in (haystack or ""), f"missing {needle!r} in {haystack!r}"


class _Collection:
    def __init__(self, document):
        self.document = document

    def find_one(self, query, *args, **kwargs):
        return self.document


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return _Collection(self._collections[name])


def _install_db(monkeypatch, module, collections):
    monkeypatch.setattr(module, "get_db", lambda: _Database(collections))


def test_passport_request_then_residence_request_keeps_both():
    """Gulf Air sample order: DOCS APISRequest, then a sibling DOCA APISRequest."""
    xml = _trip_search(
        _apis(_docs(gender="F", dob="1994-08-08", nationality="GB"))
        + _apis(_doca(RESIDENCE))
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)


def test_residence_request_then_passport_request_keeps_both():
    """The other sibling order drops gender, date of birth, and nationality."""
    xml = _trip_search(
        _apis(_doca(RESIDENCE))
        + _apis(_docs(gender="F", dob="1994-08-08", nationality="GB"))
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)


def test_later_docs_entry_fills_fields_the_first_gendered_document_lacks():
    """One APISRequest, two DOCSEntry elements. Stop-at-first-gender drops the rest."""
    xml = _trip_search(
        _apis(
            _docs(gender="M"),
            _docs(dob="1988-04-04", nationality="IN"),
        )
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "M", "1988-04-04", "IN")


def test_later_doca_entry_on_the_same_request_is_kept():
    """DOCAEntry[0] has no free text. The residence is on the next entry."""
    xml = _trip_search(
        _apis(
            _docs(gender="F", dob="1994-08-08", nationality="GB"),
            "<stl19:DOCAEntry><stl19:ActionCode>HK</stl19:ActionCode></stl19:DOCAEntry>",
            _doca(RESIDENCE),
        )
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)


def test_residence_texts_on_different_requests_are_all_kept():
    xml = _trip_search(
        _apis(_docs(gender="F", dob="1994-08-08", nationality="GB"), _doca(RESIDENCE))
        + _apis(_doca(DESTINATION))
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)
    _assert_contains(pax["docaAddress"], DESTINATION)


def test_docs_and_doca_on_the_same_single_request_are_both_kept():
    xml = _trip_search(
        _apis(
            _docs(gender="F", dob="1994-08-08", nationality="GB"),
            _doca(RESIDENCE),
        )
    )
    _, pax = _passenger(convert_trip_search(xml))
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)


def test_pnr_route_returns_residence_stored_from_the_second_apis_request(monkeypatch):
    """GET /flights/{flight}/reservations/{pnr} returns the stored passenger."""
    xml = _trip_search(
        _apis(_docs(gender="F", dob="1994-08-08", nationality="GB"))
        + _apis(_doca(RESIDENCE)),
        locator="TST001",
        last_name="EXAMPLE",
        first_name="JORDAN",
    )
    stored = convert_trip_search(xml)
    import backend.api.routes.reservations as reservations_route

    _install_db(monkeypatch, reservations_route, {"reservations": stored})
    response = get_reservation_by_pnr(FLIGHT, "TST001", date=DATE)
    pax = response["reservations"][0]["passengers"][0]
    _assert_identity(pax, "F", "1994-08-08", "GB")
    _assert_contains(pax["docaAddress"], RESIDENCE)


def test_reservation_list_route_keeps_identity_from_the_later_apis_request(monkeypatch):
    """The reservations list is what the operations reservation table reads."""
    xml = _trip_search(
        _apis(_doca(RESIDENCE))
        + _apis(_docs(gender="F", dob="1994-08-08", nationality="GB"))
    )
    stored = convert_trip_search(xml)
    import backend.api.routes.reservations as reservations_route

    _install_db(monkeypatch, reservations_route, {"reservations": stored})
    response = get_reservations(FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None)
    # ReservationsResponse is the list route's response_model. It keeps
    # gender, date of birth, and nationality, and drops fields it does not declare.
    modeled = ReservationsResponse.model_validate(response)
    pax = modeled.reservations[0].passengers[0]
    assert pax.gender == "F"
    assert pax.dateOfBirth == "1994-08-08"
    assert pax.nationality == "GB"
    _assert_contains(response["reservations"][0]["passengers"][0]["docaAddress"], RESIDENCE)


def test_passenger_list_nationality_comes_from_the_later_apis_request(monkeypatch):
    xml = _trip_search(
        _apis(_doca(RESIDENCE))
        + _apis(_docs(gender="F", dob="1994-08-08", nationality="GB")),
        locator="PNR001",
        last_name="DOE",
    )
    stored = convert_trip_search(xml)
    passenger_list = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "passengers": [
            {"pnr": "PNR001", "lastName": "DOE", "firstName": "JANE", "cabin": "Y"},
        ],
    }
    import backend.api.routes.passengers as passengers_route

    _install_db(
        monkeypatch,
        passengers_route,
        {"passenger_list": passenger_list, "reservations": stored},
    )
    response = get_passengers(FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None)
    assert response["passengers"][0]["nationality"] == "GB"


def test_dashboard_gender_and_nationality_use_the_later_apis_request():
    xml = _trip_search(
        _apis(_doca(RESIDENCE))
        + _apis(_docs(gender="F", dob="1994-08-08", nationality="GB")),
        locator="PNR001",
        last_name="DOE",
    )
    stored = convert_trip_search(xml)
    passenger_list = {
        "totalPassengers": 1,
        "passengers": [
            {
                "pnr": "PNR001",
                "lastName": "DOE",
                "firstName": "JANE",
                "cabin": "Y",
                "isRevenue": True,
                "passengerType": "F",
                "isCheckedIn": False,
                "isBoarded": False,
                "isChild": False,
                "hasInfant": False,
            }
        ],
    }
    payload = _build_dashboard_payload(
        {},
        passenger_list,
        ORIGIN,
        DATE,
        {},
        reservation_doc=stored,
    )
    assert payload["analysis"]["totalFemale"] == 1
    assert payload["analysis"]["nationalityCounts"].get("GB") == 1


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _sample_problems(path):
    """Return (problems, stats) after parsing a committed Trip_SearchRS file."""
    with open(path, encoding="utf-8") as handle:
        xml = handle.read()
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    converted = convert_reservations(body, "GF", FLIGHT, ORIGIN, DATE)
    data = _strip_ns(body)
    raw_reservations = _as_list(
        ((data.get("ReservationsList") or {}).get("Reservations") or {}).get("Reservation")
    )
    problems = []
    stats = {"passengers": 0, "multi_apis": 0, "later_doca": 0, "later_docs": 0}
    if len(raw_reservations) != len(converted["reservations"]):
        problems.append(
            f"reservation count {len(raw_reservations)} != {len(converted['reservations'])}"
        )
        return problems, stats

    for res_index, (raw, conv) in enumerate(zip(raw_reservations, converted["reservations"])):
        inner = ((raw.get("GetReservationRS") or {}).get("Reservation") or {})
        raw_passengers = _as_list(
            ((inner.get("PassengerReservation") or {}).get("Passengers") or {}).get("Passenger")
        )
        conv_passengers = conv.get("passengers") or []
        if len(raw_passengers) != len(conv_passengers):
            problems.append(
                f"reservation {res_index} passenger count "
                f"{len(raw_passengers)} != {len(conv_passengers)}"
            )
            continue
        for pax_index, (raw_pax, conv_pax) in enumerate(zip(raw_passengers, conv_passengers)):
            stats["passengers"] += 1
            requests = _as_list((raw_pax.get("SpecialRequests") or {}).get("APISRequest"))
            requests = [item for item in requests if isinstance(item, dict)]
            if len(requests) > 1:
                stats["multi_apis"] += 1
            genders = []
            dobs = []
            nationalities = []
            addresses = []
            for req_index, request in enumerate(requests):
                for entry in _as_list(request.get("DOCSEntry")):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("Gender"):
                        genders.append(entry["Gender"])
                        if req_index > 0:
                            stats["later_docs"] += 1
                    if entry.get("DateOfBirth"):
                        dobs.append(entry["DateOfBirth"])
                    if entry.get("DocumentNationalityCountry"):
                        nationalities.append(entry["DocumentNationalityCountry"])
                for entry in _as_list(request.get("DOCAEntry")):
                    if isinstance(entry, dict) and entry.get("FreeText"):
                        addresses.append(entry["FreeText"])
                        if req_index > 0:
                            stats["later_doca"] += 1
            label = f"reservation {res_index} passenger {pax_index}"
            if genders and conv_pax.get("gender") not in genders:
                problems.append(f"{label} dropped gender")
            if dobs and conv_pax.get("dateOfBirth") not in dobs:
                problems.append(f"{label} dropped dateOfBirth")
            if nationalities and conv_pax.get("nationality") not in nationalities:
                problems.append(f"{label} dropped nationality")
            stored_address = conv_pax.get("docaAddress") or ""
            for text in addresses:
                if text not in stored_address:
                    problems.append(f"{label} dropped docaAddress")
    return problems, stats


@pytest.mark.parametrize(
    "filename, expect_later_docs",
    [
        ("Trip_SearchRS_GF2057_BOM_2026-03-18.xml", False),
        ("Trip_SearchRS_GF2057_BOM_2026-03-19.xml", True),
    ],
)
def test_committed_gulf_air_trip_search_keeps_every_apis_field(filename, expect_later_docs):
    path = os.path.join(SAMPLE_DIR, filename)
    problems, stats = _sample_problems(path)
    assert stats["multi_apis"] > 0, filename
    assert stats["later_doca"] > 0, filename
    if expect_later_docs:
        assert stats["later_docs"] > 0, filename
    assert problems == [], filename + ": " + "; ".join(problems[:12])
