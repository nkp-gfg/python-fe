"""Re-ingest must keep the flight sequence the board already loaded.

These tests fail while a missing flightSequenceNumber is treated as optional:
the feeder looks the flight up by flight_date equality and, when that lookup
returns nothing, stores a Sabre business-error body as the current flight
status. They pass once a missing sequence is rejected before anything is
written, and once a supplied sequence is the sequence that is stored.
"""

from __future__ import annotations

import copy
import os
import subprocess
import uuid

# Placeholders only. Set before backend imports so load_dotenv cannot replace
# them, and so the Sabre client never opens a real session.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "https://sabre.invalid"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["POSTGRES_URI"] = "postgresql:///falconeye?host=/var/run/postgresql"
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

import psycopg2
import pytest
from fastapi import HTTPException

from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre import client as sabre_client
from backend.tasks.ingestion import run_batch_ingestion

AIRLINE = "GF"
FLIGHT_NUMBER = "70"
ORIGIN = "BAH"
LOCAL_DEPARTURE_DATE = "2026-03-20"
OPERATIONAL_FLIGHT_DATE = "2026-03-19"
BOARD_SEQUENCE = 1001
OTHER_DEPARTURE_SEQUENCE = 1002
SEEDED_STATUS = "OPENCI"
SEEDED_GATE = "A12"
SEEDED_FETCHED_AT = "2026-03-19T22:00:00Z"


def _envelope(inner: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    {inner}
  </soap-env:Body>
</soap-env:Envelope>"""


def _flight_detail_body(result_xml: str, itinerary_xml: str = "") -> str:
    # Same envelope shape as sample_data ACS_FlightDetailRS: ns3 body, ns2 result fields.
    return _envelope(
        f"""<ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      {itinerary_xml}
      {result_xml}
    </ns3:ACS_FlightDetailRS>"""
    )


def _business_error_xml(message: str) -> str:
    return _flight_detail_body(
        f"""<Result messageId="ID-test" timeStamp="2026-03-20T02:00:00Z">
        <ns2:Status>BusinessLogicError</ns2:Status>
        <ns2:CompletionStatus>NotProcessed</ns2:CompletionStatus>
        <ns2:SystemSpecificResults>
          <ns2:ErrorMessage code="2566">{message}</ns2:ErrorMessage>
        </ns2:SystemSpecificResults>
      </Result>"""
    )


def _live_itinerary_xml() -> str:
    return _flight_detail_body(
        """<Result messageId="ID-test" timeStamp="2026-03-20T02:00:00Z">
        <ns2:Status>Success</ns2:Status>
        <ns2:CompletionStatus>Complete</ns2:CompletionStatus>
        <ns2:System>ACS-BSO</ns2:System>
      </Result>""",
        f"""<ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT_NUMBER}</Flight>
          <Origin>{ORIGIN}</Origin>
          <DepartureDate>{LOCAL_DEPARTURE_DATE}</DepartureDate>
          <ScheduledDepartureDate>{LOCAL_DEPARTURE_DATE}</ScheduledDepartureDate>
          <ScheduledDepartureTime>01:30AM</ScheduledDepartureTime>
          <DepartureGate>B41</DepartureGate>
          <FlightStatus>OPENCI</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>""",
    )


SESSION_XML = _envelope("")
EMPTY_SOAP = _envelope("<noop/>")


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Subset of pymongo collection used by backend.feeder.storage."""

    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: dict = {}

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", str(uuid.uuid4()))
        self.docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    @staticmethod
    def _matches(doc, filt):
        return all(doc.get(key) == value for key, value in (filt or {}).items())

    def _sorted(self, docs, sort):
        ordered = list(docs)
        for key, direction in reversed(list(sort or [])):
            ordered.sort(
                key=lambda doc, field=key: (doc.get(field) is None, doc.get(field)),
                reverse=direction < 0,
            )
        return ordered

    def find(self, filt=None, *args, **kwargs):
        matched = [copy.deepcopy(doc) for doc in self.docs if self._matches(doc, filt)]
        return _Cursor(self._sorted(matched, kwargs.get("sort")))

    def find_one(self, filt=None, *args, **kwargs):
        docs = list(self.find(filt, sort=kwargs.get("sort")))
        if not docs:
            return None
        doc = docs[0]
        projection = kwargs.get("projection")
        if not projection:
            return doc
        included = {key for key, flag in projection.items() if flag and key != "_id"}
        projected = {}
        if projection.get("_id", 1) and "_id" in doc:
            projected["_id"] = doc["_id"]
        for key in included:
            if key in doc:
                projected[key] = doc[key]
        return projected

    def update_one(self, filt, update, upsert=False, **kwargs):
        for doc in self.docs:
            if self._matches(doc, filt):
                self._apply(doc, update, on_insert=False)
                return
        if not upsert:
            return
        doc = {key: copy.deepcopy(value) for key, value in filt.items()}
        doc["_id"] = str(uuid.uuid4())
        self._apply(doc, update, on_insert=True)
        self.docs.append(doc)

    @staticmethod
    def _apply(doc, update, on_insert):
        if on_insert:
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = copy.deepcopy(value)
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value

    def index_information(self):
        return dict(self.indexes)

    def create_index(self, keys, **kwargs):
        name = kwargs.get("name") or "idx"
        self.indexes[name] = {"key": keys}
        return name

    def drop_index(self, name):
        self.indexes.pop(name, None)


