"""Bag check-in mode must survive ACS ingestion and the flight-status reads.

Sabre ACS Flight Detail sends BagCheckInOption (P = pieces, PW = pieces and
weight). These tests parse that element with the SOAP client, run the feeder
pipeline, and read it back through the flight-status routes. They fail while
the converter drops the element.

The React operations board (flight-workbench, status-history, flight-header)
is not executed here: this repository has no frontend test runner. Change
detection in diff_flight_status and the data-audit field map are also
untested. Historical dashboard and tree reads call get_snapshot_data_as_of
with positional arguments, but that helper accepts those arguments by keyword
only, so those two routes raise before they can return a flight-status
document. The as-of flight-status route, which calls the helper by keyword,
is covered.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import changes, flights
from backend.feeder.converter import convert_flight_status
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

SAMPLE_DIR = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
)
SAMPLE_FILES = sorted(SAMPLE_DIR.glob("ACS_FlightDetailRS_*.xml"))

# Redact any session token before the SOAP body is parsed. The assertion
# path uses the body only; the placeholder is not a credential.
_TOKEN_RE = re.compile(
    r"(<wsse:BinarySecurityToken\b[^>]*>)(.*?)(</wsse:BinarySecurityToken>)",
    re.IGNORECASE | re.DOTALL,
)

BAG_MODE = "bagCheckInOption"


def _parse_acs_body(xml_text):
    """Same parse and body extract as SabreClient.get_flight_status."""
    xml_text = _TOKEN_RE.sub(r"\1PLACEHOLDER\3", xml_text)
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _load_sample(path):
    return _parse_acs_body(Path(path).read_text(encoding="utf-8"))


def _itinerary(body):
    itin = body["ItineraryResponseList"]["ItineraryInfoResponse"]
    assert isinstance(itin, dict)
    return itin


def _public(doc):
    """Flight-status fields a caller can see after _raw is removed."""
    visible = copy.deepcopy(doc)
    visible.pop("_raw", None)
    visible.pop("_id", None)
    return visible


def _assert_mode(doc, expected):
    visible = _public(doc)
    actual = visible.get(BAG_MODE)
    assert actual == expected, (
        f"{BAG_MODE}={actual!r}, expected {expected!r}, "
        f"public keys={sorted(visible)}"
    )
    seat = (visible.get("aircraft") or {}).get("seatConfig")
    assert actual != seat


def _identity(body):
    itin = _itinerary(body)
    return {
        "airline": itin["Airline"],
        "flightNumber": str(itin["Flight"]),
        "origin": itin["Origin"],
        "departureDate": itin.get("DepartureDate")
        or itin.get("ScheduledDepartureDate")
        or "",
    }


def _synthetic_body(option, seat_config, free_text, flight_number):
    option_xml = (
        f"<BagCheckInOption>{option}</BagCheckInOption>" if option is not None else ""
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight_number}</Flight>
          <Origin>BAH</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>08:00AM</ScheduledDepartureTime>
          <FlightStatus>OPENCI</FlightStatus>
          <AircraftType>321</AircraftType>
          <SeatConfig>{seat_config}</SeatConfig>
          {option_xml}
          <FreeTextInfoList>
            <ns2:FreeTextInfo>
              <ns2:TextLine>
                <ns2:Text>{free_text}</ns2:Text>
              </ns2:TextLine>
            </ns2:FreeTextInfo>
          </FreeTextInfoList>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    return _parse_acs_body(xml)


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs.sort(key=lambda doc: _sort_value(doc.get(key)), reverse=direction < 0)
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, query=None, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: _sort_value(doc.get(key)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return _project_one(matches[0], projection)

    def find(self, query=None, projection=None):
        docs = [
            _project_one(copy.deepcopy(doc), projection)
            for doc in self.docs
            if _matches(doc, query or {})
        ]
        return _Cursor(docs)

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = [copy.deepcopy(doc) for doc in self.docs]
        for stage in pipeline:
            kind = next(iter(stage))
            if kind == "$match":
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif kind == "$sort":
                for key, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(
                        key=lambda doc: _sort_value(doc.get(key)),
                        reverse=direction < 0,
                    )
            elif kind == "$group":
                grouped = {}
                for doc in docs:
                    group_id = _eval_expr(stage["$group"]["_id"], doc)
                    group_key = json.dumps(group_id, sort_keys=True, default=str)
                    if group_key not in grouped:
                        grouped[group_key] = {"_id": group_id, "doc": doc}
                docs = list(grouped.values())
            elif kind == "$project":
                docs = [_exclude_paths(doc, stage["$project"]) for doc in docs]
            elif not docs:
                continue
            else:
                raise AssertionError(f"unsupported aggregation stage {stage}")
        return docs


class _Database(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection()
        return dict.__getitem__(self, name)


def _sort_value(value):
    if value is None:
        return ""
    return value


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            if any(op not in {"$lte"} for op in expected):
                raise AssertionError(f"unsupported query {key}={expected}")
        elif actual != expected:
            return False
    return True


def _project_one(doc, projection):
    if not projection:
        return doc
    if any(flag == 1 for flag in projection.values()):
        included = {key: copy.deepcopy(doc.get(key)) for key, flag in projection.items() if flag == 1}
        if "_id" not in projection:
            included["_id"] = doc.get("_id")
        return included
    return _exclude_paths(doc, projection)


def _exclude_paths(doc, projection):
    result = copy.deepcopy(doc)
    for path, flag in projection.items():
        if flag != 0:
            continue
        cursor = result
        parts = path.split(".")
        for part in parts[:-1]:
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        if isinstance(cursor, dict):
            cursor.pop(parts[-1], None)
    return result


def _eval_expr(expr, doc):
    if isinstance(expr, str) and expr.startswith("$"):
        return _lookup(doc, expr[1:])
    if isinstance(expr, dict) and set(expr) == {"$ifNull"}:
        path, default = expr["$ifNull"]
        value = _eval_expr(path, doc)
        return default if value is None else value
    if isinstance(expr, dict):
        return {key: _eval_expr(value, doc) for key, value in expr.items()}
    return expr


def _lookup(doc, path):
    cursor = doc
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _convert(body):
    ident = _identity(body)
    return convert_flight_status(
        body,
        ident["airline"],
        ident["flightNumber"],
        ident["origin"],
        ident["departureDate"],
    )


def _run_pipeline(body):
    ident = _identity(body)
    stored = {}

    def store_raw_request(**kwargs):
        stored["raw_response"] = kwargs.get("response_json")
        return "request-1"

    def store_snapshot(**kwargs):
        stored["snapshot"] = kwargs["data"]
        return "snapshot-1", "checksum", False

    def store_flight_status(doc):
        stored["flight_status"] = doc
        return "flight-status-1"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "backend.feeder.runner.storage.store_raw_request", store_raw_request
    )
    monkeypatch.setattr(
        "backend.feeder.runner.storage.store_snapshot", store_snapshot
    )
    monkeypatch.setattr(
        "backend.feeder.runner.storage.store_flight_status", store_flight_status
    )
    monkeypatch.setattr(
        "backend.feeder.runner.storage.get_previous_snapshot", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "backend.feeder.runner.storage.update_flight_state", lambda *args, **kwargs: None
    )
    try:
        _process_api_call(
            "FlightStatus",
            "flight_status",
            ident,
            body,
            "<redacted-response/>",
            {
                "requestXml": "<redacted-request/>",
                "httpStatus": 200,
                "durationMs": 1,
                "sessionToken": "PLACEHOLDER",
                "conversationId": "PLACEHOLDER",
            },
            convert_flight_status,
            (
                body,
                ident["airline"],
                ident["flightNumber"],
                ident["origin"],
                ident["departureDate"],
            ),
        )
    finally:
        monkeypatch.undo()
    return stored


@pytest.fixture()
def api(monkeypatch):
    database = _Database()
    monkeypatch.setattr(flights, "get_db", lambda: database)
    monkeypatch.setattr(changes, "get_db", lambda: database)
    flights._dashboard_cache.clear()
    app = FastAPI()
    app.include_router(flights.router)
    app.include_router(changes.router)
    return TestClient(app), database


def _publish(database, doc, sequence=1):
    database["flight_status"].insert_one(doc)
    database["snapshots"].insert_one(
        {
            "flightNumber": doc["flightNumber"],
            "origin": doc["origin"],
            "departureDate": doc["departureDate"],
            "snapshotType": "flight_status",
            "sequenceNumber": sequence,
            "capturedAt": doc.get("fetchedAt", ""),
            "data": copy.deepcopy(doc),
        }
    )


def _status_url(doc):
    return (
        f"/flights/{doc['flightNumber']}/status"
        f"?origin={doc['origin']}&date={doc['departureDate']}"
    )


@pytest.mark.parametrize("sample_path", SAMPLE_FILES, ids=lambda path: path.name)
def test_pipeline_stores_sabre_bag_check_in_option(sample_path):
    body = _load_sample(sample_path)
    itin = _itinerary(body)
    assert itin["BagCheckInOption"] == "PW"

    stored = _run_pipeline(body)
    _assert_mode(stored["flight_status"], "PW")
    _assert_mode(stored["snapshot"], "PW")
    assert stored["raw_response"]["ItineraryResponseList"]["ItineraryInfoResponse"][
        "BagCheckInOption"
    ] == "PW"


@pytest.mark.parametrize("sample_path", SAMPLE_FILES, ids=lambda path: path.name)
def test_status_routes_return_sabre_bag_check_in_option(sample_path, api):
    client, database = api
    body = _load_sample(sample_path)
    assert _itinerary(body)["BagCheckInOption"] == "PW"
    doc = _convert(body)
    _publish(database, doc)

    live = client.get(_status_url(doc))
    assert live.status_code == 200
    assert "_raw" not in live.json()
    _assert_mode(live.json(), "PW")

    history = client.get(_status_url(doc).replace("/status", "/status/history"))
    assert history.status_code == 200
    assert history.json()
    assert "_raw" not in history.json()[0]
    _assert_mode(history.json()[0], "PW")

    historical = client.get(_status_url(doc) + "&snapshot_sequence=1")
    assert historical.status_code == 200
    historical_body = historical.json()
    historical_body.pop("_raw", None)
    _assert_mode(historical_body, "PW")


@pytest.mark.parametrize("sample_path", SAMPLE_FILES, ids=lambda path: path.name)
def test_dashboard_and_list_return_sabre_bag_check_in_option(sample_path, api):
    client, database = api
    body = _load_sample(sample_path)
    doc = _convert(body)
    _publish(database, doc)
    flights._dashboard_cache.clear()

    dashboard = client.get(
        f"/flights/{doc['flightNumber']}/dashboard"
        f"?origin={doc['origin']}&date={doc['departureDate']}"
    )
    assert dashboard.status_code == 200
    flight_status = dashboard.json()["flightStatus"]
    flight_status.pop("_raw", None)
    _assert_mode(flight_status, "PW")

    listed = client.get(f"/flights?date={doc['departureDate']}")
    assert listed.status_code == 200
    match = [
        item
        for item in listed.json()
        if item["flightNumber"] == doc["flightNumber"] and item["origin"] == doc["origin"]
    ]
    assert match
    _assert_mode(match[0], "PW")

    compared = client.get(
        f"/flights/{doc['flightNumber']}/snapshots/compare"
        f"?origin={doc['origin']}&date={doc['departureDate']}&snapshot_sequence=1"
    )
    assert compared.status_code == 200
    deltas = compared.json()["types"]["flight_status"]["deltas"]
    assert deltas[BAG_MODE]["selected"] == "PW"
    assert deltas[BAG_MODE]["latest"] == "PW"


def test_same_seat_config_keeps_distinct_sabre_codes(api):
    client, database = api
    modes = {}
    for option, flight_number in (("P", "9101"), ("PW", "9102")):
        body = _synthetic_body(option, "12/180", "12/180", flight_number)
        assert _itinerary(body)["BagCheckInOption"] == option
        assert _itinerary(body)["SeatConfig"] == "12/180"
        stored = _run_pipeline(body)
        _assert_mode(stored["flight_status"], option)
        doc = _convert(body)
        _publish(database, doc)
        response = client.get(_status_url(doc))
        assert response.status_code == 200
        _assert_mode(response.json(), option)
        modes[option] = _public(response.json())["aircraft"]["seatConfig"]
    assert modes["P"] == modes["PW"] == "12/180"


def test_seat_config_text_does_not_override_sabre_code(api):
    client, database = api
    cases = (
        ("P", "PIECES AND WEIGHT", "pieces and weight", "9103"),
        ("PW", "PIECES ONLY", "pieces", "9104"),
    )
    for option, seat_config, free_text, flight_number in cases:
        body = _synthetic_body(option, seat_config, free_text, flight_number)
        doc = _convert(body)
        _publish(database, doc)
        response = client.get(_status_url(doc))
        assert response.status_code == 200
        visible = _public(response.json())
        assert visible[BAG_MODE] == option
        assert visible["aircraft"]["seatConfig"] == seat_config


def test_missing_bag_check_in_option_is_not_invented(api):
    client, database = api
    body = _synthetic_body(None, "12/180", "pieces and weight", "9105")
    assert "BagCheckInOption" not in _itinerary(body)
    doc = _convert(body)
    _publish(database, doc)
    response = client.get(_status_url(doc))
    assert response.status_code == 200
    visible = _public(response.json())
    assert visible.get(BAG_MODE) in (None, "")
    assert visible["aircraft"]["seatConfig"] == "12/180"
