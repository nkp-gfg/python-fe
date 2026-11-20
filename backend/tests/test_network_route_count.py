"""Network analytics must count a route as a city pair, not a flight number.

`GET /analytics/network` builds `overview.uniqueRoutes` from the flights in the
requested dates. The analytics page prints that number next to the word
"routes". These tests fail while that number is the count of distinct
flight numbers.

A route is one origin plus the destination stored on the passenger list for
that departure. The destination is the itinerary destination
`convert_passenger_list` writes. When several passenger lists exist for the
same departure, the one with the latest `fetchedAt` is the current
destination, matching the lookup already used for analytics filter routes.
Departures that share a city pair add one route. A departure with no
destination is not a route. `totalFlights` stays the number of departures.

The React page only interpolates `overview.uniqueRoutes`. It does not
compute a second count, so it is not covered here.
"""

import os

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")

import pytest
from fastapi.testclient import TestClient

from backend.api.database import close_db
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.feeder import storage


DATE_FROM = "2026-04-01"
DATE_TO = "2026-04-30"


def _ingest_passenger_list(flight_number, origin, departure_date, destination, fetched_at=None):
    """Store one passenger list the same way the feeder pipeline does.

    `fetched_at` overrides the converter clock after the document is written
    so two fetches of one departure have a deterministic order.
    """
    raw = {
        "ItineraryInfo": {
            "Itinerary": {"Destination": destination},
        }
    }
    flight_info = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
    }
    _process_api_call(
        api_type="passenger_list",
        snapshot_type="passenger_list",
        flight_info=flight_info,
        raw_data=raw,
        raw_xml="<GetPassengerListRS/>",
        meta={"requestXml": "<GetPassengerListRQ/>", "httpStatus": 200, "durationMs": 1},
        converter_fn=convert_passenger_list,
        converter_args=(raw, "GF", flight_number, departure_date, origin),
    )
    if fetched_at is None:
        return
    db = storage.get_db()
    stored = db["passenger_list"].find_one(
        {
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "destination": destination,
        },
        sort=[("_id", -1)],
    )
    assert stored is not None
    db["passenger_list"].update_one(
        {"_id": stored["_id"]},
        {"$set": {"fetchedAt": fetched_at}},
    )


def _ingest_flight_without_passenger_list(flight_number, origin, departure_date):
    """A flight-status ingest creates the departure and stores no destination."""
    flight_info = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
    }
    _process_api_call(
        api_type="flight_status",
        snapshot_type="flight_status",
        flight_info=flight_info,
        raw_data={},
        raw_xml="<ACS_FlightDetailRS/>",
        meta={"requestXml": "<ACS_FlightDetailRQ/>", "httpStatus": 200, "durationMs": 1},
        converter_fn=convert_flight_status,
        converter_args=({}, "GF", flight_number, origin, departure_date),
    )


