"""Form-of-payment changes must be recorded when two reservation snapshots are ingested.

These tests exercise the feeder path that stores Trip Search snapshots and the
change-list and activity-feed readers that surface those events. They fail while
reservation diffing records only a party-size change.
"""

from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend.api import database as api_database
from backend.api.routes.changes import (
    _categorize_event,
    get_activity_feed,
    get_changes,
    get_changes_summary,
    get_flight_timeline,
)
from backend.api.routes.flights import _dashboard_cache, get_flight_dashboard
from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)
SAMPLE_LOCATOR = "DQAZHV"
REPLACEMENT_FOP = "FOP-REPLACEMENT-TOKEN"
FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


class _Clock:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-03-19T10:00:{self.n:02d}Z"


class _InsertResult:
    def __init__(self, inserted_id=None, inserted_ids=None):
        self.inserted_id = inserted_id
        self.inserted_ids = list(inserted_ids or [])


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, str):
            self._docs.sort(
                key=lambda doc: _sort_value(doc.get(key)),
                reverse=direction is not None and direction < 0,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(doc)
        return _InsertResult(inserted_id=doc["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertResult(inserted_ids=ids)

    def find(self, query=None, projection=None, sort=None):
        matched = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                matched.sort(
                    key=lambda doc, field=field: _sort_value(doc.get(field)),
                    reverse=direction < 0,
                )
        return _Cursor(matched)

    def find_one(self, query=None, projection=None, sort=None):
        found = list(self.find(query, projection=projection, sort=sort).limit(1))
        return found[0] if found else None

    def update_one(self, query, update, upsert=False):
        doc = self.find_one(query)
        created = False
        if doc is None:
            if not upsert:
                return
            doc = {}
            self.docs.append(doc)
            created = True
        if created:
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = deepcopy(value)
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _sort_value(value):
    if value is None:
        return ("",)
    return (value,)


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


@pytest.fixture
def memory_db(monkeypatch):
    db = _Database()
    storage.init_db(db)
    monkeypatch.setattr(api_database, "_db", db)
    clock = _Clock()
    monkeypatch.setattr("backend.feeder.converter._now_iso", clock)
    _dashboard_cache.clear()
    yield db
    _dashboard_cache.clear()


def _meta():
    return {
        "requestXml": "<Trip_SearchRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
    }


def _ingest(body, flight_info):
    return _process_api_call(
        "Reservations",
        "reservations",
        flight_info,
        body,
        "<Trip_SearchRS/>",
        _meta(),
        convert_reservations,
        (
            body,
            flight_info["airline"],
            flight_info["flightNumber"],
            flight_info["origin"],
            flight_info["departureDate"],
        ),
    )


def _parse_sample():
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        parsed = SabreClient._parse_xml(handle.read())
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _first_fp_text(body, locator):
    reservations = body["ReservationsList"]["Reservations"]["Reservation"]
    reservation = next(item for item in reservations if item.get("@Locator") == locator)
    elements = reservation["stl19:GetReservationRS"]["stl19:Reservation"][
        "stl19:OpenReservationElements"
    ]["or114:OpenReservationElement"]
    for element in elements:
        if element.get("@type") == "FP":
            return element["or114:FormOfPayment"]["or114:Other"]["or114:Text"]
    raise AssertionError(f"no form of payment element for {locator}")


def _set_first_fp_text(body, locator, text):
    reservations = body["ReservationsList"]["Reservations"]["Reservation"]
    reservation = next(item for item in reservations if item.get("@Locator") == locator)
    elements = reservation["stl19:GetReservationRS"]["stl19:Reservation"][
        "stl19:OpenReservationElements"
    ]["or114:OpenReservationElement"]
    for element in elements:
        if element.get("@type") == "FP":
            element["or114:FormOfPayment"]["or114:Other"]["or114:Text"] = text
            return
    raise AssertionError(f"no form of payment element for {locator}")


def _trip_body(locator, party, fop_text):
    return {
        "ReservationsList": {
            "@NumberResults": "1",
            "Reservations": {
                "Reservation": {
                    "@Locator": locator,
                    "GetReservationRS": {
                        "Reservation": {
                            "@numberInParty": str(party),
                            "BookingDetails": {"RecordLocator": locator},
                            "PassengerReservation": {},
                            "OpenReservationElements": {
                                "OpenReservationElement": {
                                    "@type": "FP",
                                    "FormOfPayment": {"Other": {"Text": fop_text}},
                                }
                            },
                        }
                    },
                }
            },
        }
    }


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _assert_no_raw_text(payload, forbidden):
    for found in _iter_strings(payload):
        for secret in forbidden:
            if secret and secret in found:
                raise AssertionError(
                    "payment event payload contains the raw form-of-payment text"
                )


def _payment_events(events):
    return [event for event in events if event.get("category") == "payment"]


def _safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _seed_passenger_list(db, flight_info):
    db["passenger_list"].insert_one(
        {
            "flightNumber": flight_info["flightNumber"],
            "origin": flight_info["origin"],
            "departureDate": flight_info["departureDate"],
            "fetchedAt": "2026-03-19T09:00:00Z",
            "passengers": [
                {
                    "pnr": SAMPLE_LOCATOR,
                    "lastName": "SAMPLE",
                    "firstName": "PAX",
                    "cabin": "Y",
                    "isRevenue": True,
                }
            ],
            "totalPassengers": 1,
        }
    )


def test_sample_payment_text_change_reaches_change_list_and_activity_feed(memory_db):
    """A later Trip Search snapshot whose stored card text changed must emit one payment event."""
    body = _parse_sample()
    original = _first_fp_text(body, SAMPLE_LOCATOR)
    if not original or original == REPLACEMENT_FOP:
        raise AssertionError("sample form-of-payment text was not available to replace")

    first = _ingest(body, FLIGHT)
    assert first["isDuplicate"] is False
    stored = convert_reservations(
        body, FLIGHT["airline"], FLIGHT["flightNumber"], FLIGHT["origin"], FLIGHT["departureDate"]
    )
    saved = next(item for item in stored["reservations"] if item["pnr"] == SAMPLE_LOCATOR)
    if saved["formOfPayment"] != original:
        raise AssertionError("converter did not store the Trip Search form-of-payment text")
    party = saved["numberInParty"]

    _set_first_fp_text(body, SAMPLE_LOCATOR, REPLACEMENT_FOP)
    second = _ingest(body, FLIGHT)
    assert second["isDuplicate"] is False

    updated = convert_reservations(
        body, FLIGHT["airline"], FLIGHT["flightNumber"], FLIGHT["origin"], FLIGHT["departureDate"]
    )
    updated_row = next(item for item in updated["reservations"] if item["pnr"] == SAMPLE_LOCATOR)
    assert updated_row["formOfPayment"] == REPLACEMENT_FOP
    assert updated_row["numberInParty"] == party

    _seed_passenger_list(memory_db, FLIGHT)
    _dashboard_cache.clear()
    dashboard = get_flight_dashboard(
        FLIGHT["flightNumber"],
        origin=FLIGHT["origin"],
        date=FLIGHT["departureDate"],
        snapshot_sequence=None,
    )
    payment_counts = dashboard["insights"]["paymentMethods"]
    assert payment_counts[REPLACEMENT_FOP] >= 1

    changes = _safe_call(
        get_changes,
        FLIGHT["flightNumber"],
        origin=FLIGHT["origin"],
        date=FLIGHT["departureDate"],
        change_type=None,
        limit=1000,
    ) or []
    payment_changes = [
        change
        for change in changes
        if _categorize_event(change.get("changeType", "")) == "payment"
    ]
    assert len(payment_changes) == 1
    payment = payment_changes[0]
    if not any(SAMPLE_LOCATOR in text for text in _iter_strings(payment)):
        raise AssertionError("payment change does not name the record locator")
    assert not any(change.get("changeType") == "RESERVATION_PARTY_CHANGE" for change in changes)
    _assert_no_raw_text(payment, [original, REPLACEMENT_FOP])

    summary = _safe_call(
        get_changes_summary,
        FLIGHT["flightNumber"],
        origin=FLIGHT["origin"],
        date=FLIGHT["departureDate"],
    )
    assert summary is not None
    assert any(
        _categorize_event(change_type) == "payment"
        for change_type in summary["changeTypes"]
    )

    feed = get_activity_feed(date=FLIGHT["departureDate"], limit=200, categories=None)
    feed_payment = [
        event
        for event in feed["events"]
        if event.get("flightNumber") == FLIGHT["flightNumber"]
        and event.get("category") == "payment"
    ]
    assert len(feed_payment) == 1
    _assert_no_raw_text(feed_payment, [original, REPLACEMENT_FOP])

    timeline = _safe_call(
        get_flight_timeline,
        FLIGHT["flightNumber"],
        origin=FLIGHT["origin"],
        date=FLIGHT["departureDate"],
    )
    assert timeline is not None
    timeline_payment = _payment_events(timeline["events"])
    assert len(timeline_payment) == 1
    _assert_no_raw_text(timeline_payment, [original, REPLACEMENT_FOP])
    assert second["changesStored"] == len(changes)


def test_party_size_and_payment_text_each_emit_their_own_event(memory_db):
    flight = {
        "airline": "GF",
        "flightNumber": "2058",
        "origin": "BAH",
        "departureDate": "2026-03-20",
    }
    before = "FOP-BEFORE"
    after = "FOP-AFTER"
    _ingest(_trip_body("LOC001", 2, before), flight)
    _ingest(_trip_body("LOC001", 3, after), flight)

    changes = _safe_call(
        get_changes,
        flight["flightNumber"],
        origin=flight["origin"],
        date=flight["departureDate"],
        change_type=None,
        limit=100,
    ) or []
    payment = [change for change in changes if _categorize_event(change.get("changeType", "")) == "payment"]
    party = [change for change in changes if change.get("changeType") == "RESERVATION_PARTY_CHANGE"]
    assert len(payment) == 1
    assert len(party) == 1
    assert party[0]["oldValue"] == "2"
    assert party[0]["newValue"] == "3"
    if not any("LOC001" in text for text in _iter_strings(payment[0])):
        raise AssertionError("payment change does not name the record locator")
    _assert_no_raw_text(payment[0], [before, after])


def test_unchanged_payment_text_emits_no_payment_event(memory_db):
    flight = {
        "airline": "GF",
        "flightNumber": "2059",
        "origin": "BAH",
        "departureDate": "2026-03-21",
    }
    _ingest(_trip_body("LOC002", 1, "FOP-SAME"), flight)
    second = _ingest(_trip_body("LOC002", 1, "FOP-SAME"), flight)
    assert second["isDuplicate"] is False

    changes = _safe_call(
        get_changes,
        flight["flightNumber"],
        origin=flight["origin"],
        date=flight["departureDate"],
        change_type=None,
        limit=100,
    ) or []
    assert not any(
        _categorize_event(change.get("changeType", "")) == "payment" for change in changes
    )


def test_party_size_change_is_still_recorded(memory_db):
    flight = {
        "airline": "GF",
        "flightNumber": "2060",
        "origin": "BAH",
        "departureDate": "2026-03-22",
    }
    _ingest(_trip_body("LOC003", 1, "FOP-SAME"), flight)
    _ingest(_trip_body("LOC003", 4, "FOP-SAME"), flight)
    changes = get_changes(
        flight["flightNumber"],
        origin=flight["origin"],
        date=flight["departureDate"],
        change_type=None,
        limit=100,
    )
    party = [change for change in changes if change.get("changeType") == "RESERVATION_PARTY_CHANGE"]
    assert len(party) == 1
    assert party[0]["oldValue"] == "1"
    assert party[0]["newValue"] == "4"
