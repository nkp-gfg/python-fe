"""Snapshot restore must not become the current Sabre flight record.

These tests exercise the product route
POST /flights/{flight_number}/snapshots/{sequence}/restore with no
credentials. They fail while a restore copies historical snapshot data into
passenger_list, flight_status, or reservations and stamps fetchedAt with the
current time, because those collections are what the operational reads treat
as the latest Sabre fetch (sort fetchedAt descending).

They pass once a restore no longer publishes that copy as the newest ingested
document. The live collections must keep the same documents, and every read
below must keep returning the newest ingested payload and its original
fetchedAt.

Not covered here:
- GET /flights/{flight_number}/passengers/{pnr}/detail uses the same
  passenger_list sort, then calls Sabre. This process does not call Sabre.
- GET /data-audit/{flight_number}/compare selects flight_status by
  _ingestTimestamp, which restore does not set.
- GET /compare/flights reads the flights collection, which restore does not
  write.
- Schedule and trip-report reads are unchanged because restore does not write
  those collections.
"""

import copy
import os

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["POSTGRES_URI"] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"

import pytest
from fastapi.testclient import TestClient

from backend.api.database import get_db
from backend.api.main import app
from backend.api.routes import flights as flights_routes

LATEST_FETCHED_AT = "2026-03-19T12:00:00Z"
OLD_FETCHED_AT = "2026-03-19T08:00:00Z"
ORIGIN = "BAH"
DATE = "2026-03-19"
FLIGHT = "2057"

# A second operating day whose sequence is higher than the current day's.
OTHER_DATE = "2026-03-18"
OTHER_FETCHED_AT = "2026-03-18T09:00:00Z"
OTHER_FLIGHT = "2060"


def _passenger(last, first, pnr, seat, checked, boarded, booking_class="Y",
               priority="", group=""):
    return {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "isCheckedIn": checked,
        "isBoarded": boarded,
        "isRevenue": True,
        "cabin": "Y",
        "bookingClass": booking_class,
        "seat": seat,
        "priorityCode": priority,
        "groupCode": group,
        "lineNumber": 1,
        "passengerType": "F",
    }


def _passenger_list(flight, date, fetched_at, passengers):
    return {
        "airline": "GF",
        "flightNumber": flight,
        "origin": ORIGIN,
        "destination": "LHR",
        "departureDate": date,
        "fetchedAt": fetched_at,
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": len(passengers),
        "cabinSummary": [],
        "groupBookings": [],
        "passengers": passengers,
    }


def _flight_status(flight, date, fetched_at, status, gate, terminal):
    return {
        "airline": "GF",
        "flightNumber": flight,
        "origin": ORIGIN,
        "departureDate": date,
        "fetchedAt": fetched_at,
        "status": status,
        "gate": gate,
        "terminal": terminal,
        "aircraft": {"type": "32N"},
        "boarding": {"time": "", "indicator": ""},
    }


def _reservations(flight, date, fetched_at, reservations, total):
    return {
        "airline": "GF",
        "flightNumber": flight,
        "departureAirport": ORIGIN,
        "departureDate": date,
        "fetchedAt": fetched_at,
        "totalResults": total,
        "reservations": reservations,
    }


def _current_passengers():
    return [
        _passenger("CURRENT", "ADA", "ABC123", "14C", True, True),
        _passenger("EXTRA", "BEN", "NEW999", "21A", False, False),
    ]


def _old_passengers():
    return [
        _passenger("EARLY", "CAM", "ABC123", "12A", False, False,
                   booking_class="W", priority="UPG", group="G1"),
    ]


def _current_reservations():
    return [{
        "pnr": "ABC123",
        "numberInParty": 2,
        "numberOfInfants": 0,
        "passengers": [{
            "lastName": "CURRENT",
            "firstName": "ADA",
            "gender": "M",
            "nationality": "BH",
        }],
    }]


def _old_reservations():
    return [{
        "pnr": "ABC123",
        "numberInParty": 1,
        "numberOfInfants": 0,
        "passengers": [{
            "lastName": "EARLY",
            "firstName": "CAM",
            "gender": "F",
            "nationality": "XX",
        }],
    }]


def _insert_generation(db, flight, date, sequence, fetched_at, passengers,
                       reservations, status, gate, terminal):
    pax_doc = _passenger_list(flight, date, fetched_at, passengers)
    status_doc = _flight_status(flight, date, fetched_at, status, gate, terminal)
    res_doc = _reservations(flight, date, fetched_at, reservations, len(reservations))
    db["passenger_list"].insert_one(copy.deepcopy(pax_doc))
    db["flight_status"].insert_one(copy.deepcopy(status_doc))
    db["reservations"].insert_one(copy.deepcopy(res_doc))
    for snapshot_type, data in (
        ("passenger_list", pax_doc),
        ("flight_status", status_doc),
        ("reservations", res_doc),
    ):
        db["snapshots"].insert_one({
            "snapshotId": f"{flight}-{date}-{snapshot_type}-{sequence}",
            "flightNumber": flight,
            "origin": ORIGIN,
            "departureDate": date,
            "snapshotType": snapshot_type,
            "sequenceNumber": sequence,
            "capturedAt": fetched_at,
            "data": copy.deepcopy(data),
        })


