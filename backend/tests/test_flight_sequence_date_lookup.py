"""Sequence lookup must follow the Sabre departure date, not otp.flight_date.

These tests fail while the feeder resolves a missing flightSequenceNumber with:

    WHERE flight_number = %s AND scheduled_origin = %s AND flight_date = %s

Overnight OTP rows keep the operational flight_date on the previous calendar
day and put the local departure on scheduled_departure_local. Quick ingest
(flight workbench and the manual ingestion panel) and the synchronous and
batch ingest endpoints all reach run_feeder without a sequence. The sequence
that lookup returns is written onto the live flights document.

The date chain asserted here is the one the OTP list already uses for Sabre:
scheduled_departure_local's calendar date, then local_flight_date, then
leg_departure_date, and only then flight_date.

data_audit._fetch_pg filters otp.flight_xml_current by flight_date when a
date is supplied. That read path does not choose the sequence written during
ingest, so it is not covered here.

Requires a local PostgreSQL reachable by peer auth. The fixture creates the
database named by POSTGRES_URI (default dbname fe_seq_test).
"""

import copy
import os
import sys

import psycopg2
import pytest
from pymongo import DESCENDING

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault(
    "POSTGRES_URI",
    "dbname=fe_seq_test host=/var/run/postgresql user=ubuntu",
)

from backend.api.postgres import close_pool
from backend.api.routes.ingestion import SabreIngestRequest
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreError


_MAINTENANCE_DSN = "dbname=postgres host=/var/run/postgresql user=ubuntu"
_ORIGIN = "BAH"
_AIRLINE = "GF"


class MemoryCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            specs = list(key)
        else:
            specs = [(key, direction)]
        for field, direc in reversed(specs):
            self._docs.sort(
                key=lambda doc, field=field: (
                    doc.get(field) is None,
                    doc.get(field),
                ),
                reverse=direc == DESCENDING,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    """In-process stand-in for the MongoDB collections storage.py writes.

    pymongo Collection.update_one forwards the filter and update document to
    the server (pymongo 4.20 synchronous/collection.py update_one). This
    applies the operators update_flight_state sends: $set, $inc, and
    $setOnInsert, and on insert copies equality fields from the filter.
    insert_one returns an object with inserted_id, which store_flight_status
    reads before update_flight_state runs.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"mem-{len(self.docs)}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, filt, *args, sort=None, projection=None):
        if args:
            projection = args[0]
        matches = [doc for doc in self.docs if _matches(doc, filt)]
        if sort:
            matches = list(MemoryCursor(matches).sort(sort))
        if not matches:
            return None
        return _project(matches[0], projection)

    def find(self, filt, *args, sort=None, projection=None):
        if args:
            projection = args[0]
        matches = [_project(doc, projection) for doc in self.docs if _matches(doc, filt)]
        cursor = MemoryCursor(matches)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, filt):
                _apply_update(doc, update, inserted=False)
                return
        if not upsert:
            return
        created = {}
        for key, value in filt.items():
            if not str(key).startswith("$"):
                created[key] = copy.deepcopy(value)
        created["_id"] = f"mem-{len(self.docs)}"
        _apply_update(created, update, inserted=True)
        self.docs.append(created)


class MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        collection = self._collections.get(name)
        if collection is None:
            collection = MemoryCollection()
            self._collections[name] = collection
        return collection


def _matches(doc, filt):
    return all(doc.get(key) == value for key, value in filt.items())


def _project(doc, projection):
    copied = copy.deepcopy(doc)
    if not projection:
        return copied
    keep = {key for key, flag in projection.items() if flag}
    keep.add("_id")
    return {key: value for key, value in copied.items() if key in keep}


def _apply_update(doc, update, inserted):
    if inserted:
        for key, value in (update.get("$setOnInsert") or {}).items():
            doc[key] = copy.deepcopy(value)
    for key, value in (update.get("$set") or {}).items():
        doc[key] = copy.deepcopy(value)
    for key, value in (update.get("$inc") or {}).items():
        doc[key] = doc.get(key, 0) + value


class _SabreStandIn:
    """Avoids the network. Flight status still runs the real storage pipeline."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        return (
            {},
            "<Response/>",
            {"requestXml": "<Request/>", "httpStatus": 200, "durationMs": 1},
        )

    def __getattr__(self, name):
        def _unavailable(*args, **kwargs):
            raise SabreError(f"{name} unavailable in test")

        return _unavailable


