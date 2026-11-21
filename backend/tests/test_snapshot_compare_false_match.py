"""Spec tests for false snapshot matches in flight history.

These tests exercise the history compare and restore routes. They fail while
compare treats a snapshot as the same flight when only counters agree, and
while restore copies that snapshot into the live flight collections.

A green match must compare schedule, aircraft, and a stable passenger
identity, and the compare payload must name those fields even when they
agree. Restore must not write a snapshot while any compared field differs
unless the request confirms the value on the document that will be written.
Confirming a different value, or a value from an earlier lookup than the
document inserted, must leave the live flight unchanged.

No Authorization header is sent. These routes do not declare one.

Not covered here:
- frontend/components/dashboard/history-overview.tsx and the snapshot card in
  frontend/components/dashboard/status-history.tsx only render this payload.
  They are not rendered in this file.
- backend/feeder/differ.py records ingestion events. It does not decide this
  compare result or what restore writes.
"""

import copy
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import changes, flights, passengers, reservations
from backend.feeder.differ import _pax_key

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
LATEST_FETCHED_AT = "2026-03-19T12:00:00+00:00"

FLIGHT_STATUS_FIELDS = (
    "status",
    "gate",
    "terminal",
    "aircraft.type",
    "aircraft.registration",
    "schedule.scheduledDeparture",
    "schedule.estimatedDeparture",
)
PASSENGER_FIELDS = (
    "totalPassengers",
    "totalSouls",
    "adultCount",
    "childCount",
    "infantCount",
    "checkedIn",
    "boarded",
    "aircraftType",
    "scheduledDeparture",
    "departureTime",
    "passengerIdentities",
)
RESERVATION_FIELDS = (
    "totalResults",
    "reservationCount",
    "reservationPnrs",
)
COMPARED_FIELDS = {
    "flight_status": FLIGHT_STATUS_FIELDS,
    "passenger_list": PASSENGER_FIELDS,
    "reservations": RESERVATION_FIELDS,
}


def _passenger(pnr, last, first, line, checked_in=True, boarded=False):
    return {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "lineNumber": line,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "cabin": "Y",
        "passengerType": "F" if pnr else "E",
        "isRevenue": bool(pnr),
    }


def _identities(people):
    return sorted(_pax_key(person) for person in people)


def _manifest(first_name="JOHN", crew_name="SAM"):
    """Two travelers with the same headcount, check-in count, and boarded count."""
    return [
        _passenger("ABC123", "SMITH", first_name, 1, checked_in=True, boarded=False),
        _passenger("", "CREW", crew_name, 2, checked_in=True, boarded=True),
    ]


def _passenger_document(people, aircraft="32N", scheduled="2026-03-19T14:00:00",
                        departure_time="14:00:00", fetched_at=LATEST_FETCHED_AT):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "destination": "LHR",
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "aircraftType": aircraft,
        "scheduledDeparture": scheduled,
        "departureTime": departure_time,
        "totalPassengers": len(people),
        "totalSouls": len(people),
        "adultCount": len(people),
        "childCount": 0,
        "infantCount": 0,
        "passengers": people,
    }


def _status_document(aircraft_type="32N", registration="A9C-AB",
                     scheduled="2026-03-19T14:00:00",
                     estimated="2026-03-19T14:10:00",
                     fetched_at=LATEST_FETCHED_AT):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "status": "OPENCI",
        "gate": "A1",
        "terminal": "1",
        "aircraft": {
            "type": aircraft_type,
            "registration": registration,
            "configNumber": "1",
            "seatConfig": "Y168",
        },
        "schedule": {
            "scheduledDeparture": scheduled,
            "estimatedDeparture": estimated,
            "scheduledArrival": "2026-03-19T18:00:00",
            "estimatedArrival": "2026-03-19T18:10:00",
        },
    }


def _reservation_document(pnr="ABC123", fetched_at=LATEST_FETCHED_AT):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "totalResults": 1,
        "reservations": [
            {
                "pnr": pnr,
                "numberInParty": 1,
                "numberOfInfants": 0,
                "passengers": [
                    {"lastName": "SMITH", "firstName": "JOHN", "nameId": "01.01"},
                ],
            }
        ],
    }


