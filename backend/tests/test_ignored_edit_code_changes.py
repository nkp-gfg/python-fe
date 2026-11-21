"""Passenger change detection must record operational Sabre edit codes.

These tests follow a saved GetPassengerListRS response through the same
parser, converter, and snapshot pipeline the ingestion route runs, then
read the records that the changes tab and activity feed would return.

They fail while wheelchair codes (WCHR, WCHS, WCHC) and APP are ignored,
and while a code that disappears is ignored. They pass once those
additions and removals are stored.

Not covered here:
- Other edit codes (ET, F, TKT, LC, CHD, INF, and the rest) are still ignored.
- A passenger who is added or removed entirely is recorded as
  PASSENGER_ADDED or PASSENGER_REMOVED, without a separate edit-code event.
- Reservation edit codes are never compared by diff_reservations.
- The Celery batch task is not started; it calls the same run_feeder
  pipeline exercised below.
"""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from backend.api.main import app
from backend.api.routes import changes as changes_route
from backend.api.routes.flights import _build_insights
from backend.api.routes.ingestion import router as ingestion_router
from backend.feeder.converter import _strip_ns, convert_passenger_list
from backend.feeder.differ import _pax_key
from backend.feeder.runner import _process_api_call
from backend.feeder.storage import _compute_checksum
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "sample_data" / "sample_xmlfiles"

# Committed Gulf Air manifests that actually contain each code.
SAMPLE_FOR_CODE = {
    "WCHR": "GetPassengerListRS_GF2056_DMM_2026-03-16.xml",
    "WCHS": "GetPassengerListRS_GF2130_DMM_2026-03-18.xml",
    "WCHC": "GetPassengerListRS_GF2754_DMM_2026-03-18.xml",
    "APP": "GetPassengerListRS_GF2056_DMM_2026-03-16.xml",
}
OPERATIONAL_CODES = ("WCHR", "WCHS", "WCHC", "APP")
WHEELCHAIR_CODES = {"WCHR", "WCHS", "WCHC"}


class MemoryStorage:
    """In-memory stand-in for the snapshot sequence in storage.py.

    Checksums use the real _compute_checksum. Sequence numbers, duplicate
    detection, and "previous snapshot" selection follow store_snapshot and
    get_previous_snapshot.
    """

    def __init__(self):
        self.snapshots: list[dict] = []
        self.changes: list[dict] = []
        self.passenger_lists: list[dict] = []

    def store_raw_request(self, **kwargs):
        return f"req-{len(self.snapshots) + 1}"

    def store_snapshot(
        self,
        request_id,
        airline,
        flight_number,
        origin,
        departure_date,
        snapshot_type,
        data,
    ):
        checksum = _compute_checksum(data)
        same = [
            snap
            for snap in self.snapshots
            if snap["flightNumber"] == flight_number
            and snap["origin"] == origin
            and snap["departureDate"] == departure_date
            and snap["snapshotType"] == snapshot_type
        ]
        seq = (same[-1]["sequenceNumber"] + 1) if same else 1
        snapshot_id = f"snap-{flight_number}-{snapshot_type}-{seq}"
        doc = {
            "snapshotId": snapshot_id,
            "requestId": request_id,
            "airline": airline,
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "snapshotType": snapshot_type,
            "sequenceNumber": seq,
            "checksum": checksum,
            "data": data,
        }
        self.snapshots.append(doc)
        is_duplicate = False
        if seq > 1:
            previous = next(
                snap
                for snap in self.snapshots
                if snap["sequenceNumber"] == seq - 1
                and snap["flightNumber"] == flight_number
                and snap["origin"] == origin
                and snap["departureDate"] == departure_date
                and snap["snapshotType"] == snapshot_type
            )
            if previous.get("checksum") == checksum:
                is_duplicate = True
        return snapshot_id, checksum, is_duplicate

    def get_previous_snapshot(self, flight_number, origin, departure_date, snapshot_type):
        matches = [
            snap
            for snap in self.snapshots
            if snap["flightNumber"] == flight_number
            and snap["origin"] == origin
            and snap["departureDate"] == departure_date
            and snap["snapshotType"] == snapshot_type
        ]
        matches.sort(key=lambda snap: snap["sequenceNumber"], reverse=True)
        return matches[1] if len(matches) >= 2 else None

    def store_changes(self, changes):
        if not changes:
            return 0
        self.changes.extend(changes)
        return len(changes)

    def store_passenger_list(self, doc):
        self.passenger_lists.append(doc)
        return "passenger-list"

    def store_flight_status(self, doc):
        return None

    def store_reservations(self, doc):
        return None

    def update_flight_state(self, *args, **kwargs):
        return None


