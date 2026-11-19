"""Passenger timeline must not treat per-departure snapshot sequences as one series.

Sequence numbers are allocated by ``backend.feeder.storage._next_sequence``
for one flight number, origin, departure date, and snapshot type, and each
series starts at 1. These tests store that way, then call the HTTP handlers.

A request that does not name both the departure date and the origin must be
rejected (400 or 422) and must not write a restored document. A request that
names both must return that departure only, even when another departure of
the same flight number was ingested more times and therefore has a higher
sequence number.

The assertions fail while the handlers still rank snapshots by sequence
number across every departure of the flight number.
"""

import os
import time

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder import storage

FLIGHT = "2057"
PNR = "ABC123"
PNR_LOW = "ONLYLOW"

# Early, heavily ingested departure. Its sequence numbers are the highest.
EARLY = {
    "origin": "BAH",
    "date": "2026-04-01",
    "seat": "12A",
    "cabin": "Y",
    "bookingClass": "V",
    "boarded": True,
    "checkedIn": True,
    "bags": 2,
    "total": 180,
    "gate": "A1",
    "status": "PDC",
}
# Same date, different origin, ingested once after EARLY.
SAME_DATE = {
    "origin": "DOH",
    "date": "2026-04-01",
    "seat": "22B",
    "cabin": "Y",
    "bookingClass": "M",
    "boarded": False,
    "checkedIn": True,
    "bags": 1,
    "total": 7,
    "gate": "C3",
    "status": "OPENCI",
}
# Different date and origin, ingested once. The passenger ONLYLOW is only here.
LATER = {
    "origin": "DXB",
    "date": "2026-04-02",
    "seat": "4C",
    "cabin": "J",
    "bookingClass": "C",
    "boarded": False,
    "checkedIn": False,
    "bags": 0,
    "total": 40,
    "gate": "B9",
    "status": "OPENCI",
}
# Same origin as EARLY, later date, ingested once.
SAME_ORIGIN = {
    "origin": "BAH",
    "date": "2026-05-01",
    "seat": "18F",
    "cabin": "Y",
    "bookingClass": "N",
    "boarded": False,
    "checkedIn": False,
    "bags": 0,
    "total": 9,
    "gate": "A4",
    "status": "OPENCI",
}


def _passenger(spec, pnr, seat, boarded, checked_in, bags):
    return {
        "lastName": "SMITH",
        "firstName": "JANE",
        "pnr": pnr,
        "passengerId": pnr,
        "bookingClass": spec["bookingClass"],
        "cabin": spec["cabin"],
        "seat": seat,
        "passengerType": "F",
        "bagCount": bags,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "isRevenue": True,
        "priorityCode": "UPG",
        "groupCode": "GRP1",
        "lineNumber": 1,
    }


def _store_departure(spec, repeats, extra_passengers=None):
    passengers = [
        _passenger(
            spec, PNR, spec["seat"], spec["boarded"], spec["checkedIn"], spec["bags"]
        )
    ]
    if extra_passengers:
        passengers.extend(extra_passengers)
    passenger_list = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": spec["origin"],
        "departureDate": spec["date"],
        "destination": "LHR",
        "totalPassengers": spec["total"],
        "adultCount": spec["total"],
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": spec["total"],
        "passengers": passengers,
        "cabinSummary": [],
    }
    flight_status = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": spec["origin"],
        "departureDate": spec["date"],
        "status": spec["status"],
        "gate": spec["gate"],
        "terminal": "1",
    }
    reservations = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": spec["origin"],
        "departureDate": spec["date"],
        "totalResults": 1,
        "reservations": [
            {
                "pnr": PNR,
                "numberInParty": 1,
                "passengers": [
                    {"lastName": "SMITH", "firstName": "JANE", "seatNumber": spec["seat"]}
                ],
                "segments": [],
            }
        ],
    }
    for _ in range(repeats):
        for snapshot_type, data in (
            ("passenger_list", passenger_list),
            ("flight_status", flight_status),
            ("reservations", reservations),
        ):
            storage.store_snapshot(
                request_id="req-test",
                airline="GF",
                flight_number=FLIGHT,
                origin=spec["origin"],
                departure_date=spec["date"],
                snapshot_type=snapshot_type,
                data=data,
            )


