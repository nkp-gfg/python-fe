"""Lower-case airport codes must still find departures stored in upper case.

These tests fail while lookup filters compare the query text as typed.
Ingestion and the stored documents use three upper-case letters. The shared
airport check accepts a lower-case code because it upper-cases only the copy
it tests, and the flight, passenger, audit, history, reservation, data-audit,
and network-analytics routes then filter with the original text.

Not covered here, because they do not reach this filter:

- Dashboard and tree requests with snapshot_sequence. The parallel fetch
  calls get_snapshot_data_as_of positionally, but that helper is keyword-only,
  so the call raises for every origin before the airport comparison.
- Passenger detail. Its Mongo lookup ignores origin and then calls Sabre.
- HTTP /passengers/history-badges. An earlier /passengers/{pnr} route takes
  that URL. The badges operation is called directly below.
- The feeder sequence lookup. Its origin is the ingest payload, which the
  ingest model rejects unless it is three upper-case letters.

A fix that only repeats the format check, or that drops the airport filter,
still fails: each request asks for BAH (typed as bah / Bah) and must not
return the later DXB departure that shares the flight number.

Run (the suite is red until the filters use the normalized code):

    export MONGODB_URI='mongodb://127.0.0.1:27017/'
    export POSTGRES_URI='dbname=falconeye user=ubuntu host=/var/run/postgresql'
    python3 -m pytest backend/tests/test_airport_origin_case.py -q
"""

import os

import psycopg2
import psycopg2.extras
import pytest
from pymongo import MongoClient

FLIGHT = "4242"
RESTORE_FLIGHT = "7777"
DATE = "2026-11-23"
BAH_TS = "2026-11-23T10:00:00Z"
DXB_TS = "2026-11-23T12:00:00Z"
LOWER_ORIGINS = ("bah", "Bah")


def _pax(last, first, pnr, **extra):
    doc = {
        "lastName": last,
        "firstName": first,
        "pnr": pnr,
        "cabin": "Y",
        "bookingClass": "Y",
        "isCheckedIn": False,
        "isBoarded": False,
        "isRevenue": True,
        "passengerType": "F",
        "lineNumber": 1,
        "seat": "12A",
    }
    doc.update(extra)
    return doc


def _smith():
    return _pax(
        "SMITH",
        "JOHN",
        "ABC123",
        isBoarded=True,
        isCheckedIn=False,
        priorityCode="UPG",
        groupCode="GRP1",
    )


def _dxb_passengers():
    people = [
        _pax(
            "JONES",
            "MARY",
            "DXB999",
            isCheckedIn=True,
            isBoarded=True,
            lineNumber=1,
            seat="99Z",
        )
    ]
    for i in range(2, 6):
        people.append(
            _pax(
                f"PAX{i}",
                "TEST",
                f"DXB99{i}",
                isCheckedIn=True,
                isBoarded=True,
                lineNumber=i,
                seat="99Z",
            )
        )
    return people


def _passenger_list(origin, destination, fetched_at, passengers, total):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": origin,
        "destination": destination,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "totalPassengers": total,
        "adultCount": total,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": total,
        "cabinSummary": [{"cabin": "Y", "count": total, "authorized": 100, "available": 100 - total}],
        "passengers": passengers,
        "departureGate": "A1" if origin == "BAH" else "B9",
    }


def _flight_status(origin, fetched_at, status, gate):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "_ingestTimestamp": fetched_at,
        "status": status,
        "gate": gate,
        "terminal": "1",
    }


def _reservations(origin, fetched_at, pnr, last):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": origin,
        "origin": origin,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "totalResults": 1,
        "reservations": [
            {
                "pnr": pnr,
                "numberInParty": 1,
                "passengers": [{"lastName": last, "firstName": "JOHN", "nationality": "BH"}],
                "segments": [],
            }
        ],
    }


def _change(origin, detected_at, change_type, pnr, last):
    return {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": DATE,
        "detectedAt": detected_at,
        "changeType": change_type,
        "field": "status",
        "oldValue": "",
        "newValue": change_type,
        "passenger": {"pnr": pnr, "lastName": last, "firstName": "JOHN"},
    }


def _snapshot(snapshot_id, origin, snapshot_type, sequence, fetched_at, data):
    return {
        "snapshotId": snapshot_id,
        "flightNumber": data.get("flightNumber", FLIGHT),
        "origin": origin,
        "departureDate": DATE,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": fetched_at,
        "fetchedAt": fetched_at,
        "data": data,
    }


