"""Audit alerts must not disclose staff seniority dates or the staff marker.

The audit route is unauthenticated. These tests call that route with a flight
number (and, where noted, origin and date) after the passenger manifest has
been stored in the shape the feeder writes. They fail while upgrade and staff
rules copy seniority dates and the staff corporate marker into alert messages
or detail objects. They pass once those values are compared inside the rules
and the response keeps only the operational conclusion, such as which record
locators were skipped.
"""

import copy

import pytest
from fastapi.testclient import TestClient

from backend.api import database
from backend.api import main as api_main
from backend.api.main import app
from backend.api.routes import audit as audit_routes
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import _make_change

# Synthetic values. They stand in for employee seniority dates and the
# corporate identifier the staff rule compares. They are not credentials.
SKIPPED_SENIORITY = "1998-04-17"
UPGRADED_SENIORITY = "2011-08-23"
LATER_SENIORITY = "2020-01-01"
DESIRED_SENIORITY = "1987-02-09"
STAFF_CORP_MARKER = "T"
OTHER_CORP_ID = "C"

FLIGHT = "2057"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-04-02"
SKIPPED_PNR = "SKIP01"
UPGRADED_PNR = "UPGR01"
LATER_PNR = "LATE01"
DESIRED_PNR = "DESR01"
STAFF_PNR = "STAF01"
OTHER_CORP_PNR = "CORP01"


class _Cursor:
    """Minimal stand-in for the cursor ``Collection.find`` returns.

    pymongo 4.20 ``find`` returns the stored documents that match the filter
    and applies an inclusion/exclusion projection without rewriting other
    fields (``pymongo/synchronous/collection.py``). ``find_one`` returns the
    first of those documents. ``Cursor.sort`` orders that same set.
    """

    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs.sort(
            key=lambda doc: (doc.get(key) is None, doc.get(key)),
            reverse=int(direction) < 0,
        )
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, filter=None, *args, **kwargs):
        matches = _matching(self.docs, filter or {})
        sort = kwargs.get("sort")
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: (
                        doc.get(field) is None,
                        doc.get(field),
                    ),
                    reverse=int(direction) < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, filter=None, projection=None, *args, **kwargs):
        matches = [
            _apply_projection(doc, projection)
            for doc in _matching(self.docs, filter or {})
        ]
        return _Cursor(matches)

    def create_index(self, *args, **kwargs):
        return None

    def drop_index(self, *args, **kwargs):
        return None

    def index_information(self):
        return {}


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]

    def command(self, *args, **kwargs):
        return {"ok": 1}


def _matching(docs, query):
    found = []
    for doc in docs:
        if all(doc.get(key) == value for key, value in query.items()):
            found.append(doc)
    return found


def _apply_projection(doc, projection):
    copied = copy.deepcopy(doc)
    if not projection:
        return copied
    for field, include in projection.items():
        if include in (0, False) and field in copied:
            copied.pop(field)
    return copied


def _passenger(
    last_name,
    first_name,
    pnr,
    *,
    line,
    cabin,
    booking,
    desired="",
    seniority="",
    corp_id="",
    boarded=False,
):
    """One Sabre passenger element, using the field names the converter reads."""
    return {
        "Name_Details": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": {"#text": pnr},
        "LineNumber": str(line),
        "BookingClass": booking,
        "DesiredBookingClass": desired,
        "Cabin": cabin,
        "CorpID": corp_id,
        "SeniorityDate": seniority,
        "PassengerType": "F",
        "BoardingPassFlag": "true",
        "CheckIn_Info": {"CheckInStatus": "true"},
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "EditCodeList": {"EditCode": ["DOCS"]},
    }


def _manifest(passengers, fetched_at):
    raw = {
        "PassengerInfoList": {"PassengerInfo": passengers},
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR"}},
    }
    doc = convert_passenger_list(raw, "GF", FLIGHT, DEPARTURE_DATE, ORIGIN)
    doc["fetchedAt"] = fetched_at
    return doc


def _upgrade_change(change_type, metadata=None):
    flight_info = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
    }
    change = _make_change(
        flight_info,
        change_type,
        "snap-before",
        "snap-after",
        passenger={
            "pnr": UPGRADED_PNR,
            "lastName": "YOUSIF",
            "firstName": "OMAR",
        },
        field="cabin",
        old_value="Y",
        new_value="J",
        metadata=metadata,
    )
    change["sequenceNumber"] = 1
    return change


def _queue_passengers():
    return [
        _passenger(
            "HADDAD",
            "LEILA",
            SKIPPED_PNR,
            line=2,
            cabin="Y",
            booking="Y",
            desired="J",
            seniority=SKIPPED_SENIORITY,
        ),
        _passenger(
            "YOUSIF",
            "OMAR",
            UPGRADED_PNR,
            line=9,
            cabin="J",
            booking="J",
            desired="J",
            seniority=UPGRADED_SENIORITY,
            boarded=True,
        ),
        _passenger(
            "FARAJ",
            "SARA",
            LATER_PNR,
            line=4,
            cabin="Y",
            booking="Y",
            desired="J",
            seniority=LATER_SENIORITY,
        ),
        _passenger(
            "NASER",
            "AMAL",
            DESIRED_PNR,
            line=6,
            cabin="Y",
            booking="Y",
            desired="F",
            seniority=DESIRED_SENIORITY,
            boarded=True,
        ),
        _passenger(
            "KARIM",
            "NOOR",
            STAFF_PNR,
            line=3,
            cabin="J",
            booking="J",
            corp_id=STAFF_CORP_MARKER,
        ),
        _passenger(
            "ALI",
            "RASHED",
            OTHER_CORP_PNR,
            line=8,
            cabin="J",
            booking="J",
            corp_id=OTHER_CORP_ID,
        ),
    ]


