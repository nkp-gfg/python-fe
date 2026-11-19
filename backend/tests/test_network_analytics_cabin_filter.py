"""Cabin filter on GET /analytics/network.

An operator who selects Economy (Y) or Business (J) must see headline
passenger totals, load factor, route ranking, daily passenger trend, and
cabin mix computed from that cabin alone. Booking-class counts and
check-in / boarding rates already honor the cabin; these tests require the
other passenger aggregations to match them.

The assertions fail while those aggregations still sum every cabin.

Not covered here: nationality, loyalty, meals, wheelchair, party size,
point of sale, booking lead time, change counts, and upgrade counts.
Those pipelines read reservations or changes and do not take a cabin.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import MongoClient

from backend.api.routes.analytics import router

DATE_FROM = "2026-04-01"
DATE_TO = "2026-04-02"
MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018")
DB_NAME = "falconeye_cabin_filter_test"


def _passenger(cabin, booking_class, checked_in, boarded):
    return {
        "cabin": cabin,
        "bookingClass": booking_class,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "isRevenue": True,
        "isStandby": False,
    }


def _passenger_list(flight_number, origin, destination, departure_date, fetched_at,
                    passengers, economy_authorized, business_authorized):
    """Document shape produced by convert_passenger_list."""
    economy = [p for p in passengers if p["cabin"] == "Y"]
    business = [p for p in passengers if p["cabin"] == "J"]
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "destination": destination,
        "departureDate": departure_date,
        "fetchedAt": fetched_at,
        "cabinSummary": [
            {
                "cabin": "Y",
                "count": len(economy),
                "authorized": economy_authorized,
                "available": economy_authorized - len(economy),
                "destination": destination,
            },
            {
                "cabin": "J",
                "count": len(business),
                "authorized": business_authorized,
                "available": business_authorized - len(business),
                "destination": destination,
            },
        ],
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": len(passengers),
        "passengers": passengers,
    }


def _seed(db):
    """Two latest flight snapshots plus one older snapshot that must be ignored.

    Latest GF100 BAH 2026-04-01: 3 economy (authorized 10), 1 business (authorized 4).
    Latest GF200 LHR 2026-04-02: 1 economy (authorized 10), 2 business (authorized 4).
    Economy across the network is 4; business is 3; both cabins together are 7.
    """
    bah_latest = [
        _passenger("Y", "V", True, True),
        _passenger("Y", "V", True, False),
        _passenger("Y", "W", False, False),
        _passenger("J", "C", True, True),
    ]
    bah_stale = [
        _passenger("Y", "V", False, False)
        for _ in range(50)
    ] + [
        _passenger("J", "C", False, False)
        for _ in range(50)
    ]
    lhr_latest = [
        _passenger("Y", "M", False, False),
        _passenger("J", "D", True, True),
        _passenger("J", "D", True, False),
    ]
    db.passenger_list.insert_many([
        _passenger_list("100", "BAH", "LHR", "2026-04-01", "2026-04-01T12:00:00+00:00",
                        bah_latest, 10, 4),
        _passenger_list("100", "BAH", "LHR", "2026-04-01", "2026-04-01T01:00:00+00:00",
                        bah_stale, 100, 40),
        _passenger_list("200", "LHR", "BAH", "2026-04-02", "2026-04-02T12:00:00+00:00",
                        lhr_latest, 10, 4),
    ])
    db.flights.insert_many([
        {"airline": "GF", "flightNumber": "100", "origin": "BAH", "departureDate": "2026-04-01"},
        {"airline": "GF", "flightNumber": "200", "origin": "LHR", "departureDate": "2026-04-02"},
    ])


@pytest.fixture
def api_client():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[DB_NAME]
    client.drop_database(DB_NAME)
    _seed(db)

    app = FastAPI()
    app.include_router(router)
    with patch("backend.api.routes.analytics.get_db", lambda: db):
        with TestClient(app) as test_client:
            yield test_client
    client.drop_database(DB_NAME)
    client.close()


def _network(api_client, cabin=None):
    params = {"date_from": DATE_FROM, "date_to": DATE_TO}
    if cabin is not None:
        params["cabin"] = cabin
    response = api_client.get("/analytics/network", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _routes(body):
    return {row["origin"]: row for row in body["routePerformance"]}


def _trends(body):
    return {row["date"]: row["passengers"] for row in body["dailyTrends"]}


def test_network_without_cabin_sums_every_cabin(api_client):
    body = _network(api_client)
    overview = body["overview"]

    assert body["filters"]["cabin"] is None
    assert overview["totalPassengers"] == 7
    assert overview["totalSouls"] == 7
    assert overview["economyPassengers"] == 4
    assert overview["businessPassengers"] == 3
    assert overview["avgPassengersPerFlight"] == 3.5
    assert overview["maxPassengersOnFlight"] == 4
    assert overview["avgLoadFactor"] == 25.0

    routes = _routes(body)
    assert [row["origin"] for row in body["routePerformance"]] == ["BAH", "LHR"]
    assert routes["BAH"]["totalPassengers"] == 4
    assert routes["BAH"]["avgPassengers"] == 4.0
    assert routes["LHR"]["totalPassengers"] == 3
    assert routes["LHR"]["avgPassengers"] == 3.0
    assert routes["BAH"]["flightCount"] == 1
    assert routes["LHR"]["flightCount"] == 1

    assert _trends(body) == {"2026-04-01": 4, "2026-04-02": 3}

    rates = body["operationalRates"]
    assert rates["totalPassengers"] == 7
    assert rates["checkedIn"] == 5
    assert rates["boarded"] == 3
    assert overview["totalPassengers"] == rates["totalPassengers"]


def test_economy_cabin_limits_headlines_routes_trend_and_mix(api_client):
    """Same request the analytics page sends when Economy is selected."""
    body = _network(api_client, "Y")
    overview = body["overview"]
    rates = body["operationalRates"]

    assert body["filters"]["cabin"] == "Y"
    assert overview["economyPassengers"] == 4
    assert overview["businessPassengers"] == 0
    assert overview["totalPassengers"] == 4
    assert overview["totalPassengers"] == overview["economyPassengers"]
    assert overview["totalSouls"] == 4
    assert overview["avgPassengersPerFlight"] == 2.0
    assert overview["maxPassengersOnFlight"] == 3
    # 4 booked economy seats / 20 authorized economy seats.
    assert overview["avgLoadFactor"] == 20.0
    assert overview["totalPassengers"] == rates["totalPassengers"]

    routes = _routes(body)
    assert [row["origin"] for row in body["routePerformance"]] == ["BAH", "LHR"]
    assert routes["BAH"]["totalPassengers"] == 3
    assert routes["BAH"]["avgPassengers"] == 3.0
    assert routes["LHR"]["totalPassengers"] == 1
    assert routes["LHR"]["avgPassengers"] == 1.0

    assert _trends(body) == {"2026-04-01": 3, "2026-04-02": 1}

    classes = body["bookingClassDistribution"]
    assert classes
    assert {item["cabin"] for item in classes} == {"Y"}
    assert sum(item["count"] for item in classes) == 4
    assert rates["checkedIn"] == 2
    assert rates["boarded"] == 1
    assert rates["checkInRate"] == 50.0
    assert rates["boardingRate"] == 25.0


def test_business_cabin_limits_headlines_routes_trend_and_mix(api_client):
    """Same request the analytics page sends when Business is selected."""
    body = _network(api_client, "J")
    overview = body["overview"]
    rates = body["operationalRates"]

    assert body["filters"]["cabin"] == "J"
    assert overview["businessPassengers"] == 3
    assert overview["economyPassengers"] == 0
    assert overview["totalPassengers"] == 3
    assert overview["totalPassengers"] == overview["businessPassengers"]
    assert overview["totalSouls"] == 3
    assert overview["avgPassengersPerFlight"] == 1.5
    assert overview["maxPassengersOnFlight"] == 2
    # 3 booked business seats / 8 authorized business seats.
    assert overview["avgLoadFactor"] == 37.5
    assert overview["totalPassengers"] == rates["totalPassengers"]

    routes = _routes(body)
    assert [row["origin"] for row in body["routePerformance"]] == ["LHR", "BAH"]
    assert routes["LHR"]["totalPassengers"] == 2
    assert routes["LHR"]["avgPassengers"] == 2.0
    assert routes["BAH"]["totalPassengers"] == 1
    assert routes["BAH"]["avgPassengers"] == 1.0

    assert _trends(body) == {"2026-04-01": 1, "2026-04-02": 2}

    classes = body["bookingClassDistribution"]
    assert classes
    assert {item["cabin"] for item in classes} == {"J"}
    assert sum(item["count"] for item in classes) == 3
    assert rates["checkedIn"] == 3
    assert rates["boarded"] == 2
    assert rates["checkInRate"] == 100.0
    assert rates["boardingRate"] == 66.7
