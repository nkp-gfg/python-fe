"""Business booking classes must survive network analytics when economy is larger.

The analytics page requests ``GET /analytics/network`` with no cabin filter
(``selectedCabin`` starts empty) and then builds the business chart from rows
whose cabin is ``J``, keeping at most five:

    bookingClassDistribution.filter((c) => c.cabin === "J").slice(0, 5)

An empty slice is rendered as "No business class data". Cabin mix is a
different number: ``overview.businessPassengers`` is the sum of every
``cabinSummary`` row with cabin ``J`` on the latest passenger list per flight.

``_run_network_analytics`` sorts booking classes from every cabin together and
keeps twenty rows. These tests fail while that shared cap can push stored
business classes out of the rows the page charts.

They pass once each cabin is limited on its own, or the full class distribution
is returned so the page can cap economy and business after the split.
"""

import os
import sys

# Local mongod only. Do not inherit a developer URI from the environment.
os.environ["MONGODB_URI"] = os.environ.get(
    "FALCONEYE_TEST_MONGODB_URI", "mongodb://127.0.0.1:27017"
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi.testclient import TestClient

from backend.api.database import get_db
from backend.api.main import app

MARKER = "booking-class-cap"
CROWDED_DATE = "2099-11-21"
PARTIAL_DATE = "2099-12-01"
EMPTY_DATE = "2099-12-15"
OUT_OF_RANGE_DATE = "2098-01-01"

# Twenty-one economy classes, every one strictly larger than every business
# class once the two latest snapshots are summed. One code ("C") is also used
# in cabin J so a cabin-blind group would merge the counts.
ECONOMY_CLASSES = ["C"] + [f"E{i:02d}" for i in range(1, 21)]
FLIGHT_A_ECONOMY_COUNT = 22
FLIGHT_B_ECONOMY_COUNT = 10
# Latest flight 1001 contributes J=1, C=1. Latest flight 1002 contributes
# J=2, C=1, D=1. Combined business counts are therefore J=3, C=2, D=1.
FLIGHT_A_BUSINESS = {"J": 1, "C": 1}
FLIGHT_B_BUSINESS = {"J": 2, "C": 1, "D": 1}
COMBINED_BUSINESS = {"J": 3, "C": 2, "D": 1}


def _passengers(counts_by_cabin):
    passengers = []
    serial = 0
    for cabin, class_counts in counts_by_cabin:
        for booking_class, count in class_counts.items():
            for _ in range(count):
                serial += 1
                passengers.append(
                    {
                        "passengerId": str(serial),
                        "cabin": cabin,
                        "bookingClass": booking_class,
                    }
                )
    return passengers


def _cabin_summary(passengers):
    totals = {}
    for passenger in passengers:
        totals[passenger["cabin"]] = totals.get(passenger["cabin"], 0) + 1
    return [
        {
            "cabin": cabin,
            "count": count,
            "authorized": count + 5,
            "available": 5,
            "destination": "",
        }
        for cabin, count in totals.items()
    ]


def _passenger_list(
    flight_number,
    origin,
    destination,
    departure_date,
    fetched_at,
    passengers,
):
    return {
        "_test": MARKER,
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "destination": destination,
        "departureDate": departure_date,
        "fetchedAt": fetched_at,
        "passengers": passengers,
        "cabinSummary": _cabin_summary(passengers),
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": len(passengers),
    }


def _economy_counts(per_class):
    return {booking_class: per_class for booking_class in ECONOMY_CLASSES}


def _crowded_documents():
    """Two latest lists whose economy classes fill a shared top-twenty."""
    latest_a = _passengers(
        [("Y", _economy_counts(FLIGHT_A_ECONOMY_COUNT)), ("J", FLIGHT_A_BUSINESS)]
    )
    # Older snapshot for the same flight. Lexicographic fetchedAt order puts
    # this document first in an ascending sort and last in the pipeline's
    # descending sort, so only a latest-snapshot read may ignore class Z.
    stale_a = _passengers([("J", {"Z": 500})])
    latest_b = _passengers(
        [("Y", _economy_counts(FLIGHT_B_ECONOMY_COUNT)), ("J", FLIGHT_B_BUSINESS)]
    )
    outside = _passengers([("J", {"Q": 40})])
    return [
        _passenger_list(
            "1001", "BAH", "DOH", CROWDED_DATE, "2099-11-21T12:00:00Z", latest_a
        ),
        _passenger_list(
            "1001", "BAH", "DOH", CROWDED_DATE, "2099-11-21T01:00:00Z", stale_a
        ),
        _passenger_list(
            "1002", "BAH", "DOH", CROWDED_DATE, "2099-11-21T11:00:00Z", latest_b
        ),
        _passenger_list(
            "1999", "BAH", "DOH", OUT_OF_RANGE_DATE, "2098-01-01T00:00:00Z", outside
        ),
    ]


def _partial_documents():
    """Eighteen large economy classes leave room for only two business classes."""
    economy = {f"P{i:02d}": 50 - i for i in range(18)}  # 50 down to 33
    business = {"J": 4, "C": 3, "D": 2, "I": 1}
    latest = _passengers([("Y", economy), ("J", business)])
    stale = _passengers([("J", {"Z": 500})])
    return [
        _passenger_list(
            "3001", "BAH", "DOH", PARTIAL_DATE, "2099-12-01T12:00:00Z", latest
        ),
        _passenger_list(
            "3001", "BAH", "DOH", PARTIAL_DATE, "2099-12-01T01:00:00Z", stale
        ),
    ]


def _economy_only_documents():
    passengers = _passengers([("Y", {"Y": 4, "B": 3})])
    return [
        _passenger_list(
            "4001", "BAH", "DOH", EMPTY_DATE, "2099-12-15T12:00:00Z", passengers
        )
    ]


def business_rows_shown_on_page(booking_class_distribution):
    """Rows the analytics page plots for business booking classes.

    Mirrors ``classChart`` in ``frontend/app/analytics/page.tsx``: keep cabin J
    in API order, then take at most five. The section renders "No business
    class data" when this list is empty.
    """
    return [row for row in booking_class_distribution if row["cabin"] == "J"][:5]


def economy_rows_shown_on_page(booking_class_distribution):
    """Economy bars from the same ``classChart`` memo, capped at twelve."""
    return [row for row in booking_class_distribution if row["cabin"] == "Y"][:12]


def _classes(rows):
    return {row["bookingClass"]: row["count"] for row in rows}


def assert_chart_matches_stored_business(payload, expected_business):
    shown = business_rows_shown_on_page(payload["bookingClassDistribution"])
    assert payload["overview"]["businessPassengers"] == sum(expected_business.values())
    assert _classes(shown) == expected_business
    assert all(row["cabin"] == "J" for row in shown)
    stale_classes = {
        row["bookingClass"]
        for row in payload["bookingClassDistribution"]
        if row["bookingClass"] in {"Z", "Q"}
    }
    assert stale_classes == set()


@pytest.fixture(scope="module")
def client():
    documents = (
        _crowded_documents() + _partial_documents() + _economy_only_documents()
    )
    with TestClient(app) as test_client:
        db = get_db()
        db.passenger_list.delete_many({"_test": MARKER})
        db.passenger_list.insert_many(documents)
        try:
            yield test_client
        finally:
            db.passenger_list.delete_many({"_test": MARKER})


def _get(client, date_from, date_to, **filters):
    params = {"date_from": date_from, "date_to": date_to}
    params.update({key: value for key, value in filters.items() if value is not None})
    # No Authorization header: the page calls this route without an account.
    response = client.get("/analytics/network", params=params, headers={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "bookingClassDistribution" in body
    assert "overview" in body
    return body


def test_unfiltered_network_keeps_business_classes_when_economy_fills_shared_cap(client):
    payload = _get(client, CROWDED_DATE, CROWDED_DATE)

    economy = economy_rows_shown_on_page(payload["bookingClassDistribution"])
    assert len(economy) == 12
    assert all(row["count"] == FLIGHT_A_ECONOMY_COUNT + FLIGHT_B_ECONOMY_COUNT for row in economy)

    assert_chart_matches_stored_business(payload, COMBINED_BUSINESS)


def test_origin_filter_keeps_business_classes(client):
    payload = _get(client, CROWDED_DATE, CROWDED_DATE, origin="BAH")
    assert_chart_matches_stored_business(payload, COMBINED_BUSINESS)


def test_destination_filter_keeps_business_classes(client):
    payload = _get(client, CROWDED_DATE, CROWDED_DATE, destination="DOH")
    assert_chart_matches_stored_business(payload, COMBINED_BUSINESS)


def test_flight_filter_keeps_business_classes_on_each_flight(client):
    flight_a = _get(client, CROWDED_DATE, CROWDED_DATE, flight_number="1001")
    assert_chart_matches_stored_business(flight_a, FLIGHT_A_BUSINESS)

    flight_b = _get(client, CROWDED_DATE, CROWDED_DATE, flight_number="1002")
    assert_chart_matches_stored_business(flight_b, FLIGHT_B_BUSINESS)


def test_lower_volume_business_classes_are_not_cut_by_larger_economy_classes(client):
    payload = _get(client, PARTIAL_DATE, PARTIAL_DATE)
    assert_chart_matches_stored_business(
        payload, {"J": 4, "C": 3, "D": 2, "I": 1}
    )
    economy = economy_rows_shown_on_page(payload["bookingClassDistribution"])
    assert [row["count"] for row in economy] == list(range(50, 38, -1))


def test_explicit_business_cabin_filter_returns_stored_classes(client):
    payload = _get(client, CROWDED_DATE, CROWDED_DATE, cabin="J")
    assert_chart_matches_stored_business(payload, COMBINED_BUSINESS)


def test_date_without_business_passengers_stays_empty(client):
    payload = _get(client, EMPTY_DATE, EMPTY_DATE)
    assert payload["overview"]["businessPassengers"] == 0
    assert business_rows_shown_on_page(payload["bookingClassDistribution"]) == []
    assert _classes(economy_rows_shown_on_page(payload["bookingClassDistribution"])) == {
        "Y": 4,
        "B": 3,
    }
