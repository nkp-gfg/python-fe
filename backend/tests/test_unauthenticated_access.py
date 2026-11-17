"""Spec for unauthenticated access to passenger data and Sabre actions.

These tests exercise the FastAPI routes mounted in ``backend.api.main``. They
fail while those routes read MongoDB, call Sabre, restore snapshots, or queue
jobs with no staff credential. A fix makes them pass by:

- accepting ``Authorization: Bearer <token>``
- treating ``FALCONEYE_STAFF_TOKEN`` as a staff identity that may read stored
  operational data
- treating ``FALCONEYE_PRIVILEGED_TOKEN`` as the only identity that may call
  Sabre, restore a snapshot, or queue ingestion and comparison jobs
- rejecting every other non-health request with 401 or 403 before any
  database, Sabre, Redis, or broker call
- leaving ``GET /``, ``GET /health/app``, and ``GET /health/db`` open
- echoing ``Access-Control-Allow-Origin`` only for ``FALCONEYE_UI_ORIGIN``

The placeholder token values are not production secrets. They are the
contract this file sets in the environment before the application is imported.
"""

import copy
import json
import os

# Set before importing the app. python-dotenv load_dotenv(override=False)
# will not replace these with values from a local .env file.
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:1/falconeye"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["POSTGRES_URI"] = ""
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "placeholder"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["FALCONEYE_STAFF_TOKEN"] = "placeholder-staff"
os.environ["FALCONEYE_PRIVILEGED_TOKEN"] = "placeholder-privileged"
os.environ["FALCONEYE_UI_ORIGIN"] = "http://ops.example.test"

import celery
import pytest
import requests
from fastapi.testclient import TestClient

from backend.api import database, main
from backend.api import redis_client
from backend.feeder import storage as feeder_storage
from backend.api.routes import (
    analytics,
    audit,
    changes,
    data_audit,
    flights,
    passengers,
    reservations,
    schedule,
)
from backend.sabre import client as sabre_client

STAFF_TOKEN = os.environ["FALCONEYE_STAFF_TOKEN"]
PRIVILEGED_TOKEN = os.environ["FALCONEYE_PRIVILEGED_TOKEN"]
UI_ORIGIN = os.environ["FALCONEYE_UI_ORIGIN"]
EVIL_ORIGIN = "https://evil.example"
FLIGHT = "9999"
OTHER_FLIGHT = "8888"
ORIGIN = "BAH"
DATE = "2026-03-19"
PNR = "LOCATOR1"
CURRENT_NAME = "CURRENT"
STALE_NAME = "STALE"
OTHER_NAME = "OTHERFLIGHT"
REREAD_NAME = "REREAD"
EMAIL = "passenger@example.test"
STALE_EMAIL = "stale@example.test"
PHONE = "+10000000000"
FF_NUMBER = "FFPLACEHOLDER"
DOCUMENT = "XPLACEHOLDER9"
DOCS = f"P/ZZ/{DOCUMENT}/ZZ/01JAN70/F/01JAN30/EXAMPLE/PASSENGER"
JOB_MARKER = "JOBMARKER"

PASSENGERS_URL = f"/flights/{FLIGHT}/passengers?origin={ORIGIN}&date={DATE}"
RESERVATIONS_URL = f"/flights/{FLIGHT}/reservations?origin={ORIGIN}&date={DATE}"
DETAIL_URL = (
    f"/flights/{FLIGHT}/passengers/{PNR}/detail"
    f"?origin={ORIGIN}&date={DATE}&airline=GF"
)
RESTORE_URL = f"/flights/{FLIGHT}/snapshots/1/restore?origin={ORIGIN}&date={DATE}"

SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope>
  <soap-env:Header>
    <wsse:Security>
      <wsse:BinarySecurityToken>placeholder-session</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body></soap-env:Body>
