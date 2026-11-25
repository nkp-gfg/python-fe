"""Spec for reservation payment and selling-office disclosure.

These tests fail while the single-locator reservation route, and the flight
dashboard built from the same stored bookings, return form-of-payment text or
selling-office fields. They pass once those responses are limited to the fields
the collection route's reservation model already keeps.

The stored documents are produced by the Sabre XML parser and reservation
converter. The HTTP calls go through the real route functions with no
credentials. MongoDB is replaced only as the document store the handlers query.
"""

import copy
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights, passengers, reservations
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-11-25"
OTHER_DATE = "2026-11-26"
TARGET_PNR = "LOCATOR1"
OTHER_PNR = "LOCATOR2"
PAYMENT = "FOP-ALPHA-PLACEHOLDER"
OTHER_PAYMENT = "FOP-BETA-PLACEHOLDER"
NEWER_PAYMENT = "FOP-GAMMA-PLACEHOLDER"
AGENT = "AGENT-PLACEHOLDER"
HEADER = "BOOKING-HEADER-PLACEHOLDER"
SINE = "SINE-PLACEHOLDER"
PCC = "PCC-PLACEHOLDER"
HOME_PCC = "HOME-PCC-PLACEHOLDER"
LAST_NAME = "TRAVELER"
OTHER_LAST_NAME = "OTHERTRAVELER"

# Values the locator response must not contain, tied to the chosen booking
# or to the other booking stored beside it.
FORBIDDEN_VALUES = (
    PAYMENT,
    OTHER_PAYMENT,
    NEWER_PAYMENT,
    AGENT,
    HEADER,
    SINE,
    PCC,
    HOME_PCC,
)


def _sabre_body(xml_text, response_key):
    """Run the product's SOAP parse and body extraction without a session."""
    parser = object.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    return parser._extract_body(parsed, response_key)


def _envelope(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _reservation_xml(pnr, payment_text, last_name):
    return f"""
    <Reservation Locator="{pnr}">
      <stl19:GetReservationRS>
        <stl19:Reservation numberInParty="1" numberOfInfants="0">
          <stl19:BookingDetails>
            <stl19:Header>{HEADER}</stl19:Header>
            <stl19:CreationAgentID>{AGENT}</stl19:CreationAgentID>
            <stl19:CreationTimestamp>2026-11-01T00:00:00</stl19:CreationTimestamp>
            <stl19:PNRSequence>4</stl19:PNRSequence>
            <stl19:FlightsRange Start="2026-11-25" End="2026-11-25"/>
          </stl19:BookingDetails>
          <stl19:POS>
            <stl19:Source AgentDutyCode="4" AgentSine="{SINE}" AirlineVendorID="GF"
              BookingSource="SRC" PseudoCityCode="{PCC}" HomePseudoCityCode="{HOME_PCC}"
              ISOCountry="BH"/>
          </stl19:POS>
          <stl19:PassengerReservation>
            <stl19:Passengers>
              <stl19:Passenger nameId="1.1" nameType="R">
                <stl19:LastName>{last_name}</stl19:LastName>
                <stl19:FirstName>TEST</stl19:FirstName>
              </stl19:Passenger>
            </stl19:Passengers>
          </stl19:PassengerReservation>
          <stl19:OpenReservationElements>
            <or114:OpenReservationElement type="FP">
              <or114:FormOfPayment>
                <or114:Other>
                  <or114:Text>{payment_text}</or114:Text>
                </or114:Other>
              </or114:FormOfPayment>
            </or114:OpenReservationElement>
          </stl19:OpenReservationElements>
        </stl19:Reservation>
      </stl19:GetReservationRS>
    </Reservation>
    """


def _trip_search_xml(payment_for_target, second_payment=OTHER_PAYMENT):
    reservations = _reservation_xml(TARGET_PNR, payment_for_target, LAST_NAME)
    reservations += _reservation_xml(OTHER_PNR, second_payment, OTHER_LAST_NAME)
    body = f"""
    <Trip_SearchRS>
      <ReservationsList NumberResults="2">
        <Reservations>
          {reservations}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
    """
    return _envelope(body)


def _passenger_list_xml():
    body = f"""
    <GetPassengerListRS>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>{LAST_NAME}</LastName>
            <FirstName>TEST</FirstName>
          </Name_Details>
          <PNRLocator>{TARGET_PNR}</PNRLocator>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
    """
    return _envelope(body)


def _stored_reservations(departure_date, fetched_at, payment_for_target):
    raw = _sabre_body(_trip_search_xml(payment_for_target), "Trip_SearchRS")
    doc = convert_reservations(raw, "GF", FLIGHT, ORIGIN, departure_date)
    doc["fetchedAt"] = fetched_at
    return doc


def _stored_passengers():
    raw = _sabre_body(_passenger_list_xml(), "GetPassengerListRS")
    doc = convert_passenger_list(raw, "GF", FLIGHT, DATE, ORIGIN)
    doc["fetchedAt"] = "2026-11-25T00:00:00"
    return doc


def _values_at(doc, dotted):
    current = [doc]
    for part in dotted.split("."):
        nxt = []
        for value in current:
            if isinstance(value, dict) and part in value:
                nxt.append(value[part])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and part in item:
                        nxt.append(item[part])
        current = nxt
    return current


def _matches(doc, query):
    for key, expected in query.items():
        if key.startswith("$"):
            raise AssertionError(f"unsupported query operator: {key}")
        if expected not in _values_at(doc, key):
            return False
    return True


class _MemoryCollection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query=None, *args, **kwargs):
        sort = kwargs.get("sort")
        matches = [doc for doc in self._docs if _matches(doc, query or {})]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: doc.get(field) or "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query=None, projection=None, **kwargs):
        return [
            copy.deepcopy(doc)
            for doc in self._docs
            if _matches(doc, query or {})
        ]


