"""Multi-itinerary ACS flight detail must not abort the rest of a batch.

These tests describe the behavior required once the bug is fixed. They fail
while convert_flight_status treats ItineraryInfoResponse as a single object
and while run_feeder lets that AttributeError escape the flight-status step.

The ACS body is not a hand-built dict. It is SOAP XML, parsed by
SabreClient._parse_xml (xmltodict) and SabreClient._extract_body, which is
the same pair get_flight_status uses after the HTTP response is read.

Sabre itself is not called. requests.post is replaced with a response chosen
from the SOAP action and the flight number in the request body. MongoDB and
Redis are replaced with in-memory stand-ins that implement the operations
storage.py and redis_client.py actually call. Placeholder Sabre settings are
environment variables only; they are not credentials.

Not covered as its own process: `python -m backend.feeder.runner` parses a
local JSON file and calls the same run_feeder function exercised here.
"""

from __future__ import annotations

import os

# Class attribute is read when SabreClient is defined. Set this before import.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.redis_client import get_job
from backend.feeder import storage
from backend.feeder.converter import convert_flight_status
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient
from backend.tasks.ingestion import run_batch_ingestion

SabreClient.API_CALL_DELAY = 0.0


MATCHING_STATUS = "PDC"
MATCHING_GATE = "A12"
MATCHING_AIRCRAFT = "32N"
OTHER_STATUS = "OPENCI"
OTHER_GATE = "B41"
SECOND_FLIGHT_GATE = "C3"


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return _Cursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


def _matches(doc, filt):
    for key, expected in (filt or {}).items():
        if doc.get(key) != expected:
            return False
    return True


class MemoryCollection:
    """Subset of a Mongo collection used by backend.feeder.storage."""

    def __init__(self):
        self.docs = []
        self.indexes = {}

    def insert_one(self, doc):
        stored = dict(doc)
        stored.setdefault("_id", len(self.docs) + 1)
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def _sorted(self, docs, sort):
        ordered = list(docs)
        for key, direction in reversed(sort or []):
            ordered.sort(key=lambda item: item.get(key, 0), reverse=direction < 0)
        return ordered

    def find_one(self, filt=None, sort=None, projection=None):
        matches = self._sorted(
            [doc for doc in self.docs if _matches(doc, filt)], sort)
        if not matches:
            return None
        doc = matches[0]
        if not projection:
            return doc
        return {key: doc.get(key) for key, include in projection.items() if include}

    def find(self, filt=None, sort=None, projection=None):
        docs = self._sorted(
            [doc for doc in self.docs if _matches(doc, filt)], sort)
        if projection:
            docs = [
                {key: doc.get(key) for key, include in projection.items() if include}
                for doc in docs
            ]
        return _Cursor(docs)

    def update_one(self, filt, update, upsert=False):
        matches = [doc for doc in self.docs if _matches(doc, filt)]
        if matches:
            target = matches[0]
        elif upsert:
            target = {}
            target.update(update.get("$setOnInsert") or {})
            self.docs.append(target)
        else:
            return
        for key, value in (update.get("$set") or {}).items():
            target[key] = value
        for key, value in (update.get("$inc") or {}).items():
            target[key] = target.get(key, 0) + value

    def index_information(self):
        return dict(self.indexes)

    def drop_index(self, name):
        self.indexes.pop(name, None)

    def create_index(self, keys, name=None, unique=False):
        self.indexes[name or repr(keys)] = {"key": keys, "unique": unique}


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


class MemoryRedis:
    """Subset of Redis used by the ingestion job store."""

    def __init__(self):
        self.store = {}
        self.published = []

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def scan(self, cursor=0, match=None, count=None):
        return 0, []

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _itinerary(origin, flight, status, gate, aircraft, departure_date, departure_time):
    return f"""<ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <Origin>{origin}</Origin>
          <DepartureDate>{departure_date}</DepartureDate>
          <DepartureTime>{departure_time}</DepartureTime>
          <ScheduledDepartureDate>{departure_date}</ScheduledDepartureDate>
          <ScheduledDepartureTime>{departure_time}</ScheduledDepartureTime>
          <EstimatedDepartureDate>{departure_date}</EstimatedDepartureDate>
          <EstimatedDepartureTime>{departure_time}</EstimatedDepartureTime>
          <DepartureGate>{gate}</DepartureGate>
          <AircraftType>{aircraft}</AircraftType>
          <FlightStatus>{status}</FlightStatus>
        </ItineraryInfoResponse>"""