</soap-env:Envelope>
"""

DETAIL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope>
  <soap-env:Body>
    <GetPassengerDataRS Status="Success" CompletionStatus="Complete">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>EXAMPLE</LastName>
          <FirstName>PASSENGER</FirstName>
          <PassengerID>1</PassengerID>
          <PNRLocator>{PNR}</PNRLocator>
          <FreeTextInfoList>
            <FreeTextInfo>
              <EditCode>DOCS</EditCode>
              <Text>{DOCS}</Text>
            </FreeTextInfo>
          </FreeTextInfoList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

EMPTY_SOAP = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope><soap-env:Body></soap-env:Body></soap-env:Envelope>
"""


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _sort_value(doc, field):
    current = doc
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return "" if current is None else current


def _field_matches(doc, key, expected):
    if not isinstance(doc, dict):
        return False
    if "." in key:
        head, rest = key.split(".", 1)
        value = doc.get(head)
        if isinstance(value, list):
            return any(
                _field_matches(item, rest, expected)
                for item in value
                if isinstance(item, dict)
            )
        if isinstance(value, dict):
            return _field_matches(value, rest, expected)
        return False
    if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
        if "$lte" in expected:
            value = doc.get(key)
            if not isinstance(value, (int, float)) or value > expected["$lte"]:
                return False
        if "$in" in expected and doc.get(key) not in expected["$in"]:
            return False
        if "$exists" in expected and (key in doc) != bool(expected["$exists"]):
            return False
        return True
    return doc.get(key) == expected


def _matches(doc, query):
    return all(_field_matches(doc, key, expected) for key, expected in (query or {}).items())


def _delete_path(doc, path):
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _copy_path(source, dest, path):
    parts = path.split(".")
    current_s = source
    current_d = dest
    for part in parts[:-1]:
        if not isinstance(current_s, dict) or part not in current_s:
            return
        current_s = current_s[part]
        current_d = current_d.setdefault(part, {})
    if isinstance(current_s, dict) and parts[-1] in current_s:
        current_d[parts[-1]] = copy.deepcopy(current_s[parts[-1]])


def _project(doc, projection):
    projected = copy.deepcopy(doc)
    if not projection:
        return projected
    included = [key for key, flag in projection.items() if flag]
    excluded = [key for key, flag in projection.items() if not flag]
    if included:
        result = {}
        if projected.get("_id") is not None and projection.get("_id", 1):
            result["_id"] = projected["_id"]
        for key in included:
            if key != "_id":
                _copy_path(projected, result, key)
        return result
    for key in excluded:
        _delete_path(projected, key)
    return projected


class _Cursor:
    def __init__(self, docs):
        self._docs = docs
        self._sort = None
        self._limit = None

    def sort(self, key, direction=None):
        if isinstance(key, list):
            self._sort = list(key)
        else:
            self._sort = [(key, 1 if direction is None else direction)]
        return self

    def limit(self, count):
        self._limit = count
        return self

    def __iter__(self):
        docs = list(self._docs)
        if self._sort:
            for field, direction in reversed(self._sort):
                docs.sort(
                    key=lambda doc, field=field: _sort_value(doc, field),
                    reverse=direction < 0,
                )
        if self._limit is not None:
            docs = docs[: self._limit]
        return iter(docs)


class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.docs = []

    def _record(self, op, query):
        self.db.calls.append((op, self.name, query))

    def index_information(self):
        return {}

    def create_index(self, *args, **kwargs):
        return None

    def drop_index(self, *args, **kwargs):
        return None

    def find_one(self, query=None, *args, **kwargs):
        projection = kwargs.get("projection")
        sort = kwargs.get("sort")
        if args and isinstance(args[0], dict):
            projection = args[0]
        matched = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for field, direction in reversed(sort):
                matched.sort(
                    key=lambda doc, field=field: _sort_value(doc, field),
                    reverse=direction < 0,
                )
        self._record("find_one", query or {})
        if not matched:
            return None
        chosen = _project(matched[0], projection)
        return self.db._maybe_reread(self.name, query or {}, chosen)

    def find(self, query=None, projection=None, *args, **kwargs):
        if projection is None and args and isinstance(args[0], dict):
            projection = args[0]
        projection = kwargs.get("projection", projection)
        matched = [
            _project(doc, projection)
            for doc in self.docs
            if _matches(doc, query or {})
        ]
        self._record("find", query or {})
        return _Cursor(matched)

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = self.db.next_id()
        self.docs.append(stored)
        self._record("insert_one", {"flightNumber": stored.get("flightNumber")})
        return stored

    def aggregate(self, pipeline, **kwargs):
        self._record("aggregate", {})
        raise RuntimeError(f"aggregate on {self.name} was reached")


