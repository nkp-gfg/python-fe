"""Trip Search departure time must be the station-local timestamp.

Today's Flights currently copies scheduledDepartureUtc into departureDateTime,
and the feeder writes that string into Trip_SearchRQ DepartureDateTime Is.
An empty but successful Trip_SearchRS is then stored as the current
reservation list and diffed as removals.

These tests describe the fixed behavior. They fail while that path is present.
"""

import json
import os
import shutil
import subprocess
from datetime import date, datetime, timezone

# Read at class-body import time inside SabreClient.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-not-a-secret")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "BAH")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.routes.ingestion import SabreBatchRequest, SabreIngestRequest, ingest_flight
from backend.api.routes.otp import _row_to_flight
from backend.api.routes.reservations import get_reservations
from backend.feeder.runner import run_feeder
from backend.feeder import storage
import backend.api.database as api_database

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TODAY_PAGE = os.path.join(REPO_ROOT, "frontend", "app", "today", "page.tsx")
WORKBENCH = os.path.join(
    REPO_ROOT, "frontend", "components", "dashboard", "flight-workbench.tsx"
)

# Envelope shape from sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-19.xml
# (soap-env:Envelope / soap-env:Body / Trip_SearchRS / Success / ReservationsList).
# Empty Reservations element from sabre_docs/Trip_Search/Trip_Search_User_Guide.txt.
EMPTY_TRIP_SEARCH_RS = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="0" NumberPages="1" TotalResults="0">
        <Reservations/>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

SESSION_RS = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""

_LOAD_JS = r"""
const fs = require("fs");
const vm = require("vm");

function extractFunction(src, name) {
  const marker = "function " + name;
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("missing function " + name);
  let i = src.indexOf("{", start);
  let depth = 0;
  let quote = null;
  let escape = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      if (escape) { escape = false; continue; }
      if (c === "\\") { escape = true; continue; }
      if (c === quote) quote = null;
      continue;
    }
    if (c === "'" || c === "\"" || c === "`") { quote = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function stripSignature(fn) {
  return fn.replace(
    /function\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*:[^{]+\{/,
    function (_, name, params) {
      const cleaned = params.replace(/:\s*[^,)]+/g, "");
      return "function " + name + "(" + cleaned + ") {";
    }
  );
}

const request = JSON.parse(fs.readFileSync(0, "utf8"));
const chunks = [];
for (const item of request.functions) {
  const src = fs.readFileSync(item.file, "utf8");
  chunks.push(stripSignature(extractFunction(src, item.name)));
}
const js = chunks.join("\n") + "\nmodule.exports = { " +
  request.functions.map((item) => item.name).join(", ") + " };";
const sandbox = { module: { exports: {} }, exports: {} };
sandbox.exports = sandbox.module.exports;
vm.createContext(sandbox);
vm.runInContext(js, sandbox);
const fn = sandbox.module.exports[request.call];
if (typeof fn !== "function") throw new Error("did not load " + request.call);
const results = request.argsList.map((args) => fn.apply(null, args));
process.stdout.write(JSON.stringify(results));
"""