class _ChangesCollection:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find(self, query, projection=None):
        self.queries.append(query)
        matched = []
        for doc in self.docs:
            if all(_lookup(doc, key) == value for key, value in query.items()):
                matched.append(copy.deepcopy(doc))

        class _Cursor(list):
            def sort(self, *args, **kwargs):
                return self

            def limit(self, count):
                del self[count:]
                return self

        return _Cursor(matched)


def _lookup(doc, path):
    """Resolve a Mongo dotted path such as passenger.pnr."""
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class _ChangesDb:
    def __init__(self, docs):
        self._changes = _ChangesCollection(docs)

    def __getitem__(self, name):
        if name != "changes":
            raise KeyError(name)
        return self._changes


def _load_body(filename: str) -> dict:
    xml_text = (SAMPLE_DIR / filename).read_text(encoding="utf-8")
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state.
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "GetPassengerListRS")


_BODY_CACHE: dict[str, dict] = {}


def _body_for(code: str) -> dict:
    filename = SAMPLE_FOR_CODE[code]
    if filename not in _BODY_CACHE:
        _BODY_CACHE[filename] = _load_body(filename)
    return _BODY_CACHE[filename]


def _local_name(key: str) -> str:
    return key.split(":")[-1]


def _walk_edit_code_values(node, code: str, mutate):
    """Visit every EditCode value. mutate(parent, key, value) may replace it."""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if _local_name(key) == "EditCode":
                mutate(node, key, value)
            else:
                _walk_edit_code_values(value, code, mutate)
    elif isinstance(node, list):
        for item in node:
            _walk_edit_code_values(item, code, mutate)


def _strip_edit_code(node, code: str) -> int:
    removed = 0

    def mutate(parent, key, value):
        nonlocal removed
        if isinstance(value, list):
            kept = []
            for item in value:
                if item == code:
                    removed += 1
                else:
                    kept.append(item)
            parent[key] = kept
        elif value == code:
            parent[key] = []
            removed += 1

    _walk_edit_code_values(node, code, mutate)
    return removed


def _append_edit_code_once(node, code: str) -> bool:
    """Append code to the first EditCode list that does not already contain it."""

    def mutate(parent, key, value):
        nonlocal done
        if done:
            return
        if isinstance(value, list):
            if code not in value:
                parent[key] = [*value, code]
                done = True
        elif value != code:
            parent[key] = [value, code]
            done = True

    done = False
    _walk_edit_code_values(node, code, mutate)
    return done


def _flight_info(body: dict) -> dict:
    data = _strip_ns(body)
    itinerary = data["ItineraryInfo"]["Itinerary"]
    return {
        "airline": itinerary["Airline"],
        "flightNumber": str(itinerary["Flight"]),
        "origin": itinerary["Origin"],
        "departureDate": itinerary["DepartureDate"],
    }


def _ingest(memory: MemoryStorage, body: dict, flight_info: dict) -> dict:
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        flight_info,
        body,
        "<response/>",
        {"requestXml": "<request/>", "httpStatus": 200, "durationMs": 1},
        convert_passenger_list,
        (
            body,
            flight_info["airline"],
            flight_info["flightNumber"],
            flight_info["departureDate"],
            flight_info["origin"],
        ),
    )


def _unique_passengers(passengers: list[dict]) -> dict[str, dict]:
    counts = Counter(_pax_key(passenger) for passenger in passengers)
    return {
        _pax_key(passenger): passenger
        for passenger in passengers
        if counts[_pax_key(passenger)] == 1
    }