def _flight_pair(db, flight_number):
    """Insert a BAH departure and a later DXB departure with the same number."""
    bah_pax = [_smith()] if flight_number == FLIGHT else [_smith()]
    # Restore flight uses the same passenger shape so the restored doc is identifiable.
    dxb_pax = _dxb_passengers()
    bah_pl = _passenger_list("BAH", "LHR", BAH_TS, bah_pax, 1)
    dxb_pl = _passenger_list("DXB", "CDG", DXB_TS, dxb_pax, 5)
    bah_pl["flightNumber"] = flight_number
    dxb_pl["flightNumber"] = flight_number
    bah_fs = _flight_status("BAH", BAH_TS, "OPEN", "A1")
    dxb_fs = _flight_status("DXB", DXB_TS, "PDC", "B9")
    bah_fs["flightNumber"] = flight_number
    dxb_fs["flightNumber"] = flight_number
    bah_res = _reservations("BAH", BAH_TS, "ABC123", "SMITH")
    dxb_res = _reservations("DXB", DXB_TS, "DXB999", "JONES")
    bah_res["flightNumber"] = flight_number
    dxb_res["flightNumber"] = flight_number

    db["passenger_list"].insert_many([bah_pl, dxb_pl])
    db["flight_status"].insert_many([bah_fs, dxb_fs])
    db["reservations"].insert_many([bah_res, dxb_res])
    db["flights"].insert_many(
        [
            {
                "airline": "GF",
                "flightNumber": flight_number,
                "origin": "BAH",
                "departureDate": DATE,
                "flightSequenceNumber": 1,
            },
            {
                "airline": "GF",
                "flightNumber": flight_number,
                "origin": "DXB",
                "departureDate": DATE,
                "flightSequenceNumber": 2,
            },
        ]
    )
    db["snapshots"].insert_many(
        [
            _snapshot(f"{flight_number}-bah-pl", "BAH", "passenger_list", 1, BAH_TS, bah_pl),
            _snapshot(f"{flight_number}-bah-fs", "BAH", "flight_status", 1, BAH_TS, bah_fs),
            _snapshot(f"{flight_number}-bah-rs", "BAH", "reservations", 1, BAH_TS, bah_res),
            _snapshot(f"{flight_number}-dxb-pl", "DXB", "passenger_list", 2, DXB_TS, dxb_pl),
            _snapshot(f"{flight_number}-dxb-fs", "DXB", "flight_status", 2, DXB_TS, dxb_fs),
            _snapshot(f"{flight_number}-dxb-rs", "DXB", "reservations", 2, DXB_TS, dxb_res),
        ]
    )
    if flight_number == FLIGHT:
        db["changes"].insert_many(
            [
                _change("BAH", "2026-11-23T10:01:00Z", "CHECKED_IN", "ABC123", "SMITH"),
                _change("BAH", "2026-11-23T10:02:00Z", "BOARDED", "ABC123", "SMITH"),
                _change("DXB", "2026-11-23T12:01:00Z", "CHECKED_IN", "XYZ999", "JONES"),
                _change("DXB", "2026-11-23T12:02:00Z", "SEAT_CHANGE", "ABC123", "SMITH"),
            ]
        )


