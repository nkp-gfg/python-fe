"""Failing spec for cabin counters omitted from flight change detection.

Ingestion stores every cabin counter from ACS_FlightDetailRS. Change detection
must emit a COUNT_CHANGE for each counter that differs between two snapshots
of the same flight, and those records must show up on the change and activity
feeds. These tests fail while authorized, available, thru, local, meals,
revenue, and nonRevenue are left out of the comparison.

POST /flights/ingest/batch and the feeder CLI call the same run_feeder path
and are not given a separate test. Passenger-list cabinSummary authorized and
available counts are also stored and not compared; they are not asserted here.

Run from the repository root:

    python -m backend.tests.test_capacity_change_detection
"""

import asyncio
import copy
from pathlib import Path

from backend.api.routes.changes import get_activity_feed, get_changes
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.feeder import storage
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"
)

_FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list, direction=None):
        if direction is None and isinstance(key_or_list, (list, tuple)):
            spec = list(key_or_list)
        else:
            spec = [(key_or_list, 1 if direction is None else direction)]
        self._docs = _sort_docs(self._docs, spec)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        if "_id" not in stored:
            stored["_id"] = self._next_id
            self._next_id += 1
        self.docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def find_one(self, filt=None, projection=None, sort=None, **kwargs):
        if "projection" in kwargs and projection is None:
            projection = kwargs["projection"]
        matches = [doc for doc in self.docs if _matches(doc, filt)]
        if sort:
            matches = _sort_docs(matches, sort)
        if not matches:
            return None
        return _project(matches[0], projection)

    def find(self, filt=None, projection=None, sort=None, **kwargs):
        if "projection" in kwargs and projection is None:
            projection = kwargs["projection"]
        matches = [_project(doc, projection) for doc in self.docs if _matches(doc, filt)]
        cursor = _Cursor(matches)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, filt):
                _apply_update(doc, update, inserting=False)
                return
        if upsert:
            created = {}
            _apply_update(created, update, inserting=True)
            self.insert_one(created)


class _MemoryDb(dict):
    def __getitem__(self, name):
        if name not in self:
            dict.__setitem__(self, name, _Collection())
        return dict.__getitem__(self, name)


def _matches(doc, filt):
    if not filt:
        return True
    for key, expected in filt.items():
        if doc.get(key) != expected:
            return False
    return True


def _sort_docs(docs, spec):
    ordered = list(docs)
    for field, direction in reversed(list(spec)):
        ordered.sort(
            key=lambda doc, field=field: (
                doc.get(field) is None,
                "" if doc.get(field) is None else doc.get(field),
            ),
            reverse=direction < 0,
        )
    return ordered


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    included = {key for key, flag in projection.items() if flag and key != "_id"}
    projected = {}
    if projection.get("_id", 1) and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key in included:
        if key in doc:
            projected[key] = copy.deepcopy(doc[key])
    return projected


def _apply_update(doc, update, inserting):
    for key, value in update.get("$set", {}).items():
        doc[key] = copy.deepcopy(value)
    if inserting:
        for key, value in update.get("$setOnInsert", {}).items():
            doc[key] = copy.deepcopy(value)
    for key, value in update.get("$inc", {}).items():
        doc[key] = doc.get(key, 0) + value


class _ScriptedSabre:
    """Stand-in for the SOAP transport only.

    Responses are the body dictionaries produced by SabreClient._parse_xml and
    SabreClient._extract_body. No session is opened and no credential is read.
    """

    flight_bodies = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        body = _ScriptedSabre.flight_bodies.pop(0)
        return body, "<ACS_FlightDetailRS/>", _meta()

    def get_passenger_list(self, *args, **kwargs):
        return {}, "<GetPassengerListRS/>", _meta()

    def get_reservations(self, *args, **kwargs):
        return {}, "<Trip_SearchRS/>", _meta()

    def get_trip_report(self, *args, **kwargs):
        return {}, "<Trip_ReportsRS/>", _meta()

    def verify_flight_details(self, *args, **kwargs):
        return (
            {"ApplicationResults": {"@status": "Complete"}},
            "<VerifyFlightDetailsRS/>",
            _meta(),
        )