def _seed_two_generations(db, flight=FLIGHT):
    _insert_generation(
        db, flight, DATE, 1, OLD_FETCHED_AT,
        _old_passengers(), _old_reservations(),
        "BOARDING", "A1", "T1",
    )
    _insert_generation(
        db, flight, DATE, 2, LATEST_FETCHED_AT,
        _current_passengers(), _current_reservations(),
        "FINAL", "B22", "T2",
    )


def _counts(db, flight):
    return {
        "passenger_list": db["passenger_list"].count_documents({"flightNumber": flight}),
        "flight_status": db["flight_status"].count_documents({"flightNumber": flight}),
        "reservations": db["reservations"].count_documents({"flightNumber": flight}),
    }


def _fetched_at_values(db, flight):
    values = {}
    for name in ("passenger_list", "flight_status", "reservations"):
        values[name] = sorted(
            doc["fetchedAt"] for doc in db[name].find(
                {"flightNumber": flight}, {"fetchedAt": 1}
            )
        )
    return values


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db(api):
    database = get_db()
    database.client.drop_database("falconeye")
    flights_routes._dashboard_cache.clear()
    yield database
    flights_routes._dashboard_cache.clear()


def _row(rows, field):
    match = [row for row in rows if row["field"] == field]
    assert match, field
    return match[0]