def _change(spec, change_type, detected_at, pnr=PNR, old=None, new=None, field=None):
    return {
        "flightNumber": FLIGHT,
        "origin": spec["origin"],
        "departureDate": spec["date"],
        "changeType": change_type,
        "detectedAt": detected_at,
        "passenger": {"pnr": pnr, "lastName": "SMITH", "firstName": "JANE"},
        "field": field,
        "oldValue": old,
        "newValue": new,
        "metadata": {},
    }


def _assert_departure_required(response):
    assert response.status_code in (400, 422), response.text


def _legacy_counts(db):
    query = {"flightNumber": FLIGHT}
    return {
        name: db[name].count_documents(query)
        for name in ("passenger_list", "flight_status", "reservations")
    }


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def db(client):
    database = storage.get_db()
    database.client.drop_database(database.name)

    _store_departure(EARLY, repeats=3)
    time.sleep(1.1)
    _store_departure(SAME_DATE, repeats=1)
    time.sleep(1.1)
    only_low = _passenger(LATER, PNR_LOW, "9D", False, False, 0)
    _store_departure(LATER, repeats=1, extra_passengers=[only_low])
    time.sleep(1.1)
    _store_departure(SAME_ORIGIN, repeats=1)

    storage.store_changes([
        _change(EARLY, "PASSENGER_ADDED", "2026-04-01T06:00:00Z"),
        _change(EARLY, "CHECKED_IN", "2026-04-01T08:00:00Z", field="isCheckedIn", old=False, new=True),
        _change(EARLY, "CABIN_CHANGE", "2026-04-01T09:00:00Z", field="cabin", old="Y", new="J"),
        _change(SAME_DATE, "PASSENGER_ADDED", "2026-04-01T06:30:00Z"),
        _change(SAME_DATE, "SEAT_CHANGE", "2026-04-01T10:00:00Z", field="seat", old="21A", new="22B"),
        _change(LATER, "PASSENGER_ADDED", "2026-04-02T06:00:00Z"),
        _change(LATER, "SEAT_CHANGE", "2026-04-02T08:00:00Z", field="seat", old="", new="4C"),
        _change(LATER, "PASSENGER_ADDED", "2026-04-02T06:05:00Z", pnr=PNR_LOW),
        _change(SAME_ORIGIN, "PASSENGER_ADDED", "2026-05-01T06:00:00Z"),
        _change(SAME_ORIGIN, "BOARDED", "2026-05-01T09:00:00Z", field="isBoarded", old=False, new=True),
    ])

    early_seq = database["snapshots"].find_one(
        {
            "flightNumber": FLIGHT,
            "origin": EARLY["origin"],
            "departureDate": EARLY["date"],
            "snapshotType": "passenger_list",
        },
        sort=[("sequenceNumber", -1)],
    )
    later_seq = database["snapshots"].find_one(
        {
            "flightNumber": FLIGHT,
            "origin": LATER["origin"],
            "departureDate": LATER["date"],
            "snapshotType": "passenger_list",
        },
        sort=[("sequenceNumber", -1)],
    )
    assert early_seq["sequenceNumber"] == 3
    assert later_seq["sequenceNumber"] == 1
    assert early_seq["capturedAt"] < later_seq["capturedAt"]
    return database


def test_timeline_without_date_or_origin_is_rejected(client, db):
    response = client.get(f"/flights/{FLIGHT}/passengers/{PNR}/timeline")
    _assert_departure_required(response)