def _meta():
    return {
        "requestXml": "<request/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": None,
    }


def _parse_flight_detail(xml_text):
    """Same parse and body extraction the Sabre client uses after a SOAP post."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _load_sample_body():
    xml_text = _SAMPLE.read_text(encoding="utf-8")
    return _parse_flight_detail(xml_text)


def _cabin_counts(body):
    counts = body.get("PassengerCounts")
    if isinstance(counts, dict):
        counts = [counts]
    return counts or []


def _set_cabin_tag(body, cabin, tag, value):
    for counts in _cabin_counts(body):
        if counts.get("@classOfService") == cabin:
            counts[tag] = str(value)
            return
    raise AssertionError(f"class {cabin} not found on parsed flight detail")


def _bind_db(db):
    storage.init_db(db)
    # The activity and change routes import get_db by name. Production shares
    # that database with feeder storage through the API lifespan hook.
    import backend.api.database as database
    import backend.api.routes.changes as changes

    database.get_db = lambda: db
    changes.get_db = lambda: db


def _payload():
    return SabreIngestRequest(
        airline=_FLIGHT["airline"],
        flightNumber=_FLIGHT["flightNumber"],
        origin=_FLIGHT["origin"],
        departureDate=_FLIGHT["departureDate"],
        departureDateTime="2026-03-19T17:40:00",
    )


def _latest_flight_status(db):
    cursor = db["snapshots"].find(
        {
            "flightNumber": _FLIGHT["flightNumber"],
            "origin": _FLIGHT["origin"],
            "departureDate": _FLIGHT["departureDate"],
            "snapshotType": "flight_status",
        },
        sort=[("sequenceNumber", -1)],
    ).limit(1)
    snaps = list(cursor)
    assert snaps, "ingestion stored no flight-status snapshot"
    return snaps[0]


def _count_changes():
    changes = get_changes(
        _FLIGHT["flightNumber"],
        origin=_FLIGHT["origin"],
        date=_FLIGHT["departureDate"],
        change_type="COUNT_CHANGE",
        limit=100,
    )
    return [change for change in changes if change["changeType"] == "COUNT_CHANGE"]


def _assert_count_change(changes, field, old_value, new_value):
    matches = [change for change in changes if change.get("field") == field]
    assert matches, f"no COUNT_CHANGE for {field}; saw {[c.get('field') for c in changes]}"
    assert matches[0]["oldValue"] == str(old_value)
    assert matches[0]["newValue"] == str(new_value)
    assert matches[0]["flightNumber"] == _FLIGHT["flightNumber"]
    assert matches[0]["origin"] == _FLIGHT["origin"]
    assert matches[0]["departureDate"] == _FLIGHT["departureDate"]


def _feed_fields():
    feed = get_activity_feed(date=_FLIGHT["departureDate"], limit=50, categories=None)
    fields = []
    for event in feed["events"]:
        if event["eventType"] == "COUNT_CHANGE":
            assert event["category"] == "capacity"
            assert event["flightNumber"] == _FLIGHT["flightNumber"]
            fields.append(event["description"])
    return fields


def _mounted_post_routes(app, path):
    """Return (route, include-dependencies) for POST routes at path.

    FastAPI 0.165 keeps included routers nested, so app.routes does not list
    /flights/ingest directly.
    """
    found = []
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            include_deps = list(route.include_context.dependencies or [])
            for inner in route.original_router.routes:
                methods = getattr(inner, "methods", set()) or set()
                if getattr(inner, "path", None) == path and "POST" in methods:
                    found.append((inner, include_deps))
        else:
            methods = getattr(route, "methods", set()) or set()
            if getattr(route, "path", None) == path and "POST" in methods:
                found.append((route, []))
    return found


def test_ingest_route_has_no_credentials():
    """POST /flights/ingest is mounted with no security dependency."""
    from backend.api.main import app

    mounted = _mounted_post_routes(app, "/flights/ingest")
    assert len(mounted) == 1, mounted
    route, include_deps = mounted[0]
    assert route.endpoint is ingest_flight
    assert list(route.dependencies) == []
    assert include_deps == []
    assert list(app.router.dependencies) == []
    schema = app.openapi()
    operation = schema["paths"]["/flights/ingest"]["post"]
    assert "security" not in operation
    assert not schema.get("security")


def test_booked_change_is_still_recorded():
    """Control: a booked-count change already produces a COUNT_CHANGE."""
    db = _MemoryDb()
    _bind_db(db)
    first = _load_sample_body()
    second = copy.deepcopy(first)
    _set_cabin_tag(second, "Y", "Booked", 50)
    _ScriptedSabre.flight_bodies = [first, second]
    with _patched_client():
        _, second_result = _ingest_twice_unpatched()
    status = second_result["result"]["apis"]["flightStatus"]
    assert status["isDuplicate"] is False
    changes = _count_changes()
    _assert_count_change(changes, "passengerCounts.Y.booked", 56, 50)
    assert any("passengerCounts.Y.booked" in text for text in _feed_fields())


def test_authorized_available_and_meal_cuts_reach_the_change_feed():
    """A cut in authorized seats, open seats, or meals must be a COUNT_CHANGE.

    Booked, on-board, and boarding-pass totals stay at the sample values, so
    the only differences are the counters change detection currently skips.
    """
    db = _MemoryDb()
    _bind_db(db)
    first = _load_sample_body()
    economy = next(c for c in _cabin_counts(first) if c.get("@classOfService") == "Y")
    assert economy["Authorized"] == "180"
    assert economy["Available"] == "124"
    assert economy["Meals"] == "57"
    assert economy["Booked"] == "56"
    assert economy["TotalOnBoard"] == "56"
    assert economy["TotalBoardingPassIssued"] == "56"

    second = copy.deepcopy(first)
    _set_cabin_tag(second, "Y", "Authorized", 160)
    _set_cabin_tag(second, "Y", "Available", 104)
    _set_cabin_tag(second, "Y", "Meals", 40)

    with _patched_client():
        _ScriptedSabre.flight_bodies = [first, second]
        _, second_result = _ingest_twice_unpatched()

    latest = _latest_flight_status(db)
    stored = latest["data"]["passengerCounts"]["Y"]
    assert stored["authorized"] == 160
    assert stored["available"] == 104
    assert stored["meals"] == 40
    assert stored["booked"] == 56
    assert stored["onBoard"] == 56
    assert stored["boardingPasses"] == 56

    status = second_result["result"]["apis"]["flightStatus"]
    assert status["isDuplicate"] is False
    assert status["changesStored"] >= 3, status["changesStored"]

    changes = _count_changes()
    _assert_count_change(changes, "passengerCounts.Y.authorized", 180, 160)
    _assert_count_change(changes, "passengerCounts.Y.available", 124, 104)
    _assert_count_change(changes, "passengerCounts.Y.meals", 57, 40)
    fields = {change["field"] for change in changes}
    assert "passengerCounts.Y.booked" not in fields
    assert "passengerCounts.Y.onBoard" not in fields
    assert "passengerCounts.Y.boardingPasses" not in fields

    feed = _feed_fields()
    for field in (
        "passengerCounts.Y.authorized",
        "passengerCounts.Y.available",
        "passengerCounts.Y.meals",
    ):
        assert any(field in text for text in feed), feed


def test_thru_local_non_revenue_and_revenue_counters_are_recorded():
    """Every other stored cabin counter is part of the same comparison."""
    db = _MemoryDb()
    _bind_db(db)
    first = _load_sample_body()
    second = copy.deepcopy(first)
    _set_cabin_tag(second, "Y", "Thru", 4)
    _set_cabin_tag(second, "Y", "Local", 40)
    _set_cabin_tag(second, "Y", "NonRevenueLocal", 6)

    with _patched_client():
        _ScriptedSabre.flight_bodies = [first, second]
        _, second_result = _ingest_twice_unpatched()

    latest = _latest_flight_status(db)
    stored = latest["data"]["passengerCounts"]["Y"]
    assert stored["thru"] == 4
    assert stored["local"] == 40
    assert stored["nonRevenue"] == 6
    assert stored["revenue"] == 34  # local 40 minus non-revenue 6
    assert stored["booked"] == 56

    status = second_result["result"]["apis"]["flightStatus"]
    assert status["isDuplicate"] is False

    changes = _count_changes()
    _assert_count_change(changes, "passengerCounts.Y.thru", 0, 4)
    _assert_count_change(changes, "passengerCounts.Y.local", 55, 40)
    _assert_count_change(changes, "passengerCounts.Y.nonRevenue", 1, 6)
    _assert_count_change(changes, "passengerCounts.Y.revenue", 54, 34)
    fields = {change["field"] for change in changes}
    assert "passengerCounts.Y.booked" not in fields

    feed = _feed_fields()
    for field in (
        "passengerCounts.Y.thru",
        "passengerCounts.Y.local",
        "passengerCounts.Y.nonRevenue",
        "passengerCounts.Y.revenue",
    ):
        assert any(field in text for text in feed), feed


def test_each_omitted_counter_is_reported_on_its_own():
    """A fix that compares only one of the omitted counters still fails."""
    expectations = [
        ("authorized", "Authorized", 180, 150, None),
        ("available", "Available", 124, 90, None),
        ("thru", "Thru", 0, 3, None),
        ("local", "Local", 55, 48, ("revenue", 54, 47)),
        ("meals", "Meals", 57, 20, None),
        ("nonRevenue", "NonRevenueLocal", 1, 0, ("revenue", 54, 55)),
    ]
    for metric, tag, old, new, derived in expectations:
        db = _MemoryDb()
        _bind_db(db)
        first = _load_sample_body()
        second = copy.deepcopy(first)
        _set_cabin_tag(second, "Y", tag, new)
        with _patched_client():
            _ScriptedSabre.flight_bodies = [first, second]
            _ingest_twice_unpatched()
        changes = _count_changes()
        _assert_count_change(changes, f"passengerCounts.Y.{metric}", old, new)
        if derived:
            derived_metric, derived_old, derived_new = derived
            _assert_count_change(
                changes,
                f"passengerCounts.Y.{derived_metric}",
                derived_old,
                derived_new,
            )
        fields = {change["field"] for change in changes}
        assert "passengerCounts.Y.booked" not in fields


class _patched_client:
    def __enter__(self):
        import backend.feeder.runner as runner
        self._runner = runner
        self._original = runner.SabreClient
        runner.SabreClient = _ScriptedSabre
        return self

    def __exit__(self, exc_type, exc, tb):
        self._runner.SabreClient = self._original
        return False


def _ingest_twice_unpatched():
    first = asyncio.run(ingest_flight(_payload()))
    second = asyncio.run(ingest_flight(_payload()))
    return first, second


def _run_all():
    tests = [
        test_ingest_route_has_no_credentials,
        test_booked_change_is_still_recorded,
        test_authorized_available_and_meal_cuts_reach_the_change_feed,
        test_thru_local_non_revenue_and_revenue_counters_are_recorded,
        test_each_omitted_counter_is_reported_on_its_own,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} test(s) failed")
        raise SystemExit(1)
    print("ALL TESTS PASS")


if __name__ == "__main__":
    _run_all()