def _overview(client, **filters):
    params = {"date_from": DATE_FROM, "date_to": DATE_TO, **filters}
    response = client.get("/analytics/network", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["overview"], body


@pytest.fixture(scope="module")
def client():
    from backend.api.main import app

    with TestClient(app) as test_client:
        yield test_client
    close_db()
    storage.close()


@pytest.fixture(autouse=True)
def _empty_analytics_collections():
    db = storage.get_db()
    for name in ("flights", "passenger_list", "reservations", "changes", "flight_status"):
        db[name].delete_many({})
    yield


def test_one_flight_number_on_two_city_pairs_counts_both_routes(client):
    """GF100 BAH-DXB and GF100 MCT-DOH are two routes and one flight number."""
    _ingest_passenger_list("100", "BAH", "2026-04-01", "DXB")
    _ingest_passenger_list("100", "MCT", "2026-04-01", "DOH")
    # Outside the window. Must not change the in-range route count.
    _ingest_passenger_list("100", "KWI", "2026-03-15", "AUH")

    overview, _ = _overview(client)

    assert overview["totalFlights"] == 2
    assert overview["uniqueStations"] == 2
    assert overview["uniqueRoutes"] == 2


def test_two_flight_numbers_on_one_city_pair_count_as_one_route(client):
    """GF501 and GF502 both fly BAH-LHR. That is one route and two flight numbers."""
    _ingest_passenger_list("501", "BAH", "2026-04-02", "LHR")
    _ingest_passenger_list("502", "BAH", "2026-04-03", "LHR")
    # Same city pair again on another day. Still one route, three departures.
    _ingest_passenger_list("501", "BAH", "2026-04-04", "LHR")

    overview, _ = _overview(client)

    assert overview["totalFlights"] == 3
    assert overview["uniqueStations"] == 1
    assert overview["uniqueRoutes"] == 1


def test_city_pair_count_is_neither_flight_numbers_nor_stations(client):
    """One flight number, two origins, three city pairs."""
    _ingest_passenger_list("10", "BAH", "2026-04-05", "DXB")
    _ingest_passenger_list("10", "BAH", "2026-04-06", "LHR")
    _ingest_passenger_list("10", "MCT", "2026-04-07", "DOH")

    overview, _ = _overview(client)

    assert overview["totalFlights"] == 3
    assert overview["uniqueStations"] == 2
    assert overview["uniqueRoutes"] == 3


def test_latest_passenger_list_destination_is_the_route(client):
    """An earlier fetch of the same departure is not a second route.

    Both April departures were first stored as BAH-DXB. The later fetch of
    each moved them to different cities. The stale BAH-DXB pair must not
    remain in the count, and the two later cities must both remain.
    """
    _ingest_passenger_list("10", "BAH", "2026-04-01", "DXB", fetched_at="2026-04-01T00:00:00Z")
    _ingest_passenger_list("10", "BAH", "2026-04-01", "LHR", fetched_at="2026-04-01T02:00:00Z")
    _ingest_passenger_list("10", "BAH", "2026-04-02", "DXB", fetched_at="2026-04-02T00:00:00Z")
    _ingest_passenger_list("10", "BAH", "2026-04-02", "CAI", fetched_at="2026-04-02T02:00:00Z")

    overview, _ = _overview(client)

    assert overview["totalFlights"] == 2
    assert overview["uniqueRoutes"] == 2


def test_departure_without_a_destination_is_not_a_route(client):
    """Flight status alone has no city pair. A blank itinerary destination neither."""
    _ingest_passenger_list("200", "BAH", "2026-04-08", "DXB")
    _ingest_flight_without_passenger_list("201", "RUH", "2026-04-08")
    _ingest_passenger_list("202", "RUH", "2026-04-08", "")

    overview, _ = _overview(client)

    assert overview["totalFlights"] == 3
    assert overview["uniqueRoutes"] == 1


def test_origin_filter_still_counts_city_pairs(client):
    """Under origin=BAH, one flight number to two cities is two routes."""
    _ingest_passenger_list("400", "BAH", "2026-04-09", "DXB")
    _ingest_passenger_list("400", "BAH", "2026-04-10", "LHR")
    _ingest_passenger_list("400", "MCT", "2026-04-09", "DOH")

    overview, _ = _overview(client, origin="BAH")

    assert overview["totalFlights"] == 2
    assert overview["uniqueStations"] == 1
    assert overview["uniqueRoutes"] == 2


def test_network_response_uses_the_same_route_count(client):
    """The handler returns the overview the page labels as routes."""
    _ingest_passenger_list("100", "BAH", "2026-04-11", "DXB")
    _ingest_passenger_list("100", "MCT", "2026-04-11", "DOH")
    _ingest_passenger_list("100", "BAH", "2026-04-12", "DXB")

    overview, body = _overview(client)

    assert body["dateRange"] == {"from": DATE_FROM, "to": DATE_TO}
    assert overview["totalFlights"] == 3
    assert overview["uniqueRoutes"] == 2