def _assert_current_operational_view(client, flight=FLIGHT):
    """The newest ingested manifest, status, and reservations stay current."""
    flights_routes._dashboard_cache.clear()
    params = {"origin": ORIGIN, "date": DATE}

    passengers = client.get(f"/flights/{flight}/passengers", params=params)
    assert passengers.status_code == 200, passengers.text
    body = passengers.json()
    assert body["fetchedAt"] == LATEST_FETCHED_AT
    by_name = {pax["lastName"]: pax for pax in body["passengers"]}
    assert set(by_name) == {"CURRENT", "EXTRA"}
    assert by_name["CURRENT"]["seat"] == "14C"
    assert by_name["CURRENT"]["isBoarded"] is True
    assert by_name["CURRENT"]["isCheckedIn"] is True

    summary = client.get(f"/flights/{flight}/passengers/summary", params=params)
    assert summary.status_code == 200, summary.text
    summary_body = summary.json()
    assert summary_body["fetchedAt"] == LATEST_FETCHED_AT
    assert summary_body["totalPassengers"] == 2
    assert summary_body["boarded"] == 1
    assert summary_body["checkedIn"] == 1

    standby = client.get(f"/flights/{flight}/passengers/standby-list", params=params)
    assert standby.status_code == 200, standby.text
    standby_body = standby.json()
    assert standby_body["fetchedAt"] == LATEST_FETCHED_AT
    assert standby_body["upgrade"]["total"] == 0
    assert standby_body["standby"]["total"] == 0

    groups = client.get(f"/flights/{flight}/passengers/groups", params=params)
    assert groups.status_code == 200, groups.text
    assert groups.json()["fetchedAt"] == LATEST_FETCHED_AT
    assert groups.json()["totalGroups"] == 0

    by_pnr = client.get(f"/flights/{flight}/passengers/ABC123", params={"date": DATE})
    assert by_pnr.status_code == 200, by_pnr.text
    matched = by_pnr.json()["passengers"]
    assert len(matched) == 1
    assert matched[0]["lastName"] == "CURRENT"
    assert matched[0]["isBoarded"] is True
    assert matched[0]["seat"] == "14C"

    status = client.get(f"/flights/{flight}/status", params=params)
    assert status.status_code == 200, status.text
    assert status.json()["fetchedAt"] == LATEST_FETCHED_AT
    assert status.json()["status"] == "FINAL"
    assert status.json()["gate"] == "B22"
    assert status.json()["terminal"] == "T2"

    history = client.get(f"/flights/{flight}/status/history", params=params)
    assert history.status_code == 200, history.text
    newest = history.json()[0]
    assert newest["fetchedAt"] == LATEST_FETCHED_AT
    assert newest["gate"] == "B22"
    assert newest["status"] == "FINAL"

    reservations = client.get(f"/flights/{flight}/reservations", params=params)
    assert reservations.status_code == 200, reservations.text
    res_body = reservations.json()
    assert res_body["fetchedAt"] == LATEST_FETCHED_AT
    assert res_body["totalResults"] == 1
    assert res_body["reservations"][0]["numberInParty"] == 2
    assert res_body["reservations"][0]["passengers"][0]["lastName"] == "CURRENT"
    assert res_body["reservations"][0]["passengers"][0]["nationality"] == "BH"

    one_pnr = client.get(
        f"/flights/{flight}/reservations/ABC123", params={"date": DATE})
    assert one_pnr.status_code == 200, one_pnr.text
    assert one_pnr.json()["reservations"][0]["numberInParty"] == 2
    assert one_pnr.json()["reservations"][0]["passengers"][0]["lastName"] == "CURRENT"

    dashboard = client.get(f"/flights/{flight}/dashboard", params=params)
    assert dashboard.status_code == 200, dashboard.text
    dash = dashboard.json()
    assert dash["fetchedAt"] == LATEST_FETCHED_AT
    assert dash["flightStatus"]["status"] == "FINAL"
    assert dash["flightStatus"]["gate"] == "B22"
    assert dash["flightStatus"]["terminal"] == "T2"
    assert dash["passengerSummary"]["totalPassengers"] == 2
    assert dash["analysis"]["boarded"] == 1
    assert dash["analysis"]["totalMale"] == 1
    assert dash["analysis"]["totalFemale"] == 0
    assert dash["overview"]["soulsOnBoard"] == 1
    assert dash["stateSummary"]["boarded"]["totalPassengers"] == 1

    tree = client.get(f"/flights/{flight}/tree", params=params)
    assert tree.status_code == 200, tree.text
    boarded_card = next(card for card in tree.json()["statusCards"] if card["id"] == "boarded")
    assert boarded_card["value"] == 1
    assert boarded_card["subLabel"] == "of 2"

    audit = client.get(f"/flights/{flight}/audit", params=params)
    assert audit.status_code == 200, audit.text
    assert audit.json()["fetchedAt"] == LATEST_FETCHED_AT

    listed = client.get("/flights", params={"date": DATE})
    assert listed.status_code == 200, listed.text
    flight_row = next(row for row in listed.json() if row["flightNumber"] == flight)
    assert flight_row["fetchedAt"] == LATEST_FETCHED_AT
    assert flight_row["gate"] == "B22"
    assert flight_row["status"] == "FINAL"
    assert flight_row["operationalSummary"]["boarded"] == 1
    assert flight_row["passengerSummary"]["totalPassengers"] == 2

    journey = client.get(f"/flights/{flight}/phase-journey", params=params)
    assert journey.status_code == 200, journey.text
    phases = journey.json()["phases"]
    current_phase = next(phase for phase in phases if phase["sequenceNumber"] == 2)
    assert current_phase["summary"]["totalPassengers"] == 2
    assert current_phase["summary"]["totalMale"] == 1
    assert current_phase["summary"]["boarded"]["totalPassengers"] == 1

    data_audit = client.get(
        f"/data-audit/{flight}/passengers", params=params)
    assert data_audit.status_code == 200, data_audit.text
    assert data_audit.json()["mongoFound"] is True
    assert _row(data_audit.json()["rows"], "Boarded")["mongoValue"] == "1"
    assert _row(data_audit.json()["rows"], "Total Passengers")["mongoValue"] == "2"

    analytics = client.get(
        "/analytics/network",
        params={"date_from": DATE, "date_to": DATE, "flight_number": flight},
    )
    assert analytics.status_code == 200, analytics.text
    analytics_body = analytics.json()
    assert analytics_body["overview"]["totalPassengers"] == 2
    assert analytics_body["operationalRates"]["boarded"] == 1
    assert analytics_body["operationalRates"]["totalPassengers"] == 2
    nationalities = {
        item["nationality"] for item in analytics_body["nationalityDistribution"]
    }
    assert "BH" in nationalities
    assert "XX" not in nationalities

    distribution = client.get("/compare/status-distribution", params={"date": DATE})
    assert distribution.status_code == 200, distribution.text
    cabin = distribution.json()["byCabin"]["Y"]
    assert distribution.json()["grandTotal"] == 2
    assert cabin["total"] == 2
    assert cabin["boarded"] == 1

    compared = client.get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": flight},
    )
    assert compared.status_code == 200, compared.text
    compared_flight = next(
        row for row in compared.json()["flights"] if row["flightNumber"] == flight
    )
    assert compared_flight["totalPax"] == 2
    assert compared_flight["boarded"] == 1

    mismatches = client.get("/compare/pnr-mismatches", params={"date": DATE})
    assert mismatches.status_code == 200, mismatches.text
    flight_mismatch = next(
        row for row in mismatches.json()["flights"]
        if row["flightNumber"] == flight
    )
    assert "NEW999" in flight_mismatch["inPaxNotReservations"]