class FakeDB:
    def __init__(self):
        self._collections = {}
        self.calls = []
        self._ids = 0
        self.reread_passenger_list = False
        self.reread_snapshots = False
        self._passenger_reads = 0
        self._snapshot_reads = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(self, name)
        return self._collections[name]

    def command(self, name):
        self.calls.append(("command", name, {}))
        return {"ok": 1}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def next_id(self):
        self._ids += 1
        return f"id-{self._ids}"

    def reset_log(self):
        self.calls = []
        self._passenger_reads = 0
        self._snapshot_reads = {}
        self.reread_passenger_list = False
        self.reread_snapshots = False

    def _maybe_reread(self, collection, query, doc):
        if doc is None:
            return None
        if collection == "passenger_list" and self.reread_passenger_list:
            self._passenger_reads += 1
            if self._passenger_reads >= 2:
                rewritten = copy.deepcopy(doc)
                for person in rewritten.get("passengers", []):
                    person["lastName"] = REREAD_NAME
                return rewritten
        if collection == "snapshots" and self.reread_snapshots:
            key = json.dumps(query, sort_keys=True, default=str)
            seen = self._snapshot_reads.get(key, 0)
            self._snapshot_reads[key] = seen + 1
            if seen >= 1:
                rewritten = copy.deepcopy(doc)
                data = rewritten.get("data")
                if isinstance(data, dict):
                    data["flightNumber"] = OTHER_FLIGHT
                    for person in data.get("passengers", []):
                        person["lastName"] = REREAD_NAME
                    for record in data.get("reservations", []):
                        record["emails"] = ["reread@example.test"]
                return rewritten
        return doc


class MemoryPubSub:
    def subscribe(self, channel):
        self.channel = channel

    def get_message(self, timeout=None):
        return None

    def unsubscribe(self, *args, **kwargs):
        return None

    def close(self):
        return None


class MemoryRedis:
    def __init__(self):
        self.store = {}
        self.writes = []

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        self.writes.append(key)
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def scan(self, cursor=0, match=None, count=None):
        return 0, []

    def publish(self, channel, message):
        return 0

    def pubsub(self):
        return MemoryPubSub()


FAKE_DB = FakeDB()
MEMORY_REDIS = MemoryRedis()
SABRE_POSTS = []
PUBLISHED_TASKS = []


def _get_db():
    return FAKE_DB


def _get_redis():
    return MEMORY_REDIS


def _install_fakes():
    for module in (
        database,
        main,
        passengers,
        reservations,
        changes,
        flights,
        schedule,
        audit,
        analytics,
        data_audit,
    ):
        module.get_db = _get_db
    redis_client.get_redis = _get_redis
    main.get_redis = _get_redis
    feeder_storage.init_db(FAKE_DB)

    def fake_post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        SABRE_POSTS.append({"url": url, "action": action, "body": data or ""})
        if action == "SessionCreateRQ":
            text = SESSION_XML
        elif action == "GetPassengerDataRQ":
            text = DETAIL_XML
        else:
            text = EMPTY_SOAP

        class _Response:
            status_code = 200

            def __init__(self, payload):
                self.text = payload

        return _Response(text)

    sabre_client.requests.post = fake_post
    requests.post = fake_post

    def fake_send_task(self, name, args=None, kwargs=None, **options):
        PUBLISHED_TASKS.append({"name": name, "args": args, "kwargs": kwargs})

        class _Result:
            id = "placeholder-task"

        return _Result()

    celery.Celery.send_task = fake_send_task


