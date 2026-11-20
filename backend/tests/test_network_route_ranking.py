"""City-pair ranking for GET /analytics/network.

The network analytics page calls this endpoint and plots routePerformance as
"Top Routes by Passengers". These tests fail while that series sums every
destination from a departure airport into one bar.

They are expected to fail until route performance is grouped by origin and
destination, using the latest passenger list for each flight.
"""

import os

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

import pytest
from fastapi.testclient import TestClient

import backend.api.database as database
import backend.feeder.storage as storage
from backend.api.main import app
from backend.feeder.converter import convert_passenger_list

TEST_DB = "falconeye_route_ranking_test"
DATE = "2026-04-01"
DATE_TO = "2026-04-02"


def _raw_passenger_list(destination: str, count: int) -> dict:
    passengers = []
    for index in range(count):
        passengers.append(
            {
                "Name_Details": {"LastName": f"LAST{index}", "FirstName": "FIRST"},
                "PNRLocator": {"#text": f"P{index:04d}"},
                "PassengerID": str(index),
                "BookingClass": "Y",
                "Cabin": "Y",
                "PassengerType": "F",
            }
        )
    return {
        "ItineraryInfo": {"Itinerary": {"Destination": destination}},
        "PassengerInfoList": {"PassengerInfo": passengers},
    }


def _store_passenger_list(
    flight_number: str,
    origin: str,
    departure_date: str,
    destination: str,
    count: int,
    fetched_at: str,
) -> None:
    """Store a passenger list the same way ingestion does, then pin fetchedAt.

    convert_passenger_list stamps fetchedAt with the current time. Two snapshots
    of one flight need an explicit order, so the timestamp is set after conversion
    to the value the converter would have written at that fetch.
    """
    document = convert_passenger_list(
        _raw_passenger_list(destination, count),
        "GF",
        flight_number,
        departure_date,
        origin,
    )
    assert document["origin"] == origin
    assert document["destination"] == destination
    assert document["totalPassengers"] == count
    document["fetchedAt"] = fetched_at
    storage.store_passenger_list(document)


def _city_pair(routes: list[dict], origin: str, destination: str) -> dict:
    matches = [
        route
        for route in routes
        if route.get("origin") == origin and route.get("destination") == destination
    ]
    assert len(matches) == 1, {
        "origin": origin,
        "destination": destination,
        "routePerformance": routes,
    }
    return matches[0]


@pytest.fixture()
def client():
    database.DB_NAME = TEST_DB
    database.close_db()
    storage._db = None
    storage._client = None
    storage._owns_connection = False
    with TestClient(app) as test_client:
        db = database.get_db()
        assert db.name == TEST_DB
        db.passenger_list.delete_many({})
        yield test_client
        db.client.drop_database(TEST_DB)
    database.close_db()
    storage._db = None
    storage._client = None


def _load_split_hub() -> None:
    """One origin feeding two city pairs, plus a busier single route.

    BAH passengers are 100 on BAH-LHR and 80 on BAH-DXB (180 combined).
    DOH-MCT has 150. A station ranking puts BAH first. A route ranking puts
    DOH-MCT first. An older BAH-LHR snapshot of 400 passengers must not count,
    and a BAH-LHR list outside the date range must not count.
    """
    _store_passenger_list("101", "BAH", DATE, "LHR", 400, "2026-04-01T00:00:00Z")
    _store_passenger_list("101", "BAH", DATE, "LHR", 70, "2026-04-01T12:00:00Z")
    _store_passenger_list("103", "BAH", DATE, "LHR", 30, "2026-04-01T12:00:00Z")
    _store_passenger_list("102", "BAH", DATE, "DXB", 80, "2026-04-01T12:00:00Z")
    _store_passenger_list("201", "DOH", DATE_TO, "MCT", 150, "2026-04-02T08:00:00Z")
    _store_passenger_list("999", "BAH", "2026-03-01", "LHR", 1000, "2026-03-01T08:00:00Z")


def test_network_analytics_ranks_city_pairs_not_departure_airports(client):
    _load_split_hub()

    response = client.get(
        "/analytics/network",
        params={"date_from": DATE, "date_to": DATE_TO},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["routePerformance"]

    bah_lhr = _city_pair(routes, "BAH", "LHR")
    bah_dxb = _city_pair(routes, "BAH", "DXB")
    doh_mct = _city_pair(routes, "DOH", "MCT")

    assert bah_lhr["totalPassengers"] == 100
    assert bah_lhr["flightCount"] == 2
    assert bah_lhr["avgPassengers"] == 50

    assert bah_dxb["totalPassengers"] == 80
    assert bah_dxb["flightCount"] == 1
    assert bah_dxb["avgPassengers"] == 80

    assert doh_mct["totalPassengers"] == 150
    assert doh_mct["flightCount"] == 1
    assert doh_mct["avgPassengers"] == 150

    assert len(routes) == 3
    assert [route["origin"] for route in routes] == ["DOH", "BAH", "BAH"]
    assert [route["destination"] for route in routes] == ["MCT", "LHR", "DXB"]
    assert [route["totalPassengers"] for route in routes] == [150, 100, 80]
    assert all(route["totalPassengers"] != 180 for route in routes)


def test_network_analytics_keeps_city_pairs_when_origin_is_filtered(client):
    _load_split_hub()

    response = client.get(
        "/analytics/network",
        params={"date_from": DATE, "date_to": DATE_TO, "origin": "BAH"},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["routePerformance"]

    bah_lhr = _city_pair(routes, "BAH", "LHR")
    bah_dxb = _city_pair(routes, "BAH", "DXB")
    assert bah_lhr["totalPassengers"] == 100
    assert bah_lhr["flightCount"] == 2
    assert bah_dxb["totalPassengers"] == 80
    assert bah_dxb["flightCount"] == 1
    assert len(routes) == 2
    assert [route["destination"] for route in routes] == ["LHR", "DXB"]
    assert [route["totalPassengers"] for route in routes] == [100, 80]
    assert all(route["totalPassengers"] != 180 for route in routes)


def test_network_analytics_keeps_city_pair_when_destination_is_filtered(client):
    _load_split_hub()

    response = client.get(
        "/analytics/network",
        params={"date_from": DATE, "date_to": DATE_TO, "destination": "LHR"},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["routePerformance"]

    bah_lhr = _city_pair(routes, "BAH", "LHR")
    assert bah_lhr["totalPassengers"] == 100
    assert bah_lhr["flightCount"] == 2
    assert bah_lhr["avgPassengers"] == 50
    assert len(routes) == 1
    assert routes[0]["origin"] == "BAH"
    assert routes[0]["destination"] == "LHR"