def _seed(db, change_docs):
    passenger_list = db["passenger_list"]
    # Older snapshot without the sensitive passengers. The route must use the
    # latest fetchedAt document, which is what the feeder stores last.
    passenger_list.insert_one(
        _manifest([], "2026-04-02T00:00:00Z")
    )
    passenger_list.insert_one(
        _manifest(_queue_passengers(), "2026-04-02T06:00:00Z")
    )
    db["reservations"].insert_one(
        {
            "flightNumber": FLIGHT,
            "departureDate": DEPARTURE_DATE,
            "fetchedAt": "2026-04-02T06:00:00Z",
            "reservations": [],
        }
    )
    for doc in change_docs:
        db["changes"].insert_one(doc)


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _string_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, str):
        yield value


def _alerts(body, rule_id, pnr=None):
    found = []
    for alert in body["alerts"]:
        if alert["ruleId"] != rule_id:
            continue
        if pnr is not None and alert.get("pnr") != pnr:
            continue
        found.append(alert)
    return found


def _assert_no_seniority(alert):
    serialized = str(alert)
    for seniority in (
        SKIPPED_SENIORITY,
        UPGRADED_SENIORITY,
        LATER_SENIORITY,
        DESIRED_SENIORITY,
    ):
        assert seniority not in serialized, alert
    for key in _keys(alert):
        assert key not in {
            "seniorityDate",
            "skippedSeniority",
            "upgradedSeniority",
        }, alert


def _assert_no_corp_marker(alert):
    assert "corpId" not in set(_keys(alert)), alert
    assert STAFF_CORP_MARKER not in set(_string_values(alert)), alert
    assert OTHER_CORP_ID not in set(_string_values(alert)), alert


@pytest.fixture
def client(monkeypatch):
    db = _Database()
    # main.py and the audit route each bind get_db at import time.
    monkeypatch.setattr(database, "get_db", lambda: db)
    monkeypatch.setattr(api_main, "get_db", lambda: db)
    monkeypatch.setattr(audit_routes, "get_db", lambda: db)
    with TestClient(app) as test_client:
        yield test_client, db


def _get_audit(test_client, flight=FLIGHT, **params):
    # No Authorization header, cookie, or other credential is sent.
    response = test_client.get(
        f"/flights/{flight}/audit",
        params=params,
        headers={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_upgrade_queue_skip_hides_seniority_for_confirmed_upgrade(client):
    test_client, db = client
    _seed(db, [_upgrade_change("UPGRADE_CONFIRMED")])

    body = _get_audit(test_client, origin=ORIGIN, date=DEPARTURE_DATE)

    skipped = _alerts(body, "UPGRADE_QUEUE_SKIPPED", SKIPPED_PNR)
    assert len(skipped) == 1
    alert = skipped[0]
    serialized = str(alert)
    assert UPGRADED_PNR in serialized
    assert "J" in serialized
    _assert_no_seniority(alert)
    assert _alerts(body, "UPGRADE_QUEUE_SKIPPED", LATER_PNR) == []


def test_upgrade_queue_skip_hides_seniority_for_cabin_change(client):
    test_client, db = client
    _seed(
        db,
        [_upgrade_change("CABIN_CHANGE", metadata={"direction": "UPGRADE"})],
    )

    body = _get_audit(test_client)

    skipped = _alerts(body, "UPGRADE_QUEUE_SKIPPED", SKIPPED_PNR)
    assert len(skipped) == 1
    serialized = str(skipped[0])
    assert UPGRADED_PNR in serialized
    _assert_no_seniority(skipped[0])
    assert _alerts(body, "UPGRADE_QUEUE_SKIPPED", LATER_PNR) == []


def test_wrapped_change_history_hides_seniority(client):
    test_client, db = client
    change = _upgrade_change("UPGRADE_CONFIRMED")
    _seed(
        db,
        [
            {
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DEPARTURE_DATE,
                "sequenceNumber": 1,
                "changes": [change],
            }
        ],
    )

    body = _get_audit(test_client, origin=ORIGIN, date=DEPARTURE_DATE)
    skipped = _alerts(body, "UPGRADE_QUEUE_SKIPPED", SKIPPED_PNR)
    assert len(skipped) == 1
    assert UPGRADED_PNR in str(skipped[0])
    _assert_no_seniority(skipped[0])


def test_desired_upgrade_hides_seniority_date(client):
    test_client, db = client
    _seed(db, [])

    body = _get_audit(test_client, origin=ORIGIN, date=DEPARTURE_DATE)
    desired = _alerts(body, "UPGRADE_DESIRED_NOT_PROCESSED", DESIRED_PNR)
    assert len(desired) == 1
    alert = desired[0]
    serialized = str(alert)
    assert "F" in serialized
    assert "Y" in serialized
    _assert_no_seniority(alert)
    assert "seniorityDate" not in set(_keys(alert))


def test_staff_premium_hides_corporate_marker(client):
    test_client, db = client
    _seed(db, [])

    body = _get_audit(test_client, origin=ORIGIN, date=DEPARTURE_DATE)
    staff = _alerts(body, "STAFF_PREMIUM_NO_UPGRADE", STAFF_PNR)
    assert len(staff) == 1
    alert = staff[0]
    serialized = str(alert)
    assert STAFF_PNR in serialized
    assert "J" in serialized
    _assert_no_corp_marker(alert)
    assert _alerts(body, "STAFF_PREMIUM_NO_UPGRADE", OTHER_CORP_PNR) == []
    for other in body["alerts"]:
        _assert_no_corp_marker(other)