def _passenger(last_name, pnr=PNR):
    return {
        "lastName": last_name,
        "firstName": "PAX",
        "pnr": pnr,
        "priorityCode": "UPG",
        "groupCode": "GRP1",
        "isCheckedIn": False,
        "isBoarded": False,
        "isRevenue": True,
        "lineNumber": 1,
        "bookingClass": "Y",
        "cabin": "Y",
        "seat": "1A",
    }


def _reservation(last_name, email):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-03-19T12:00:00Z",
        "totalResults": 1,
        "reservations": [
            {
                "pnr": PNR,
                "numberInParty": 1,
                "passengers": [
                    {
                        "lastName": last_name,
                        "firstName": "PAX",
                        "nationality": "ZZ",
                        "dateOfBirth": "1970-01-01",
                        "frequentFlyerNumber": FF_NUMBER,
                        "frequentFlyerAirline": "GF",
                        "ffTierLevel": "9",
                        "ffTierName": "PLACEHOLDER",
                    }
                ],
                "emails": [email],
                "phones": [{"number": PHONE, "type": "M"}],
            }
        ],
    }


def _passenger_list(flight_number, last_name, fetched_at):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "passengers": [_passenger(last_name)],
    }


def _snapshot(flight_number, snapshot_type, sequence, data):
    return {
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": "2026-03-19T08:00:00Z",
        "data": data,
    }


def _seed():
    for name in list(FAKE_DB._collections):
        FAKE_DB._collections[name].docs = []
    FAKE_DB.reset_log()
    MEMORY_REDIS.store = {}
    MEMORY_REDIS.writes = []
    SABRE_POSTS.clear()
    PUBLISHED_TASKS.clear()
    flights._dashboard_cache.clear()

    current_passengers = _passenger_list(FLIGHT, CURRENT_NAME, "2026-03-19T12:00:00Z")
    other_passengers = _passenger_list(OTHER_FLIGHT, OTHER_NAME, "2026-03-19T12:00:00Z")
    stale_passengers = _passenger_list(FLIGHT, STALE_NAME, "2026-03-19T08:00:00Z")
    current_reservations = _reservation(CURRENT_NAME, EMAIL)
    stale_reservations = _reservation(STALE_NAME, STALE_EMAIL)
    stale_reservations["fetchedAt"] = "2026-03-19T08:00:00Z"
    status = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-03-19T12:00:00Z",
        "status": "OPEN",
        "gate": "A1",
    }
    stale_status = dict(status, fetchedAt="2026-03-19T08:00:00Z", gate="OLD")

    FAKE_DB["passenger_list"].docs.extend([current_passengers, other_passengers])
    FAKE_DB["reservations"].docs.append(current_reservations)
    FAKE_DB["flight_status"].docs.append(status)
    FAKE_DB["changes"].docs.append(
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "detectedAt": "2026-03-19T11:00:00Z",
            "changeType": "BOARDED",
            "passenger": {"pnr": PNR, "lastName": CURRENT_NAME},
        }
    )
    FAKE_DB["snapshots"].docs.extend(
        [
            _snapshot(FLIGHT, "passenger_list", 1, stale_passengers),
            _snapshot(FLIGHT, "reservations", 1, stale_reservations),
            _snapshot(FLIGHT, "flight_status", 1, stale_status),
            _snapshot(OTHER_FLIGHT, "passenger_list", 1, other_passengers),
        ]
    )
    FAKE_DB.reset_log()


def _latest(collection, flight_number):
    matched = [
        doc
        for doc in FAKE_DB[collection].docs
        if doc.get("flightNumber") == flight_number
    ]
    matched.sort(key=lambda doc: doc.get("fetchedAt", ""))
    return matched[-1] if matched else None


_install_fakes()
CLIENT = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh_state():
    _seed()
    yield
    _seed()


def _side_effects():
    return {
        "db": list(FAKE_DB.calls),
        "sabre": [post["action"] for post in SABRE_POSTS],
        "redis": list(MEMORY_REDIS.writes),
        "tasks": list(PUBLISHED_TASKS),
    }


