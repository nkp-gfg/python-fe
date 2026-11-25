"""Reservation list must return special-service fields conversion already stored.

These tests fail while ReservationPassenger omits specialMeal, wheelchairCode,
hasEmergencyContact, ffTierLevel, and ffTierName. GET /flights/{flight}/reservations
validates the stored document with that model, so those values disappear from
the JSON the reservation card reads. They pass once the model keeps the five
fields and still leaves formOfPayment off the response.

The single-PNR route is not covered here. It has no response model, so it
returns the stored passenger document unchanged.
"""

import copy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reservations as reservations_route
from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

SERVICE_FIELDS = (
    "specialMeal",
    "wheelchairCode",
    "hasEmergencyContact",
    "ffTierLevel",
    "ffTierName",
)

# Same element names and namespace prefixes as a Trip_SearchRS body.
# Wheelchair and emergency-contact elements are absent from the GF2057 sample,
# so this fixture is how those two converter branches are exercised.
WHEELCHAIR_TRIP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="WCHAIR">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-18T01:00:00</stl19:CreationTimestamp>
                  <stl19:UpdateTimestamp>2026-03-18T02:00:00</stl19:UpdateTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01" nameType="S">
                      <stl19:LastName>RAO</stl19:LastName>
                      <stl19:FirstName>ANITA</stl19:FirstName>
                      <stl19:FrequentFlyer>
                        <stl19:Number>10000001</stl19:Number>
                        <stl19:TierLevelNumber>3</stl19:TierLevelNumber>
                        <stl19:ShortText>GLD</stl19:ShortText>
                        <stl19:ReceivingCarrierCode>GF</stl19:ReceivingCarrierCode>
                        <stl19:StatusCode>HK</stl19:StatusCode>
                      </stl19:FrequentFlyer>
                      <stl19:SpecialRequests>
                        <stl19:SpecialMealRequest>
                          <stl19:MealType>VGML</stl19:MealType>
                        </stl19:SpecialMealRequest>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHR</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                        <stl19:EmergencyContactRequest>
                          <stl19:PassengerRefusal>false</stl19:PassengerRefusal>
                        </stl19:EmergencyContactRequest>
                      </stl19:SpecialRequests>
                      <stl19:Seats>
                        <stl19:PreReservedSeats>
                          <stl19:PreReservedSeat>
                            <stl19:SeatNumber>12A</stl19:SeatNumber>
                          </stl19:PreReservedSeat>
                        </stl19:PreReservedSeats>
                      </stl19:Seats>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
                <stl19:OpenReservationElements>
                  <stl19:OpenReservationElement type="FP">
                    <stl19:FormOfPayment>
                      <stl19:Other>
                        <stl19:Text>FOP-TEXT-PLACEHOLDER</stl19:Text>
                      </stl19:Other>
                    </stl19:FormOfPayment>
                  </stl19:OpenReservationElement>
                </stl19:OpenReservationElements>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query=None, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=key: _sort_value(doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _FakeDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _FakeCollection())


def _sort_value(value):
    if value is None:
        return ""
    return value


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$lte" not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _parse_trip_search(xml_text):
    """Parse a Trip_Search SOAP body the same way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _ingest(monkeypatch, xml_text, airline, flight_number, origin, departure_date):
    database = _FakeDB()
    monkeypatch.setattr(storage, "get_db", lambda: database)
    monkeypatch.setattr(reservations_route, "get_db", lambda: database)

    raw = _parse_trip_search(xml_text)
    converted = convert_reservations(
        raw, airline, flight_number, origin, departure_date
    )
    storage.store_reservations(converted)
    storage.store_snapshot(
        request_id="req-special-services",
        airline=airline,
        flight_number=flight_number,
        origin=origin,
        departure_date=departure_date,
        snapshot_type="reservations",
        data=converted,
    )
    return converted


def _client():
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def _reservation(document, pnr):
    return next(item for item in document["reservations"] if item["pnr"] == pnr)


def _passenger(reservation, last_name):
    return next(
        passenger
        for passenger in reservation["passengers"]
        if passenger["lastName"] == last_name
    )


def _assert_services_match_stored(stored, body):
    assert body["reservations"], "reservation list was empty"
    for stored_reservation in stored["reservations"]:
        returned = _reservation(body, stored_reservation["pnr"])
        assert "formOfPayment" not in returned
        for stored_passenger in stored_reservation["passengers"]:
            returned_passenger = _passenger(returned, stored_passenger["lastName"])
            assert returned_passenger["seatNumber"] == stored_passenger["seatNumber"]
            assert (
                returned_passenger["frequentFlyerNumber"]
                == stored_passenger["frequentFlyerNumber"]
            )
            for field in SERVICE_FIELDS:
                assert field in returned_passenger
                assert returned_passenger[field] == stored_passenger[field]


def _get_list(client, flight_number, origin, departure_date, snapshot_sequence=None):
    params = {"origin": origin, "date": departure_date}
    if snapshot_sequence is not None:
        params["snapshot_sequence"] = snapshot_sequence
    # No Authorization header: this route has no auth dependency.
    return client.get(f"/flights/{flight_number}/reservations", params=params)


def test_current_reservation_list_returns_meal_and_tier_from_trip_search(monkeypatch):
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    stored = _ingest(monkeypatch, xml_text, "GF", "2057", "BOM", "2026-03-18")

    karthik = _passenger(_reservation(stored, "CMGMYS"), "KARTHIK")
    memon = _passenger(_reservation(stored, "WUFOLD"), "MEMON")
    assert karthik["specialMeal"] == "AVML"
    assert memon["ffTierLevel"] == "8"
    assert memon["ffTierName"] == "BLU"
    assert any(item.get("formOfPayment") for item in stored["reservations"])

    response = _get_list(_client(), "2057", "BOM", "2026-03-18")
    assert response.status_code == 200
    _assert_services_match_stored(stored, response.json())


def test_snapshot_reservation_list_returns_meal_and_tier_from_trip_search(monkeypatch):
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    stored = _ingest(monkeypatch, xml_text, "GF", "2057", "BOM", "2026-03-18")

    response = _get_list(
        _client(), "2057", "BOM", "2026-03-18", snapshot_sequence=1
    )
    assert response.status_code == 200
    _assert_services_match_stored(stored, response.json())


def test_reservation_list_returns_wheelchair_meal_tier_and_emergency_contact(monkeypatch):
    stored = _ingest(
        monkeypatch, WHEELCHAIR_TRIP_XML, "GF", "2058", "BAH", "2026-03-18"
    )
    passenger = stored["reservations"][0]["passengers"][0]
    assert passenger["specialMeal"] == "VGML"
    assert passenger["wheelchairCode"] == "WCHR"
    assert passenger["hasEmergencyContact"] is True
    assert passenger["ffTierLevel"] == "3"
    assert passenger["ffTierName"] == "GLD"
    assert stored["reservations"][0]["formOfPayment"] == "FOP-TEXT-PLACEHOLDER"

    response = _get_list(_client(), "2058", "BAH", "2026-03-18")
    assert response.status_code == 200
    assert "FOP-TEXT-PLACEHOLDER" not in response.text
    _assert_services_match_stored(stored, response.json())

    snapshot = _get_list(
        _client(), "2058", "BAH", "2026-03-18", snapshot_sequence=1
    )
    assert snapshot.status_code == 200
    assert "FOP-TEXT-PLACEHOLDER" not in snapshot.text
    _assert_services_match_stored(stored, snapshot.json())
