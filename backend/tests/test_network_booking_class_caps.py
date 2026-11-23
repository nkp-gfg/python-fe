"""Network analytics booking-class rows must not be dropped in silence.

The commercial chart is built from ``bookingClassDistribution``. These tests
call the real ``GET /analytics/network`` route, with no credentials, against
the passenger lists stored for a date. They fail while a class that is still
selling is removed from that list without a count of how many rows were
removed.

Two outcomes satisfy the tests:

* every booking class on the latest passenger list for the filter is present
* or the JSON body sets ``bookingClassOmittedCount`` to the number of grouped
  cabin/class rows that were left out, and the rows that remain are the ones
  with the highest passenger counts

An older snapshot for the same flight must not contribute classes.
"""

import os

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27018"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

import backend.api.redis_client as redis_client


class _RedisDown:
    def ping(self):
        raise ConnectionError("redis is not part of this test")


redis_client.get_redis = lambda: _RedisDown()

from fastapi.testclient import TestClient

from backend.api.database import close_db, get_db
from backend.api.main import app

DATE = "2026-11-01"
ORIGIN = "BAH"
FLIGHT = "2057"


def _passengers(spec):
    rows = []
    for cabin, booking_class, count in spec:
        for index in range(count):
            rows.append({
                "cabin": cabin,
                "bookingClass": booking_class,
                "pnr": f"{booking_class}{index}",
            })
    return rows


def _passenger_list(spec, fetched_at):
    passengers = _passengers(spec)
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "destination": "LHR",
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": len(passengers),
        "cabinSummary": [],
        "passengers": passengers,
    }


def _expected_pairs(spec):
    return {(cabin, booking_class) for cabin, booking_class, count in spec if count > 0}


def _seed(spec):
    db = get_db()
    for name in ("flights", "passenger_list", "reservations", "changes"):
        db[name].delete_many({})
    db.flights.insert_one({
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "status": "SKD",
    })
    db.passenger_list.insert_many([
        _passenger_list([("J", "GHOST", 500), ("Y", "GHOST", 500)], "2026-10-01T00:00:00Z"),
        _passenger_list(spec, "2026-11-01T12:00:00Z"),
    ])


def _distribution(body):
    return {
        (row["cabin"], row["bookingClass"]): row["count"]
        for row in body["bookingClassDistribution"]
    }


def _assert_no_silent_omission(body, spec):
    got = _distribution(body)
    expected = _expected_pairs(spec)
    assert "GHOST" not in {booking_class for _, booking_class in got}
    missing = expected - set(got)
    counts = [row["count"] for row in body["bookingClassDistribution"]]
    assert counts == sorted(counts, reverse=True)
    if not missing:
        for cabin, booking_class, count in spec:
            assert got[(cabin, booking_class)] == count
        return
    omitted = body.get("bookingClassOmittedCount")
    assert omitted == len(missing), (
        "booking classes %s are missing from bookingClassDistribution "
        "and bookingClassOmittedCount is %r, not %s"
        % (sorted(missing), omitted, len(missing))
    )
    present_counts = [
        count for cabin, booking_class, count in spec
        if (cabin, booking_class) in got
    ]
    missing_counts = [
        count for cabin, booking_class, count in spec
        if (cabin, booking_class) in missing
    ]
    if present_counts and missing_counts:
        assert min(present_counts) >= max(missing_counts)


def test_unauthenticated_business_filter_keeps_every_returned_class():
    """Six business classes, plus an economy class that the cabin filter excludes."""
    business = [("J", code, count) for code, count in (
        ("J", 60), ("C", 50), ("D", 40), ("Z", 30), ("I", 20), ("R", 11),
    )]
    _seed(business + [("Y", "Y", 200)])
    with TestClient(app) as client:
        response = client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE, "cabin": "J"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Y" not in {row["cabin"] for row in body["bookingClassDistribution"]}
    _assert_no_silent_omission(body, business)


def test_booking_class_cap_is_not_silent():
    """Twenty-one business classes, one past the aggregation limit."""
    business = [("J", f"B{index:02d}", 30 - index) for index in range(21)]
    _seed(business)
    with TestClient(app) as client:
        response = client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE, "cabin": "J"},
            headers={},
        )
    assert response.status_code == 200, response.text
    _assert_no_silent_omission(response.json(), business)


def test_mixed_cabins_are_not_silently_capped():
    """Economy rows with higher counts must not erase a business class with no notice."""
    economy = [("Y", f"Y{index:02d}", 100 - index) for index in range(15)]
    business = [("J", code, count) for code, count in (
        ("J", 20), ("C", 19), ("D", 18), ("Z", 17), ("I", 16), ("R", 15),
    )]
    spec = economy + business
    _seed(spec)
    with TestClient(app) as client:
        response = client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE},
        )
    assert response.status_code == 200, response.text
    _assert_no_silent_omission(response.json(), spec)


def teardown_module():
    close_db()