def _assert_refused(response, marker=None):
    problems = []
    if response.status_code not in (401, 403):
        problems.append(f"status={response.status_code}")
    effects = _side_effects()
    if effects["db"] or effects["sabre"] or effects["redis"] or effects["tasks"]:
        problems.append(effects)
    if marker and marker in response.text:
        problems.append(f"body contains {marker}")
    assert not problems, {"problems": problems, "body": response.text[:2000]}


def test_health_checks_stay_open():
    assert CLIENT.get("/").status_code == 200
    assert CLIENT.get("/health/app").status_code == 200
    db_health = CLIENT.get("/health/db")
    assert db_health.status_code == 200
    assert db_health.json()["database"] == "connected"


def test_cross_origin_reads_are_limited_to_the_operations_origin():
    health = CLIENT.get("/health/app", headers={"Origin": EVIL_ORIGIN})
    assert health.headers.get("access-control-allow-origin") not in ("*", EVIL_ORIGIN)

    passengers = CLIENT.get(PASSENGERS_URL, headers={"Origin": EVIL_ORIGIN})
    assert passengers.headers.get("access-control-allow-origin") not in (
        "*",
        EVIL_ORIGIN,
    )

    preflight = CLIENT.options(
        PASSENGERS_URL,
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.headers.get("access-control-allow-origin") not in (
        "*",
        EVIL_ORIGIN,
    )

    allowed = CLIENT.get("/health/app", headers={"Origin": UI_ORIGIN})
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == UI_ORIGIN

    staff_preflight = CLIENT.options(
        PASSENGERS_URL,
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert staff_preflight.status_code == 200
    assert staff_preflight.headers.get("access-control-allow-origin") == UI_ORIGIN


def test_staff_can_read_stored_manifest_and_reservation_contacts():
    manifest = CLIENT.get(PASSENGERS_URL, headers=_bearer(STAFF_TOKEN))
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    passenger = body["passengers"][0]
    assert passenger["lastName"] == CURRENT_NAME
    assert passenger["pnr"] == PNR
    assert passenger["nationality"] == "ZZ"
    assert passenger["ffTierLevel"] == "9"

    reservations = CLIENT.get(RESERVATIONS_URL, headers=_bearer(STAFF_TOKEN))
    assert reservations.status_code == 200, reservations.text
    record = reservations.json()["reservations"][0]
    assert EMAIL in record["emails"]
    assert record["phones"][0]["number"] == PHONE
    assert record["passengers"][0]["dateOfBirth"] == "1970-01-01"
    assert record["passengers"][0]["frequentFlyerNumber"] == FF_NUMBER

    by_pnr = CLIENT.get(
        f"/flights/{FLIGHT}/reservations/{PNR}?date={DATE}",
        headers=_bearer(STAFF_TOKEN),
    )
    assert by_pnr.status_code == 200, by_pnr.text
    assert EMAIL in by_pnr.json()["reservations"][0]["emails"]


def test_anonymous_manifest_and_reservation_reads_are_refused():
    manifest = CLIENT.get(PASSENGERS_URL)
    _assert_refused(manifest, CURRENT_NAME)

    _seed()
    reservations = CLIENT.get(RESERVATIONS_URL)
    _assert_refused(reservations, EMAIL)

    _seed()
    by_pnr = CLIENT.get(f"/flights/{FLIGHT}/reservations/{PNR}?date={DATE}")
    _assert_refused(by_pnr, PHONE)

    _seed()
    one_passenger = CLIENT.get(f"/flights/{FLIGHT}/passengers/{PNR}?date={DATE}")
    _assert_refused(one_passenger, PNR)


def test_anonymous_live_sabre_detail_is_refused():
    _assert_refused(CLIENT.get(DETAIL_URL), DOCUMENT)


def test_staff_live_sabre_detail_is_refused():
    _assert_refused(CLIENT.get(DETAIL_URL, headers=_bearer(STAFF_TOKEN)), DOCUMENT)


def test_wrong_token_live_sabre_detail_is_refused():
    _assert_refused(CLIENT.get(DETAIL_URL, headers=_bearer("placeholder-wrong")), DOCUMENT)


def test_privileged_detail_returns_the_document_from_the_authorized_passenger():
    FAKE_DB.reread_passenger_list = True
    response = CLIENT.get(DETAIL_URL, headers=_bearer(PRIVILEGED_TOKEN))
    assert response.status_code == 200, response.text
    detail_calls = [
        post for post in SABRE_POSTS if post["action"] == "GetPassengerDataRQ"
    ]
    assert len(detail_calls) == 1
    assert f"<LastName>{CURRENT_NAME}</LastName>" in detail_calls[0]["body"]
    assert REREAD_NAME not in detail_calls[0]["body"]
    passenger = response.json()["passengers"][0]
    assert passenger["docsData"]["documentNumber"] == DOCUMENT
    assert passenger["docsData"]["dateOfBirth"] == "01JAN70"
    assert passenger["freeText"][0]["editCode"] == "DOCS"
    assert passenger["freeText"][0]["text"] == DOCS


def _assert_manifest_unchanged(response):
    current = _latest("passenger_list", FLIGHT)["passengers"][0]["lastName"]
    other = _latest("passenger_list", OTHER_FLIGHT)["passengers"][0]["lastName"]
    assert (
        response.status_code in (401, 403)
        and current == CURRENT_NAME
        and other == OTHER_NAME
        and not _side_effects()["db"]
    ), {
        "status": response.status_code,
        "current": current,
        "other": other,
        "effects": _side_effects(),
        "body": response.text[:1500],
    }


def test_anonymous_restore_does_not_replace_the_current_manifest():
    _assert_manifest_unchanged(CLIENT.post(RESTORE_URL))


def test_staff_restore_does_not_replace_the_current_manifest():
    _assert_manifest_unchanged(
        CLIENT.post(RESTORE_URL, headers=_bearer(STAFF_TOKEN))
    )


def test_privileged_restore_writes_only_the_requested_flight():
    FAKE_DB.reread_snapshots = True
    response = CLIENT.post(RESTORE_URL, headers=_bearer(PRIVILEGED_TOKEN))
    assert response.status_code == 200, response.text
    restored = _latest("passenger_list", FLIGHT)
    assert restored["passengers"][0]["lastName"] == STALE_NAME
    assert restored["flightNumber"] == FLIGHT
    assert _latest("reservations", FLIGHT)["reservations"][0]["emails"] == [STALE_EMAIL]
    assert _latest("passenger_list", OTHER_FLIGHT)["passengers"][0]["lastName"] == OTHER_NAME
    body = response.json()
    assert body["flightNumber"] == FLIGHT
    assert {item["targetCollection"] for item in body["restored"]} == {
        "flight_status",
        "passenger_list",
        "reservations",
    }


def _ingest_payload():
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "departureDateTime": f"{DATE}T08:00:00",
    }


def test_anonymous_synchronous_ingest_does_not_call_sabre():
    _assert_refused(CLIENT.post("/flights/ingest", json=_ingest_payload()))


def test_staff_synchronous_ingest_does_not_call_sabre():
    _assert_refused(
        CLIENT.post(
            "/flights/ingest",
            json=_ingest_payload(),
            headers=_bearer(STAFF_TOKEN),
        )
    )


def test_anonymous_batch_ingest_is_not_queued():
    response = CLIENT.post(
        "/flights/ingest/batch",
        json={"flights": [_ingest_payload()]},
    )
    _assert_refused(response)


def test_anonymous_schedule_lookup_does_not_call_sabre():
    response = CLIENT.post(
        "/flights/schedule/lookup",
        json={"airline": "GF", "flightNumber": FLIGHT, "departureDate": DATE},
    )
    _assert_refused(response)


def test_anonymous_flight_comparison_job_is_not_queued():
    _assert_refused(CLIENT.post(f"/compare/flights/async?date={DATE}"))


def test_anonymous_pnr_comparison_job_is_not_queued():
    _assert_refused(CLIENT.post(f"/compare/pnr-mismatches/async?date={DATE}"))


def test_privileged_ingest_opens_a_sabre_session():
    payload = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "departureDateTime": f"{DATE}T08:00:00",
    }
    response = CLIENT.post(
        "/flights/ingest",
        json=payload,
        headers=_bearer(PRIVILEGED_TOKEN),
    )
    assert response.status_code not in (401, 403), response.text
    assert any(post["action"] == "SessionCreateRQ" for post in SABRE_POSTS)