def _code_transitions(before_doc: dict, after_doc: dict, code: str, direction: str):
    before = _unique_passengers(before_doc.get("passengers", []))
    after = _unique_passengers(after_doc.get("passengers", []))
    found = []
    for key in before.keys() & after.keys():
        before_codes = set(before[key].get("editCodes") or [])
        after_codes = set(after[key].get("editCodes") or [])
        if direction == "added" and code in after_codes - before_codes:
            found.append(after[key])
        elif direction == "removed" and code in before_codes - after_codes:
            found.append(before[key])
    return found


def _matches(change: dict, passenger: dict, code: str, direction: str) -> bool:
    recorded = change.get("passenger") or {}
    if recorded.get("pnr") != passenger.get("pnr"):
        return False
    if recorded.get("lastName") != passenger.get("lastName"):
        return False
    if recorded.get("firstName") != passenger.get("firstName"):
        return False
    if change.get("field") != "editCodes":
        return False
    if direction == "added":
        return change.get("newValue") == code
    return change.get("oldValue") == code


@pytest.fixture
def memory(monkeypatch):
    store = MemoryStorage()
    import backend.feeder.runner as runner

    for name in (
        "store_raw_request",
        "store_snapshot",
        "get_previous_snapshot",
        "store_changes",
        "store_passenger_list",
        "store_flight_status",
        "store_reservations",
        "update_flight_state",
    ):
        monkeypatch.setattr(runner.storage, name, getattr(store, name))
    return store


def _run_pair(memory: MemoryStorage, before_body: dict, after_body: dict):
    flight_info = _flight_info(after_body)
    first = _ingest(memory, before_body, flight_info)
    second = _ingest(memory, after_body, flight_info)
    before_doc = memory.snapshots[0]["data"]
    after_doc = memory.snapshots[1]["data"]
    return flight_info, first, second, before_doc, after_doc


def _assert_code_recorded(memory, flight_info, passengers, code, direction):
    assert passengers, f"No passenger {direction} {code}; the sample was not transformed"
    missing = [
        passenger.get("pnr")
        for passenger in passengers
        if not any(_matches(change, passenger, code, direction) for change in memory.changes)
    ]
    stored_types = sorted({change.get("changeType") for change in memory.changes})
    assert not missing, (
        f"{len(missing)} of {len(passengers)} passengers {direction} {code} "
        f"with no editCodes change stored. Example PNR {missing[0]}. "
        f"Stored change types: {stored_types}"
    )

    for passenger in passengers:
        change = next(
            item for item in memory.changes if _matches(item, passenger, code, direction)
        )
        description = changes_route._describe_event(change)
        assert code in description, (
            f"Activity text for {code} does not name the code: {description}"
        )

    example = passengers[0]
    db = _ChangesDb(memory.changes)

    def _fake_get_db():
        return db

    original = changes_route.get_db
    changes_route.get_db = _fake_get_db
    try:
        listed = changes_route.get_passenger_changes(
            flight_info["flightNumber"],
            example.get("pnr"),
            date=flight_info["departureDate"],
            limit=1000,
        )
        feed = changes_route.get_activity_feed(
            date=flight_info["departureDate"],
            limit=200,
            categories=None,
        )
    finally:
        changes_route.get_db = original

    assert any(_matches(change, example, code, direction) for change in listed), (
        f"GET /flights/{{flight}}/changes/passenger/{{pnr}} did not return "
        f"the {direction} {code} event for {example.get('pnr')}"
    )
    # The feed has no change-type whitelist. A page cap of 200 can omit one
    # code when a snapshot drops hundreds of document codes at once.
    activity_queries = [
        query for query in db["changes"].queries if "passenger.pnr" not in query
    ]
    assert activity_queries
    assert "changeType" not in activity_queries[-1]
    feed_names_code = any(code in (event.get("description") or "") for event in feed["events"])
    assert feed_names_code or len(memory.changes) > 200, (
        f"GET /activity/feed did not describe the {direction} {code} event"
    )