def _ensure_postgres():
    uri = os.environ["POSTGRES_URI"]
    conn = psycopg2.connect(uri)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS otp")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
            flight_sequence_number integer,
            flight_number text,
            scheduled_origin text,
            scheduled_destination text,
            actual_origin text,
            actual_destination text,
            flight_date date,
            scheduled_departure_utc timestamptz,
            estimated_block_off_utc timestamptz,
            scheduled_arrival_utc timestamptz,
            estimated_block_on_utc timestamptz,
            actual_block_off_utc timestamptz,
            actual_block_on_utc timestamptz,
            actual_takeoff_utc timestamptz,
            actual_touchdown_utc timestamptz,
            scheduled_departure_local text,
            scheduled_arrival_local text,
            flight_status text,
            aircraft_type text,
            aircraft_registration text,
            service_type_code text,
            cancel_reason_code text,
            passenger_counts jsonb,
            delay_details jsonb,
            source text
        )
        """
    )
    cur.execute("DELETE FROM otp.flight_xml_current WHERE flight_number = %s", (FLIGHT,))
    cur.execute(
        """
        INSERT INTO otp.flight_xml_current
            (flight_sequence_number, flight_number, scheduled_origin,
             scheduled_destination, flight_date, flight_status, passenger_counts)
        VALUES
            (1, %s, 'BAH', 'LHR', %s, 'OPEN', %s),
            (2, %s, 'DXB', 'CDG', %s, 'PDC', %s)
        """,
        (
            FLIGHT,
            DATE,
            psycopg2.extras.Json([{"@classOfService": "Y", "Count": 1}]),
            FLIGHT,
            "2026-11-24",
            psycopg2.extras.Json([{"@classOfService": "Y", "Count": 5}]),
        ),
    )
    cur.close()
    conn.close()


@pytest.fixture(scope="session")
def mongo_db():
    uri = os.environ.get("MONGODB_URI")
    if not uri or not os.environ.get("POSTGRES_URI"):
        pytest.fail("MONGODB_URI and POSTGRES_URI must be set")
    client = MongoClient(uri)
    db = client["falconeye"]
    for name in (
        "passenger_list",
        "flight_status",
        "reservations",
        "flights",
        "snapshots",
        "changes",
        "trip_reports",
    ):
        db[name].delete_many({})
    _flight_pair(db, FLIGHT)
    _flight_pair(db, RESTORE_FLIGHT)
    _ensure_postgres()
    yield db
    client.close()


@pytest.fixture(scope="session")
def client(mongo_db):
    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    from backend.api.routes.flights import _dashboard_cache

    _dashboard_cache.clear()
    yield
    _dashboard_cache.clear()


def _body(response):
    assert response.status_code == 200, response.text
    return response.json()


def _params(origin, **extra):
    query = {"origin": origin, "date": DATE}
    query.update(extra)
    return query


# ── Controls: the stored upper-case code is findable ─────────────────────


def test_uppercase_origin_returns_bahrain_departure(client):
    body = _body(client.get(f"/flights/{FLIGHT}/dashboard", params=_params("BAH")))
    assert body["route"]["origin"] == "BAH"
    assert body["overview"]["manifestRecords"] == 1


def test_invalid_airport_code_is_rejected(client):
    response = client.get(f"/flights/{FLIGHT}/dashboard", params=_params("BA"))
    assert response.status_code == 400


def test_ingestion_rejects_lowercase_origin_before_any_write(client, mongo_db):
    before = mongo_db["sabre_requests"].count_documents({})
    response = client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": FLIGHT,
            "origin": "bah",
            "departureDate": DATE,
            "departureDateTime": "2026-11-23T10:00:00",
        },
    )
    assert response.status_code == 422
    assert mongo_db["sabre_requests"].count_documents({}) == before


# ── Flight page lookups ───────────────────────────────────────────────────


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_dashboard_lowercase_origin_returns_stored_bahrain_flight(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/dashboard", params=_params(origin)))
    assert body["route"]["origin"] == "BAH"
    assert body["route"]["destination"] == "LHR"
    assert body["overview"]["manifestRecords"] == 1
    assert body["flightStatus"]["status"] == "OPEN"
    assert body["flightStatus"]["gate"] == "A1"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_tree_lowercase_origin_counts_bahrain_manifest(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/tree", params=_params(origin)))
    root = next(node for node in body["nodes"] if node["id"] == "root")
    assert root["value"] == 1


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_flight_status_lowercase_origin_returns_bahrain(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/status", params=_params(origin)))
    assert body["origin"] == "BAH"
    assert body["status"] == "OPEN"
    assert body["gate"] == "A1"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_flight_status_snapshot_lowercase_origin_returns_bahrain(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/status",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    assert body["origin"] == "BAH"
    assert body["status"] == "OPEN"
    assert body["gate"] == "A1"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_flight_status_history_lowercase_origin_excludes_dubai(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/status/history", params=_params(origin)))
    assert body
    assert {row["origin"] for row in body} == {"BAH"}
    assert {row["status"] for row in body} == {"OPEN"}


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_phase_journey_lowercase_origin_uses_bahrain_snapshot(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/phase-journey", params=_params(origin)))
    totals = [phase["summary"]["totalPassengers"] for phase in body["phases"]]
    assert totals
    assert totals == [1]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_passengers_lowercase_origin_returns_smith(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/passengers", params=_params(origin)))
    assert body["origin"] == "BAH"
    assert [p["lastName"] for p in body["passengers"]] == ["SMITH"]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_passengers_historical_snapshot_uses_normalized_origin(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/passengers",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    assert body["origin"] == "BAH"
    assert [p["lastName"] for p in body["passengers"]] == ["SMITH"]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_passenger_summary_lowercase_origin_counts_bahrain(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/passengers/summary", params=_params(origin)))
    assert body["origin"] == "BAH"
    assert body["totalPassengers"] == 1
    assert body["boarded"] == 1


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_standby_list_lowercase_origin_returns_bahrain_upgrade(client, origin):
    body = _body(
        client.get(f"/flights/{FLIGHT}/passengers/standby-list", params=_params(origin))
    )
    assert body["origin"] == "BAH"
    assert body["upgrade"]["total"] == 1
    assert body["upgrade"]["passengers"][0]["lastName"] == "SMITH"
    assert body["upgrade"]["passengers"][0]["pnr"] == "ABC123"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_standby_snapshot_lowercase_origin_returns_bahrain_upgrade(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/passengers/standby-list",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    assert body["origin"] == "BAH"
    assert body["upgrade"]["total"] == 1
    assert body["upgrade"]["passengers"][0]["pnr"] == "ABC123"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_group_snapshot_lowercase_origin_returns_bahrain_group(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/passengers/groups",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    assert body["origin"] == "BAH"
    assert body["totalGroups"] == 1
    assert body["groups"][0]["groupCode"] == "GRP1"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_group_bookings_lowercase_origin_returns_bahrain_group(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/passengers/groups", params=_params(origin)))
    assert body["origin"] == "BAH"
    assert body["totalGroups"] == 1
    assert body["groups"][0]["groupCode"] == "GRP1"
    assert body["groups"][0]["members"][0]["lastName"] == "SMITH"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_process_audit_lowercase_origin_sees_bahrain_manifest(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/audit", params=_params(origin)))
    alerts = [alert for alert in body["alerts"] if alert["ruleId"] == "BOARDED_WITHOUT_CHECKIN"]
    assert [alert["pnr"] for alert in alerts] == ["ABC123"]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_reservations_lowercase_origin_returns_bahrain_pnr(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/reservations", params=_params(origin)))
    assert body["departureAirport"] == "BAH"
    assert [row["pnr"] for row in body["reservations"]] == ["ABC123"]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_reservations_historical_snapshot_uses_normalized_origin(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/reservations",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    assert body["departureAirport"] == "BAH"
    assert [row["pnr"] for row in body["reservations"]] == ["ABC123"]


# ── History and change routes ─────────────────────────────────────────────


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_changes_lowercase_origin_returns_only_bahrain_events(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/changes", params=_params(origin)))
    assert {row["origin"] for row in body} == {"BAH"}
    assert sorted(row["changeType"] for row in body) == ["BOARDED", "CHECKED_IN"]


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_changes_summary_lowercase_origin_counts_bahrain_only(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/changes/summary", params=_params(origin)))
    assert body["totalChanges"] == 2
    assert body["changeTypes"] == {"CHECKED_IN": 1, "BOARDED": 1}


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_snapshots_lowercase_origin_lists_bahrain_only(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/snapshots", params=_params(origin)))
    assert body
    assert {row["origin"] for row in body} == {"BAH"}
    assert {row["sequenceNumber"] for row in body} == {1}


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_snapshot_compare_lowercase_origin_reads_bahrain_manifest(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/snapshots/compare",
            params=_params(origin, snapshot_sequence=2),
        )
    )
    passenger_list = body["types"]["passenger_list"]
    assert passenger_list["available"] is True
    assert passenger_list["deltas"]["totalPassengers"]["selected"] == 1
    assert passenger_list["deltas"]["totalPassengers"]["latest"] == 1


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_passenger_timeline_lowercase_origin_omits_dubai_seat_change(client, origin):
    body = _body(
        client.get(
            f"/flights/{FLIGHT}/passengers/ABC123/timeline",
            params=_params(origin),
        )
    )
    assert sorted(event["changeType"] for event in body["events"]) == ["BOARDED", "CHECKED_IN"]
    assert body["currentState"]["seat"] == "12A"
    assert body["currentState"]["isBoarded"] is True


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_flight_timeline_lowercase_origin_omits_dubai_sequence(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/timeline", params=_params(origin)))
    sequences = [
        event["details"]["sequenceNumber"]
        for event in body["events"]
        if event["eventType"] == "SNAPSHOT_TAKEN"
    ]
    change_types = [
        event["eventType"] for event in body["events"] if event["category"] != "snapshot"
    ]
    assert sequences
    assert set(sequences) == {1}
    assert "SEAT_CHANGE" not in change_types
    assert "CHECKED_IN" in change_types


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_boarding_progress_lowercase_origin_counts_bahrain_only(client, origin):
    body = _body(client.get(f"/flights/{FLIGHT}/boarding-progress", params=_params(origin)))
    assert body["totalPassengers"] == 1
    assert body["checkinProgress"]["current"] == 1
    assert body["boardingProgress"]["current"] == 1


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_history_badges_lowercase_origin_counts_bahrain_changes(mongo_db, client, origin):
    """The badges function filters like the other history routes.

    HTTP /passengers/history-badges is dispatched to the PNR lookup registered
    earlier, so this calls the badges operation itself.
    """
    del mongo_db, client
    from backend.api.routes.changes import get_passengers_history_badges

    badges = get_passengers_history_badges(FLIGHT, origin=origin, date=DATE)
    assert set(badges) == {"ABC123"}
    assert badges["ABC123"]["changeCount"] == 2


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_restore_lowercase_origin_writes_bahrain_snapshot(client, mongo_db, origin):
    before = mongo_db["passenger_list"].count_documents({"flightNumber": RESTORE_FLIGHT})
    body = _body(
        client.post(
            f"/flights/{RESTORE_FLIGHT}/snapshots/2/restore",
            params=_params(origin),
        )
    )
    restored_types = {item["snapshotType"] for item in body["restored"]}
    assert "passenger_list" in restored_types
    assert all(item["sourceSequence"] == 1 for item in body["restored"])
    newest = mongo_db["passenger_list"].find_one(
        {"flightNumber": RESTORE_FLIGHT},
        sort=[("fetchedAt", -1)],
    )
    assert newest["origin"] == "BAH"
    assert newest["passengers"][0]["lastName"] == "SMITH"
    assert newest["totalPassengers"] == 1
    assert mongo_db["passenger_list"].count_documents({"flightNumber": RESTORE_FLIGHT}) == before + 1


# ── Network analytics does not use the shared check ──────────────────────


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_network_analytics_lowercase_origin_counts_bahrain_only(client, origin):
    body = _body(
        client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE, "origin": origin},
        )
    )
    assert body["overview"]["totalFlights"] == 2
    assert body["overview"]["totalPassengers"] == 2
    assert {row["origin"] for row in body["routePerformance"]} == {"BAH"}


@pytest.mark.parametrize("destination", ("lhr", "Lhr"))
def test_network_analytics_lowercase_destination_counts_london_only(client, destination):
    body = _body(
        client.get(
            "/analytics/network",
            params={"date_from": DATE, "date_to": DATE, "destination": destination},
        )
    )
    assert body["overview"]["totalPassengers"] == 2


def test_network_analytics_lowercase_origin_does_not_take_other_destination(client):
    body = _body(
        client.get(
            "/analytics/network",
            params={
                "date_from": DATE,
                "date_to": DATE,
                "origin": "bah",
                "destination": "cdg",
            },
        )
    )
    assert body["overview"]["totalFlights"] == 2
    assert body["overview"]["totalPassengers"] == 0


# ── PostgreSQL and Mongo data-audit filters ──────────────────────────────


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_data_audit_compare_lowercase_origin_reads_bahrain_row(client, origin):
    body = _body(
        client.get(f"/data-audit/{FLIGHT}/compare", params=_params(origin))
    )
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    origin_row = next(row for row in body["rows"] if row["field"] == "Origin")
    assert origin_row["pgValue"] == "BAH"
    assert origin_row["mongoValue"] == "BAH"
    assert origin_row["match"] == "match"


@pytest.mark.parametrize("origin", LOWER_ORIGINS)
def test_data_audit_passengers_lowercase_origin_counts_bahrain(client, origin):
    body = _body(
        client.get(f"/data-audit/{FLIGHT}/passengers", params=_params(origin))
    )
    assert body["pgFound"] is True
    assert body["mongoFound"] is True
    total = next(row for row in body["rows"] if row["field"] == "Total Passengers")
    assert total["pgValue"] == "1"
    assert total["mongoValue"] == "1"
