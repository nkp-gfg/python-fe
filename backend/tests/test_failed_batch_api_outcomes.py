"""Per-flight Sabre outcomes survive an all-failed batch job.

The feeder records flight-status, passenger-list, and reservation results
for every flight, including a flight whose other calls succeeded. When every
flight has success=false, set_ingestion_job_finished still stores that array
and puts only the first flight's first API error on the job. These tests
drive that path. The UI tests in frontend/tests/failed-batch-results.test.tsx
require those stored outcomes to stay visible.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api import redis_client
from backend.feeder import storage
from backend.feeder.runner import SabreError, run_feeder

PASSENGER_LIST_FAILURE = "All 4 passenger list calls failed"
RESERVATION_FAILURE = "RESERVATIONS-2007-rejected"
PASSENGER_CALL_FAILURE = "PAXLIST-2006-call-rejected"

FLIGHT_2006 = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "BAH",
    "departureDate": "2026-03-19",
    "departureDateTime": "2026-03-19T08:00:00",
    "flightSequenceNumber": 101,
}
FLIGHT_2007 = {
    "airline": "GF",
    "flightNumber": "2007",
    "origin": "LHR",
    "departureDate": "2026-03-19",
    "departureDateTime": "2026-03-19T09:00:00",
    "flightSequenceNumber": 102,
}


class MemoryRedis:
    """In-memory stand-in for the Redis methods redis_client actually calls."""

    def __init__(self):
        self.store = {}
        self.published = []

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


class ScriptedSabre:
    """Returns the (body, xml, meta) tuple SabreClient methods return, or SabreError.

    Built from backend/sabre/client.py get_flight_status, get_passenger_list,
    get_reservations, get_trip_report, and verify_flight_details. Session
    open and close are not needed because run_feeder only uses the context
    manager to obtain the client.
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _ok(self, body):
        meta = {
            "requestXml": "<Request/>",
            "httpStatus": 200,
            "durationMs": 5,
            "sessionToken": None,
            "conversationId": None,
        }
        return body, "<Response/>", meta

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        return self._ok({})

    def get_passenger_list(self, airline, flight_number, departure_date, origin, display_codes=None):
        if flight_number == "2006":
            raise SabreError(PASSENGER_CALL_FAILURE)
        return self._ok({"PassengerInfoList": {"PassengerInfo": []}})

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        if flight_number == "2007":
            raise SabreError(RESERVATION_FAILURE)
        return self._ok({})

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        raise SabreError("trip-report-rejected")

    def verify_flight_details(self, airline, flight_number, departure_date, origin, destination=""):
        raise SabreError("schedule-rejected")


def _install_memory_storage():
    """Replace Mongo writes with the return values _process_api_call reads."""
    originals = {}

    def store_raw_request(*args, **kwargs):
        return "request-1"

    def store_snapshot(*args, **kwargs):
        return "snapshot-1", "checksum-1", False

    def get_previous_snapshot(*args, **kwargs):
        return None

    def update_flight_state(*args, **kwargs):
        return None

    def store_doc(doc):
        return "inserted-1"

    replacements = {
        "store_raw_request": store_raw_request,
        "store_snapshot": store_snapshot,
        "get_previous_snapshot": get_previous_snapshot,
        "update_flight_state": update_flight_state,
        "store_flight_status": store_doc,
        "store_passenger_list": store_doc,
        "store_reservations": store_doc,
        "store_trip_reports": store_doc,
        "store_flight_schedule": store_doc,
    }
    for name, fn in replacements.items():
        originals[name] = getattr(storage, name)
        setattr(storage, name, fn)
    return originals


def _restore(module, originals):
    for name, fn in originals.items():
        setattr(module, name, fn)


def _finish_job(flights):
    memory = MemoryRedis()
    original_redis = redis_client.get_redis
    original_client = sabre_client_slot()
    storage_originals = _install_memory_storage()
    redis_client.get_redis = lambda: memory
    # run_feeder looks up SabreClient on its own module.
    import backend.feeder.runner as runner
    runner.SabreClient = ScriptedSabre
    try:
        redis_client.create_job("job-all-failed", len(flights), job_type="ingestion")
        feeder = run_feeder(flights)
        redis_client.set_ingestion_job_finished(
            "job-all-failed",
            feeder["processedFlights"],
            feeder["results"],
        )
        stored = redis_client.get_job("job-all-failed")
        return feeder, stored
    finally:
        redis_client.get_redis = original_redis
        runner.SabreClient = original_client
        _restore(storage, storage_originals)


def sabre_client_slot():
    import backend.feeder.runner as runner
    return runner.SabreClient


def test_all_failed_batch_keeps_each_flights_api_outcomes():
    feeder, job = _finish_job([FLIGHT_2006, FLIGHT_2007])

    assert feeder["processedFlights"] == 2
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == f"All 2 flights failed. First error: {PASSENGER_LIST_FAILURE}"
    assert RESERVATION_FAILURE not in job["error"]
    assert job["results"] is not None and len(job["results"]) == 2

    by_number = {result["flight"]["flightNumber"]: result for result in job["results"]}

    first = by_number["2006"]
    assert first["success"] is False
    assert first["apis"]["flightStatus"]["status"] == "success"
    assert first["apis"]["flightStatus"].get("error") in (None, "")
    assert first["apis"]["passengerList"]["status"] == "error"
    assert first["apis"]["passengerList"]["error"] == PASSENGER_LIST_FAILURE
    assert first["apis"]["reservations"]["status"] == "success"

    second = by_number["2007"]
    assert second["success"] is False
    assert second["apis"]["flightStatus"]["status"] == "success"
    assert second["apis"]["passengerList"]["status"] == "success"
    assert second["apis"]["reservations"]["status"] == "error"
    assert second["apis"]["reservations"]["error"] == RESERVATION_FAILURE

    # The poll route returns this document unchanged.
    assert job["results"] == feeder["results"]


def test_single_failed_flight_keeps_mixed_api_outcomes():
    feeder, job = _finish_job([FLIGHT_2006])

    assert job["status"] == "failed"
    assert job["error"] == PASSENGER_LIST_FAILURE
    result = job["results"][0]
    assert result["success"] is False
    assert result["apis"]["flightStatus"]["status"] == "success"
    assert result["apis"]["passengerList"]["status"] == "error"
    assert result["apis"]["passengerList"]["error"] == PASSENGER_LIST_FAILURE
    assert result["apis"]["reservations"]["status"] == "success"
    assert feeder["results"][0]["apis"]["passengerList"]["calls"]


if __name__ == "__main__":
    test_all_failed_batch_keeps_each_flights_api_outcomes()
    test_single_failed_flight_keeps_mixed_api_outcomes()
    print("PASS: failed batch keeps per-flight Sabre outcomes")
