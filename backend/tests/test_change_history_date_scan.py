"""Date-scoped change reads must not walk the rest of the change history.

An unauthenticated caller can pass one departure date to the comparison
change summary, the one-day network analytics view, and the activity feed.
Those handlers query ``changes`` by ``departureDate`` while every index on
that collection starts with another field, so MongoDB reads change documents
from every other date as well.

The assertions below call those routes with no credentials and read
MongoDB's profiler for the commands the handlers actually issued. They fail
while any of those reads examines more change documents or index keys than
the requested date contains. A leading ``(departureDate, ...)`` index, or a
summary written at ingestion time, keeps the same responses inside that bound.

Per-flight change routes are not covered. Their filter includes a flight
number, and ``chg_flight_lookup`` starts with that field. ``GET /activity/feed``
with no date is also not covered: that query has no departure-date predicate,
so a date index would not bound it. ``/compare/pnr-mismatches`` and
``/compare/status-distribution`` scan ``passenger_list`` rather than ``changes``.
"""

from __future__ import annotations

import os

# Pin the test database before the app loads its environment file.
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

import pytest
from pymongo import MongoClient
from starlette.testclient import TestClient

from backend.api.database import get_db
from backend.api.main import app
from backend.feeder import storage
from backend.feeder.storage import DB_NAME

