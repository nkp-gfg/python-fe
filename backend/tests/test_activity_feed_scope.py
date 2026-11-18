"""Activity feed must not sort the whole changes collection.

GET /activity/feed with no departure date currently matches every change,
sorts that collection by detectedAt, and returns passenger record locators
and names from flights the caller did not name. These tests exercise that
request against a local MongoDB using the indexes the application creates.

They fail while that behavior is present. They pass when a request that
omits the date is refused before any changes query, or is limited to one
departure date or a detectedAt window of at most 24 hours, and the query
that does run is served by an index (no collection scan, no in-memory sort
of the collection). The result limit stays at most 200.

Flight-scoped change routes always include a flight number, and the
analytics change queries require a date range. Those are different
operations and are not covered here.
"""

import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

_TEST_URI = os.environ.get(
    "FALCONEYE_TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")
_parsed = urlparse(_TEST_URI)
if _parsed.hostname not in {"127.0.0.1", "localhost"}:
    raise RuntimeError(
        "Activity-feed tests only connect to a local MongoDB")
if _parsed.username or _parsed.password:
    raise RuntimeError(
        "Activity-feed tests do not accept credentials in the MongoDB URI")
os.environ["MONGODB_URI"] = _TEST_URI

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from backend.api.main import app
from backend.feeder import storage as feeder_storage
from backend.feeder.differ import _make_change

ON_DATE = "2026-04-02"
OFF_DATE = "2024-01-15"
ON_DETECTED = "2026-04-02T09:00:00Z"
OFF_DETECTED = "2024-01-15T10:00:00Z"
OFF_PNR = "OFF0001"
OFF_BOARDED_PNR = "OFFBRD1"
ON_PNR = "NEW1111"
ON_BOARDED_PNR = "NEW2222"
MAX_WINDOW = timedelta(hours=24)
MAX_LIMIT = 200


def _change(flight, date, detected, pnr, change_type):
    """Build a change document with the same passenger shape the feeder stores."""
    doc = _make_change(
        {"flightNumber": flight, "origin": "BAH", "departureDate": date},
        change_type,
        "before-snap",
        "after-snap",
        passenger={"pnr": pnr, "lastName": "PAX" + pnr, "firstName": "TEST"},
    )
    doc["detectedAt"] = detected
    return doc


def _seed_documents():
    docs = []
    for i in range(24):
        docs.append(_change(
            f"{3000 + i}", OFF_DATE, OFF_DETECTED, f"OFF{i:04d}", "CHECKED_IN"))
    docs.append(_change("3099", OFF_DATE, OFF_DETECTED, OFF_BOARDED_PNR, "BOARDED"))
    docs.append(_change("1111", ON_DATE, ON_DETECTED, ON_PNR, "CHECKED_IN"))
    docs.append(_change(
        "2222", ON_DATE, "2026-04-02T09:05:00Z", ON_BOARDED_PNR, "BOARDED"))
    return docs