def _ensure_database():
    uri = os.environ["POSTGRES_URI"]
    dbname = "fe_seq_test"
    for part in uri.split():
        if part.startswith("dbname="):
            dbname = part.split("=", 1)[1]
    maintenance = psycopg2.connect(_MAINTENANCE_DSN)
    maintenance.autocommit = True
    with maintenance.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{dbname}"')
    maintenance.close()

    conn = psycopg2.connect(uri)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS otp")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
                flight_sequence_number integer PRIMARY KEY,
                flight_number text NOT NULL,
                scheduled_origin text NOT NULL,
                flight_date date,
                local_flight_date date,
                leg_departure_date date,
                scheduled_departure_local timestamp
            )
            """
        )
    conn.close()


def _connect():
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = True
    return conn


def _insert_otp(
    sequence,
    flight_number,
    flight_date,
    scheduled_departure_local=None,
    local_flight_date=None,
    leg_departure_date=None,
):
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otp.flight_xml_current (
                flight_sequence_number, flight_number, scheduled_origin,
                flight_date, local_flight_date, leg_departure_date,
                scheduled_departure_local
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sequence,
                flight_number,
                _ORIGIN,
                flight_date,
                local_flight_date,
                leg_departure_date,
                scheduled_departure_local,
            ),
        )
    conn.close()


def _clear_flight(flight_number):
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM otp.flight_xml_current WHERE flight_number = %s",
            (flight_number,),
        )
    conn.close()


def _ingest(flight_number, departure_date, sequence=None):
    """Build the payload the ingest endpoints pass to run_feeder.

    SabreIngestRequest.model_dump(mode="json") is what POST /flights/ingest
    and POST /flights/ingest/batch do before calling run_feeder. Omitting
    flightSequenceNumber leaves it null, which is the workbench quick-ingest
    and ingestion-panel payload.
    """
    body = {
        "airline": _AIRLINE,
        "flightNumber": flight_number,
        "origin": _ORIGIN,
        "departureDate": departure_date,
        "departureDateTime": f"{departure_date}T01:15:00",
    }
    if sequence is not None:
        body["flightSequenceNumber"] = sequence
    dumped = SabreIngestRequest(**body).model_dump(mode="json")
    if sequence is None:
        assert dumped["flightSequenceNumber"] is None
    result = run_feeder([dumped])
    return result["results"][0]


def _stored_sequence(flight_number, departure_date):
    doc = storage.get_db()["flights"].find_one(
        {
            "airline": _AIRLINE,
            "flightNumber": flight_number,
            "origin": _ORIGIN,
            "departureDate": departure_date,
        }
    )
    assert doc is not None, "ingest did not write a flights document"
    assert doc.get("snapshotCount", 0) >= 1
    return doc.get("flightSequenceNumber")


@pytest.fixture(scope="session", autouse=True)
def _otp_schema():
    _ensure_database()


@pytest.fixture(autouse=True)
def _storage_and_sabre(monkeypatch):
    storage.init_db(MemoryDatabase())
    monkeypatch.setattr("backend.feeder.runner.SabreClient", _SabreStandIn)
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE otp.flight_xml_current")
    conn.close()
    yield
    close_pool()
    storage.init_db(None)


def test_omitted_sequence_matches_local_departure_not_other_flight_date():
    """A same-number row whose flight_date equals the Sabre day must not win."""
    flight_number = "4101"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    # Insert the decoy first. Its flight_date is the requested calendar day,
    # but its local departure is the next day.
    _insert_otp(
        222,
        flight_number,
        flight_date="2026-03-20",
        local_flight_date="2026-03-21",
        leg_departure_date="2026-03-21",
        scheduled_departure_local="2026-03-21 00:30:00",
    )
    _insert_otp(
        111,
        flight_number,
        flight_date="2026-03-19",
        local_flight_date="2026-03-20",
        leg_departure_date="2026-03-20",
        scheduled_departure_local="2026-03-20 00:40:00",
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] == 111
    assert _stored_sequence(flight_number, sabre_date) == 111


def test_omitted_sequence_finds_overnight_row_when_flight_date_differs():
    flight_number = "4102"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        111,
        flight_number,
        flight_date="2026-03-19",
        local_flight_date="2026-03-20",
        leg_departure_date="2026-03-20",
        scheduled_departure_local="2026-03-20 00:40:00",
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] == 111
    assert _stored_sequence(flight_number, sabre_date) == 111


def test_omitted_sequence_uses_local_flight_date_when_scheduled_local_is_null():
    flight_number = "4103"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        333,
        flight_number,
        flight_date="2026-03-19",
        local_flight_date="2026-03-20",
        leg_departure_date="2026-03-19",
        scheduled_departure_local=None,
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] == 333
    assert _stored_sequence(flight_number, sabre_date) == 333


def test_omitted_sequence_uses_leg_departure_date_when_earlier_dates_are_null():
    flight_number = "4104"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        444,
        flight_number,
        flight_date="2026-03-19",
        local_flight_date=None,
        leg_departure_date="2026-03-20",
        scheduled_departure_local=None,
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] == 444
    assert _stored_sequence(flight_number, sabre_date) == 444


def test_omitted_sequence_keeps_flight_date_when_it_is_the_only_date():
    flight_number = "4105"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        555,
        flight_number,
        flight_date="2026-03-20",
        local_flight_date=None,
        leg_departure_date=None,
        scheduled_departure_local=None,
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] == 555
    assert _stored_sequence(flight_number, sabre_date) == 555


def test_lower_priority_date_does_not_match_when_scheduled_local_disagrees():
    flight_number = "4106"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        666,
        flight_number,
        flight_date="2026-03-20",
        local_flight_date="2026-03-20",
        leg_departure_date="2026-03-20",
        scheduled_departure_local="2026-03-21 10:00:00",
    )

    outcome = _ingest(flight_number, sabre_date)

    assert outcome["flight"]["flightSequenceNumber"] is None
    assert _stored_sequence(flight_number, sabre_date) is None


def test_supplied_sequence_is_stored_unchanged():
    """Today-page ingest sends a sequence. Lookup must not replace it."""
    flight_number = "4107"
    sabre_date = "2026-03-20"
    _clear_flight(flight_number)
    _insert_otp(
        222,
        flight_number,
        flight_date="2026-03-20",
        local_flight_date="2026-03-21",
        leg_departure_date="2026-03-21",
        scheduled_departure_local="2026-03-21 00:30:00",
    )
    _insert_otp(
        111,
        flight_number,
        flight_date="2026-03-19",
        local_flight_date="2026-03-20",
        leg_departure_date="2026-03-20",
        scheduled_departure_local="2026-03-20 00:40:00",
    )

    outcome = _ingest(flight_number, sabre_date, sequence=777)

    assert outcome["flight"]["flightSequenceNumber"] == 777
    assert _stored_sequence(flight_number, sabre_date) == 777


def test_batch_payloads_resolve_each_omitted_sequence_by_local_departure():
    """POST /flights/ingest/batch dumps each flight and passes the list through."""
    first = "4108"
    second = "4109"
    sabre_date = "2026-04-02"
    _clear_flight(first)
    _clear_flight(second)
    _insert_otp(
        811,
        first,
        flight_date="2026-04-01",
        local_flight_date="2026-04-02",
        leg_departure_date="2026-04-02",
        scheduled_departure_local="2026-04-02 00:10:00",
    )
    _insert_otp(
        822,
        second,
        flight_date="2026-04-02",
        local_flight_date="2026-04-03",
        leg_departure_date="2026-04-03",
        scheduled_departure_local="2026-04-03 00:10:00",
    )
    _insert_otp(
        922,
        second,
        flight_date="2026-04-01",
        local_flight_date="2026-04-02",
        leg_departure_date="2026-04-02",
        scheduled_departure_local="2026-04-02 00:20:00",
    )

    payloads = []
    for flight_number in (first, second):
        dumped = SabreIngestRequest(
            airline=_AIRLINE,
            flightNumber=flight_number,
            origin=_ORIGIN,
            departureDate=sabre_date,
            departureDateTime=f"{sabre_date}T01:15:00",
        ).model_dump(mode="json")
        assert dumped["flightSequenceNumber"] is None
        payloads.append(dumped)

    results = run_feeder(payloads)["results"]

    assert results[0]["flight"]["flightSequenceNumber"] == 811
    assert _stored_sequence(first, sabre_date) == 811
    assert results[1]["flight"]["flightSequenceNumber"] == 922
    assert _stored_sequence(second, sabre_date) == 922
