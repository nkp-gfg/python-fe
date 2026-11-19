"""Unconfirmed Sabre meal and wheelchair requests must not be services to deliver.

Trip_SearchRS carries SpecialMealRequest and WheelchairRequest with a Sabre
action code. HK and KK are the confirmed statuses this product already treats
as held (the reservation view highlights an SSR only for those codes). NN is
an unconfirmed request. UC is unable, not a service to stage.

These tests follow the ingestion path the dashboard actually runs:

  SOAP XML
    -> SabreClient._parse_xml / _extract_body
    -> convert_reservations
    -> reservations document (store_reservations inserts that document)
    -> GET /flights/{flight_number}/dashboard
         (_fetch_dashboard_data_parallel strips _raw, then
          _build_dashboard_payload counts services)

They fail while conversion drops the action code and the dashboard counts
every stored meal type and wheelchair code.

Contract the fix has to meet:

- Each passenger keeps specialMeal / wheelchairCode and the Sabre action
  code on specialMealActionCode / wheelchairActionCode.
- specialRequestsSummary.specialMeals, totalSpecialMeals, wheelchairs, and
  totalWheelchairs count only HK and KK.
- NN is reported separately as pendingSpecialMeals, totalPendingSpecialMeals,
  pendingWheelchairs, and totalPendingWheelchairs.
- UC (and any other code) is in neither bucket.
- insights.mealCodes counts only confirmed meals. NN is pendingMealCodes.
- The passenger list enrichment copies the same action codes.

Not covered here, and still the same bug if left unchanged:

- backend/api/routes/analytics.py meal and wheelchair aggregations. They
  count every non-empty specialMeal and wheelchairCode and need MongoDB.
- The flight-workbench tab header, stat-cards, passenger-table badges, and
  reservation-view badges. They print the API fields above and do not apply
  their own status check. This repo has no component test runner.
- A passenger with more than one SpecialMealRequest. Conversion keeps only
  the first element.
- Wheelchair totals derived from passenger-list editCodes
  (insights.wheelchairTypes), which are not Trip_SearchRS wheelchair requests.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    _dashboard_cache,
    get_flight_dashboard,
)
from backend.api.routes.passengers import get_passengers  # noqa: E402
from backend.feeder.converter import convert_reservations  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_TRIP_SEARCH = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        matches = []
        for doc in self._docs:
            if all(doc.get(key) == value for key, value in query.items()):
                matches.append(doc)
        if sort:
            for field, direction in reversed(sort):
                matches.sort(key=lambda item: item.get(field) or "", reverse=direction < 0)
        if not matches:
            return None
        return dict(matches[0])

    def find(self, query, projection=None):
        found = []
        for doc in self._docs:
            if all(doc.get(key) == value for key, value in query.items()):
                found.append(dict(doc))
        return _Cursor(found)


class _DB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _passenger_xml(name_id, last_name, first_name, meal=None, wheelchair=None):
    requests = []
    if meal:
        meal_type, action = meal
        requests.append(
            f"""<stl19:SpecialMealRequest id="{name_id}-meal" type="A">
              <stl19:MealType>{meal_type}</stl19:MealType>
              <stl19:FlightNumber>2057</stl19:FlightNumber>
              <stl19:VendorCode>GF</stl19:VendorCode>
              <stl19:FlightDate>2026-03-19</stl19:FlightDate>
              <stl19:BoardCity>BOM</stl19:BoardCity>
              <stl19:ActionCode>{action}</stl19:ActionCode>
              <stl19:NumberInParty>1</stl19:NumberInParty>
            </stl19:SpecialMealRequest>"""
        )
    if wheelchair:
        code, action = wheelchair
        requests.append(
            f"""<stl19:WheelchairRequest id="{name_id}-wc" type="A">
              <stl19:WheelchairCode>{code}</stl19:WheelchairCode>
              <stl19:ActionCode>{action}</stl19:ActionCode>
              <stl19:NumberInParty>1</stl19:NumberInParty>
            </stl19:WheelchairRequest>"""
        )
    body = "\n".join(requests)
    return f"""<stl19:Passenger nameId="{name_id}" nameType="R">
      <stl19:LastName>{last_name}</stl19:LastName>
      <stl19:FirstName>{first_name}</stl19:FirstName>
      <stl19:SpecialRequests>
        {body}
      </stl19:SpecialRequests>
    </stl19:Passenger>"""


def _trip_search_xml(locator, passengers_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord">
      <Success>Success</Success>
      <ReservationsList NumberResults="1" NumberPages="1" TotalResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                  <stl19:CreationTimestamp>2026-03-19T01:00:00</stl19:CreationTimestamp>
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
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert_trip_search(xml, flight_number="2057", origin="BOM", departure_date="2026-03-19"):
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", flight_number, origin, departure_date)


def _passengers_of(doc):
    found = []
    for reservation in doc.get("reservations", []):
        for passenger in reservation.get("passengers", []):
            found.append(passenger)
    return found


def _dashboard_db(flight_number, origin, departure_date, reservation_doc, manifest):
    return _DB({
        "flight_status": _Collection([{
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "status": "OPEN",
            "fetchedAt": "2026-03-19T00:00:00Z",
        }]),
        "passenger_list": _Collection([{
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "fetchedAt": "2026-03-19T00:00:00Z",
            "totalPassengers": len(manifest),
            "passengers": manifest,
        }]),
        "reservations": _Collection([reservation_doc]),
        "changes": _Collection([]),
    })


def _load_dashboard(monkeypatch, flight_number, origin, departure_date, reservation_doc, manifest):
    _dashboard_cache.clear()
    db = _dashboard_db(flight_number, origin, departure_date, reservation_doc, manifest)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: db)
    # Call the route function directly. Omit the FastAPI Query defaults:
    # those objects are truthy and would take the snapshot-history branch.
    return get_flight_dashboard(
        flight_number, origin=origin, date=departure_date, snapshot_sequence=None,
    )


def _manifest_from(doc):
    manifest = []
    for reservation in doc.get("reservations", []):
        pnr = reservation.get("pnr", "")
        for passenger in reservation.get("passengers", []):
            manifest.append({
                "lastName": passenger.get("lastName", ""),
                "firstName": passenger.get("firstName", ""),
                "pnr": pnr,
            })
    return manifest


def test_conversion_keeps_action_code_on_meal_and_wheelchair():
    xml = _trip_search_xml(
        "NNHK01",
        "\n".join([
            _passenger_xml("01.01", "PENDING", "MEAL", meal=("AVML", "NN"), wheelchair=("WCHS", "NN")),
            _passenger_xml("02.01", "HELD", "MEAL", meal=("VGML", "HK"), wheelchair=("WCHR", "KK")),
            _passenger_xml("03.01", "DENIED", "MEAL", meal=("GFML", "UC")),
        ]),
    )
    doc = _convert_trip_search(xml)
    by_name = {p["lastName"]: p for p in _passengers_of(doc)}

    pending = by_name["PENDING"]
    assert pending["specialMeal"] == "AVML"
    assert pending["specialMealActionCode"] == "NN"
    assert pending["wheelchairCode"] == "WCHS"
    assert pending["wheelchairActionCode"] == "NN"

    held = by_name["HELD"]
    assert held["specialMeal"] == "VGML"
    assert held["specialMealActionCode"] == "HK"
    assert held["wheelchairCode"] == "WCHR"
    assert held["wheelchairActionCode"] == "KK"

    denied = by_name["DENIED"]
    assert denied["specialMeal"] == "GFML"
    assert denied["specialMealActionCode"] == "UC"
    assert denied["wheelchairCode"] == ""
    assert denied["wheelchairActionCode"] == ""


def test_dashboard_counts_only_confirmed_services(monkeypatch):
    xml = _trip_search_xml(
        "NNHK01",
        "\n".join([
            _passenger_xml("01.01", "PENDING", "MEAL", meal=("AVML", "NN"), wheelchair=("WCHS", "NN")),
            _passenger_xml("02.01", "HELD", "MEAL", meal=("VGML", "HK"), wheelchair=("WCHR", "KK")),
            _passenger_xml("03.01", "DENIED", "MEAL", meal=("GFML", "UC")),
            _passenger_xml("04.01", "SECOND", "HELD", meal=("AVML", "KK")),
        ]),
    )
    doc = _convert_trip_search(xml, flight_number="2057")
    dashboard = _load_dashboard(
        monkeypatch, "2057", "BOM", "2026-03-19", doc, _manifest_from(doc),
    )
    summary = dashboard["specialRequestsSummary"]

    assert summary["specialMeals"] == {"VGML": 1, "AVML": 1}
    assert summary["totalSpecialMeals"] == 2
    assert summary["wheelchairs"] == {"WCHR": 1}
    assert summary["totalWheelchairs"] == 1
    assert summary["pendingSpecialMeals"] == {"AVML": 1}
    assert summary["totalPendingSpecialMeals"] == 1
    assert summary["pendingWheelchairs"] == {"WCHS": 1}
    assert summary["totalPendingWheelchairs"] == 1

    for bucket in ("specialMeals", "wheelchairs", "pendingSpecialMeals", "pendingWheelchairs"):
        assert "GFML" not in summary[bucket]
        assert "UC" not in summary[bucket]

    insights = dashboard["insights"]
    assert insights["mealCodes"] == {"VGML": 1, "AVML": 1}
    assert insights["pendingMealCodes"] == {"AVML": 1}
    assert "GFML" not in insights["mealCodes"]
    assert "GFML" not in insights["pendingMealCodes"]


def test_dashboard_ignores_meal_and_wheelchair_without_action_code(monkeypatch):
    """A stored code with no action code is not a confirmed service.

    The dashboard fetch removes _raw before counting, so the count cannot
    recover a status that conversion failed to copy onto the passenger.
    """
    reservation_doc = {
        "airline": "GF",
        "flightNumber": "3001",
        "departureAirport": "BAH",
        "departureDate": "2026-04-01",
        "fetchedAt": "2026-04-01T00:00:00Z",
        "reservations": [{
            "pnr": "NOSTAT",
            "passengers": [{
                "lastName": "UNKNOWN",
                "firstName": "STATUS",
                "specialMeal": "AVML",
                "wheelchairCode": "WCHS",
            }],
        }],
    }
    manifest = [{"lastName": "UNKNOWN", "firstName": "STATUS", "pnr": "NOSTAT"}]
    dashboard = _load_dashboard(
        monkeypatch, "3001", "BAH", "2026-04-01", reservation_doc, manifest,
    )
    summary = dashboard["specialRequestsSummary"]
    assert summary["totalSpecialMeals"] == 0
    assert summary["specialMeals"] == {}
    assert summary["totalWheelchairs"] == 0
    assert summary["wheelchairs"] == {}
    assert summary["totalPendingSpecialMeals"] == 0
    assert summary["totalPendingWheelchairs"] == 0
    assert dashboard["insights"]["mealCodes"] == {}


def test_gf2057_sample_nn_requests_are_not_services_to_deliver(monkeypatch):
    xml = SAMPLE_TRIP_SEARCH.read_text(encoding="utf-8")
    doc = _convert_trip_search(xml, flight_number="2057", origin="BOM", departure_date="2026-03-19")

    dashboard = _load_dashboard(
        monkeypatch, "2057", "BOM", "2026-03-19", doc, _manifest_from(doc),
    )
    summary = dashboard["specialRequestsSummary"]
    assert summary["totalSpecialMeals"] == 0
    assert summary["specialMeals"] == {}
    assert summary["totalWheelchairs"] == 0
    assert summary["wheelchairs"] == {}
    assert summary["pendingSpecialMeals"] == {"AVML": 3, "VJML": 1}
    assert summary["totalPendingSpecialMeals"] == 4
    assert summary["pendingWheelchairs"] == {"WCHS": 1}
    assert summary["totalPendingWheelchairs"] == 1
    assert dashboard["insights"]["mealCodes"] == {}
    assert dashboard["insights"]["pendingMealCodes"] == {"AVML": 3, "VJML": 1}

    stored = [
        p for p in _passengers_of(doc)
        if p.get("specialMeal") or p.get("wheelchairCode")
    ]
    assert stored, "sample Trip_SearchRS produced no meal or wheelchair requests"
    meals = {}
    wheelchairs = {}
    for passenger in stored:
        if passenger.get("specialMeal"):
            assert passenger.get("specialMealActionCode") == "NN"
            meals[passenger["specialMeal"]] = meals.get(passenger["specialMeal"], 0) + 1
        if passenger.get("wheelchairCode"):
            assert passenger.get("wheelchairActionCode") == "NN"
            code = passenger["wheelchairCode"]
            wheelchairs[code] = wheelchairs.get(code, 0) + 1
    assert meals == {"AVML": 3, "VJML": 1}
    assert wheelchairs == {"WCHS": 1}


def test_passenger_api_copies_action_code(monkeypatch):
    xml = _trip_search_xml(
        "PAXAPI",
        "\n".join([
            _passenger_xml("01.01", "PENDING", "MEAL", meal=("AVML", "NN"), wheelchair=("WCHS", "NN")),
            _passenger_xml("02.01", "HELD", "MEAL", meal=("VGML", "HK"), wheelchair=("WCHR", "KK")),
        ]),
    )
    doc = _convert_trip_search(xml, flight_number="2058", origin="BAH", departure_date="2026-03-20")
    db = _DB({
        "passenger_list": _Collection([{
            "flightNumber": "2058",
            "origin": "BAH",
            "departureDate": "2026-03-20",
            "fetchedAt": "2026-03-20T00:00:00Z",
            "passengers": _manifest_from(doc),
        }]),
        "reservations": _Collection([doc]),
    })
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: db)
    response = get_passengers(
        "2058", origin="BAH", date="2026-03-20", snapshot_sequence=None,
    )
    by_name = {p["lastName"]: p for p in response["passengers"]}

    pending = by_name["PENDING"]
    assert pending["specialMeal"] == "AVML"
    assert pending["specialMealActionCode"] == "NN"
    assert pending["wheelchairCode"] == "WCHS"
    assert pending["wheelchairActionCode"] == "NN"

    held = by_name["HELD"]
    assert held["specialMeal"] == "VGML"
    assert held["specialMealActionCode"] == "HK"
    assert held["wheelchairCode"] == "WCHR"
    assert held["wheelchairActionCode"] == "KK"