class _MemoryDB:
    def __init__(self, collections):
        self._collections = {
            name: _MemoryCollection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections.setdefault(name, _MemoryCollection([]))


def _app(db):
    application = FastAPI()
    application.include_router(flights.router)
    application.include_router(passengers.router)
    application.include_router(reservations.router)

    def get_db():
        return db

    reservations.get_db = get_db
    passengers.get_db = get_db
    flights.get_db = get_db
    flights._dashboard_cache.clear()
    return application


def _database():
    dated = _stored_reservations(DATE, "2026-11-25T01:00:00", PAYMENT)
    newer = _stored_reservations(OTHER_DATE, "2026-11-26T01:00:00", NEWER_PAYMENT)
    return _MemoryDB({
        "reservations": [dated, newer],
        "passenger_list": [_stored_passengers()],
    })


def _client():
    return TestClient(_app(_database()))


def _no_extra_fields(actual, allowed):
    """actual may omit model defaults, and must not add stored fields."""
    if isinstance(actual, dict):
        assert isinstance(allowed, dict)
        extra = set(actual) - set(allowed)
        assert extra == set(), extra
        for key, value in actual.items():
            _no_extra_fields(value, allowed[key])
    elif isinstance(actual, list):
        assert isinstance(allowed, list)
        assert len(actual) == len(allowed)
        for item, allowed_item in zip(actual, allowed):
            _no_extra_fields(item, allowed_item)
    else:
        assert actual == allowed


def _assert_payment_absent(response):
    for value in FORBIDDEN_VALUES:
        assert value not in response.text


def test_locator_lookup_omits_payment_and_selling_office_for_chosen_record():
    client = _client()
    listed = client.get(f"/flights/{FLIGHT}/passengers", params={"date": DATE})
    assert listed.status_code == 200
    assert PAYMENT not in listed.text
    pnr = listed.json()["passengers"][0]["pnr"]
    assert pnr == TARGET_PNR

    collection = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"date": DATE},
    )
    assert collection.status_code == 200
    _assert_payment_absent(collection)
    filtered = [
        record for record in collection.json()["reservations"]
        if record["pnr"] == pnr
    ]
    assert len(filtered) == 1
    assert filtered[0]["passengers"][0]["lastName"] == LAST_NAME

    locator = client.get(
        f"/flights/{FLIGHT}/reservations/{pnr}",
        params={"date": DATE},
    )
    assert locator.status_code == 200
    _assert_payment_absent(locator)
    body = locator.json()
    assert body["flightNumber"] == FLIGHT
    assert body["departureDate"] == DATE
    assert [record["pnr"] for record in body["reservations"]] == [pnr]
    assert OTHER_LAST_NAME not in locator.text
    _no_extra_fields(body["reservations"][0], filtered[0])


def test_locator_lookup_without_date_omits_newest_booking_payment_fields():
    client = _client()
    collection = client.get(f"/flights/{FLIGHT}/reservations")
    assert collection.status_code == 200
    _assert_payment_absent(collection)
    filtered = [
        record for record in collection.json()["reservations"]
        if record["pnr"] == TARGET_PNR
    ]
    assert len(filtered) == 1

    locator = client.get(f"/flights/{FLIGHT}/reservations/{TARGET_PNR}")
    assert locator.status_code == 200
    _assert_payment_absent(locator)
    body = locator.json()
    assert body["departureDate"] == OTHER_DATE
    assert [record["pnr"] for record in body["reservations"]] == [TARGET_PNR]
    _no_extra_fields(body["reservations"][0], filtered[0])


def test_dashboard_omits_stored_payment_strings():
    client = _client()
    dashboard = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert dashboard.status_code == 200
    payment_methods = (dashboard.json().get("insights") or {}).get("paymentMethods") or {}
    for value in (PAYMENT, OTHER_PAYMENT, NEWER_PAYMENT):
        assert value not in payment_methods
        assert value not in dashboard.text