def _node_call(functions, call, args_list):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to execute the frontend payload builders")
    payload = {"functions": functions, "call": call, "argsList": args_list}
    # The loader script is the test harness; the functions it runs are read
    # from the frontend source files named in `functions`.
    proc = subprocess.run(
        [node, "-e", _LOAD_JS],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"frontend loader failed\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return json.loads(proc.stdout)


def _page_payloads(flights):
    return _node_call(
        [
            {"file": TODAY_PAGE, "name": "getSabreDepartureDate"},
            {"file": TODAY_PAGE, "name": "buildIngestPayload"},
        ],
        "buildIngestPayload",
        [[flight] for flight in flights],
    )


def _workbench_times(cases):
    """cases: list of [raw, fallbackDate]."""
    return _node_call(
        [{"file": WORKBENCH, "name": "toIsoDateTime"}],
        "toIsoDateTime",
        cases,
    )


def _otp_flight(**overrides):
    row = {
        "flight_sequence_number": 9001,
        "carrier_code": "GF",
        "flight_number": "2057",
        "scheduled_origin": "BAH",
        "scheduled_destination": "LHR",
        "flight_date": date(2026, 3, 30),
        "flight_status": "SCHEDULED",
        "service_type_code": "J",
        "scheduled_departure_local": datetime(2026, 3, 30, 14, 30, 0),
        "scheduled_departure_utc": datetime(2026, 3, 30, 11, 30, 0),
    }
    row.update(overrides)
    return _row_to_flight(row).model_dump(mode="json")


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class SabreCapture:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        body = data if isinstance(data, str) else (data or b"").decode()
        self.calls.append({"action": action, "body": body, "url": url})
        if action in ("SessionCreateRQ", "SessionCloseRQ"):
            return _Response(200, SESSION_RS)
        if action == "Trip_SearchRQ":
            return _Response(200, EMPTY_TRIP_SEARCH_RS)
        return _Response(500, "placeholder upstream failure")

    def trip_search_is_values(self):
        import re
        found = []
        for call in self.calls:
            if call["action"] != "Trip_SearchRQ":
                continue
            match = re.search(r'<DepartureDateTime Is="([^"]*)"', call["body"])
            flight = re.search(r"<FlightNumber>([^<]*)</FlightNumber>", call["body"])
            found.append({
                "flightNumber": flight.group(1) if flight else "",
                "is": match.group(1) if match else None,
            })
        return found


@pytest.fixture
def sabre(monkeypatch):
    capture = SabreCapture()
    monkeypatch.setattr("backend.sabre.client.requests.post", capture.post)
    return capture


@pytest.fixture
def memory_db():
    import mongomock

    client = mongomock.MongoClient()
    db = client["falconeye"]
    storage._client = client
    storage._db = db
    storage._owns_connection = False
    api_database._client = client
    api_database._db = db
    yield db
    storage._client = None
    storage._db = None
    api_database._client = None
    api_database._db = None


def _dump_batch(payloads):
    batch = SabreBatchRequest(flights=payloads)
    return [flight.model_dump(mode="json") for flight in batch.flights]


def _run_sync(payload):
    try:
        import asyncio
        asyncio.run(ingest_flight(SabreIngestRequest(**payload)))
    except HTTPException:
        return
    except ValidationError:
        raise


def test_today_page_sends_station_local_time_for_single_and_batch(sabre, memory_db):
    """UTC clock and a missing UTC value must not become Trip Search Is."""
    same_day = _otp_flight()
    aware_utc = _otp_flight(
        flight_sequence_number=9002,
        flight_number="2060",
        scheduled_departure_utc=datetime(2026, 3, 30, 11, 30, tzinfo=timezone.utc),
    )
    rollover = _otp_flight(
        flight_sequence_number=9003,
        flight_number="70",
        scheduled_origin="DXB",
        flight_date=date(2026, 3, 30),
        scheduled_departure_local=datetime(2026, 3, 31, 1, 30, 0),
        scheduled_departure_utc=datetime(2026, 3, 30, 21, 30, 0),
    )
    utc_missing = _otp_flight(
        flight_sequence_number=9004,
        flight_number="71",
        scheduled_departure_utc=None,
    )

    assert same_day["scheduledDepartureLocal"] == "2026-03-30T14:30:00"
    assert same_day["scheduledDepartureUtc"] == "2026-03-30T11:30:00"
    assert aware_utc["scheduledDepartureUtc"] == "2026-03-30T11:30:00+00:00"
    assert rollover["sabreDepartureDate"] == "2026-03-31"
    assert rollover["scheduledDepartureUtc"] == "2026-03-30T21:30:00"

    # handleRowIngest and runBatchIngest both call buildIngestPayload.
    single, aware, overnight, missing_utc = _page_payloads(
        [same_day, aware_utc, rollover, utc_missing]
    )
    batch_dumped = _dump_batch([single, aware, overnight, missing_utc])
    single_model = SabreIngestRequest(**single).model_dump(mode="json")
    assert single_model["departureDateTime"] == batch_dumped[0]["departureDateTime"]

    _run_sync(single)
    run_feeder(batch_dumped)

    sent = {(item["flightNumber"], item["is"]) for item in sabre.trip_search_is_values()}
    assert ("2057", "2026-03-30T14:30:00") in sent
    assert ("2060", "2026-03-30T14:30:00") in sent
    assert ("70", "2026-03-31T01:30:00") in sent
    assert ("71", "2026-03-30T14:30:00") in sent
    forbidden = {
        "2026-03-30T11:30:00",
        "2026-03-30T11:30:00+00:00",
        "2026-03-30T11:30:00Z",
        "2026-03-30T21:30:00",
        "2026-03-30T00:00:00",
        "2026-03-31T00:00:00",
    }
    assert not (forbidden & {item["is"] for item in sabre.trip_search_is_values()})


def test_missing_local_time_is_not_sent_as_exact_departure(sabre, memory_db):
    """No station-local time: do not query midnight or the UTC clock."""
    utc_only = _otp_flight(
        flight_number="72",
        scheduled_departure_local=None,
        scheduled_departure_utc=datetime(2026, 3, 30, 11, 30, 0),
    )
    neither = _otp_flight(
        flight_sequence_number=9005,
        flight_number="73",
        scheduled_departure_local=None,
        scheduled_departure_utc=None,
        local_flight_date=date(2026, 3, 30),
    )
    payloads = _page_payloads([utc_only, neither])
    for payload in payloads:
        sent = payload.get("departureDateTime")
        assert sent in (None, ""), (
            f"{payload['flightNumber']} sent departureDateTime={sent!r} "
            "without a station-local time"
        )

    accepted = []
    for payload in payloads:
        try:
            accepted.append(SabreIngestRequest(**payload).model_dump(mode="json"))
        except ValidationError:
            continue
    if accepted:
        run_feeder(accepted)

    forbidden_prefixes = ("2026-03-30T11:30:00", "2026-03-30T00:00:00")
    for item in sabre.trip_search_is_values():
        assert item["flightNumber"] not in {"72", "73"}
        assert not str(item["is"]).startswith(forbidden_prefixes)


def test_empty_trip_search_does_not_replace_stored_reservations(sabre, memory_db):
    """A parseable Trip Search body with no reservations is a failed call."""
    flight_number = "2057"
    origin = "BAH"
    departure_date = "2026-03-30"
    stored = {
        "airline": "GF",
        "flightNumber": flight_number,
        "departureAirport": origin,
        "departureDate": departure_date,
        "fetchedAt": "2020-01-01T00:00:00Z",
        "totalResults": 1,
        "reservations": [
            {
                "pnr": "ABC123",
                "numberInParty": 1,
                "passengers": [
                    {"lastName": "SMITH", "firstName": "JOHN"},
                ],
            }
        ],
    }
    storage.store_snapshot(
        request_id="prior-request",
        airline="GF",
        flight_number=flight_number,
        origin=origin,
        departure_date=departure_date,
        snapshot_type="reservations",
        data=stored,
    )
    memory_db["reservations"].insert_one(dict(stored))

    payload = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "departureDateTime": "2026-03-30T14:30:00",
        "flightSequenceNumber": 9001,
        "serviceTypeCode": "J",
    }
    # The page's batch request is one model_dump per flight, then run_feeder.
    result = run_feeder(_dump_batch([payload]))
    flight_result = result["results"][0]
    reservations_api = flight_result["apis"].get("reservations") or {}

    current = get_reservations(
        flight_number,
        origin=origin,
        date=departure_date,
        snapshot_sequence=None,
    )
    current_pnrs = [item.get("pnr") for item in current.get("reservations", [])]
    passenger_names = [
        passenger.get("lastName")
        for reservation in current.get("reservations", [])
        for passenger in reservation.get("passengers", [])
    ]
    latest_snapshot = memory_db["snapshots"].find_one(
        {
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "snapshotType": "reservations",
        },
        sort=[("sequenceNumber", -1)],
    )
    snapshot_pnrs = [
        item.get("pnr")
        for item in (latest_snapshot or {}).get("data", {}).get("reservations", [])
    ]
    removals = list(memory_db["changes"].find({"changeType": "RESERVATION_REMOVED"}))

    assert sabre.trip_search_is_values() == [
        {"flightNumber": "2057", "is": "2026-03-30T14:30:00"},
    ]
    assert {
        "status": reservations_api.get("status"),
        "currentPnrs": current_pnrs,
        "passengers": passenger_names,
        "snapshotPnrs": snapshot_pnrs,
        "removals": [item.get("changeType") for item in removals],
    } == {
        "status": "error",
        "currentPnrs": ["ABC123"],
        "passengers": ["SMITH"],
        "snapshotPnrs": ["ABC123"],
        "removals": [],
    }


def test_workbench_keeps_schedule_local_time_and_omits_midnight():
    """The dashboard ingest uses the same departureDateTime field.

    A real schedule timestamp is station-local and must be preserved.
    A missing schedule time must not become midnight.
    """
    preserved, afternoon, missing, unparsed = _workbench_times([
        ["2026-03-19T17:40:00", "2026-03-19"],
        ["2026-03-20T04:00PM", "2026-03-20"],
        [None, "2026-03-19"],
        ["not-a-time", "2026-03-19"],
    ])
    assert preserved == "2026-03-19T17:40:00"
    assert afternoon == "2026-03-20T16:00:00"
    assert missing in (None, "")
    assert unparsed in (None, "")
    assert missing != "2026-03-19T00:00:00"
    assert unparsed != "2026-03-19T00:00:00"