def _parse_detected(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _filter_is_bounded(query_filter):
    """A feed query may name one departure date or a detectedAt span of <= 24h."""
    if not isinstance(query_filter, dict) or not query_filter:
        return False

    departure = query_filter.get("departureDate")
    if isinstance(departure, str) and len(departure) == 10:
        return True
    if isinstance(departure, dict):
        lower = departure.get("$gte") or departure.get("$gt")
        upper = departure.get("$lte") or departure.get("$lt")
        if isinstance(lower, str) and lower == upper:
            return True

    detected = query_filter.get("detectedAt")
    if isinstance(detected, dict):
        lower = _parse_detected(detected.get("$gte") or detected.get("$gt"))
        upper = _parse_detected(detected.get("$lte") or detected.get("$lt"))
        if lower and upper and timedelta(0) <= (upper - lower) <= MAX_WINDOW:
            return True
    return False


def _change_finds(db):
    finds = []
    for entry in db["system.profile"].find():
        command = entry.get("command") or {}
        if command.get("find") == "changes":
            finds.append(entry)
    return finds


def _summarize(finds):
    summarized = []
    for entry in finds:
        command = entry.get("command") or {}
        summarized.append({
            "filter": command.get("filter"),
            "sort": command.get("sort"),
            "limit": command.get("limit"),
            "planSummary": entry.get("planSummary"),
            "docsExamined": entry.get("docsExamined"),
            "hasSortStage": entry.get("hasSortStage"),
        })
    return summarized


def _begin_profile(db):
    db.command("profile", 0)
    db["system.profile"].drop()
    db.command("profile", 2)


def _assert_feed_does_not_sweep(response, finds, total_docs, forbidden_pnrs):
    summary = _summarize(finds)
    for pnr in forbidden_pnrs:
        assert pnr not in response.text, (
            f"passenger record locator {pnr} was returned without a single "
            f"departure date or a detectedAt window of at most 24 hours; "
            f"queries={summary}"
        )
    if response.status_code >= 400:
        assert finds == [], (
            "the feed queried changes before refusing the request: "
            f"{summary}"
        )
        return

    assert response.status_code == 200, response.text
    assert finds, "a successful activity feed did not query changes"
    for entry in finds:
        command = entry.get("command") or {}
        query_filter = command.get("filter") or {}
        assert _filter_is_bounded(query_filter), (
            "activity feed query is not limited to one departure date or a "
            f"24h detectedAt window: {summary}"
        )
        assert command.get("limit", MAX_LIMIT + 1) <= MAX_LIMIT, summary
        plan = entry.get("planSummary") or ""
        assert "COLLSCAN" not in plan, (
            "activity feed sorted changes with a collection scan: "
            f"{summary}"
        )
        assert entry.get("hasSortStage") is not True, (
            "activity feed sorted changes in memory: " f"{summary}"
        )
        examined = entry.get("docsExamined", 0)
        assert examined < total_docs, (
            "activity feed examined every change document: " f"{summary}"
        )


@pytest.fixture(scope="module")
def feed():
    mongo = MongoClient(_TEST_URI, serverSelectionTimeoutMS=5_000)
    mongo.admin.command("ping")
    db = mongo["falconeye"]
    feeder_storage.init_db(db)
    try:
        db["flight_status"].drop_index("flight_lookup")
    except Exception:
        pass
    db["changes"].drop()
    feeder_storage.ensure_indexes()
    index_info = db["changes"].index_information()
    for name in ("chg_flight_lookup", "chg_snapshot", "chg_pnr", "chg_type"):
        assert name in index_info
    docs = _seed_documents()
    db["changes"].insert_many(docs)
    with TestClient(app) as http:
        yield {"http": http, "db": db, "total": len(docs)}
    mongo.close()


def _get(feed, **params):
    _begin_profile(feed["db"])
    # No Authorization header: the route is reachable without credentials.
    response = feed["http"].get("/activity/feed", params=params)
    finds = _change_finds(feed["db"])
    return response, finds


def test_omitted_date_does_not_sort_every_change_or_return_other_flights(feed):
    response, finds = _get(feed)
    _assert_feed_does_not_sweep(
        response,
        finds,
        feed["total"],
        forbidden_pnrs=[OFF_PNR, OFF_BOARDED_PNR, ON_PNR, ON_BOARDED_PNR],
    )


def test_limit_200_without_date_does_not_return_cross_flight_passengers(feed):
    response, finds = _get(feed, limit=200)
    _assert_feed_does_not_sweep(
        response,
        finds,
        feed["total"],
        forbidden_pnrs=[OFF_PNR, OFF_BOARDED_PNR, ON_PNR, ON_BOARDED_PNR],
    )


def test_category_without_date_does_not_return_other_flights(feed):
    response, finds = _get(feed, categories="boarding")
    _assert_feed_does_not_sweep(
        response,
        finds,
        feed["total"],
        forbidden_pnrs=[OFF_BOARDED_PNR, ON_BOARDED_PNR],
    )


def test_dated_feed_does_not_scan_or_sort_unrelated_changes(feed):
    response, finds = _get(feed, date=ON_DATE)
    assert response.status_code == 200, response.text
    body = response.json()
    returned = {
        (event.get("passenger") or {}).get("pnr")
        for event in body["events"]
    }
    assert returned == {ON_PNR, ON_BOARDED_PNR}
    assert OFF_PNR not in response.text
    assert OFF_BOARDED_PNR not in response.text
    summary = _summarize(finds)
    assert finds, summary
    on_date_count = 2
    for entry in finds:
        command = entry.get("command") or {}
        assert command.get("filter", {}).get("departureDate") == ON_DATE, summary
        assert command.get("limit", MAX_LIMIT + 1) <= MAX_LIMIT, summary
        plan = entry.get("planSummary") or ""
        assert "COLLSCAN" not in plan, summary
        assert entry.get("hasSortStage") is not True, summary
        assert entry.get("docsExamined", 0) <= on_date_count, summary


def test_category_and_date_does_not_scan_unrelated_changes(feed):
    response, finds = _get(feed, date=ON_DATE, categories="boarding")
    assert response.status_code == 200, response.text
    returned = {
        (event.get("passenger") or {}).get("pnr")
        for event in response.json()["events"]
    }
    assert returned == {ON_BOARDED_PNR}
    assert OFF_BOARDED_PNR not in response.text
    summary = _summarize(finds)
    assert finds, summary
    for entry in finds:
        command = entry.get("command") or {}
        query_filter = command.get("filter") or {}
        assert query_filter.get("departureDate") == ON_DATE, summary
        assert "COLLSCAN" not in (entry.get("planSummary") or ""), summary
        assert entry.get("hasSortStage") is not True, summary
        assert entry.get("docsExamined", 0) < feed["total"], summary
        assert command.get("limit", MAX_LIMIT + 1) <= MAX_LIMIT, summary