def test_anonymous_job_status_is_refused():
    MEMORY_REDIS.store["job:placeholder-job"] = json.dumps(
        {
            "jobId": "placeholder-job",
            "status": "completed",
            "flightsQueued": 1,
            "flightsProcessed": 1,
            "submittedAt": "2026-03-19T00:00:00Z",
            "error": JOB_MARKER,
        }
    )
    MEMORY_REDIS.writes = []
    FAKE_DB.reset_log()
    ingest_job = CLIENT.get("/flights/ingest/jobs/placeholder-job")
    _assert_refused(ingest_job, JOB_MARKER)

    MEMORY_REDIS.store["job:placeholder-job"] = json.dumps(
        {
            "jobId": "placeholder-job",
            "status": "completed",
            "flightsQueued": 1,
            "flightsProcessed": 1,
            "submittedAt": "2026-03-19T00:00:00Z",
            "error": JOB_MARKER,
        }
    )
    MEMORY_REDIS.writes = []
    comparison_job = CLIENT.get("/compare/jobs/placeholder-job")
    _assert_refused(comparison_job, JOB_MARKER)


@pytest.mark.parametrize(
    "path",
    [
        "/flights",
        f"/flights?date={DATE}",
        PASSENGERS_URL,
        f"/flights/{FLIGHT}/passengers/summary?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/passengers/standby-list?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/passengers/groups?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/passengers/{PNR}?date={DATE}",
        DETAIL_URL,
        RESERVATIONS_URL,
        f"/flights/{FLIGHT}/reservations/{PNR}?date={DATE}",
        f"/flights/{FLIGHT}/changes?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/changes/summary?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/changes/passenger/{PNR}?date={DATE}",
        f"/flights/{FLIGHT}/snapshots?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/snapshots/compare?snapshot_sequence=1&origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/status?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/status/history?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/dashboard?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/tree?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/schedule?date={DATE}",
        f"/flights/{FLIGHT}/audit?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline?date={DATE}&origin={ORIGIN}",
        f"/flights/{FLIGHT}/timeline?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/boarding-progress?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/phase-journey?origin={ORIGIN}&date={DATE}",
        f"/flights/{FLIGHT}/passengers/history-badges?origin={ORIGIN}&date={DATE}",
        f"/activity/feed?date={DATE}",
        f"/otp/flights?date={DATE}",
        f"/analytics/filters?date_from={DATE}&date_to={DATE}",
        f"/analytics/network?date_from={DATE}&date_to={DATE}",
        f"/data-audit/{FLIGHT}/compare?origin={ORIGIN}&date={DATE}",
        f"/data-audit/{FLIGHT}/passengers?origin={ORIGIN}&date={DATE}",
        f"/compare/flights?date={DATE}",
        f"/compare/passengers?date={DATE}",
        f"/compare/pnr-mismatches?date={DATE}",
        f"/compare/status-distribution?date={DATE}",
        f"/compare/change-summary?date={DATE}",
    ],
)
def test_anonymous_data_routes_are_refused_before_any_lookup(path):
    response = CLIENT.get(path)
    _assert_refused(response)


def test_anonymous_event_stream_is_refused_before_it_starts():
    missing = CLIENT.get("/events/jobs/placeholder-job")
    _assert_refused(missing)


def test_anonymous_global_event_stream_is_refused():
    with CLIENT.stream("GET", "/events/jobs") as stream:
        status = stream.status_code
    assert status in (401, 403)
    assert _side_effects()["sabre"] == []
    assert _side_effects()["tasks"] == []
