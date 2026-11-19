"""PNR mismatch reports must compare each sector with its own reservations.

Passenger lists are stored per origin. Reservation snapshots store that airport
on departureAirport. A later snapshot for another departure airport of the same
flight number must not supply the record locators for a different sector.

These tests fail while the lookup keeps the single newest reservation for the
flight number and date. They pass once that lookup is limited to the newest
document for the passenger list's origin.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"

import backend.api.database as database
import backend.feeder.storage as storage

database.DB_NAME = "falconeye_pnr_mismatch_test"
storage.DB_NAME = "falconeye_pnr_mismatch_test"

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.redis_client import create_job, get_job
from backend.feeder.aggregations import find_missing_pnrs
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.feeder.storage import get_db, store_passenger_list, store_reservations
from backend.tasks.comparison import run_pnr_comparison


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_flight_collections():
    db = get_db()
    db["passenger_list"].delete_many({})
    db["reservations"].delete_many({})
    yield
    db["passenger_list"].delete_many({})
    db["reservations"].delete_many({})


def _passenger_list_payload(locators):
    return {
        "PassengerInfoList": {
            "PassengerInfo": [
                {
                    "PNRLocator": {"#text": locator},
                    "Name_Details": {"LastName": "PAX", "FirstName": locator},
                }
                for locator in locators
            ]
        }
    }


def _reservation_payload(locators):
    return {
        "ReservationsList": {
            "@NumberResults": str(len(locators)),
            "Reservations": {
                "Reservation": [{"@Locator": locator} for locator in locators]
            },
        }
    }


def _store_passenger_list(flight_number, origin, departure_date, locators, fetched_at):
    doc = convert_passenger_list(
        _passenger_list_payload(locators),
        "GF",
        flight_number,
        departure_date,
        origin,
    )
    assert doc["origin"] == origin
    assert doc["flightNumber"] == flight_number
    assert [pax["pnr"] for pax in doc["passengers"]] == list(locators)
    doc["fetchedAt"] = fetched_at
    store_passenger_list(doc)


def _store_reservations(flight_number, origin, departure_date, locators, fetched_at):
    doc = convert_reservations(
        _reservation_payload(locators),
        "GF",
        flight_number,
        origin,
        departure_date,
    )
    assert doc["departureAirport"] == origin
    assert doc["flightNumber"] == flight_number
    assert [res["pnr"] for res in doc["reservations"]] == list(locators)
    doc["fetchedAt"] = fetched_at
    store_reservations(doc)


def _rows_by_origin(report):
    return {row["origin"]: row for row in report["flights"]}


def _seed_reconciled_sectors_with_newer_other_origin(departure_date, flight_number):
    """Both sectors match their own bookings. The other origin was fetched later."""
    _store_passenger_list(
        flight_number, "BAH", departure_date, ["AAA111", "BBB222"], f"{departure_date}T08:00:00Z"
    )
    _store_reservations(
        flight_number, "BAH", departure_date, ["AAA111", "BBB222"], f"{departure_date}T08:30:00Z"
    )
    _store_passenger_list(
        flight_number, "DXB", departure_date, ["CCC333"], f"{departure_date}T09:00:00Z"
    )
    _store_reservations(
        flight_number, "DXB", departure_date, ["CCC333"], f"{departure_date}T10:00:00Z"
    )


def _seed_hidden_gap_and_stale_snapshots(departure_date, flight_number):
    """BAH is missing MISS03. A newer DXB snapshot happens to contain MISS03.

    An older BAH passenger list contains GONE02, and an older BAH reservation
    snapshot contains OLD999. Neither stale locator belongs in the report.
    """
    _store_passenger_list(
        flight_number, "BAH", departure_date, ["KEEP01", "GONE02"], f"{departure_date}T07:00:00Z"
    )
    _store_passenger_list(
        flight_number, "BAH", departure_date, ["KEEP01", "MISS03"], f"{departure_date}T08:00:00Z"
    )
    _store_reservations(
        flight_number, "BAH", departure_date, ["KEEP01", "OLD999"], f"{departure_date}T08:30:00Z"
    )
    _store_reservations(
        flight_number, "BAH", departure_date, ["KEEP01"], f"{departure_date}T09:00:00Z"
    )
    _store_passenger_list(
        flight_number,
        "DXB",
        departure_date,
        ["KEEP01", "MISS03", "DXBONL"],
        f"{departure_date}T10:00:00Z",
    )
    _store_reservations(
        flight_number,
        "DXB",
        departure_date,
        ["KEEP01", "MISS03", "DXBONL"],
        f"{departure_date}T11:00:00Z",
    )


def _assert_reconciled_sectors(report):
    assert report["flightsWithMismatches"] == 0
    assert report["totalPaxOnlyPnrs"] == 0
    assert report["totalResOnlyPnrs"] == 0
    assert report["flights"] == []


def _assert_hidden_gap(report, flight_number):
    rows = _rows_by_origin(report)
    assert set(rows) == {"BAH"}
    bah = rows["BAH"]
    assert bah["flightNumber"] == flight_number
    assert set(bah["inPaxNotReservations"]) == {"MISS03"}
    assert set(bah["inReservationsNotPax"]) == set()
    assert bah["totalPaxPnrs"] == 2
    assert bah["totalResPnrs"] == 1
    assert report["totalPaxOnlyPnrs"] == 1
    assert report["totalResOnlyPnrs"] == 0


def test_matched_sector_is_not_reported_missing_against_another_origin():
    departure_date = "2026-06-02"
    _seed_reconciled_sectors_with_newer_other_origin(departure_date, "2058")

    report = find_missing_pnrs(departure_date)

    _assert_reconciled_sectors(report)


def test_each_origin_uses_its_newest_reservation_snapshot():
    departure_date = "2026-06-01"
    flight_number = "2057"
    _seed_hidden_gap_and_stale_snapshots(departure_date, flight_number)

    report = find_missing_pnrs(departure_date)

    assert report["date"] == departure_date
    _assert_hidden_gap(report, flight_number)


def test_same_origin_gap_is_still_reported():
    departure_date = "2026-06-05"
    _store_passenger_list(
        "2060", "BAH", departure_date, ["AAA111", "MISS03"], f"{departure_date}T08:00:00Z"
    )
    _store_reservations(
        "2060", "BAH", departure_date, ["AAA111"], f"{departure_date}T09:00:00Z"
    )

    report = find_missing_pnrs(departure_date)

    assert _rows_by_origin(report).keys() == {"BAH"}
    bah = _rows_by_origin(report)["BAH"]
    assert set(bah["inPaxNotReservations"]) == {"MISS03"}
    assert set(bah["inReservationsNotPax"]) == set()
    assert bah["totalResPnrs"] == 1


def test_http_pnr_mismatches_compares_within_origin(client):
    reconciled_date = "2026-06-03"
    gap_date = "2026-06-07"
    _seed_reconciled_sectors_with_newer_other_origin(reconciled_date, "2058")
    _seed_hidden_gap_and_stale_snapshots(gap_date, "2057")
    _store_passenger_list(
        "2060", "BAH", "2026-06-08", ["AAA111", "MISS03"], "2026-06-08T08:00:00Z"
    )
    _store_reservations(
        "2060", "BAH", "2026-06-08", ["AAA111"], "2026-06-08T09:00:00Z"
    )

    reconciled = client.get("/compare/pnr-mismatches", params={"date": reconciled_date})
    hidden = client.get("/compare/pnr-mismatches", params={"date": gap_date})
    real_gap = client.get("/compare/pnr-mismatches", params={"date": "2026-06-08"})

    assert reconciled.status_code == 200
    assert hidden.status_code == 200
    assert real_gap.status_code == 200
    reconciled_body = reconciled.json()
    hidden_body = hidden.json()
    real_gap_body = real_gap.json()
    assert reconciled_body["date"] == reconciled_date
    assert hidden_body["date"] == gap_date
    _assert_reconciled_sectors(reconciled_body)
    _assert_hidden_gap(hidden_body, "2057")
    assert set(_rows_by_origin(real_gap_body)["BAH"]["inPaxNotReservations"]) == {"MISS03"}


def test_pnr_comparison_task_compares_within_origin():
    departure_date = "2026-06-04"
    _seed_hidden_gap_and_stale_snapshots(departure_date, "2057")
    _seed_reconciled_sectors_with_newer_other_origin("2026-06-09", "2059")
    job_id = "pnr-origin-2057"
    other_job_id = "pnr-origin-2059"
    create_job(job_id, 1, job_type="pnr_comparison")
    create_job(other_job_id, 1, job_type="pnr_comparison")

    result = run_pnr_comparison.run(job_id, departure_date)
    other = run_pnr_comparison.run(other_job_id, "2026-06-09")

    assert result["jobId"] == job_id
    _assert_hidden_gap(result, "2057")
    _assert_reconciled_sectors(other)
    stored = get_job(job_id)
    assert stored["status"] == "completed"
    _assert_hidden_gap(stored["results"], "2057")
    _assert_reconciled_sectors(get_job(other_job_id)["results"])