class _Database:
    def __init__(self):
        self._collections: dict[str, _Collection] = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())


def _latest_board_status(db, departure_date=LOCAL_DEPARTURE_DATE):
    """Mirror flights._latest_per_flight_agg: newest fetchedAt per flight key."""
    matches = [
        doc
        for doc in db["flight_status"].docs
        if doc.get("airline", "GF") == AIRLINE
        and doc.get("flightNumber") == FLIGHT_NUMBER
        and doc.get("origin", "") == ORIGIN
        and doc.get("departureDate", "") == departure_date
    ]
    matches.sort(key=lambda doc: doc.get("fetchedAt", ""), reverse=True)
    return matches[0] if matches else None


def _seed_live_board(db):
    db["flight_status"].insert_one(
        {
            "airline": AIRLINE,
            "flightNumber": FLIGHT_NUMBER,
            "origin": ORIGIN,
            "departureDate": LOCAL_DEPARTURE_DATE,
            "fetchedAt": SEEDED_FETCHED_AT,
            "status": SEEDED_STATUS,
            "gate": SEEDED_GATE,
            "schedule": {
                "scheduledDeparture": f"{LOCAL_DEPARTURE_DATE}T01:30AM",
            },
        }
    )
    db["flights"].insert_one(
        {
            "airline": AIRLINE,
            "flightNumber": FLIGHT_NUMBER,
            "origin": ORIGIN,
            "departureDate": LOCAL_DEPARTURE_DATE,
            "flightSequenceNumber": BOARD_SEQUENCE,
            "summary": {"status": SEEDED_STATUS, "gate": SEEDED_GATE},
            "snapshotCount": 4,
        }
    )


def _board_payload(sequence):
    payload = {
        "airline": AIRLINE,
        "flightNumber": FLIGHT_NUMBER,
        "origin": ORIGIN,
        "departureDate": LOCAL_DEPARTURE_DATE,
        "departureDateTime": f"{LOCAL_DEPARTURE_DATE}T01:30:00",
    }
    if sequence is not None:
        payload["flightSequenceNumber"] = sequence
    return payload