def _snapshot(snapshot_type, sequence, data):
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "data": data,
    }


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list=None, direction=None):
        return self

    def limit(self, count):
        return self

    def __iter__(self):
        return iter(self._docs)


def _matches(document, query):
    for key, expected in (query or {}).items():
        value = document.get(key)
        if isinstance(expected, dict):
            if set(expected) != {"$lte"}:
                raise AssertionError(f"unsupported query operator in {expected}")
            if value is None or not value <= expected["$lte"]:
                return False
        elif value != expected:
            return False
    return True


def _sorted_docs(docs, sort):
    ordered = list(docs)
    if not sort:
        return ordered
    # pymongo DESCENDING is -1. Sort the last key first so the first key wins.
    for key, direction in reversed(list(sort)):
        ordered.sort(key=lambda doc, field=key: doc.get(field), reverse=direction == -1)
    return ordered


class MemoryCollection:
    """In-memory stand-in for the collection calls these routes make.

    find_one returns the first document after equality/$lte filtering and the
    sort list the route passes. insert_one stores that document and records
    the call. This follows pymongo Collection.find_one (filter, then find
    kwargs including sort) and insert_one adding an _id when missing.
    """

    def __init__(self, name, docs, insert_log):
        self.name = name
        self.docs = docs
        self.insert_log = insert_log

    def find_one(self, filter=None, projection=None, *args, **kwargs):
        matched = _sorted_docs(
            [doc for doc in self.docs if _matches(doc, filter)],
            kwargs.get("sort"),
        )
        if not matched:
            return None
        return copy.deepcopy(matched[0])

    def find(self, filter=None, projection=None, *args, **kwargs):
        matched = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, filter)]
        return _Cursor(matched)

    def insert_one(self, document, *args, **kwargs):
        if "_id" not in document:
            document["_id"] = f"{self.name}-{len(self.docs) + 1}"
        stored = copy.deepcopy(document)
        self.docs.append(stored)
        self.insert_log.append((self.name, stored))
        return stored


class SnapshotCollection(MemoryCollection):
    """Snapshots collection that can return a different as-of passenger list on a later read."""

    def __init__(self, docs, insert_log, swapped_docs):
        super().__init__("snapshots", docs, insert_log)
        self.swapped_docs = swapped_docs
        self.swap_on_second_as_of = False
        self.as_of_passenger_reads = 0

    def find_one(self, filter=None, projection=None, *args, **kwargs):
        sequence = (filter or {}).get("sequenceNumber")
        as_of_passenger = (
            (filter or {}).get("snapshotType") == "passenger_list"
            and isinstance(sequence, dict)
            and "$lte" in sequence
        )
        if as_of_passenger and self.swap_on_second_as_of:
            self.as_of_passenger_reads += 1
            if self.as_of_passenger_reads >= 2:
                matched = _sorted_docs(
                    [doc for doc in self.swapped_docs if _matches(doc, filter)],
                    kwargs.get("sort"),
                )
                if not matched:
                    return None
                return copy.deepcopy(matched[0])
        return super().find_one(filter, projection, *args, **kwargs)


class MemoryDB:
    def __init__(self, snapshots, live, swapped_snapshots=None):
        self.inserts = []
        self.snapshots = SnapshotCollection(
            copy.deepcopy(snapshots),
            self.inserts,
            copy.deepcopy(swapped_snapshots or []),
        )
        self._collections = {
            "snapshots": self.snapshots,
        }
        for name, docs in live.items():
            self._collections[name] = MemoryCollection(
                name, copy.deepcopy(docs), self.inserts)

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection(name, [], self.inserts)
        return self._collections[name]


def _case(old_passengers, new_passengers, old_status, new_status, old_reservations,
          new_reservations, swapped_passengers=None):
    snapshots = [
        _snapshot("passenger_list", 1, old_passengers),
        _snapshot("passenger_list", 2, new_passengers),
        _snapshot("flight_status", 1, old_status),
        _snapshot("flight_status", 2, new_status),
        _snapshot("reservations", 1, old_reservations),
        _snapshot("reservations", 2, new_reservations),
    ]
    live = {
        "passenger_list": [new_passengers],
        "flight_status": [new_status],
        "reservations": [new_reservations],
    }
    swapped = None
    if swapped_passengers is not None:
        swapped = [_snapshot("passenger_list", 1, swapped_passengers)]
    return MemoryDB(snapshots, live, swapped)