def acs_flight_detail_xml(itineraries):
    """SOAP envelope shaped like the captured ACS_FlightDetailRS samples.

    ItineraryResponseList may contain up to 99 ItineraryInfoResponse elements
    (ACS_FlightDetailRQRS_v3-0-0.xsd, ItineraryResponseList.ACS).
    """
    body = "\n".join(itineraries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        {body}
      </ItineraryResponseList>
      <LegInfoList>
        <LegInfo><City>BAH</City><LegStatus>OPEN</LegStatus></LegInfo>
      </LegInfoList>
      <PassengerCounts classOfService="Y">
        <Authorized>100</Authorized>
        <Booked>10</Booked>
        <Available>90</Available>
        <Thru>0</Thru>
        <Local>10</Local>
        <NonRevenueLocal>0</NonRevenueLocal>
        <TotalOnBoard>0</TotalOnBoard>
        <TotalBoardingPassIssued>0</TotalBoardingPassIssued>
      </PassengerCounts>
      <JumpSeat>
        <Cockpit inUse="false">0</Cockpit>
        <Cabin inUse="false">0</Cabin>
      </JumpSeat>
      <Result>
        <ns2:Status>Success</ns2:Status>
        <ns2:CompletionStatus>Complete</ns2:CompletionStatus>
      </Result>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def parse_flight_detail_body(xml_text):
    """Run the product's SOAP parser and body extractor."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(
        SabreClient.__new__(SabreClient), parsed, "ns3:ACS_FlightDetailRS")


def multi_itinerary_xml():
    return acs_flight_detail_xml([
        _itinerary("LHR", "2006", OTHER_STATUS, OTHER_GATE, "789",
                   "2026-03-19", "09:50PM"),
        _itinerary("BAH", "2006", MATCHING_STATUS, MATCHING_GATE, MATCHING_AIRCRAFT,
                   "2026-04-02", "01:15AM"),
    ])


def single_itinerary_xml():
    return acs_flight_detail_xml([
        _itinerary("BOM", "2057", "OPENCI", SECOND_FLIGHT_GATE, "320",
                   "2026-03-19", "02:40AM"),
    ])


SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
</soap-env:Envelope>"""


FLIGHTS = [
    {
        "airline": "GF",
        "flightNumber": "2006",
        "origin": "BAH",
        "departureDate": "2026-04-02",
        "departureDateTime": "2026-04-02T01:15:00",
        "flightSequenceNumber": 101,
    },
    {
        "airline": "GF",
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T02:40:00",
        "flightSequenceNumber": 202,
    },
]


def install_sabre_transport(monkeypatch):
    """Return ACS XML for flight detail and a session token for session calls.

    Every other Sabre action responds with HTTP 500 so the feeder's existing
    SabreError handlers skip that call. Flight detail is the call under test.
    """
    seen = []

    def _post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        body = data if isinstance(data, str) else ""
        seen.append(action)
        if action == "SessionCreateRQ":
            return _Response(200, SESSION_XML)
        if action == "SessionCloseRQ":
            return _Response(200, "<ok/>")
        if action == "ACS_FlightDetailRQ":
            if "<Flight>2057</Flight>" in body:
                return _Response(200, single_itinerary_xml())
            if "<Flight>2006</Flight>" in body:
                return _Response(200, multi_itinerary_xml())
        return _Response(500, "unavailable")

    monkeypatch.setattr("backend.sabre.client.requests.post", _post)
    return seen


def install_datastores(monkeypatch):
    db = MemoryDB()
    redis = MemoryRedis()
    storage.init_db(db)
    storage.ensure_indexes()
    monkeypatch.setattr("backend.api.redis_client.get_redis", lambda: redis)
    return db, redis


def stored_status(db, flight_number):
    docs = [
        doc for doc in db["flight_status"].docs
        if doc.get("flightNumber") == flight_number
    ]
    return docs


def test_repeated_itinerary_info_response_converts_the_requested_origin():
    """A schema-valid repeated ItineraryInfoResponse converts without error.

    The element that matches the requested origin supplies the flight status,
    gate, and aircraft. The sibling itinerary is a different origin.
    """
    body = parse_flight_detail_body(multi_itinerary_xml())
    repeated = body["ItineraryResponseList"]["ItineraryInfoResponse"]
    assert isinstance(repeated, list) and len(repeated) == 2

    bah = convert_flight_status(body, "GF", "2006", "BAH", "2026-04-02")
    assert bah["status"] == MATCHING_STATUS
    assert bah["gate"] == MATCHING_GATE
    assert bah["aircraft"]["type"] == MATCHING_AIRCRAFT
    assert bah["schedule"]["scheduledDeparture"] == "2026-04-02T01:15AM"
    assert bah["origin"] == "BAH"

    # The requested origin is the first element here, so choosing the last
    # repeated element is not enough.
    lhr = convert_flight_status(body, "GF", "2006", "LHR", "2026-03-19")
    assert lhr["status"] == OTHER_STATUS
    assert lhr["gate"] == OTHER_GATE
    assert lhr["aircraft"]["type"] == "789"
    assert lhr["schedule"]["scheduledDeparture"] == "2026-03-19T09:50PM"
    assert lhr["origin"] == "LHR"


def test_single_itinerary_info_response_still_converts():
    """One ItineraryInfoResponse stays a dict in xmltodict and still converts."""
    body = parse_flight_detail_body(single_itinerary_xml())
    assert isinstance(body["ItineraryResponseList"]["ItineraryInfoResponse"], dict)

    doc = convert_flight_status(body, "GF", "2057", "BOM", "2026-03-19")

    assert doc["status"] == "OPENCI"
    assert doc["gate"] == SECOND_FLIGHT_GATE
    assert doc["aircraft"]["type"] == "320"


def test_unmatched_origin_does_not_raise_and_keeps_a_document():
    """No itinerary for the requested origin is still a document, not a crash."""
    body = parse_flight_detail_body(multi_itinerary_xml())

    doc = convert_flight_status(body, "GF", "2006", "DOH", "2026-04-02")

    assert isinstance(doc, dict)
    assert doc["flightNumber"] == "2006"
    assert doc["origin"] == "DOH"
    assert doc["status"] in {MATCHING_STATUS, OTHER_STATUS, ""}


def test_feeder_stores_the_flight_after_a_multi_itinerary_response(monkeypatch):
    """run_feeder keeps going after a multi-itinerary ACS body.

    Flight 2006's response has two ItineraryInfoResponse elements. Flight 2057
    is queued after it and must still be fetched and stored. The first flight's
    stored status is the itinerary for the requested origin.
    """
    db, _redis = install_datastores(monkeypatch)
    seen = install_sabre_transport(monkeypatch)

    result = run_feeder(FLIGHTS)

    assert result["processedFlights"] == 2
    assert [item["flight"]["flightNumber"] for item in result["results"]] == ["2006", "2057"]
    assert result["results"][0]["apis"]["flightStatus"]["status"] == "success"
    assert result["results"][1]["apis"]["flightStatus"]["status"] == "success"
    first = stored_status(db, "2006")
    second = stored_status(db, "2057")
    assert len(first) == 1
    assert first[0]["status"] == MATCHING_STATUS
    assert first[0]["gate"] == MATCHING_GATE
    assert len(second) == 1
    assert second[0]["status"] == "OPENCI"
    assert second[0]["gate"] == SECOND_FLIGHT_GATE
    assert "ACS_FlightDetailRQ" in seen
    assert seen.count("ACS_FlightDetailRQ") == 2


def test_batch_endpoint_worker_finishes_both_flights(monkeypatch):
    """POST /flights/ingest/batch with no credential queues both flights.

    The worker then runs the same task the route dispatches. A multi-itinerary
    ACS body on the first flight must not mark the job failed or retry it,
    and the second flight must be stored.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes.ingestion import router

    db, _redis = install_datastores(monkeypatch)
    install_sabre_transport(monkeypatch)

    dispatched = {}

    def _capture_delay(job_id, payloads):
        dispatched["job_id"] = job_id
        dispatched["payloads"] = payloads
        return None

    monkeypatch.setattr(run_batch_ingestion, "delay", _capture_delay)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/flights/ingest/batch",
        json={"flights": FLIGHTS},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["flightsQueued"] == 2
    assert body["jobId"] == dispatched["job_id"]
    assert "authorization" not in {name.lower() for name in response.request.headers}

    finished = run_batch_ingestion(dispatched["job_id"], dispatched["payloads"])

    assert finished["processedFlights"] == 2
    job = get_job(dispatched["job_id"])
    assert job["results"] is not None
    assert len(job["results"]) == 2
    assert job["flightsProcessed"] == 2
    assert "attribute" not in (job.get("error") or "").lower()
    assert len(stored_status(db, "2006")) == 1
    assert stored_status(db, "2006")[0]["status"] == MATCHING_STATUS
    assert len(stored_status(db, "2057")) == 1
    assert stored_status(db, "2057")[0]["gate"] == SECOND_FLIGHT_GATE


def test_sync_ingest_does_not_fail_the_pipeline_on_repeated_itinerary(monkeypatch):
    """POST /flights/ingest uses the same flight-status step for one flight.

    A repeated ItineraryInfoResponse must not surface as an uncaught
    pipeline error. The stored status is the itinerary for the request origin.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes.ingestion import router

    db, _redis = install_datastores(monkeypatch)
    install_sabre_transport(monkeypatch)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post("/flights/ingest", json=FLIGHTS[0])

    assert response.status_code != 502
    assert "attribute" not in response.text.lower()
    stored = stored_status(db, "2006")
    assert len(stored) == 1
    assert stored[0]["status"] == MATCHING_STATUS
    assert stored[0]["gate"] == MATCHING_GATE
