"""Overlapping ingests must keep the transition off the previous snapshot.

Two ingest runs of the same flight both go through
``backend.feeder.runner._process_api_call`` (sync ``POST /flights/ingest``
and batch ``POST /flights/ingest/batch``). That function stores a snapshot,
then diffs it. Sequence numbers are chosen by reading the current maximum
and inserting afterwards, and the snapshot index on
(flight, origin, date, type, sequence) is not unique. Change detection then
loads the two highest sequence numbers rather than the predecessor of the
snapshot just written.

These tests force that window: both runs pick a sequence and reach the
snapshot insert before either insert returns, and both runs that still diff
do so only after both inserts are visible. A fix that reads the maximum
again before inserting, without making that update atomic, still assigns
one sequence twice and still fails. A lock that keeps the two runs out of
the snapshot insert at the same time is allowed through: the gate waits
briefly for a peer and then proceeds.

Trip reports and flight schedules do not store snapshots or change records,
so they are not covered.

Requires MongoDB at mongodb://127.0.0.1:27017 (the product's pymongo driver).
The suite fails while the race can drop a real transition. The serial test
records the same gate change when the two runs do not overlap.
"""

import copy
import os
import sys
import threading
import unittest
from unittest.mock import patch

from pymongo import MongoClient
from pymongo.collection import Collection

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import backend.api.database as api_database
from backend.api.routes.changes import (
    get_activity_feed,
    get_boarding_progress,
    get_changes,
)
from backend.feeder import storage
from backend.feeder.runner import _process_api_call

# Local server only. Never follow MONGODB_URI from the environment: that
# value points at a deployment, and these tests write documents.
LOCAL_MONGODB_URI = "mongodb://127.0.0.1:27017"
TEST_DB_NAME = "falconeye_snapshot_race_test"
PEER_WAIT_SECONDS = 2.0

PRIOR_SNAPSHOT_ID = "prior-snapshot"
AIRLINE = "GF"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-11-17"


class _PeerGate:
    """Wait until ``parties`` callers overlap, then release them together.

    A caller that stays alone (the other run is still outside this point,
    for example because a lock serializes the pipeline) continues after
    ``timeout`` instead of blocking forever.
    """

    def __init__(self, parties=2, timeout=PEER_WAIT_SECONDS):
        self.parties = parties
        self.timeout = timeout
        self._lock = threading.Lock()
        self._arrived = 0
        self._all_arrived = threading.Event()

    def arrive(self):
        with self._lock:
            self._arrived += 1
            if self._arrived >= self.parties:
                self._all_arrived.set()
        self._all_arrived.wait(self.timeout)
        return self._all_arrived.is_set()


class _SnapshotInsertGate:
    """Hold overlapping snapshot inserts until each of them has committed.

    The hold starts at ``insert_one``, which is after ``_next_sequence``.
    Both runs therefore keep whatever sequence they already chose, including
    a sequence chosen by a second non-atomic read immediately before insert.
    """

    def __init__(self):
        self._arrived = _PeerGate()
        self._lock = threading.Lock()
        self._finished = 0
        self._both_finished = threading.Event()

    def wrap(self, original):
        gate = self

        def insert_one(collection, document, *args, **kwargs):
            if collection.name != "snapshots":
                return original(collection, document, *args, **kwargs)
            overlapping = gate._arrived.arrive()
            try:
                return original(collection, document, *args, **kwargs)
            finally:
                with gate._lock:
                    gate._finished += 1
                    if gate._finished >= gate._arrived.parties:
                        gate._both_finished.set()
                if overlapping:
                    gate._both_finished.wait(PEER_WAIT_SECONDS)

        return insert_one


def _identity(data):
    return copy.deepcopy(data)


def _meta():
    return {"requestXml": "<RaceTest/>", "httpStatus": 200, "durationMs": 1}


def _flight(flight_number):
    return {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
    }


def _seed_prior_snapshot(db, flight_number, snapshot_type, data):
    db["snapshots"].insert_one({
        "snapshotId": PRIOR_SNAPSHOT_ID,
        "requestId": "prior-request",
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "snapshotType": snapshot_type,
        "capturedAt": "2026-11-17T00:00:00Z",
        "sequenceNumber": 1,
        "checksum": storage._compute_checksum(data),
        "data": copy.deepcopy(data),
    })


def _run_once(snapshot_type, flight_number, new_data, api_type):
    flight = _flight(flight_number)
    return _process_api_call(
        api_type,
        snapshot_type,
        flight,
        {"race": True},
        "<RaceTest/>",
        _meta(),
        _identity,
        (new_data,),
    )


