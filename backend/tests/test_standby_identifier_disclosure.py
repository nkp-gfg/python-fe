"""Standby and manifest routes must not give workforce identifiers to an unauthenticated caller.

These tests are the spec for the disclosure in the standby/upgrade queue.
They fail while seniority dates and corporate identifiers are copied into
responses, and pass once those values are dropped or the route refuses a
caller who does not present an operations credential.

Run from the repository root:

    PYTHONPATH=. pytest backend/tests/test_standby_identifier_disclosure.py -q
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import audit as audit_routes
from backend.api.routes import flights as flight_routes
from backend.api.routes import passengers as passenger_routes
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list
from backend.sabre.client import SabreClient

# Obvious placeholders. Not credentials.
NON_OPERATIONS_AUTHORIZATION = "Bearer not-an-operations-token"

STANDBY_SENIORITY = "1972-04-17"
STANDBY_CORP = "CORP-STANDBY-8841"
UPGRADE_SENIORITY = "1968-11-22"
UPGRADE_CORP = "CORP-UPGRADE-5520"
CLEARED_SENIORITY = "1977-08-09"
CLEARED_CORP = "CORP-CHECKED-3308"
MANIFEST_SENIORITY = "1964-01-30"
MANIFEST_CORP = "CORP-MANIFEST-7714"
OLDER_SENIORITY = "1955-05-05"
OLDER_CORP = "CORP-OLDER-1001"
SNAPSHOT_SENIORITY = "1970-12-01"
SNAPSHOT_CORP = "CORP-SNAP-2468"
LATER_SNAPSHOT_SENIORITY = "1988-08-08"
SKIPPED_SENIORITY = "1961-06-06"
PROMOTED_SENIORITY = "1999-03-03"
DASH_CORP = "CORP-DASH-1199"
DASH_SENIORITY = "1974-09-14"


class MemoryCursor:
    """Applies the filter, sort, and limit pymongo forwards to the server.

    Collection.find_one returns the first document from find(...).limit(-1)
    (pymongo 4.20.0, collection.py). Cursor.sort records a list of
    (key, direction) pairs and direction -1 is descending (pymongo.DESCENDING).
    This double evaluates only the query documents the routes in this test build:
    equality, a dotted array field, and ``$lte``.
    """

    def __init__(self, docs, projection):
        self._docs = docs
        self._projection = projection
        self._limit = None

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            spec = [(key_or_list, 1 if direction is None else direction)]
        else:
            spec = []
            for item in key_or_list:
                if isinstance(item, str):
                    spec.append((item, 1))
                else:
                    spec.append((item[0], item[1]))
        docs = list(self._docs)
        for field, way in reversed(spec):
            docs.sort(key=lambda doc, field=field: doc.get(field), reverse=way < 0)
        self._docs = docs
        return self

    def limit(self, count):
        self._limit = count
        return self

    def __iter__(self):
        docs = self._docs
        if self._limit is not None and self._limit != 0:
            docs = docs[: 1 if self._limit < 0 else self._limit]
        for doc in docs:
            yield _project(copy.deepcopy(doc), self._projection)


class MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def insert_many(self, docs):
        for doc in docs:
            self.insert_one(doc)

    def find(self, filter=None, projection=None, **kwargs):
        matched = [doc for doc in self.docs if _matches(doc, filter or {})]
        cursor = MemoryCursor(matched, projection)
        if kwargs.get("sort") is not None:
            cursor.sort(kwargs["sort"])
        return cursor

    def find_one(self, filter=None, *args, **kwargs):
        projection = args[0] if args else kwargs.get("projection")
        cursor = self.find(filter, projection, sort=kwargs.get("sort"))
        for result in cursor.limit(-1):
            return result
        return None


class MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, MemoryCollection())

    def command(self, *args, **kwargs):
        raise RuntimeError("test database does not accept commands")


def _matches(doc, query):
    for key, expected in query.items():
        if "." in key:
            if not _dotted_equal(doc, key.split("."), expected):
                return False
            continue
        if isinstance(expected, dict) and "$lte" in expected:
            actual = doc.get(key)
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _dotted_equal(doc, parts, expected):
    current = doc.get(parts[0])
    if isinstance(current, list):
        return any(
            isinstance(item, dict) and item.get(parts[1]) == expected
            for item in current
            if len(parts) == 2
        )
    return False


def _project(doc, projection):
    if not projection:
        return doc
    included = [key for key, flag in projection.items() if flag]
    excluded = [key for key, flag in projection.items() if not flag]
    if excluded and not included:
        for key in excluded:
            doc.pop(key, None)
        return doc
    projected = {}
    if "_id" not in projection and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key in included:
        if key in doc:
            projected[key] = doc[key]
    return projected


def _passenger_xml(passenger):
    lines = ["        <PassengerInfo>", "          <Name_Details>"]
    lines.append(f"            <LastName>{passenger['last']}</LastName>")
    lines.append(f"            <FirstName>{passenger['first']}</FirstName>")
    lines.append("          </Name_Details>")
    lines.append(
        f"          <PNRLocator nameAssociationID=\"1\">{passenger['pnr']}</PNRLocator>"
    )
    lines.append(f"          <PassengerID>{passenger['pnr']}-ID</PassengerID>")
    lines.append(f"          <LineNumber>{passenger.get('line', 1)}</LineNumber>")
    if passenger.get("priority") is not None:
        lines.append(f"          <PriorityCode>{passenger['priority']}</PriorityCode>")
    lines.append(f"          <BookingClass>{passenger.get('booking', 'Y')}</BookingClass>")
    if "desired" in passenger:
        lines.append(
            f"          <DesiredBookingClass>{passenger['desired']}</DesiredBookingClass>"
        )
    lines.append(f"          <Cabin>{passenger.get('cabin', 'Y')}</Cabin>")
    lines.append(f"          <Seat>{passenger.get('seat', '')}</Seat>")
    lines.append("          <Destination>BAH</Destination>")
    lines.append(
        f"          <PassengerType>{passenger.get('passenger_type', 'F')}</PassengerType>"
    )
    if passenger.get("corp") is not None:
        lines.append(f"          <CorpID>{passenger['corp']}</CorpID>")
    if passenger.get("seniority") is not None:
        lines.append(f"          <SeniorityDate>{passenger['seniority']}</SeniorityDate>")
    lines.append("          <Indicators><Indicator>NotBoarded</Indicator></Indicators>")
    checked = "true" if passenger.get("checked_in") else "false"
    boarded = "true" if passenger.get("boarded") else "false"
    lines.append(f"          <CheckIn_Info><CheckInStatus>{checked}</CheckInStatus></CheckIn_Info>")
    lines.append(f"          <Boarding_Info><BoardStatus>{boarded}</BoardStatus></Boarding_Info>")
    lines.append("          <BoardingPassFlag>false</BoardingPassFlag>")
    lines.append("        </PassengerInfo>")
    return "\n".join(lines)


def manifest_document(flight_number, origin, departure_date, passengers):
    """Parse a synthetic GetPassengerListRS the way SabreClient does, then normalize it.

    SabreClient._parse_xml calls xmltodict.parse and _extract_body pulls
    GetPassengerListRS out of the SOAP body. convert_passenger_list is the
    function the feeder stores, unchanged, via store_passenger_list.
    """
    body = "\n".join(_passenger_xml(passenger) for passenger in passengers)
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{flight_number}</Flight>
          <DepartureDate>{departure_date}</DepartureDate>
          <Origin>{origin}</Origin>
          <Destination>BAH</Destination>
        </Itinerary>
        <CabinInfoList>
          <CabinInfo>
            <Destination>BAH</Destination>
            <Cabin>Y</Cabin>
            <Count>{len(passengers)}</Count>
            <Authorized>100</Authorized>
            <Available>90</Available>
          </CabinInfo>
        </CabinInfoList>
      </ItineraryInfo>
      <PassengerInfoList>
{body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml_text)
    raw = SabreClient._extract_body(object(), parsed, "GetPassengerListRS")
    return convert_passenger_list(raw, "GF", flight_number, departure_date, origin)


def _install_database(monkeypatch, database):
    def get_db():
        return database

    monkeypatch.setattr(passenger_routes, "get_db", get_db)
    monkeypatch.setattr(audit_routes, "get_db", get_db)
    monkeypatch.setattr(flight_routes, "get_db", get_db)


@pytest.fixture()
def client(monkeypatch):
    database = MemoryDatabase()
    _install_database(monkeypatch, database)
    with TestClient(app) as test_client:
        yield test_client, database


def _withhold(response, sentinels):
    body = response.text
    leaked = [value for value in sentinels if value and value in body]
    assert not leaked, (
        f"status {response.status_code} included workforce identifiers {leaked}: {body}"
    )
    assert response.status_code in (200, 401, 403), body
    return response.status_code == 200


def _queue(payload):
    upgrade = payload["upgrade"]["passengers"]
    standby = payload["standby"]["passengers"]
    return upgrade, standby


def test_standby_list_without_date_origin_or_operations_credential(client):
    """Attack path: GET the newest manifest for a flight number, with no query and no role."""
    test_client, database = client
    newest = manifest_document(
        "4242",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "QUEUE",
                "first": "STAFF",
                "pnr": "PNRST1",
                "line": 4,
                "priority": "B02",
                "corp": STANDBY_CORP,
                "seniority": STANDBY_SENIORITY,
                "passenger_type": "S",
            },
            {
                "last": "QUEUE",
                "first": "UPGRADE",
                "pnr": "PNRUP1",
                "line": 2,
                "priority": "UPG",
                "desired": "J",
                "corp": UPGRADE_CORP,
                "seniority": UPGRADE_SENIORITY,
            },
            {
                "last": "QUEUE",
                "first": "CLEARED",
                "pnr": "PNRCI1",
                "line": 8,
                "priority": "B01",
                "corp": CLEARED_CORP,
                "seniority": CLEARED_SENIORITY,
                "checked_in": True,
                "passenger_type": "S",
            },
            {
                "last": "QUEUE",
                "first": "REVENUE",
                "pnr": "PNRRV1",
                "line": 1,
                "corp": MANIFEST_CORP,
                "seniority": MANIFEST_SENIORITY,
            },
        ],
    )
    newest["fetchedAt"] = "2026-06-01T00:00:00Z"
    older = manifest_document(
        "4242",
        "LHR",
        "2026-01-01",
        [
            {
                "last": "OLDERONLY",
                "first": "MANIFEST",
                "pnr": "PNROLD",
                "line": 1,
                "priority": "B01",
                "corp": OLDER_CORP,
                "seniority": OLDER_SENIORITY,
            }
        ],
    )
    older["fetchedAt"] = "2026-01-01T00:00:00Z"
    database["passenger_list"].insert_one(older)
    database["passenger_list"].insert_one(newest)

    sentinels = [
        STANDBY_SENIORITY,
        STANDBY_CORP,
        UPGRADE_SENIORITY,
        UPGRADE_CORP,
        CLEARED_SENIORITY,
        CLEARED_CORP,
        MANIFEST_SENIORITY,
        MANIFEST_CORP,
        OLDER_SENIORITY,
        OLDER_CORP,
    ]
    response = test_client.get("/flights/4242/passengers/standby-list")
    assert "authorization" not in {name.lower() for name in response.request.headers}
    success = _withhold(response, sentinels)
    if not success:
        return

    upgrade, standby = _queue(response.json())
    for entry in upgrade + standby:
        assert "seniorityDate" not in entry
        assert "corpId" not in entry
    standby_names = {(entry["lastName"], entry["firstName"], entry["pnr"]) for entry in standby}
    upgrade_names = {(entry["lastName"], entry["firstName"], entry["pnr"]) for entry in upgrade}
    assert ("QUEUE", "STAFF", "PNRST1") in standby_names
    assert ("QUEUE", "UPGRADE", "PNRUP1") in upgrade_names
    assert "OLDERONLY" not in response.text


def test_standby_list_with_bruno_query_and_non_operations_bearer(client):
    """The collection calls the route with origin and date and with auth disabled.

    A bearer value that is not an operations credential must not unlock the fields.
    """
    test_client, database = client
    document = manifest_document(
        "4242",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "QUEUE",
                "first": "STAFF",
                "pnr": "PNRST1",
                "line": 3,
                "priority": "B02",
                "corp": STANDBY_CORP,
                "seniority": STANDBY_SENIORITY,
                "passenger_type": "S",
            },
            {
                "last": "QUEUE",
                "first": "UPGRADE",
                "pnr": "PNRUP1",
                "line": 1,
                "priority": "UPG",
                "corp": UPGRADE_CORP,
                "seniority": UPGRADE_SENIORITY,
            },
        ],
    )
    document["fetchedAt"] = "2026-06-01T00:00:00Z"
    database["passenger_list"].insert_one(document)

    response = test_client.get(
        "/flights/4242/passengers/standby-list",
        params={"origin": "BAH", "date": "2026-04-02"},
        headers={"Authorization": NON_OPERATIONS_AUTHORIZATION},
    )
    success = _withhold(
        response,
        [STANDBY_SENIORITY, STANDBY_CORP, UPGRADE_SENIORITY, UPGRADE_CORP],
    )
    if not success:
        return
    upgrade, standby = _queue(response.json())
    for entry in upgrade + standby:
        assert "seniorityDate" not in entry
        assert "corpId" not in entry
    assert any(entry["pnr"] == "PNRST1" for entry in standby)
    assert any(entry["pnr"] == "PNRUP1" for entry in upgrade)


def test_standby_list_snapshot_sequence_uses_the_selected_manifest(client):
    test_client, database = client
    selected = manifest_document(
        "4243",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "SNAP",
                "first": "SELECTED",
                "pnr": "PNRSN3",
                "line": 2,
                "priority": "B03",
                "corp": SNAPSHOT_CORP,
                "seniority": SNAPSHOT_SENIORITY,
                "passenger_type": "S",
            }
        ],
    )
    later = manifest_document(
        "4243",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "SNAP",
                "first": "LATER",
                "pnr": "PNRSN9",
                "line": 1,
                "priority": "B01",
                "corp": "CORP-LATER-9999",
                "seniority": LATER_SNAPSHOT_SENIORITY,
                "passenger_type": "S",
            }
        ],
    )
    database["snapshots"].insert_one(
        {
            "flightNumber": "4243",
            "origin": "BAH",
            "departureDate": "2026-04-02",
            "snapshotType": "passenger_list",
            "sequenceNumber": 3,
            "capturedAt": "2026-04-02T00:00:00Z",
            "data": selected,
        }
    )
    database["snapshots"].insert_one(
        {
            "flightNumber": "4243",
            "origin": "BAH",
            "departureDate": "2026-04-02",
            "snapshotType": "passenger_list",
            "sequenceNumber": 9,
            "capturedAt": "2026-04-03T00:00:00Z",
            "data": later,
        }
    )

    response = test_client.get(
        "/flights/4243/passengers/standby-list",
        params={"snapshot_sequence": 5},
    )
    success = _withhold(
        response,
        [SNAPSHOT_SENIORITY, SNAPSHOT_CORP, LATER_SNAPSHOT_SENIORITY, "CORP-LATER-9999"],
    )
    if not success:
        return
    upgrade, standby = _queue(response.json())
    for entry in upgrade + standby:
        assert "seniorityDate" not in entry
        assert "corpId" not in entry
    assert any(entry["pnr"] == "PNRSN3" for entry in standby)
    assert "LATER" not in response.text


def _stored_manifest(database, flight_number):
    document = manifest_document(
        flight_number,
        "BAH",
        "2026-04-02",
        [
            {
                "last": "MANIFEST",
                "first": "STAFF",
                "pnr": "PNRMF1",
                "line": 6,
                "priority": "B02",
                "corp": STANDBY_CORP,
                "seniority": STANDBY_SENIORITY,
                "passenger_type": "S",
            },
            {
                "last": "MANIFEST",
                "first": "REVENUE",
                "pnr": "PNRMF2",
                "line": 1,
                "corp": MANIFEST_CORP,
                "seniority": MANIFEST_SENIORITY,
            },
        ],
    )
    document["fetchedAt"] = "2026-06-01T00:00:00Z"
    database["passenger_list"].insert_one(document)


def test_passenger_manifest_omits_the_same_identifiers(client):
    test_client, database = client
    _stored_manifest(database, "4244")

    manifest = test_client.get("/flights/4244/passengers")
    success = _withhold(
        manifest,
        [STANDBY_SENIORITY, STANDBY_CORP, MANIFEST_SENIORITY, MANIFEST_CORP],
    )
    if not success:
        return
    passengers = manifest.json()["passengers"]
    assert {passenger["pnr"] for passenger in passengers} >= {"PNRMF1", "PNRMF2"}
    for passenger in passengers:
        assert "seniorityDate" not in passenger
        assert "corpId" not in passenger
    assert "_raw" not in manifest.json()


def test_passenger_pnr_lookup_omits_the_same_identifiers(client):
    test_client, database = client
    _stored_manifest(database, "4245")

    by_pnr = test_client.get("/flights/4245/passengers/PNRMF1")
    success = _withhold(by_pnr, [STANDBY_SENIORITY, STANDBY_CORP])
    if not success:
        return
    matched = by_pnr.json()["passengers"]
    assert len(matched) == 1
    assert matched[0]["pnr"] == "PNRMF1"
    assert matched[0]["lastName"] == "MANIFEST"
    assert "seniorityDate" not in matched[0]
    assert "corpId" not in matched[0]


def test_audit_alerts_do_not_repeat_seniority_or_corporate_id(client):
    test_client, database = client
    before = manifest_document(
        "5151",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "PROMOTED",
                "first": "PAX",
                "pnr": "PNRPR1",
                "line": 5,
                "desired": "J",
                "booking": "Y",
                "cabin": "Y",
                "seniority": "1990-01-01",
            }
        ],
    )
    after = manifest_document(
        "5151",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "PROMOTED",
                "first": "PAX",
                "pnr": "PNRPR1",
                "line": 5,
                "booking": "J",
                "cabin": "J",
                "seniority": PROMOTED_SENIORITY,
            }
        ],
    )
    changes = diff_passenger_list(
        {"snapshotId": "before", "data": before},
        {"snapshotId": "after", "data": after},
        {"flightNumber": "5151", "origin": "BAH", "departureDate": "2026-04-02"},
    )
    assert any(change["changeType"] == "UPGRADE_CONFIRMED" for change in changes)
    for change in changes:
        change["sequenceNumber"] = 1
    database["changes"].insert_many(changes)

    current = manifest_document(
        "5151",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "SKIPPED",
                "first": "STAFF",
                "pnr": "PNRSK1",
                "line": 1,
                "priority": "B01",
                "desired": "J",
                "booking": "Y",
                "cabin": "Y",
                "seniority": SKIPPED_SENIORITY,
                "corp": "CORP-SKIP-4242",
                "boarded": True,
                "checked_in": True,
                "passenger_type": "S",
            },
            {
                "last": "PROMOTED",
                "first": "PAX",
                "pnr": "PNRPR1",
                "line": 9,
                "priority": "UPG",
                "desired": "J",
                "booking": "J",
                "cabin": "J",
                "seniority": PROMOTED_SENIORITY,
                "boarded": True,
                "checked_in": True,
            },
            {
                "last": "PREMIUM",
                "first": "STAFF",
                "pnr": "PNRSTF",
                "line": 3,
                "cabin": "J",
                "booking": "J",
                "corp": "T",
                "seniority": "1984-04-04",
                "passenger_type": "E",
            },
        ],
    )
    current["fetchedAt"] = "2026-06-01T00:00:00Z"
    database["passenger_list"].insert_one(current)

    response = test_client.get("/flights/5151/audit")
    success = _withhold(
        response,
        [SKIPPED_SENIORITY, PROMOTED_SENIORITY, "1984-04-04", "CORP-SKIP-4242"],
    )
    if not success:
        return
    alerts = response.json()["alerts"]
    assert alerts
    for alert in alerts:
        details = alert.get("details") or {}
        assert "corpId" not in details
        assert "seniorityDate" not in details
        assert "skippedSeniority" not in details
        assert "upgradedSeniority" not in details
        assert SKIPPED_SENIORITY not in alert.get("message", "")


def test_dashboard_does_not_publish_corporate_identifiers(client):
    test_client, database = client
    document = manifest_document(
        "6161",
        "BAH",
        "2026-04-02",
        [
            {
                "last": "DASH",
                "first": "ACCOUNT",
                "pnr": "PNRDS1",
                "line": 1,
                "priority": "B02",
                "corp": DASH_CORP,
                "seniority": DASH_SENIORITY,
                "passenger_type": "S",
            }
        ],
    )
    document["fetchedAt"] = "2026-06-01T00:00:00Z"
    database["passenger_list"].insert_one(document)

    response = test_client.get("/flights/6161/dashboard")
    success = _withhold(response, [DASH_CORP, DASH_SENIORITY])
    if not success:
        return
    insights = response.json().get("insights") or {}
    companies = (insights.get("corporateTravel") or {}).get("companies") or {}
    assert DASH_CORP not in companies
    assert DASH_SENIORITY not in response.text