def _connect_otp():
    """Peer-auth local Postgres. No password is stored in this test."""
    try:
        return psycopg2.connect(os.environ["POSTGRES_URI"])
    except psycopg2.OperationalError:
        subprocess.run(["sudo", "pg_ctlcluster", "16", "main", "start"], check=False)
        subprocess.run(
            [
                "sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c",
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ubuntu') "
                "THEN CREATE ROLE ubuntu SUPERUSER LOGIN; END IF; END $$;",
            ],
            check=False,
        )
        subprocess.run(
            [
                "sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c",
                "SELECT 'CREATE DATABASE falconeye OWNER ubuntu' "
                "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'falconeye')\\gexec",
            ],
            check=False,
        )
        return psycopg2.connect(os.environ["POSTGRES_URI"])


@pytest.fixture
def otp_rows():
    connection = _connect_otp()
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS otp")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS otp.flight_xml_current (
            flight_sequence_number integer PRIMARY KEY,
            flight_number text NOT NULL,
            scheduled_origin text NOT NULL,
            flight_date date NOT NULL
        )
        """
    )
    cursor.execute("TRUNCATE otp.flight_xml_current")
    yield cursor
    cursor.execute("TRUNCATE otp.flight_xml_current")
    cursor.close()
    connection.close()


@pytest.fixture
def db(monkeypatch):
    memory = _Database()
    storage.init_db(memory)
    yield memory
    storage._db = None
    storage._client = None
    storage._owns_connection = False


def _install_rows(cursor, rows):
    for sequence, flight_date in rows:
        cursor.execute(
            """
            INSERT INTO otp.flight_xml_current
                (flight_sequence_number, flight_number, scheduled_origin, flight_date)
            VALUES (%s, %s, %s, %s)
            """,
            (sequence, FLIGHT_NUMBER, ORIGIN, flight_date),
        )


def _sabre(monkeypatch, flight_xml):
    calls = []

    def post(url, data=None, headers=None, timeout=None):
        assert str(url).startswith("https://sabre.invalid"), url
        action = (headers or {}).get("SOAPAction")
        calls.append(action)
        if action == "SessionCreateRQ":
            return _HttpResponse(SESSION_XML)
        if action == "SessionCloseRQ":
            return _HttpResponse("<ok/>")
        if action == "ACS_FlightDetailRQ":
            calls.append(data)
            return _HttpResponse(flight_xml)
        return _HttpResponse(EMPTY_SOAP)

    monkeypatch.setattr(sabre_client.requests, "post", post)
    return calls


class _HttpResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _assert_live_board_untouched(db):
    current = _latest_board_status(db)
    assert current is not None
    flight = db["flights"].docs[0]
    assert flight["flightSequenceNumber"] == BOARD_SEQUENCE
    assert current["status"] == SEEDED_STATUS
    assert current["gate"] == SEEDED_GATE
    assert current["fetchedAt"] == SEEDED_FETCHED_AT
    assert flight["summary"]["status"] == SEEDED_STATUS
    assert flight["summary"]["gate"] == SEEDED_GATE
    assert flight["snapshotCount"] == 4
    # A write that lands before the ingest is refused still fails.
    assert len(db["flight_status"].docs) == 1
    assert db["snapshots"].docs == []
    assert db["sabre_requests"].docs == []


def _run(payload):
    return run_feeder([payload])


def test_missing_sequence_does_not_store_flight_not_initialized(db, otp_rows, monkeypatch):
    _install_rows(otp_rows, ((BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),))
    _seed_live_board(db)
    _sabre(monkeypatch, _business_error_xml("FLIGHT NOT INITIALIZED"))

    _run(_board_payload(sequence=None))

    _assert_live_board_untouched(db)


def test_missing_sequence_does_not_store_invalid_date_or_city(db, otp_rows, monkeypatch):
    _install_rows(otp_rows, ((BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),))
    _seed_live_board(db)
    _sabre(monkeypatch, _business_error_xml("INVALID DATE OR CITY"))

    _run(_board_payload(sequence=None))

    _assert_live_board_untouched(db)


def test_missing_sequence_does_not_store_error_code_2566(db, otp_rows, monkeypatch):
    _install_rows(otp_rows, ())
    _seed_live_board(db)
    _sabre(monkeypatch, _business_error_xml("UNAVAILABLE"))

    _run(_board_payload(sequence=None))

    _assert_live_board_untouched(db)


def test_missing_sequence_does_not_attach_lookup_on_overnight_departure(db, otp_rows, monkeypatch):
    _install_rows(
        otp_rows,
        (
            (BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),
            (OTHER_DEPARTURE_SEQUENCE, LOCAL_DEPARTURE_DATE),
        ),
    )
    _seed_live_board(db)
    _sabre(monkeypatch, _live_itinerary_xml())

    _run(_board_payload(sequence=None))

    # Looking the departure up again and storing that sequence still fails this check.
    _assert_live_board_untouched(db)


def test_supplied_sequence_rejects_business_error_without_replacing_board(db, otp_rows, monkeypatch):
    _install_rows(
        otp_rows,
        (
            (BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),
            (OTHER_DEPARTURE_SEQUENCE, LOCAL_DEPARTURE_DATE),
        ),
    )
    _seed_live_board(db)
    _sabre(monkeypatch, _business_error_xml("FLIGHT NOT INITIALIZED"))

    result = _run(_board_payload(sequence=BOARD_SEQUENCE))

    flight_result = result["results"][0]
    assert flight_result["success"] is False
    assert "FLIGHT NOT INITIALIZED" in flight_result["apis"]["flightStatus"]["error"]
    _assert_live_board_untouched(db)


def test_supplied_sequence_is_not_replaced_by_a_later_lookup(db, otp_rows, monkeypatch):
    _install_rows(
        otp_rows,
        (
            (BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),
            (OTHER_DEPARTURE_SEQUENCE, LOCAL_DEPARTURE_DATE),
        ),
    )
    _seed_live_board(db)
    _sabre(monkeypatch, _live_itinerary_xml())

    result = _run(_board_payload(sequence=BOARD_SEQUENCE))

    assert result["results"][0]["apis"]["flightStatus"]["status"] == "success"
    current = _latest_board_status(db)
    assert current["gate"] == "B41"
    assert current["fetchedAt"] != SEEDED_FETCHED_AT
    flight = db["flights"].docs[0]
    assert flight["flightSequenceNumber"] == BOARD_SEQUENCE
    assert result["results"][0]["flight"]["flightSequenceNumber"] == BOARD_SEQUENCE


def test_sync_ingest_rejects_board_payload_without_sequence(db, otp_rows, monkeypatch):
    _install_rows(otp_rows, ((BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),))
    _seed_live_board(db)
    _sabre(monkeypatch, _business_error_xml("FLIGHT NOT INITIALIZED"))
    request = SabreIngestRequest(**_board_payload(sequence=None))

    with pytest.raises(HTTPException):
        import asyncio

        asyncio.run(ingest_flight(request))

    _assert_live_board_untouched(db)


def test_batch_task_rejects_board_payload_without_sequence(db, otp_rows, monkeypatch):
    _install_rows(
        otp_rows,
        (
            (BOARD_SEQUENCE, OPERATIONAL_FLIGHT_DATE),
            (OTHER_DEPARTURE_SEQUENCE, LOCAL_DEPARTURE_DATE),
        ),
    )
    _seed_live_board(db)
    _sabre(monkeypatch, _live_itinerary_xml())
    request = SabreIngestRequest(**_board_payload(sequence=None))
    payloads = [request.model_dump(mode="json")]

    import backend.tasks.ingestion as ingestion_task

    for name in (
        "set_job_running",
        "set_job_progress",
        "set_ingestion_job_finished",
        "set_job_failed",
        "cache_invalidate_pattern",
    ):
        monkeypatch.setattr(ingestion_task, name, lambda *args, **kwargs: None)

    run_batch_ingestion.run("job-reingest", payloads)

    _assert_live_board_untouched(db)
