"""Standby passengers must be flagged from Sabre priority data, not only type S.

These tests follow a GetPassengerList response through the client parser and
``convert_passenger_list``, then through the passenger API, the dashboard
standby total, the data-audit standby counter, and (when mongod is available)
``GET /analytics/network``. They fail while ``isStandby`` is set only for
PassengerType S.

Expected ``isStandby``:
- true when PassengerType is S
- true when a NonRevenue indicator is present and PriorityCode is non-empty
  and is not the upgrade code UPG
- false for a full-fare revenue passenger, an upgrade (UPG), a revenue
  oversale (OS), and a non-revenue employee with no priority code

Not asserted: positive-space passengers (PassengerType P) who carry a B-series
priority code and a Revenue indicator, as on GF2056. The passenger-table React
render is exercised by running the component's filter source. Displays that
only print ``insights.standbyUpgrade.standbyTotal`` are covered by the
dashboard payload test.
"""

import copy
import json
import os
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from backend.api.routes.data_audit import _aggregate_mongo_pax
from backend.api.routes.flights import _build_dashboard_payload
from backend.api.routes.passengers import get_passengers, get_standby_list
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO = Path(__file__).resolve().parents[2]
GF2057_RESPONSE = (
    REPO
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
GF2056_RESPONSE = (
    REPO
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2056_DMM_2026-03-16.xml"
)
PASSENGER_TABLE = REPO / "frontend" / "components" / "dashboard" / "passenger-table.tsx"

PLACEHOLDER_SABRE_ENV = {
    "SABRE_BASE_URL": "https://placeholder.invalid",
    "SABRE_CPAID": "PLACEHOLDER",
    "SABRE_USERNAME": "PLACEHOLDER",
    "SABRE_PASSWORD": "PLACEHOLDER",
    "SABRE_PSEUDO_CITY_CODE": "PLACEHOLDER",
    "SABRE_ORGANIZATION": "PLACEHOLDER",
    "SABRE_DOMAIN": "PLACEHOLDER",
}


def _with_placeholder_sabre_env():
    previous = {key: os.environ.get(key) for key in PLACEHOLDER_SABRE_ENV}
    os.environ.update(PLACEHOLDER_SABRE_ENV)
    return previous


def _restore_env(previous):
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def ingest_passenger_list(response_xml, airline, flight_number, departure_date, origin):
    """Run the feeder's passenger-list response path without contacting Sabre.

    ``SabreClient._post`` returns the response body unchanged. The test supplies
    that body and then uses the same parse, extract, and convert calls as
    ``get_passenger_list`` and ``run_feeder``.
    """
    previous = _with_placeholder_sabre_env()
    try:
        client = SabreClient()

        def _return_response(self, action, body, timeout=30, endpoint=None):
            return response_xml, 200, 1, "<redacted-request/>"

        client._post = _return_response.__get__(client, SabreClient)
        parsed_body, _xml, _meta = client.get_passenger_list(
            airline, flight_number, departure_date, origin
        )
    finally:
        _restore_env(previous)
    return convert_passenger_list(
        parsed_body, airline, flight_number, departure_date, origin
    )


def _passenger_xml(last_name, passenger_type, priority_code, indicators):
    priority = f"<PriorityCode>{priority_code}</PriorityCode>" if priority_code else ""
    indicator_xml = "".join(f"<Indicator>{item}</Indicator>" for item in indicators)
    return textwrap.dedent(
        f"""\
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>TEST</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">TESTPN</PNRLocator>
          <PassengerID>{last_name}</PassengerID>
          <LineNumber>1</LineNumber>
          {priority}
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>DMM</Destination>
          <BoardingPassFlag>false</BoardingPassFlag>
          <PassengerType>{passenger_type}</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>{indicator_xml}</Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>
        """
    )


def synthetic_passenger_list_response():
    passengers = "\n".join(
        [
            _passenger_xml("FARE", "F", "", ["Revenue"]),
            _passenger_xml("STAFF", "E", "B13", ["NonRevenue"]),
            _passenger_xml("FULLFARE", "F", "B01", ["NonRevenue"]),
            _passenger_xml("TYPED", "S", "RV", ["Revenue"]),
            _passenger_xml("UPGRADE", "F", "UPG", ["Revenue"]),
            _passenger_xml("OVERSALE", "F", "OS", ["Revenue"]),
            _passenger_xml("CONFIRMED", "E", "", ["NonRevenue"]),
            _passenger_xml("UPGRADESTAFF", "E", "UPG", ["NonRevenue"]),
        ]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">\n'
        "  <soap-env:Body>\n"
        "    <GetPassengerListRS>\n"
        "      <ItineraryInfo><Itinerary>\n"
        "        <Airline>GF</Airline><Flight>1</Flight>\n"
        "        <DepartureDate>2026-03-19</DepartureDate>\n"
        "        <Origin>BOM</Origin><Destination>DMM</Destination>\n"
        "      </Itinerary></ItineraryInfo>\n"
        "      <PassengerInfoList>\n"
        f"{passengers}\n"
        "      </PassengerInfoList>\n"
        "    </GetPassengerListRS>\n"
        "  </soap-env:Body>\n"
        "</soap-env:Envelope>\n"
    )


def _by_last_name(doc):
    return {passenger["lastName"]: passenger for passenger in doc["passengers"]}


class _Collection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query, sort=None, **kwargs):
        matches = [
            doc
            for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return copy.deepcopy(matches[0]) if matches else None


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def test_gf2057_employee_with_priority_code_is_standby():
    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    raheem = _by_last_name(doc)["RAHEEM"]

    assert raheem["passengerType"] == "E"
    assert raheem["priorityCode"] == "B13"
    assert raheem["isRevenue"] is False
    assert raheem["isStandby"] is True

    ordinary = [
        passenger
        for passenger in doc["passengers"]
        if passenger["passengerType"] == "F"
        and passenger["isRevenue"]
        and not passenger["priorityCode"]
    ]
    assert ordinary
    assert all(passenger["isStandby"] is False for passenger in ordinary)


def test_passenger_type_s_stays_standby_on_captured_list():
    response = GF2056_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2056", "2026-03-16", "DMM")
    almeida = _by_last_name(doc)["ALMEIDA"]
    assert almeida["passengerType"] == "S"
    assert almeida["priorityCode"] == "RV"
    assert almeida["isStandby"] is True


def test_standby_flag_uses_type_s_or_nonrevenue_priority_code():
    doc = ingest_passenger_list(
        synthetic_passenger_list_response(), "GF", "1", "2026-03-19", "BOM"
    )
    flagged = _by_last_name(doc)
    assert flagged["STAFF"]["passengerType"] == "E"
    assert flagged["STAFF"]["priorityCode"] == "B13"
    assert flagged["STAFF"]["isRevenue"] is False
    assert flagged["FULLFARE"]["passengerType"] == "F"
    assert flagged["FULLFARE"]["priorityCode"] == "B01"
    assert flagged["FULLFARE"]["isRevenue"] is False
    assert flagged["TYPED"]["passengerType"] == "S"

    assert flagged["FARE"]["isStandby"] is False
    assert flagged["UPGRADE"]["isStandby"] is False
    assert flagged["OVERSALE"]["isStandby"] is False
    assert flagged["CONFIRMED"]["isStandby"] is False
    assert flagged["UPGRADESTAFF"]["isStandby"] is False
    assert flagged["TYPED"]["isStandby"] is True
    assert flagged["STAFF"]["isStandby"] is True
    assert flagged["FULLFARE"]["isStandby"] is True


def test_dashboard_standby_total_counts_priority_coded_nonrevenue_passengers():
    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    payload = _build_dashboard_payload(
        None, doc, "BOM", "2026-03-19", {}, None, None, None
    )
    standby = payload["insights"]["standbyUpgrade"]
    assert standby["standbyTotal"] == 1
    assert standby["standbyCabins"].get("Y") == 1
    assert standby["upgradeTotal"] == 0

    synthetic = ingest_passenger_list(
        synthetic_passenger_list_response(), "GF", "1", "2026-03-19", "BOM"
    )
    synthetic_payload = _build_dashboard_payload(
        None, synthetic, "BOM", "2026-03-19", {}, None, None, None
    )
    synthetic_standby = synthetic_payload["insights"]["standbyUpgrade"]
    assert synthetic_standby["standbyTotal"] == 3
    assert synthetic_standby["upgradeTotal"] == 0


def test_data_audit_standby_counter_counts_the_same_passengers():
    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    assert _aggregate_mongo_pax(doc)["standby"] == 1

    synthetic = ingest_passenger_list(
        synthetic_passenger_list_response(), "GF", "1", "2026-03-19", "BOM"
    )
    assert _aggregate_mongo_pax(synthetic)["standby"] == 3


def test_passenger_api_returns_the_standby_flag(monkeypatch):
    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    database = _Database(
        {
            "passenger_list": _Collection([doc]),
            "reservations": _Collection([]),
        }
    )
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: database)

    queue = get_standby_list(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    queued = {passenger["lastName"] for passenger in queue["standby"]["passengers"]}
    assert "RAHEEM" in queued

    listed = get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    raheem = _by_last_name(listed)["RAHEEM"]
    assert raheem["priorityCode"] == "B13"
    assert raheem["isStandby"] is True


def _apply_passenger_table_filter(passengers, filter_connecting):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to run the passenger-table filter source")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(passengers, handle)
        payload_path = handle.name
    script = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const marker = "const filtered = useMemo(() => {";
const start = src.indexOf(marker);
if (start < 0) throw new Error("PassengerTable filter source was not found");
let depth = 0;
let quote = null;
let end = -1;
for (let i = start + marker.length - 1; i < src.length; i++) {
  const c = src[i];
  if (quote) {
    if (c === "\\") { i++; continue; }
    if (c === quote) quote = null;
    continue;
  }
  if (c === '"' || c === "'" || c === "`") { quote = c; continue; }
  if (c === "{") depth++;
  else if (c === "}") {
    depth--;
    if (depth === 0) { end = i; break; }
  }
}
if (end < 0) throw new Error("PassengerTable filter source was not closed");
const body = src.slice(start + marker.length, end);
if (!body.includes("p.isStandby")) {
  throw new Error("PassengerTable standby filter does not read isStandby");
}
const fn = new Function(
  "passengers", "debouncedSearch", "filterCabin", "filterStatus", "filterType",
  "filterLoyalty", "filterNationality", "filterSSR", "filterBP", "filterSeat",
  "filterConnecting", "filterDestination", "sortKey", "sortDir",
  body
);
const passengers = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const filtered = fn(
  passengers, "", "all", "all", "all", "all", "all", "all", "all", "all",
  process.argv[3], "all", "lastName", "asc"
);
process.stdout.write(JSON.stringify(filtered.map((p) => p.lastName)));
"""
    try:
        completed = subprocess.run(
            [node, "-e", script, str(PASSENGER_TABLE), payload_path, filter_connecting],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(payload_path)
    if completed.returncode != 0:
        pytest.fail(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def test_passenger_table_standby_filter_lists_the_employee(monkeypatch):
    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    database = _Database(
        {
            "passenger_list": _Collection([doc]),
            "reservations": _Collection([]),
        }
    )
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: database)
    listed = get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    names = _apply_passenger_table_filter(listed["passengers"], "standby")
    assert "RAHEEM" in names
    ordinary = next(
        passenger["lastName"]
        for passenger in listed["passengers"]
        if passenger["passengerType"] == "F"
        and passenger["isRevenue"]
        and not passenger["priorityCode"]
    )
    assert ordinary not in names


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _mongod_binary():
    return os.environ.get("MONGOD_BIN") or shutil.which("mongod")


@pytest.fixture(scope="module")
def mongo_uri():
    binary = _mongod_binary()
    if not binary:
        pytest.skip("mongod is not installed; network analytics test not run")
    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="standby-mongo-")
    log_path = os.path.join(data_dir, "mongod.log")
    process = subprocess.Popen(
        [
            binary,
            "--dbpath",
            data_dir,
            "--port",
            str(port),
            "--bind_ip",
            "127.0.0.1",
            "--nounixsocket",
            "--logpath",
            log_path,
            "--fork",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(timeout=30)
    if process.returncode != 0:
        shutil.rmtree(data_dir, ignore_errors=True)
        pytest.fail(f"mongod failed to start: {stderr or stdout}")
    uri = f"mongodb://127.0.0.1:{port}"
    client = MongoClient(uri, serverSelectionTimeoutMS=500)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            client.admin.command("ping")
            break
        except ServerSelectionTimeoutError:
            time.sleep(0.2)
    else:
        client.close()
        subprocess.run([binary, "--dbpath", data_dir, "--shutdown"], check=False)
        shutil.rmtree(data_dir, ignore_errors=True)
        pytest.fail("mongod did not accept connections")
    client.close()
    yield uri
    subprocess.run([binary, "--dbpath", data_dir, "--shutdown"], check=False)
    shutil.rmtree(data_dir, ignore_errors=True)


def test_network_analytics_standby_count_includes_the_employee(mongo_uri):
    import backend.api.database as database
    import backend.feeder.storage as storage
    from backend.api.routes.analytics import _run_network_analytics

    response = GF2057_RESPONSE.read_text(encoding="utf-8")
    doc = ingest_passenger_list(response, "GF", "2057", "2026-03-19", "BOM")
    previous_uri = os.environ.get("MONGODB_URI")
    os.environ["MONGODB_URI"] = mongo_uri
    database.close_db()
    storage.close()
    try:
        db = database.get_db()
        storage.init_db(db)
        storage.store_passenger_list(copy.deepcopy(doc))
        result = _run_network_analytics(
            "2026-03-19",
            "2026-03-19",
            origin="BOM",
            flight_number="2057",
        )
    finally:
        database.close_db()
        storage.close()
        if previous_uri is None:
            os.environ.pop("MONGODB_URI", None)
        else:
            os.environ["MONGODB_URI"] = previous_uri
    assert result["operationalRates"]["totalPassengers"] == 59
    assert result["operationalRates"]["standby"] == 1