def _same_flight():
    people = _manifest()
    status = _status_document()
    reservations = _reservation_document()
    return _case(
        _passenger_document(people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )


@contextmanager
def _client(db):
    app = FastAPI()
    app.include_router(changes.router)
    app.include_router(flights.router)
    app.include_router(passengers.router)
    app.include_router(reservations.router)
    flights._dashboard_cache.clear()
    with (
        patch("backend.api.routes.changes.get_db", return_value=db),
        patch("backend.api.routes.flights.get_db", return_value=db),
        patch("backend.api.routes.passengers.get_db", return_value=db),
        patch("backend.api.routes.reservations.get_db", return_value=db),
    ):
        with TestClient(app) as client:
            yield client


def _compare(client, sequence=1):
    response = client.get(
        f"/flights/{FLIGHT}/snapshots/compare",
        params={"snapshot_sequence": sequence, "origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _restore(client, confirm=None, sequence=1):
    kwargs = {}
    if confirm is not None:
        kwargs["json"] = {"confirm": confirm}
    return client.post(
        f"/flights/{FLIGHT}/snapshots/{sequence}/restore",
        params={"origin": ORIGIN, "date": DATE},
        **kwargs,
    )


def _board(client):
    flights._dashboard_cache.clear()
    params = {"origin": ORIGIN, "date": DATE}
    passenger_response = client.get(f"/flights/{FLIGHT}/passengers", params=params)
    status_response = client.get(f"/flights/{FLIGHT}/status", params=params)
    reservation_response = client.get(f"/flights/{FLIGHT}/reservations", params=params)
    dashboard_response = client.get(f"/flights/{FLIGHT}/dashboard", params=params)
    assert passenger_response.status_code == 200, passenger_response.text
    assert status_response.status_code == 200, status_response.text
    assert reservation_response.status_code == 200, reservation_response.text
    assert dashboard_response.status_code == 200, dashboard_response.text
    return {
        "passengers": passenger_response.json(),
        "status": status_response.json(),
        "reservations": reservation_response.json(),
        "dashboard": dashboard_response.json(),
    }


def _assert_fields_present(body):
    for snapshot_type, fields in COMPARED_FIELDS.items():
        result = body["types"][snapshot_type]
        assert result["available"] is True
        assert result["selectedSequence"] == 1
        assert result["latestSequence"] == 2
        for field in fields:
            assert field in result["deltas"], (snapshot_type, field)


def _changed_names(body):
    names = []
    for snapshot_type, result in body["types"].items():
        if not result.get("available"):
            continue
        for field, delta in result["deltas"].items():
            if delta["changed"]:
                names.append(f"{snapshot_type}.{field}")
    return sorted(names)


def _confirm_from_compare(body):
    confirmed = {}
    for snapshot_type, result in body["types"].items():
        for field, delta in result.get("deltas", {}).items():
            if delta["changed"]:
                confirmed[f"{snapshot_type}.{field}"] = delta["selected"]
    return confirmed


def _assert_live_unchanged(board, people, status, reservations):
    assert _identities(board["passengers"]["passengers"]) == _identities(people)
    assert board["passengers"]["aircraftType"] == "32N"
    assert board["passengers"]["scheduledDeparture"] == "2026-03-19T14:00:00"
    assert board["passengers"]["departureTime"] == "14:00:00"
    assert board["status"]["aircraft"]["type"] == status["aircraft"]["type"]
    assert board["status"]["aircraft"]["registration"] == status["aircraft"]["registration"]
    assert board["status"]["schedule"]["scheduledDeparture"] == status["schedule"]["scheduledDeparture"]
    assert board["status"]["schedule"]["estimatedDeparture"] == status["schedule"]["estimatedDeparture"]
    assert board["dashboard"]["flightStatus"]["aircraft"]["type"] == status["aircraft"]["type"]
    assert board["dashboard"]["flightStatus"]["schedule"]["scheduledDeparture"] == status["schedule"]["scheduledDeparture"]
    assert [item["pnr"] for item in board["reservations"]["reservations"]] == [
        item["pnr"] for item in reservations["reservations"]
    ]


def _assert_refused(db, response, people, status, reservations, client):
    board = _board(client)
    _assert_live_unchanged(board, people, status, reservations)
    assert db.inserts == []
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["differingFields"]


def test_compare_names_every_field_when_the_snapshots_agree():
    db = _same_flight()
    with _client(db) as client:
        body = _compare(client)
    _assert_fields_present(body)
    assert body["types"]["flight_status"]["changed"] is False
    assert body["types"]["passenger_list"]["changed"] is False
    assert body["types"]["reservations"]["changed"] is False
    assert _changed_names(body) == []


def test_identical_snapshot_can_be_restored_without_confirmation():
    people = _manifest()
    status = _status_document()
    reservations = _reservation_document()
    db = _same_flight()
    with _client(db) as client:
        response = _restore(client)
        assert response.status_code == 200, response.text
        board = _board(client)
    _assert_live_unchanged(board, people, status, reservations)


def test_compare_reports_passenger_identity_when_only_the_name_differs():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JANE", "PAT")
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        _passenger_document(older_people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(latest_people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        body = _compare(client)
    _assert_fields_present(body)
    identity = body["types"]["passenger_list"]["deltas"]["passengerIdentities"]
    assert identity["selected"] == _identities(older_people)
    assert identity["latest"] == _identities(latest_people)
    assert identity["changed"] is True
    assert body["types"]["passenger_list"]["changed"] is True
    assert body["types"]["flight_status"]["changed"] is False
    assert body["types"]["reservations"]["changed"] is False
    for field in ("totalPassengers", "checkedIn", "boarded"):
        assert body["types"]["passenger_list"]["deltas"][field]["changed"] is False


def test_restore_without_confirmation_does_not_publish_different_names():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JANE", "PAT")
    status = _status_document()
    reservations = _reservation_document()
    older = _passenger_document(older_people, fetched_at="2026-03-19T08:00:00+00:00")
    latest = _passenger_document(latest_people)
    db = _case(
        older,
        latest,
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        response = _restore(client)
        _assert_refused(db, response, latest_people, status, reservations, client)
        assert "passenger_list.passengerIdentities" in response.json()["detail"]["differingFields"]


def test_compare_reports_passenger_list_schedule_and_aircraft():
    people = _manifest()
    latest = _passenger_document(people)
    older = _passenger_document(
        people,
        aircraft="321",
        scheduled="2026-03-19T16:30:00",
        departure_time="16:30:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        older,
        latest,
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        body = _compare(client)
    _assert_fields_present(body)
    deltas = body["types"]["passenger_list"]["deltas"]
    assert deltas["aircraftType"]["selected"] == "321"
    assert deltas["aircraftType"]["latest"] == "32N"
    assert deltas["aircraftType"]["changed"] is True
    assert deltas["scheduledDeparture"]["changed"] is True
    assert deltas["departureTime"]["changed"] is True
    assert deltas["passengerIdentities"]["changed"] is False
    assert body["types"]["passenger_list"]["changed"] is True
    assert body["types"]["flight_status"]["changed"] is False


def test_restore_without_confirmation_does_not_publish_passenger_list_schedule():
    people = _manifest()
    latest = _passenger_document(people)
    older = _passenger_document(
        people,
        aircraft="321",
        scheduled="2026-03-19T16:30:00",
        departure_time="16:30:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        older,
        latest,
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        response = _restore(client)
        board = _board(client)
        assert board["passengers"]["aircraftType"] == "32N"
        assert board["passengers"]["scheduledDeparture"] == "2026-03-19T14:00:00"
        assert board["passengers"]["departureTime"] == "14:00:00"
        assert db.inserts == []
        assert response.status_code == 409, response.text


def test_compare_reports_flight_status_aircraft_and_departure_times():
    people = _manifest()
    latest_status = _status_document()
    older_status = _status_document(
        aircraft_type="321",
        registration="A9C-ZZ",
        scheduled="2026-03-19T16:30:00",
        estimated="2026-03-19T16:45:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    reservations = _reservation_document()
    db = _case(
        _passenger_document(people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(people),
        older_status,
        latest_status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        body = _compare(client)
    _assert_fields_present(body)
    deltas = body["types"]["flight_status"]["deltas"]
    assert deltas["status"]["changed"] is False
    assert deltas["gate"]["changed"] is False
    assert deltas["terminal"]["changed"] is False
    assert deltas["aircraft.type"] == {
        "selected": "321",
        "latest": "32N",
        "diff": None,
        "changed": True,
    }
    assert deltas["aircraft.registration"]["changed"] is True
    assert deltas["schedule.scheduledDeparture"]["selected"] == "2026-03-19T16:30:00"
    assert deltas["schedule.scheduledDeparture"]["latest"] == "2026-03-19T14:00:00"
    assert deltas["schedule.scheduledDeparture"]["changed"] is True
    assert deltas["schedule.estimatedDeparture"]["changed"] is True
    assert body["types"]["flight_status"]["changed"] is True
    assert body["types"]["passenger_list"]["changed"] is False
    assert body["types"]["reservations"]["changed"] is False


def test_restore_without_confirmation_does_not_publish_departure_or_aircraft():
    people = _manifest()
    latest_status = _status_document()
    older_status = _status_document(
        aircraft_type="321",
        registration="A9C-ZZ",
        scheduled="2026-03-19T16:30:00",
        estimated="2026-03-19T16:45:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    reservations = _reservation_document()
    db = _case(
        _passenger_document(people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(people),
        older_status,
        latest_status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        response = _restore(client)
        _assert_refused(db, response, people, latest_status, reservations, client)
        differing = response.json()["detail"]["differingFields"]
        assert "flight_status.aircraft.type" in differing
        assert "flight_status.schedule.scheduledDeparture" in differing
        assert "flight_status.schedule.estimatedDeparture" in differing


def test_compare_reports_reservation_identity_when_only_the_pnr_differs():
    people = _manifest()
    status = _status_document()
    latest_reservations = _reservation_document("ABC123")
    older_reservations = _reservation_document("ZZZ999", fetched_at="2026-03-19T08:00:00+00:00")
    db = _case(
        _passenger_document(people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        older_reservations,
        latest_reservations,
    )
    with _client(db) as client:
        body = _compare(client)
    _assert_fields_present(body)
    pnrs = body["types"]["reservations"]["deltas"]["reservationPnrs"]
    assert pnrs["selected"] == ["ZZZ999"]
    assert pnrs["latest"] == ["ABC123"]
    assert pnrs["changed"] is True
    assert body["types"]["reservations"]["deltas"]["totalResults"]["changed"] is False
    assert body["types"]["reservations"]["deltas"]["reservationCount"]["changed"] is False
    assert body["types"]["reservations"]["changed"] is True
    assert body["types"]["passenger_list"]["changed"] is False
    assert body["types"]["flight_status"]["changed"] is False


def test_restore_without_confirmation_does_not_publish_a_different_pnr():
    people = _manifest()
    status = _status_document()
    latest_reservations = _reservation_document("ABC123")
    older_reservations = _reservation_document("ZZZ999", fetched_at="2026-03-19T08:00:00+00:00")
    db = _case(
        _passenger_document(people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        older_reservations,
        latest_reservations,
    )
    with _client(db) as client:
        response = _restore(client)
        _assert_refused(db, response, people, status, latest_reservations, client)
        assert "reservations.reservationPnrs" in response.json()["detail"]["differingFields"]


def test_count_difference_is_still_reported():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JOHN", "SAM")
    older_people[0]["isCheckedIn"] = False
    older_people[0]["isBoarded"] = False
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        _passenger_document(older_people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(latest_people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        body = _compare(client)
    assert body["types"]["passenger_list"]["deltas"]["checkedIn"]["changed"] is True
    assert body["types"]["passenger_list"]["changed"] is True


def test_confirming_the_latest_value_does_not_restore_the_older_manifest():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JANE", "PAT")
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        _passenger_document(older_people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(latest_people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        response = _restore(client, confirm={
            "passenger_list.passengerIdentities": _identities(latest_people),
        })
        _assert_refused(db, response, latest_people, status, reservations, client)


def test_partial_confirmation_does_not_restore():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JANE", "PAT")
    latest_status = _status_document()
    older_status = _status_document(
        aircraft_type="321",
        registration="A9C-ZZ",
        scheduled="2026-03-19T16:30:00",
        estimated="2026-03-19T16:45:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    reservations = _reservation_document()
    db = _case(
        _passenger_document(older_people, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(latest_people),
        older_status,
        latest_status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
    )
    with _client(db) as client:
        response = _restore(client, confirm={
            "passenger_list.passengerIdentities": _identities(older_people),
        })
        _assert_refused(db, response, latest_people, latest_status, reservations, client)


def test_confirmed_restore_publishes_the_confirmed_snapshot_on_the_board():
    latest_people = _manifest("JOHN", "SAM")
    older_people = _manifest("JANE", "PAT")
    latest_status = _status_document()
    older_status = _status_document(
        aircraft_type="321",
        registration="A9C-ZZ",
        scheduled="2026-03-19T16:30:00",
        estimated="2026-03-19T16:45:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    latest_reservations = _reservation_document("ABC123")
    older_reservations = _reservation_document("ZZZ999", fetched_at="2026-03-19T08:00:00+00:00")
    older_passengers = _passenger_document(
        older_people,
        aircraft="321",
        scheduled="2026-03-19T16:30:00",
        departure_time="16:30:00",
        fetched_at="2026-03-19T08:00:00+00:00",
    )
    db = _case(
        older_passengers,
        _passenger_document(latest_people),
        older_status,
        latest_status,
        older_reservations,
        latest_reservations,
    )
    with _client(db) as client:
        preview = _compare(client)
        response = _restore(client, confirm=_confirm_from_compare(preview))
        assert response.status_code == 200, response.text
        board = _board(client)
    assert _identities(board["passengers"]["passengers"]) == _identities(older_people)
    assert board["passengers"]["aircraftType"] == "321"
    assert board["passengers"]["scheduledDeparture"] == "2026-03-19T16:30:00"
    assert board["status"]["aircraft"]["type"] == "321"
    assert board["status"]["aircraft"]["registration"] == "A9C-ZZ"
    assert board["status"]["schedule"]["scheduledDeparture"] == "2026-03-19T16:30:00"
    assert board["status"]["schedule"]["estimatedDeparture"] == "2026-03-19T16:45:00"
    assert board["dashboard"]["flightStatus"]["aircraft"]["type"] == "321"
    assert board["dashboard"]["flightStatus"]["schedule"]["scheduledDeparture"] == "2026-03-19T16:30:00"
    assert [item["pnr"] for item in board["reservations"]["reservations"]] == ["ZZZ999"]


def test_restore_does_not_publish_a_later_lookup_than_the_one_confirmed():
    latest_people = _manifest("JOHN", "SAM")
    first_read = _manifest("JANE", "PAT")
    second_read = _manifest("CAROL", "RORY")
    status = _status_document()
    reservations = _reservation_document()
    db = _case(
        _passenger_document(first_read, fetched_at="2026-03-19T08:00:00+00:00"),
        _passenger_document(latest_people),
        _status_document(fetched_at="2026-03-19T08:00:00+00:00"),
        status,
        _reservation_document(fetched_at="2026-03-19T08:00:00+00:00"),
        reservations,
        swapped_passengers=_passenger_document(
            second_read, fetched_at="2026-03-19T08:00:00+00:00"),
    )
    db.snapshots.swap_on_second_as_of = True
    with _client(db) as client:
        response = _restore(client, confirm={
            "passenger_list.passengerIdentities": _identities(first_read),
        })
        board = _board(client)
        live_ids = _identities(board["passengers"]["passengers"])
        assert live_ids != _identities(second_read)
        if response.status_code == 409:
            assert live_ids == _identities(latest_people)
            assert db.inserts == []
        else:
            assert response.status_code == 200, response.text
            assert live_ids == _identities(first_read)
            written = [doc for name, doc in db.inserts if name == "passenger_list"]
            assert written
            assert _identities(written[0]["passengers"]) == _identities(first_read)