MONGO_URI = os.environ["MONGODB_URI"]
TARGET_DATE = "2026-04-02"
EMPTY_DATE = "1999-12-31"
NOISE_DATES = [f"2019-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(56)]
NOISE_TYPES = (
    "BOARDED",
    "CHECKED_IN",
    "CABIN_CHANGE",
    "UPGRADE_CONFIRMED",
    "GATE_CHANGE",
)


def _noise_docs() -> list[dict]:
    docs = []
    serial = 0
    for departure_date in NOISE_DATES:
        for change_type in NOISE_TYPES:
            for copy in range(8):
                docs.append(
                    {
                        "flightNumber": str(1000 + (serial % 40)),
                        "origin": "BAH",
                        "departureDate": departure_date,
                        "changeType": change_type,
                        "beforeSnapshotId": f"before-{serial}",
                        "afterSnapshotId": f"after-{serial}",
                        "detectedAt": f"{departure_date}T00:{copy:02d}:00Z",
                        "passenger": {"pnr": f"N{serial}", "lastName": "Noise", "firstName": "Pax"},
                    }
                )
                serial += 1
    return docs


def _target_docs() -> list[dict]:
    rows = (
        ("100", "BOARDED", "00"),
        ("100", "BOARDED", "01"),
        ("200", "CHECKED_IN", "02"),
        ("100", "CABIN_CHANGE", "03"),
        ("300", "UPGRADE_CONFIRMED", "04"),
    )
    docs = []
    for flight_number, change_type, minute in rows:
        docs.append(
            {
                "flightNumber": flight_number,
                "origin": "BAH",
                "departureDate": TARGET_DATE,
                "changeType": change_type,
                "beforeSnapshotId": f"before-{flight_number}-{minute}",
                "afterSnapshotId": f"after-{flight_number}-{minute}",
                "detectedAt": f"{TARGET_DATE}T12:{minute}:00Z",
                "passenger": {"pnr": f"T{minute}", "lastName": "Target", "firstName": "Pax"},
            }
        )
    return docs


def _arm_profiler(db) -> None:
    db.command("profile", 0)
    db.system.profile.drop()
    db.command("profile", 2)


def _change_reads(db) -> list[dict]:
    return list(db.system.profile.find({"ns": f"{DB_NAME}.changes"}))


def _describe(op: dict) -> str:
    command = op.get("command") or {}
    head = command.get("pipeline") or command.get("filter") or command.get("query")
    return (
        f"plan={op.get('planSummary')!r} docsExamined={op.get('docsExamined')} "
        f"keysExamined={op.get('keysExamined')} op={op.get('op')!r} head={head!r}"
    )


def assert_change_reads_bounded(ops: list[dict], date_count: int) -> None:
    """Reads of ``changes`` may touch only documents for the requested date.

    ``date_count`` is the number of change documents stored for that date.
    A collection scan, or an index scan whose leading field is not the
    departure date, examines the other dates and fails this check. No reads
    at all is acceptable when the handler serves a summary maintained at
    ingestion time.
    """
    for op in ops:
        plan = op.get("planSummary") or ""
        if "docsExamined" not in op and "keysExamined" not in op and "planSummary" not in op:
            raise AssertionError(f"change read has no profiler stats: {sorted(op)}")
        docs = op.get("docsExamined")
        keys = op.get("keysExamined")
        if "COLLSCAN" in plan and docs is None:
            docs = date_count + 1
        docs = 0 if docs is None else docs
        keys = 0 if keys is None else keys
        assert docs <= date_count, (
            f"read examined {docs} change documents; the requested date has "
            f"{date_count}. {_describe(op)}"
        )
        assert keys <= date_count, (
            f"read examined {keys} index keys; the requested date has "
            f"{date_count}. {_describe(op)}"
        )


@pytest.fixture(scope="module")
def session():
    admin = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    admin.admin.command("ping")
    admin.drop_database(DB_NAME)
    noise = _noise_docs()
    target = _target_docs()
    with TestClient(app) as http:
        stored = storage.store_changes(noise + target)
        assert stored == len(noise) + len(target)
        db = get_db()
        assert db["changes"].count_documents({"departureDate": TARGET_DATE}) == len(target)
        assert db["changes"].count_documents({"departureDate": EMPTY_DATE}) == 0
        assert db["changes"].count_documents({}) == len(noise) + len(target)
        yield {
            "http": http,
            "db": db,
            "target_count": len(target),
            "noise_count": len(noise),
        }
    admin.drop_database(DB_NAME)
    admin.close()


def _get(http, path: str, params: dict) -> dict:
    response = http.get(path, params=params)
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    return response.json()


def test_change_summary_for_one_date_does_not_read_other_dates(session):
    http, db = session["http"], session["db"]
    _arm_profiler(db)
    body = _get(http, "/compare/change-summary", {"date": TARGET_DATE})
    first_reads = _change_reads(db)
    _arm_profiler(db)
    repeated = _get(http, "/compare/change-summary", {"date": TARGET_DATE})
    second_reads = _change_reads(db)

    counts = {row["changeType"]: row for row in body["byType"]}
    assert body["date"] == TARGET_DATE
    assert body["totalChanges"] == session["target_count"]
    assert counts["BOARDED"]["count"] == 2
    assert counts["BOARDED"]["affectedFlights"] == 1
    assert counts["CHECKED_IN"]["count"] == 1
    assert counts["CABIN_CHANGE"]["count"] == 1
    assert counts["UPGRADE_CONFIRMED"]["count"] == 1
    assert "GATE_CHANGE" not in counts
    assert repeated["totalChanges"] == body["totalChanges"]
    assert {row["changeType"]: row["count"] for row in repeated["byType"]} == {
        row["changeType"]: row["count"] for row in body["byType"]
    }
    assert_change_reads_bounded(first_reads, session["target_count"])
    assert_change_reads_bounded(second_reads, session["target_count"])


def test_change_summary_for_an_empty_date_does_not_scan_history(session):
    http, db = session["http"], session["db"]
    _arm_profiler(db)
    body = _get(http, "/compare/change-summary", {"date": EMPTY_DATE})

    assert body["date"] == EMPTY_DATE
    assert body["totalChanges"] == 0
    assert body["byType"] == []
    assert session["noise_count"] > session["target_count"]
    assert_change_reads_bounded(_change_reads(db), 0)


def test_network_analytics_for_one_day_does_not_scan_change_history(session):
    http, db = session["http"], session["db"]
    _arm_profiler(db)
    body = _get(
        http,
        "/analytics/network",
        {"date_from": TARGET_DATE, "date_to": TARGET_DATE},
    )

    distribution = {row["changeType"]: row["count"] for row in body["changeDistribution"]}
    assert body["totalChanges"] == session["target_count"]
    assert sum(distribution.values()) == session["target_count"]
    assert distribution["BOARDED"] == 2
    assert distribution["CABIN_CHANGE"] == 1
    assert distribution["UPGRADE_CONFIRMED"] == 1
    assert body["upgradeAnalytics"]["cabinChanges"] == 1
    assert body["upgradeAnalytics"]["upgradesConfirmed"] == 1
    assert_change_reads_bounded(_change_reads(db), session["target_count"])


def test_activity_feed_for_one_date_does_not_scan_change_history(session):
    http, db = session["http"], session["db"]
    _arm_profiler(db)
    body = _get(http, "/activity/feed", {"date": TARGET_DATE})

    assert body["count"] == session["target_count"]
    assert len(body["events"]) == session["target_count"]
    assert {event["departureDate"] for event in body["events"]} == {TARGET_DATE}
    assert_change_reads_bounded(_change_reads(db), session["target_count"])

    _arm_profiler(db)
    empty = _get(http, "/activity/feed", {"date": EMPTY_DATE})
    assert empty["events"] == []
    assert empty["count"] == 0
    assert_change_reads_bounded(_change_reads(db), 0)


def test_activity_feed_category_stays_inside_the_requested_date(session):
    http, db = session["http"], session["db"]
    _arm_profiler(db)
    body = _get(
        http,
        "/activity/feed",
        {"date": TARGET_DATE, "categories": "upgrade"},
    )

    assert body["count"] == 2
    assert {event["eventType"] for event in body["events"]} == {
        "CABIN_CHANGE",
        "UPGRADE_CONFIRMED",
    }
    assert {event["departureDate"] for event in body["events"]} == {TARGET_DATE}
    assert_change_reads_bounded(_change_reads(db), session["target_count"])