def test_timeline_with_only_shared_date_is_rejected(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"date": "2026-04-01"},
    )
    _assert_departure_required(response)


def test_timeline_with_only_shared_origin_is_rejected(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"origin": "BAH"},
    )
    _assert_departure_required(response)


def test_timeline_with_only_unique_date_is_rejected(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"date": LATER["date"]},
    )
    _assert_departure_required(response)


def test_timeline_with_only_unique_origin_is_rejected(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"origin": LATER["origin"]},
    )
    _assert_departure_required(response)


def test_timeline_for_low_sequence_departure_returns_that_departure(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"origin": LATER["origin"], "date": LATER["date"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    state = body["currentState"]
    assert state == {
        "cabin": LATER["cabin"],
        "bookingClass": LATER["bookingClass"],
        "seat": LATER["seat"],
        "isCheckedIn": LATER["checkedIn"],
        "isBoarded": LATER["boarded"],
        "bagCount": LATER["bags"],
    }
    change_types = {event["changeType"] for event in body["events"]}
    assert "SEAT_CHANGE" in change_types
    assert "CABIN_CHANGE" not in change_types
    assert "CHECKED_IN" not in change_types
    assert "BOARDED" not in change_types


def test_timeline_for_same_date_other_origin_excludes_higher_sequence(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"origin": SAME_DATE["origin"], "date": SAME_DATE["date"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currentState"]["seat"] == SAME_DATE["seat"]
    assert body["currentState"]["isBoarded"] is False
    change_types = {event["changeType"] for event in body["events"]}
    assert "SEAT_CHANGE" in change_types
    assert "CABIN_CHANGE" not in change_types
    assert "CHECKED_IN" not in change_types


def test_timeline_for_same_origin_later_date_excludes_higher_sequence(client, db):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
        params={"origin": SAME_ORIGIN["origin"], "date": SAME_ORIGIN["date"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currentState"]["seat"] == SAME_ORIGIN["seat"]
    assert body["currentState"]["isBoarded"] is False
    change_types = {event["changeType"] for event in body["events"]}
    assert "BOARDED" in change_types
    assert "CABIN_CHANGE" not in change_types


def test_timeline_passenger_only_on_low_sequence_departure_is_not_dropped(client, db):
    missing = client.get(f"/flights/{FLIGHT}/passengers/{PNR_LOW}/timeline")
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR_LOW}/timeline",
        params={"origin": LATER["origin"], "date": LATER["date"]},
    )
    assert response.status_code == 200, response.text
    state = response.json()["currentState"]
    assert state is not None
    assert state["seat"] == "9D"
    assert state["isBoarded"] is False
    assert state["isCheckedIn"] is False


def test_passenger_changes_require_date_and_origin(client, db):
    missing = client.get(f"/flights/{FLIGHT}/changes/passenger/{PNR}")
    _assert_departure_required(missing)

    date_only = client.get(
        f"/flights/{FLIGHT}/changes/passenger/{PNR}",
        params={"date": "2026-04-01"},
    )
    _assert_departure_required(date_only)

    scoped = client.get(
        f"/flights/{FLIGHT}/changes/passenger/{PNR}",
        params={"origin": SAME_DATE["origin"], "date": SAME_DATE["date"]},
    )
    assert scoped.status_code == 200, scoped.text
    changes = scoped.json()
    assert changes
    assert all(change["origin"] == SAME_DATE["origin"] for change in changes)
    assert all(change["departureDate"] == SAME_DATE["date"] for change in changes)
    assert {change["changeType"] for change in changes} == {"PASSENGER_ADDED", "SEAT_CHANGE"}


def test_snapshot_list_does_not_rank_other_departures_by_sequence(client, db):
    missing = client.get(f"/flights/{FLIGHT}/snapshots")
    _assert_departure_required(missing)

    shared_date = client.get(
        f"/flights/{FLIGHT}/snapshots",
        params={"date": EARLY["date"], "snapshot_type": "passenger_list"},
    )
    _assert_departure_required(shared_date)

    response = client.get(
        f"/flights/{FLIGHT}/snapshots",
        params={
            "origin": LATER["origin"],
            "date": LATER["date"],
            "snapshot_type": "passenger_list",
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows
    assert all(row["origin"] == LATER["origin"] for row in rows)
    assert all(row["departureDate"] == LATER["date"] for row in rows)
    assert max(row["sequenceNumber"] for row in rows) == 1


def test_snapshot_compare_does_not_rank_other_departures_by_sequence(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/snapshots/compare",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/snapshots/compare",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    assert response.status_code == 200, response.text
    passenger_list = response.json()["types"]["passenger_list"]
    assert passenger_list["available"] is True
    assert passenger_list["latestSequence"] == 1
    assert passenger_list["deltas"]["totalPassengers"]["latest"] == LATER["total"]
    assert passenger_list["deltas"]["boarded"]["latest"] == 0
    flight_status = response.json()["types"]["flight_status"]
    assert flight_status["latestSequence"] == 1
    assert flight_status["deltas"]["gate"]["latest"] == LATER["gate"]
    assert flight_status["deltas"]["status"]["latest"] == LATER["status"]


def test_restore_without_departure_does_not_write(client, db):
    before = _legacy_counts(db)
    response = client.post(f"/flights/{FLIGHT}/snapshots/10/restore")
    _assert_departure_required(response)
    assert _legacy_counts(db) == before

    date_only = client.post(
        f"/flights/{FLIGHT}/snapshots/10/restore",
        params={"date": EARLY["date"]},
    )
    _assert_departure_required(date_only)
    assert _legacy_counts(db) == before

    origin_only = client.post(
        f"/flights/{FLIGHT}/snapshots/10/restore",
        params={"origin": EARLY["origin"]},
    )
    _assert_departure_required(origin_only)
    assert _legacy_counts(db) == before


def test_restore_with_departure_writes_that_departure_only(client, db):
    response = client.post(
        f"/flights/{FLIGHT}/snapshots/10/restore",
        params={"origin": LATER["origin"], "date": LATER["date"]},
    )
    assert response.status_code == 200, response.text
    restored = db["passenger_list"].find_one(
        {"flightNumber": FLIGHT, "origin": LATER["origin"], "departureDate": LATER["date"]},
        sort=[("fetchedAt", -1)],
    )
    assert restored is not None
    seats = {pax["pnr"]: pax["seat"] for pax in restored["passengers"]}
    assert seats[PNR] == LATER["seat"]
    assert seats[PNR_LOW] == "9D"
    assert all(pax["seat"] != EARLY["seat"] for pax in restored["passengers"])
    status = db["flight_status"].find_one(
        {"flightNumber": FLIGHT, "origin": LATER["origin"], "departureDate": LATER["date"]},
        sort=[("fetchedAt", -1)],
    )
    assert status["gate"] == LATER["gate"]
    assert status["status"] == LATER["status"]


def test_boarding_progress_uses_the_named_departure(client, db):
    missing = client.get(f"/flights/{FLIGHT}/boarding-progress")
    _assert_departure_required(missing)

    shared_date = client.get(
        f"/flights/{FLIGHT}/boarding-progress",
        params={"date": EARLY["date"]},
    )
    _assert_departure_required(shared_date)

    response = client.get(
        f"/flights/{FLIGHT}/boarding-progress",
        params={"origin": LATER["origin"], "date": LATER["date"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalPassengers"] == LATER["total"]
    assert body["checkinProgress"]["current"] == 0
    assert body["boardingProgress"]["current"] == 0


def test_phase_journey_does_not_pair_sequences_across_departures(client, db):
    missing = client.get(f"/flights/{FLIGHT}/phase-journey")
    _assert_departure_required(missing)

    shared_origin = client.get(
        f"/flights/{FLIGHT}/phase-journey",
        params={"origin": EARLY["origin"]},
    )
    _assert_departure_required(shared_origin)

    response = client.get(
        f"/flights/{FLIGHT}/phase-journey",
        params={"origin": LATER["origin"], "date": LATER["date"]},
    )
    assert response.status_code == 200, response.text
    phases = [phase["phase"] for phase in response.json()["phases"]]
    assert phases
    assert "DEPARTED" not in phases
    assert phases[0] == "CHECK_IN"


def _assert_as_of_passenger_seat(response, seat):
    assert response.status_code == 200, response.text
    passengers = response.json()["passengers"]
    match = next(pax for pax in passengers if pax["pnr"] == PNR)
    assert match["seat"] == seat
    assert match["isBoarded"] is False


def test_passenger_list_as_of_sequence_stays_on_the_named_departure(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    _assert_as_of_passenger_seat(response, LATER["seat"])


def test_standby_list_as_of_sequence_stays_on_the_named_departure(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/passengers/standby-list",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/passengers/standby-list",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    assert response.status_code == 200, response.text
    upgrades = response.json()["upgrade"]["passengers"]
    match = next(pax for pax in upgrades if pax["pnr"] == PNR)
    assert match["seat"] == LATER["seat"]
    assert all(pax["seat"] != EARLY["seat"] for pax in upgrades)


def test_group_bookings_as_of_sequence_stays_on_the_named_departure(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/passengers/groups",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/passengers/groups",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    assert response.status_code == 200, response.text
    members = response.json()["groups"][0]["members"]
    match = next(pax for pax in members if pax["pnr"] == PNR)
    assert match["seat"] == LATER["seat"]
    assert match["isBoarded"] is False


def test_flight_status_as_of_sequence_stays_on_the_named_departure(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/status",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/status",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == LATER["gate"]
    assert body["status"] == LATER["status"]
    assert body["origin"] == LATER["origin"]
    assert body["departureDate"] == LATER["date"]


def test_reservations_as_of_sequence_stays_on_the_named_departure(client, db):
    missing = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"snapshot_sequence": 10},
    )
    _assert_departure_required(missing)

    response = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={
            "snapshot_sequence": 10,
            "origin": LATER["origin"],
            "date": LATER["date"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["departureAirport"] == LATER["origin"]
    assert body["departureDate"] == LATER["date"]
    assert body["reservations"][0]["passengers"][0]["seatNumber"] == LATER["seat"]


def test_dashboard_and_tree_snapshot_sequence_do_not_return_other_departure(client, db):
    """Historical dashboard and tree call the same as-of lookup.

    The call currently passes positional arguments to a keyword-only helper,
    so the lookup raises before a snapshot is chosen. Rejection, not-found,
    or a payload that stays on the named departure are all acceptable. The
    early departure's seat, gate, and boarded total are not.
    """
    for path in ("dashboard", "tree"):
        missing = client.get(
            f"/flights/{FLIGHT}/{path}",
            params={"snapshot_sequence": 10},
        )
        if missing.status_code == 200:
            text = missing.text
            assert EARLY["seat"] not in text
            assert EARLY["gate"] not in text
            assert '"boarded": 1' not in text and '"boarded":1' not in text
        else:
            assert missing.status_code in (400, 404, 422), missing.text
            assert EARLY["seat"] not in missing.text

        scoped = client.get(
            f"/flights/{FLIGHT}/{path}",
            params={
                "snapshot_sequence": 10,
                "origin": LATER["origin"],
                "date": LATER["date"],
            },
        )
        if scoped.status_code == 200:
            assert EARLY["seat"] not in scoped.text
            assert LATER["gate"] in scoped.text or LATER["seat"] in scoped.text
        else:
            assert scoped.status_code in (400, 404, 422, 500), scoped.text
            assert EARLY["seat"] not in scoped.text