def _restore(client, flight, sequence, params):
    # No Authorization header, cookie, or API key. The caller is anonymous.
    return client.post(
        f"/flights/{flight}/snapshots/{sequence}/restore",
        params=params,
    )


def _older_sequence(client, flight, params):
    listed = client.get(f"/flights/{flight}/snapshots", params=params)
    assert listed.status_code == 200, listed.text
    sequences = [
        snap["sequenceNumber"]
        for snap in listed.json()
        if snap.get("snapshotType") == "passenger_list"
    ]
    assert sequences
    return min(sequences)


def test_restore_with_origin_and_date_keeps_newest_ingest(api, db):
    _seed_two_generations(db, FLIGHT)
    _insert_generation(
        db, "2099", "2026-03-20", 1, LATEST_FETCHED_AT,
        _current_passengers(), _current_reservations(),
        "FINAL", "B22", "T2",
    )
    _assert_current_operational_view(api, FLIGHT)
    before = _counts(db, FLIGHT)
    before_times = _fetched_at_values(db, FLIGHT)
    other_before = _counts(db, "2099")

    older = _older_sequence(api, FLIGHT, {"origin": ORIGIN, "date": DATE})
    assert older == 1
    _restore(api, FLIGHT, older, {"origin": ORIGIN, "date": DATE})

    _assert_current_operational_view(api, FLIGHT)
    assert _counts(db, FLIGHT) == before
    assert _fetched_at_values(db, FLIGHT) == before_times
    assert _counts(db, "2099") == other_before
    assert _fetched_at_values(db, "2099") == {
        "passenger_list": [LATEST_FETCHED_AT],
        "flight_status": [LATEST_FETCHED_AT],
        "reservations": [LATEST_FETCHED_AT],
    }


def test_restore_without_origin_or_date_keeps_newest_ingest(api, db):
    _seed_two_generations(db, FLIGHT)
    _assert_current_operational_view(api, FLIGHT)
    before = _counts(db, FLIGHT)
    before_times = _fetched_at_values(db, FLIGHT)

    listed = api.get(f"/flights/{FLIGHT}/snapshots")
    assert listed.status_code == 200, listed.text
    older = min(
        snap["sequenceNumber"]
        for snap in listed.json()
        if snap.get("snapshotType") == "passenger_list"
    )
    assert older == 1
    _restore(api, FLIGHT, older, None)

    _assert_current_operational_view(api, FLIGHT)
    assert _counts(db, FLIGHT) == before
    assert _fetched_at_values(db, FLIGHT) == before_times


def test_restore_without_filters_does_not_promote_another_date(api, db):
    _insert_generation(
        db, OTHER_FLIGHT, OTHER_DATE, 4, OTHER_FETCHED_AT,
        [_passenger("HISTORICAL", "DANA", "OLD111", "1A", False, False)],
        _old_reservations(),
        "OPEN", "A1", "T1",
    )
    _insert_generation(
        db, OTHER_FLIGHT, DATE, 2, LATEST_FETCHED_AT,
        _current_passengers(), _current_reservations(),
        "FINAL", "C3", "T3",
    )
    before = _counts(db, OTHER_FLIGHT)
    before_times = _fetched_at_values(db, OTHER_FLIGHT)

    listed = api.get(f"/flights/{OTHER_FLIGHT}/snapshots")
    assert listed.status_code == 200, listed.text
    highest = max(snap["sequenceNumber"] for snap in listed.json())
    assert highest == 4
    _restore(api, OTHER_FLIGHT, highest, None)

    passengers = api.get(f"/flights/{OTHER_FLIGHT}/passengers")
    assert passengers.status_code == 200, passengers.text
    body = passengers.json()
    assert body["fetchedAt"] == LATEST_FETCHED_AT
    assert body["departureDate"] == DATE
    assert {pax["lastName"] for pax in body["passengers"]} == {"CURRENT", "EXTRA"}

    status = api.get(f"/flights/{OTHER_FLIGHT}/status")
    assert status.status_code == 200, status.text
    assert status.json()["fetchedAt"] == LATEST_FETCHED_AT
    assert status.json()["gate"] == "C3"
    assert status.json()["status"] == "FINAL"
    assert status.json()["departureDate"] == DATE

    same_day = api.get(
        f"/flights/{OTHER_FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert same_day.status_code == 200, same_day.text
    assert same_day.json()["fetchedAt"] == LATEST_FETCHED_AT
    assert {pax["lastName"] for pax in same_day.json()["passengers"]} == {
        "CURRENT", "EXTRA"}

    assert _counts(db, OTHER_FLIGHT) == before
    assert _fetched_at_values(db, OTHER_FLIGHT) == before_times
