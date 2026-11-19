"""Destination filter must apply to every network-analytics aggregation.

GET /analytics/network accepts `destination` and the analytics page sends it
whenever a destination is selected. Passenger-list pipelines honor it.
Flight, reservation, and change pipelines currently drop it, so the same
response mixes destination-scoped passenger KPIs with network-wide flight,
disruption, and commercial charts.

These tests encode the expected dashboard: when destination=DMM, every
metric the page presents as filtered must include only DMM arrivals.
They fail until that inconsistency is fixed.

Production document shapes (no destination on flights / reservations /
changes; destination on passenger_list) are used so a naive $match on a
missing field cannot satisfy the spec. A fix must correlate those
collections to passenger_list destinations, persist arrival airport on
those documents, or reject the destination parameter with HTTP 400.

Run:
    pip install pytest httpx mongomock
    PYTHONPATH=. pytest backend/tests/test_analytics_destination_filter.py -v
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

mongomock = pytest.importorskip("mongomock")

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/falconeye")

from backend.api.routes import analytics as analytics_mod  # noqa: E402


DATE = "2026-03-19"

# GF2057 BOM→DMM — the requested destination.
DMM_FLIGHT = "2057"
DMM_ORIGIN = "BOM"
DMM_DEST = "DMM"

# GF2058 BOM→BAH — same origin, other arrival. An origin filter must not
# hide this flight; destination=DMM must exclude it from every chart.
BAH_SAME_ORIGIN_FLIGHT = "2058"
BAH_DEST = "BAH"

# GF2006 LHR→BAH — other origin and arrival in the same date range.
OTHER_FLIGHT = "2006"
OTHER_ORIGIN = "LHR"


def _pax(cabin, booking_class, *, checked_in=False, boarded=False, revenue=True, standby=False):
    return {
        "cabin": cabin,
        "bookingClass": booking_class,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "isRevenue": revenue,
        "isStandby": standby,
    }


def seed_two_arrivals(db):
    """Insert production-shaped docs for one DMM flight and two BAH flights.

    Flights, reservations, and changes omit destination (ingest today does
    not store it). Passenger lists include destination, matching
    convert_passenger_list.
    """
    db.flights.insert_many([
        {
            "airline": "GF",
            "flightNumber": DMM_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "summary": {"totalPax": 2},
        },
        {
            "airline": "GF",
            "flightNumber": BAH_SAME_ORIGIN_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "summary": {"totalPax": 5},
        },
        {
            "airline": "GF",
            "flightNumber": OTHER_FLIGHT,
            "origin": OTHER_ORIGIN,
            "departureDate": DATE,
            "summary": {"totalPax": 10},
        },
    ])

    db.passenger_list.insert_many([
        {
            "airline": "GF",
            "flightNumber": DMM_FLIGHT,
            "origin": DMM_ORIGIN,
            "destination": DMM_DEST,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T13:56:57Z",
            "totalPassengers": 2,
            "adultCount": 2,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 2,
            "cabinSummary": [
                {"cabin": "Y", "count": 1, "authorized": 150, "destination": DMM_DEST},
                {"cabin": "J", "count": 1, "authorized": 16, "destination": DMM_DEST},
            ],
            "passengers": [
                _pax("Y", "V", checked_in=True, boarded=True),
                _pax("J", "J"),
            ],
        },
        {
            "airline": "GF",
            "flightNumber": BAH_SAME_ORIGIN_FLIGHT,
            "origin": DMM_ORIGIN,
            "destination": BAH_DEST,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T12:00:00Z",
            "totalPassengers": 5,
            "adultCount": 5,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 5,
            "cabinSummary": [
                {"cabin": "Y", "count": 5, "authorized": 150, "destination": BAH_DEST},
            ],
            "passengers": [_pax("Y", "Q", checked_in=True) for _ in range(5)],
        },
        {
            "airline": "GF",
            "flightNumber": OTHER_FLIGHT,
            "origin": OTHER_ORIGIN,
            "destination": BAH_DEST,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T11:00:00Z",
            "totalPassengers": 10,
            "adultCount": 8,
            "childCount": 2,
            "infantCount": 0,
            "totalSouls": 10,
            "cabinSummary": [
                {"cabin": "Y", "count": 9, "authorized": 150, "destination": BAH_DEST},
                {"cabin": "J", "count": 1, "authorized": 16, "destination": BAH_DEST},
            ],
            "passengers": (
                [_pax("Y", "Q", checked_in=True) for _ in range(9)]
                + [_pax("J", "J", checked_in=True, boarded=True)]
            ),
        },
    ])

    db.reservations.insert_many([
        {
            "airline": "GF",
            "flightNumber": DMM_FLIGHT,
            "departureAirport": DMM_ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T13:56:57Z",
            "reservations": [
                {
                    "numberInParty": 2,
                    "pointOfSale": {"isoCountry": "BH"},
                    "passengers": [
                        {
                            "nationality": "IN",
                            "frequentFlyerNumber": "GF111",
                            "ffTierLevel": "1",
                            "specialMeal": "AVML",
                            "wheelchairCode": "WCHR",
                        },
                        {
                            "nationality": "IN",
                            "frequentFlyerNumber": "",
                            "ffTierLevel": "",
                            "specialMeal": "",
                            "wheelchairCode": "",
                        },
                    ],
                    "segments": [{"segmentBookedDate": "2026-03-12"}],
                }
            ],
        },
        {
            "airline": "GF",
            "flightNumber": BAH_SAME_ORIGIN_FLIGHT,
            "departureAirport": DMM_ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T12:00:00Z",
            "reservations": [
                {
                    "numberInParty": 5,
                    "pointOfSale": {"isoCountry": "IN"},
                    "passengers": [
                        {
                            "nationality": "PK",
                            "frequentFlyerNumber": "GF222",
                            "ffTierLevel": "4",
                            "specialMeal": "HNML",
                            "wheelchairCode": "WCHC",
                        }
                    ],
                    "segments": [{"segmentBookedDate": "2026-02-01"}],
                }
            ],
        },
        {
            "airline": "GF",
            "flightNumber": OTHER_FLIGHT,
            "departureAirport": OTHER_ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T11:00:00Z",
            "reservations": [
                {
                    "numberInParty": 4,
                    "pointOfSale": {"isoCountry": "GB"},
                    "passengers": [
                        {
                            "nationality": "GB",
                            "frequentFlyerNumber": "GF333",
                            "ffTierLevel": "2",
                            "specialMeal": "VGML",
                            "wheelchairCode": "WCHS",
                        }
                    ],
                    "segments": [{"segmentBookedDate": "2026-01-01"}],
                }
            ],
        },
    ])

    db.changes.insert_many([
        {
            "flightNumber": DMM_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "changeType": "CABIN_CHANGE",
        },
        {
            "flightNumber": DMM_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "changeType": "BOARDED",
        },
        {
            "flightNumber": BAH_SAME_ORIGIN_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "changeType": "UPGRADE_CONFIRMED",
        },
        {
            "flightNumber": BAH_SAME_ORIGIN_FLIGHT,
            "origin": DMM_ORIGIN,
            "departureDate": DATE,
            "changeType": "SEAT_CHANGE",
        },
        {
            "flightNumber": OTHER_FLIGHT,
            "origin": OTHER_ORIGIN,
            "departureDate": DATE,
            "changeType": "SEAT_CHANGE",
        },
        {
            "flightNumber": OTHER_FLIGHT,
            "origin": OTHER_ORIGIN,
            "departureDate": DATE,
            "changeType": "PASSENGER_ADDED",
        },
    ])


@pytest.fixture
def db(monkeypatch):
    database = mongomock.MongoClient()["falconeye"]
    seed_two_arrivals(database)
    monkeypatch.setattr(analytics_mod, "get_db", lambda: database)
    return database


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(analytics_mod.router)
    with TestClient(app) as test_client:
        yield test_client


def _network(client, **params):
    query = {"date_from": DATE, "date_to": DATE}
    query.update(params)
    return client.get("/analytics/network", params=query)


def _require_filtered_or_reject(response):
    """400 (refuse destination) is an allowed fix; mixed 200 is not."""
    if response.status_code == 400:
        return None
    assert response.status_code == 200, response.text
    return response.json()


def assert_dmm_only(payload):
    """Every chart/KPI on the analytics page must cover only DMM arrivals."""
    overview = payload["overview"]
    assert overview["totalFlights"] == 1, (
        "Total Flights still includes arrivals other than DMM"
    )
    assert overview["uniqueRoutes"] == 1
    assert overview["uniqueStations"] == 1
    assert overview["daysTracked"] == 1
    assert overview["totalPassengers"] == 2
    assert overview["totalSouls"] == 2
    assert overview["economyPassengers"] == 1
    assert overview["businessPassengers"] == 1
    assert overview["avgPassengersPerFlight"] == 2.0
    assert overview["maxPassengersOnFlight"] == 2

    assert payload["totalChanges"] == 2, (
        "Total Changes still includes non-DMM disruption events"
    )
    change_types = {c["changeType"]: c["count"] for c in payload["changeDistribution"]}
    assert change_types == {"CABIN_CHANGE": 1, "BOARDED": 1}, (
        "Change-type chart still includes other arrivals"
    )

    upgrades = payload["upgradeAnalytics"]
    assert upgrades["cabinChanges"] == 1
    assert upgrades["upgradesConfirmed"] == 0, (
        "Upgrades card still counts UPGRADE_CONFIRMED from a BAH flight"
    )

    assert payload["nationalityDistribution"] == [{"nationality": "IN", "count": 2}], (
        "Nationality chart still includes other arrivals"
    )
    loyalty = {(item["tier"], item["count"]) for item in payload["loyaltyDistribution"]}
    assert ("1", 1) in loyalty
    assert all(item["tier"] != "2" for item in payload["loyaltyDistribution"]), (
        "Loyalty chart still includes Silver from LHR-BAH"
    )
    assert all(item["tier"] != "4" for item in payload["loyaltyDistribution"]), (
        "Loyalty chart still includes Bronze from BOM-BAH"
    )

    assert payload["mealDistribution"] == [{"mealCode": "AVML", "count": 1}], (
        "Special-meals chart still includes other arrivals"
    )
    assert payload["wheelchairDistribution"] == [{"code": "WCHR", "count": 1}], (
        "Wheelchair chart still includes other arrivals"
    )
    assert payload["partySizeDistribution"] == [{"partySize": 2, "count": 1}], (
        "Party-size chart still includes other arrivals"
    )
    assert payload["pointOfSaleDistribution"] == [{"country": "BH", "count": 1}], (
        "Point-of-sale chart still includes other arrivals"
    )

    lead = payload["bookingLeadTime"]
    assert lead["within7d"] == 1
    assert lead["within30d"] == 0
    assert lead["within90d"] == 0
    assert lead["over90d"] == 0, (
        "Booking lead time still includes the LHR-BAH January booking"
    )

    assert payload["dailyTrends"] == [
        {"date": DATE, "flights": 1, "passengers": 2, "changes": 2}
    ], "Daily flight/change series still include other arrivals"

    origins = {row["origin"] for row in payload["routePerformance"]}
    assert origins == {DMM_ORIGIN}
    assert payload["routePerformance"][0]["flightCount"] == 1
    assert payload["routePerformance"][0]["totalPassengers"] == 2

    classes = {(c["cabin"], c["bookingClass"], c["count"])
               for c in payload["bookingClassDistribution"]}
    assert classes == {("Y", "V", 1), ("J", "J", 1)}, (
        "Booking-class chart must not include Q-class from BAH flights"
    )

    rates = payload["operationalRates"]
    assert rates["totalPassengers"] == 2
    assert rates["checkedIn"] == 1
    assert rates["boarded"] == 1
    assert rates["checkInRate"] == 50.0


class TestDestinationFilterOnNetworkAnalytics:
    def test_unfiltered_response_includes_every_arrival(self, client):
        """Control: without destination, all three flights are in scope."""
        response = client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["overview"]["totalFlights"] == 3
        assert payload["overview"]["totalPassengers"] == 17
        assert payload["totalChanges"] == 6

    def test_destination_query_scopes_passenger_and_non_passenger_metrics(
        self, client
    ):
        """Attack path: GET /analytics/network?destination=DMM.

        The page shows a Destination chip and treats the whole dashboard as
        filtered. Passenger totals already follow DMM; flight counts, daily
        series, upgrades, and reservation charts must follow it too.
        """
        payload = _require_filtered_or_reject(_network(client, destination=DMM_DEST))
        if payload is None:
            return
        assert_dmm_only(payload)

    def test_origin_plus_destination_does_not_keep_same_origin_other_arrival(
        self, client
    ):
        """BOM→DMM and BOM→BAH share origin; destination=DMM must drop BAH."""
        payload = _require_filtered_or_reject(
            _network(client, origin=DMM_ORIGIN, destination=DMM_DEST)
        )
        if payload is None:
            return
        assert_dmm_only(payload)
        assert payload["overview"]["totalFlights"] == 1
        assert payload["upgradeAnalytics"]["upgradesConfirmed"] == 0
        meal_codes = {m["mealCode"] for m in payload["mealDistribution"]}
        assert "HNML" not in meal_codes

    def test_flight_counts_daily_series_and_upgrades_are_destination_scoped(
        self, client
    ):
        """Flight-collection aggregations that currently skip destination."""
        payload = _require_filtered_or_reject(_network(client, destination=DMM_DEST))
        if payload is None:
            return
        assert payload["overview"]["totalFlights"] == 1
        assert payload["dailyTrends"][0]["flights"] == 1
        assert payload["upgradeAnalytics"] == {
            "cabinChanges": 1,
            "upgradesConfirmed": 0,
        }

    def test_change_aggregations_are_destination_scoped(self, client):
        """Change-collection aggregations that currently skip destination."""
        payload = _require_filtered_or_reject(_network(client, destination=DMM_DEST))
        if payload is None:
            return
        assert payload["totalChanges"] == 2
        assert payload["dailyTrends"][0]["changes"] == 2
        assert {c["changeType"] for c in payload["changeDistribution"]} == {
            "CABIN_CHANGE",
            "BOARDED",
        }

    def test_reservation_aggregations_are_destination_scoped(self, client):
        """Reservation-collection aggregations that currently skip destination."""
        payload = _require_filtered_or_reject(_network(client, destination=DMM_DEST))
        if payload is None:
            return
        assert payload["nationalityDistribution"] == [{"nationality": "IN", "count": 2}]
        assert payload["mealDistribution"] == [{"mealCode": "AVML", "count": 1}]
        assert payload["wheelchairDistribution"] == [{"code": "WCHR", "count": 1}]
        assert payload["partySizeDistribution"] == [{"partySize": 2, "count": 1}]
        assert payload["pointOfSaleDistribution"] == [{"country": "BH", "count": 1}]
        assert payload["bookingLeadTime"]["over90d"] == 0
        assert payload["bookingLeadTime"]["within90d"] == 0
        assert payload["bookingLeadTime"]["within7d"] == 1
        tiers = {item["tier"] for item in payload["loyaltyDistribution"] if item["count"]}
        assert "2" not in tiers and "4" not in tiers

    def test_direct_run_matches_http_destination_contract(self, db):
        """Same spec through _run_network_analytics (no HTTP layer)."""
        payload = analytics_mod._run_network_analytics(
            DATE, DATE, destination=DMM_DEST
        )
        assert_dmm_only(payload)