def _run_overlapping(snapshot_type, flight_number, new_data, api_type):
    """Run two pipelines that both insert before either one diffs."""
    insert_gate = _SnapshotInsertGate()
    diff_gate = _PeerGate()
    original_insert = Collection.insert_one
    original_previous = storage.get_previous_snapshot
    results = []
    results_lock = threading.Lock()

    def gated_previous(*args, **kwargs):
        diff_gate.arrive()
        return original_previous(*args, **kwargs)

    def worker():
        try:
            outcome = ("ok", _run_once(
                snapshot_type, flight_number, new_data, api_type))
        except Exception as exc:  # noqa: BLE001 — surface the pipeline error
            outcome = ("error", exc)
        with results_lock:
            results.append(outcome)

    with patch.object(Collection, "insert_one", insert_gate.wrap(original_insert)), \
            patch.object(storage, "get_previous_snapshot", gated_previous):
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            if thread.is_alive():
                raise TimeoutError(
                    "overlapping ingest did not finish; a peer gate waited "
                    "past its timeout and the pipeline did not return"
                )
    return results


def _changes_for(flight_number):
    return list(storage.get_db()["changes"].find(
        {"flightNumber": flight_number},
        {"_id": 0},
    ))


def _new_snapshots(flight_number, snapshot_type):
    return list(storage.get_db()["snapshots"].find(
        {
            "flightNumber": flight_number,
            "snapshotType": snapshot_type,
            "snapshotId": {"$ne": PRIOR_SNAPSHOT_ID},
        },
        {"_id": 0},
    ))


class ConcurrentSnapshotSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = MongoClient(
            LOCAL_MONGODB_URI,
            serverSelectionTimeoutMS=5000,
        )
        cls.client.admin.command("ping")
        cls.db = cls.client[TEST_DB_NAME]
        cls.db.client.drop_database(TEST_DB_NAME)
        storage.init_db(cls.db)
        api_database._client = cls.client
        api_database._db = cls.db
        storage.ensure_indexes()

    @classmethod
    def tearDownClass(cls):
        client = getattr(cls, "client", None)
        if client is not None:
            client.drop_database(TEST_DB_NAME)
            client.close()
        storage._db = None
        api_database._db = None
        api_database._client = None

    def setUp(self):
        for name in (
            "snapshots",
            "changes",
            "flights",
            "sabre_requests",
            "flight_status",
            "passenger_list",
            "reservations",
        ):
            self.db[name].delete_many({})

    def test_serial_flight_status_records_gate_change(self):
        flight_number = "4101"
        old = {"status": "OPEN", "gate": "A4"}
        new = {"status": "OPEN", "gate": "B22"}
        _seed_prior_snapshot(self.db, flight_number, "flight_status", old)

        first = _run_once("flight_status", flight_number, new, "FlightStatus")
        second = _run_once("flight_status", flight_number, new, "FlightStatus")

        self.assertFalse(first["isDuplicate"])
        self.assertGreaterEqual(first["changesStored"], 1)
        self.assertTrue(second["isDuplicate"])
        self.assertEqual(second["changesStored"], 0)
        self._assert_transition(
            flight_number,
            "flight_status",
            expected=[{
                "changeType": "GATE_CHANGE",
                "field": "gate",
                "oldValue": "A4",
                "newValue": "B22",
            }],
        )
        history = get_changes(
            flight_number,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            change_type=None,
            limit=100,
        )
        self.assertTrue(any(
            row["changeType"] == "GATE_CHANGE" for row in history
        ))

    def test_overlapping_flight_status_keeps_gate_transition(self):
        flight_number = "4102"
        old = {"status": "OPEN", "gate": "A4"}
        new = {"status": "OPEN", "gate": "B22"}
        _seed_prior_snapshot(self.db, flight_number, "flight_status", old)

        results = _run_overlapping(
            "flight_status", flight_number, new, "FlightStatus")
        self._assert_both_completed(results)
        self._assert_transition(
            flight_number,
            "flight_status",
            expected=[{
                "changeType": "GATE_CHANGE",
                "field": "gate",
                "oldValue": "A4",
                "newValue": "B22",
            }],
        )
        state = self.db["flights"].find_one(
            {"flightNumber": flight_number}, {"_id": 0})
        self.assertIsNotNone(state)
        self.assertEqual(state["summary"]["gate"], "B22")

        history = get_changes(
            flight_number,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            change_type=None,
            limit=100,
        )
        self.assertTrue(any(
            row["changeType"] == "GATE_CHANGE"
            and row["beforeSnapshotId"] == PRIOR_SNAPSHOT_ID
            and row["oldValue"] == "A4"
            and row["newValue"] == "B22"
            for row in history
        ))
        feed = get_activity_feed(date=DEPARTURE_DATE, limit=50, categories=None)
        self.assertTrue(any(
            event["flightNumber"] == flight_number
            and event["eventType"] == "GATE_CHANGE"
            for event in feed["events"]
        ))

    def test_overlapping_passenger_list_keeps_checkin_boarding_and_cabin(self):
        flight_number = "4103"
        passenger_old = {
            "pnr": "TEST01",
            "lastName": "SMITH",
            "firstName": "JANE",
            "isCheckedIn": False,
            "isBoarded": False,
            "cabin": "Y",
            "bookingClass": "Y",
        }
        passenger_new = {
            "pnr": "TEST01",
            "lastName": "SMITH",
            "firstName": "JANE",
            "isCheckedIn": True,
            "isBoarded": True,
            "cabin": "J",
            "bookingClass": "J",
        }
        old = {"passengers": [passenger_old], "totalPassengers": 1}
        new = {"passengers": [passenger_new], "totalPassengers": 1}
        _seed_prior_snapshot(self.db, flight_number, "passenger_list", old)

        results = _run_overlapping(
            "passenger_list", flight_number, new, "PassengerList")
        self._assert_both_completed(results)
        self._assert_transition(
            flight_number,
            "passenger_list",
            expected=[
                {
                    "changeType": "CHECKED_IN",
                    "field": "isCheckedIn",
                    "oldValue": "False",
                    "newValue": "True",
                },
                {
                    "changeType": "BOARDED",
                    "field": "isBoarded",
                    "oldValue": "False",
                    "newValue": "True",
                },
                {
                    "changeType": "CABIN_CHANGE",
                    "field": "cabin",
                    "oldValue": "Y",
                    "newValue": "J",
                },
            ],
        )
        state = self.db["flights"].find_one(
            {"flightNumber": flight_number}, {"_id": 0})
        self.assertIsNotNone(state)
        self.assertEqual(state["summary"]["checkedIn"], 1)
        self.assertEqual(state["summary"]["boarded"], 1)

        progress = get_boarding_progress(
            flight_number, origin=ORIGIN, date=DEPARTURE_DATE)
        self.assertEqual(progress["checkinProgress"]["current"], 1)
        self.assertEqual(progress["boardingProgress"]["current"], 1)
        feed = get_activity_feed(date=DEPARTURE_DATE, limit=50, categories=None)
        seen = {
            event["eventType"]
            for event in feed["events"]
            if event["flightNumber"] == flight_number
        }
        self.assertTrue({"CHECKED_IN", "BOARDED", "CABIN_CHANGE"} <= seen)

    def test_overlapping_reservations_keeps_party_change(self):
        flight_number = "4104"
        old = {"reservations": [{"pnr": "TEST01", "numberInParty": 1}]}
        new = {"reservations": [{"pnr": "TEST01", "numberInParty": 3}]}
        _seed_prior_snapshot(self.db, flight_number, "reservations", old)

        results = _run_overlapping(
            "reservations", flight_number, new, "Reservations")
        self._assert_both_completed(results)
        self._assert_transition(
            flight_number,
            "reservations",
            expected=[{
                "changeType": "RESERVATION_PARTY_CHANGE",
                "field": "reservation.TEST01.numberInParty",
                "oldValue": "1",
                "newValue": "3",
            }],
        )
        history = get_changes(
            flight_number,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            change_type=None,
            limit=100,
        )
        self.assertTrue(any(
            row["changeType"] == "RESERVATION_PARTY_CHANGE"
            and row["beforeSnapshotId"] == PRIOR_SNAPSHOT_ID
            for row in history
        ))

    def _assert_both_completed(self, results):
        errors = [item[1] for item in results if item[0] == "error"]
        self.assertEqual(
            len(results),
            2,
            f"expected two ingest results, got {results!r}",
        )
        self.assertFalse(
            errors,
            "overlapping ingest failed before both snapshots were stored: "
            f"{errors!r}",
        )

    def _assert_transition(self, flight_number, snapshot_type, expected):
        snapshots = _new_snapshots(flight_number, snapshot_type)
        sequences = [doc["sequenceNumber"] for doc in snapshots]
        stored = _changes_for(flight_number)
        state = storage.get_db()["flights"].find_one(
            {"flightNumber": flight_number}, {"_id": 0, "summary": 1})
        stored_view = [
            {key: doc.get(key) for key in (
                "changeType", "field", "oldValue", "newValue",
                "beforeSnapshotId", "afterSnapshotId",
            )}
            for doc in stored
        ]
        detail = (
            f"sequences={sequences} stored={stored_view} "
            f"snapshots={len(snapshots)} flight_summary={state}"
        )
        self.assertEqual(
            len(snapshots),
            2,
            "both overlapping ingests must store a snapshot; " + detail,
        )
        self.assertEqual(
            len(set(sequences)),
            2,
            "overlapping ingests assigned one sequence twice; " + detail,
        )
        self.assertTrue(
            all(sequence > 1 for sequence in sequences),
            "new sequences must follow the prior snapshot; " + detail,
        )
        new_ids = {doc["snapshotId"] for doc in snapshots}
        for want in expected:
            matches = [
                doc for doc in stored
                if doc.get("changeType") == want["changeType"]
                and doc.get("field") == want["field"]
                and doc.get("oldValue") == want["oldValue"]
                and doc.get("newValue") == want["newValue"]
                and doc.get("beforeSnapshotId") == PRIOR_SNAPSHOT_ID
                and doc.get("afterSnapshotId") in new_ids
            ]
            self.assertTrue(
                matches,
                f"missing {want['changeType']} from the prior snapshot; "
                + detail,
            )


if __name__ == "__main__":
    unittest.main()