@pytest.mark.parametrize("code", OPERATIONAL_CODES)
def test_added_operational_edit_code_is_recorded(memory, code):
    original = _body_for(code)
    without_code = copy.deepcopy(original)
    removed = _strip_edit_code(without_code, code)
    assert removed > 0, f"{code} was not present in {SAMPLE_FOR_CODE[code]}"

    flight_info, _first, second, before_doc, after_doc = _run_pair(
        memory, without_code, original
    )

    assert second["isDuplicate"] is False
    assert memory.snapshots[0]["checksum"] != memory.snapshots[1]["checksum"]
    gained = _code_transitions(before_doc, after_doc, code, "added")
    assert gained, f"Converter dropped {code} before change detection"
    if code in WHEELCHAIR_CODES:
        insights = _build_insights(after_doc["passengers"], None, None, None, None)
        assert insights["wheelchairTypes"].get(code, 0) == len(
            [
                passenger
                for passenger in after_doc["passengers"]
                if code in (passenger.get("editCodes") or [])
            ]
        )

    _assert_code_recorded(memory, flight_info, gained, code, "added")


@pytest.mark.parametrize("code", OPERATIONAL_CODES)
def test_removed_operational_edit_code_is_recorded(memory, code):
    original = _body_for(code)
    without_code = copy.deepcopy(original)
    removed = _strip_edit_code(without_code, code)
    assert removed > 0, f"{code} was not present in {SAMPLE_FOR_CODE[code]}"

    flight_info, _first, second, before_doc, after_doc = _run_pair(
        memory, original, without_code
    )

    assert second["isDuplicate"] is False
    lost = _code_transitions(before_doc, after_doc, code, "removed")
    assert lost, f"Converter dropped {code} before change detection"
    _assert_code_recorded(memory, flight_info, lost, code, "removed")


def test_added_loyalty_code_is_still_recorded(memory):
    """Control: the pipeline already stores an added loyalty code."""
    original = _body_for("WCHR")
    with_loyalty = copy.deepcopy(original)
    assert _append_edit_code_once(with_loyalty, "FF")

    flight_info, _first, second, before_doc, after_doc = _run_pair(
        memory, original, with_loyalty
    )
    assert second["isDuplicate"] is False
    gained = _code_transitions(before_doc, after_doc, "FF", "added")
    assert len(gained) == 1
    _assert_code_recorded(memory, flight_info, gained, "FF", "added")
    assert any(change.get("changeType") == "LOYALTY_STATUS_ADDED" for change in memory.changes)


def test_removed_loyalty_and_document_codes_are_recorded(memory):
    """The removed-code set is computed and then ignored for every code."""
    original = _body_for("APP")
    before = copy.deepcopy(original)
    after = copy.deepcopy(original)
    assert _append_edit_code_once(before, "FF")
    assert _strip_edit_code(after, "DOCS") > 0

    flight_info, _first, second, before_doc, after_doc = _run_pair(memory, before, after)
    assert second["isDuplicate"] is False

    lost_loyalty = _code_transitions(before_doc, after_doc, "FF", "removed")
    lost_docs = _code_transitions(before_doc, after_doc, "DOCS", "removed")
    assert lost_loyalty and lost_docs
    _assert_code_recorded(memory, flight_info, lost_loyalty, "FF", "removed")
    _assert_code_recorded(memory, flight_info, lost_docs, "DOCS", "removed")


def test_ingest_routes_have_no_credential_dependency():
    """POST /flights/ingest is reachable with no credential, as the API collection states."""
    ingest_paths = {"/flights/ingest", "/flights/ingest/batch"}
    seen = set()
    assert ingestion_router.dependencies == []
    for route in ingestion_router.routes:
        if isinstance(route, APIRoute) and route.path in ingest_paths and "POST" in route.methods:
            seen.add(route.path)
            assert route.dependencies == []
            assert route.dependant.dependencies == []
    assert seen == ingest_paths

    mounted = [
        route
        for route in app.routes
        if getattr(route, "original_router", None) is ingestion_router
    ]
    assert len(mounted) == 1
    assert mounted[0].include_context.dependencies == []
    assert mounted[0].include_context.prefix == ""

    middleware_names = [middleware.cls.__name__.lower() for middleware in app.user_middleware]
    assert middleware_names
    assert not any(
        "auth" in name or "bearer" in name or "credential" in name for name in middleware_names
    )
