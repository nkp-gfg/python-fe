"""Automatic on-board indicator must survive ACS flight-detail ingestion.

Sabre ACS_FlightDetailRS puts the automatic on-board indicator in the
itinerary ``AutoOn`` element. These tests are the spec for keeping that
indicator on the flight-status document and on every operations response
that is built from it.

Contract:
- ``automaticOnBoard`` is the itinerary ``AutoOn`` text. xmltodict already
  strips surrounding whitespace. A missing element is stored as ``""``.
- ``automaticBoardingInProgress`` is true when that text is non-empty and,
  compared case-insensitively, is not ``OFF``. ``OFF``, ``off``, blank, and
  a missing element are not automatic boarding. Every other value
  (``ON``, ``AUTO``, ``YES``, ``1``) is automatic boarding in progress.
- The explicit fields are what clients read. The raw archive is not a
  substitute: status responses remove ``_raw``.

The React workbench (``frontend/components/dashboard/flight-workbench.tsx``)
and the flight list render ``overview`` / ``operationalSummary``. Those
payloads are what these tests call the operations board. Phase-journey
and the data-audit field map are not covered.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.routes import changes as changes_routes
from backend.api.routes import flights as flights_routes
from backend.feeder.converter import convert_flight_status
from backend.feeder.differ import diff_flight_status
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "sample_data" / "sample_xmlfiles"
SAMPLE_FLIGHT_DETAILS = (
    "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml",
)


def _flight_detail_envelope(auto_on: str | None, *, prefixed: bool = False) -> str:
    """ACS flight-detail body in the shape SabreClient parses. No credentials."""
    if auto_on is None:
        auto_on_xml = ""
    else:
        tag = "ns3:AutoOn" if prefixed else "AutoOn"
        auto_on_xml = f"<{tag}>{auto_on}</{tag}>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>05:40PM</ScheduledDepartureTime>
          <EstimatedDepartureDate>2026-03-19</EstimatedDepartureDate>
          <EstimatedDepartureTime>05:40PM</EstimatedDepartureTime>
          <DepartureGate>67A</DepartureGate>
          <BoardingTime boardingIndicator="BDG">05:00PM</BoardingTime>
          <AircraftType>321</AircraftType>
          <FlightStatus>OPENCI</FlightStatus>
          {auto_on_xml}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Booked>56</Booked>
        <TotalOnBoard>10</TotalOnBoard>
        <TotalBoardingPassIssued>4</TotalBoardingPassIssued>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def parse_flight_detail(xml_text: str) -> dict:
    """Run the Sabre client parse used by get_flight_status, without a session."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def convert_detail(xml_text: str, flight_number="2057", origin="BOM", departure_date="2026-03-19"):
    raw = parse_flight_detail(xml_text)
    return convert_flight_status(raw, "GF", flight_number, origin, departure_date)


def _assert_indicator(doc: dict, auto_on: str, in_progress: bool):
    assert doc.get("automaticOnBoard") == auto_on
    assert doc.get("automaticBoardingInProgress") is in_progress


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, order in reversed(key):
                self.docs.sort(key=lambda doc, field=field: doc.get(field) or "", reverse=order < 0)
        else:
            self.docs.sort(key=lambda doc, field=key: doc.get(field) or "", reverse=(direction or 1) < 0)
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, query, sort=None, projection=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        if sort:
            _Cursor(matches).sort(sort)
        return matches[0] if matches else None

    def find(self, query, projection=None):
        return _Cursor([copy.deepcopy(doc) for doc in self.docs if _match(doc, query)])

    def insert_one(self, doc):
        self.docs.append(doc)
        return type("InsertResult", (), {"inserted_id": "inserted"})()

    def aggregate(self, pipeline, allowDiskUse=False):
        """Apply the list-flights stages that select and strip a flight-status doc.

        Stages implemented are the ones ``_latest_per_flight_agg`` builds:
        ``$match``, ``$sort``, ``$group`` with ``$ifNull`` / ``$first``, and an
        exclusion ``$project``. ``$addFields`` does not carry ``AutoOn``.
        """
        docs = [copy.deepcopy(doc) for doc in self.docs]
        grouped = None
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _match(doc, stage["$match"])]
            elif "$sort" in stage:
                for field, order in stage["$sort"].items():
                    docs.sort(key=lambda doc, field=field: doc.get(field) or "", reverse=order < 0)
            elif "$group" in stage:
                spec = stage["$group"]["_id"]
                order = []
                by_key = {}
                for doc in docs:
                    key = _group_key(doc, spec)
                    frozen = tuple(sorted(key.items()))
                    if frozen not in by_key:
                        by_key[frozen] = {"_id": key, "doc": doc}
                        order.append(frozen)
                grouped = [by_key[key] for key in order]
                docs = grouped
            elif "$project" in stage:
                target = grouped if grouped is not None else [{"doc": doc} for doc in docs]
                for row in target:
                    for path, included in stage["$project"].items():
                        if included == 0 and path.startswith("doc."):
                            row.get("doc", {}).pop(path.split(".", 1)[1], None)
                grouped = target
            elif "$addFields" in stage:
                continue
            else:
                raise AssertionError(f"test double does not implement pipeline stage {stage}")
        return grouped if grouped is not None else []


class _Database(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection()
        return dict.__getitem__(self, name)


def _match(doc, query):
    for field, expected in query.items():
        actual = doc.get(field)
        if isinstance(expected, dict) and set(expected) == {"$lte"}:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


def _group_key(doc, spec):
    key = {}
    for name, expr in spec.items():
        if isinstance(expr, str) and expr.startswith("$"):
            key[name] = doc.get(expr[1:])
        elif isinstance(expr, dict) and "$ifNull" in expr:
            path, default = expr["$ifNull"]
            value = doc.get(path[1:])
            key[name] = default if value is None else value
        else:
            key[name] = expr
    return key


def _boarded_passenger_list(flight_number="2057", origin="BOM", departure_date="2026-03-19"):
    """One gate-unscanned passenger already marked boarded, as Sabre does when AutoOn is on."""
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": "2026-03-19T14:00:00Z",
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "destination": "DMM",
        "passengers": [
            {
                "lastName": "SMITH",
                "firstName": "JOHN",
                "pnr": "ABC123",
                "cabin": "Y",
                "isRevenue": True,
                "isChild": False,
                "hasInfant": False,
                "isCheckedIn": True,
                "isBoarded": True,
                "boardingPassIssued": False,
                "editCodes": [],
            }
        ],
    }


@pytest.fixture
def db(monkeypatch):
    database = _Database()
    monkeypatch.setattr(flights_routes, "get_db", lambda: database)
    monkeypatch.setattr(changes_routes, "get_db", lambda: database)
    flights_routes._dashboard_cache.clear()
    return database


def test_parsed_itinerary_keeps_auto_on_text():
    raw = parse_flight_detail(_flight_detail_envelope("ON"))
    itinerary = raw["ItineraryResponseList"]["ItineraryInfoResponse"]
    assert itinerary["AutoOn"] == "ON"
    assert itinerary["DepartureGate"] == "67A"


@pytest.mark.parametrize(
    ("auto_on", "stored", "in_progress"),
    [
        ("ON", "ON", True),
        ("AUTO", "AUTO", True),
        ("YES", "YES", True),
        ("1", "1", True),
        ("OFF", "OFF", False),
        ("off", "off", False),
        ("Off", "Off", False),
        ("", "", False),
        (None, "", False),
    ],
)
def test_converter_records_automatic_on_board(auto_on, stored, in_progress):
    doc = convert_detail(_flight_detail_envelope(auto_on))
    _assert_indicator(doc, stored, in_progress)
    assert doc["gate"] == "67A"
    assert doc["aircraft"]["type"] == "321"


def test_converter_reads_namespaced_auto_on():
    doc = convert_detail(_flight_detail_envelope("ON", prefixed=True))
    _assert_indicator(doc, "ON", True)


@pytest.mark.parametrize("filename", SAMPLE_FLIGHT_DETAILS)
def test_committed_gulf_air_flight_details_record_auto_on_off(filename):
    xml_text = (SAMPLE_DIR / filename).read_text()
    xml_text = re.sub(r"<wsse:Security\b[\s\S]*?</wsse:Security>", "", xml_text, count=1)
    raw = parse_flight_detail(xml_text)
    itinerary = raw["ItineraryResponseList"]["ItineraryInfoResponse"]
    assert itinerary.get("AutoOn") == "OFF"
    doc = convert_flight_status(
        raw,
        itinerary.get("Airline") or "GF",
        str(itinerary.get("Flight")),
        itinerary.get("Origin") or "",
        itinerary.get("ScheduledDepartureDate") or "",
    )
    _assert_indicator(doc, "OFF", False)


def test_status_response_exposes_automatic_boarding_not_the_raw_archive(db):
    doc = convert_detail(_flight_detail_envelope("ON"))
    db["flight_status"].docs.append(doc)

    body = flights_routes.get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
    )

    _assert_indicator(body, "ON", True)
    assert "_raw" not in body
    assert body["passengerCounts"]["Y"]["onBoard"] == 10


def test_status_history_exposes_automatic_boarding(db):
    db["flight_status"].docs.append(convert_detail(_flight_detail_envelope("OFF")))
    db["flight_status"].docs[-1]["fetchedAt"] = "2026-03-19T13:00:00Z"
    db["flight_status"].docs.append(convert_detail(_flight_detail_envelope("ON")))
    db["flight_status"].docs[-1]["fetchedAt"] = "2026-03-19T14:00:00Z"

    history = flights_routes.get_flight_status_history(
        "2057", origin="BOM", date="2026-03-19", limit=20,
    )

    assert [row["automaticOnBoard"] for row in history] == ["ON", "OFF"]
    assert [row["automaticBoardingInProgress"] for row in history] == [True, False]
    assert all("_raw" not in row for row in history)


def test_snapshot_status_exposes_automatic_boarding(db):
    doc = convert_detail(_flight_detail_envelope("AUTO"))
    db["snapshots"].docs.append({
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "snapshotType": "flight_status",
        "sequenceNumber": 2,
        "capturedAt": "2026-03-19T14:00:00Z",
        "data": doc,
    })

    body = flights_routes.get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=4,
    )

    _assert_indicator(body, "AUTO", True)
    assert "_raw" not in body


def test_operations_board_shows_automatic_boarding_beside_the_boarded_count(db):
    doc = convert_detail(_flight_detail_envelope("ON"))
    db["flight_status"].docs.append(doc)
    db["passenger_list"].docs.append(_boarded_passenger_list())

    board = flights_routes.get_flight_dashboard(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
    )

    assert board["overview"]["soulsOnBoard"] == 1
    _assert_indicator(board["overview"], "ON", True)
    _assert_indicator(board["flightStatus"], "ON", True)
    assert "_raw" not in board["flightStatus"]


def test_flight_list_operations_summary_shows_automatic_boarding(db):
    doc = convert_detail(_flight_detail_envelope("YES"))
    db["flight_status"].docs.append(doc)

    flights = flights_routes.list_flights(date="2026-03-19")
    match = next(item for item in flights if item["flightNumber"] == "2057")

    _assert_indicator(match["operationalSummary"], "YES", True)


def test_snapshot_compare_reports_automatic_boarding_turning_on(db):
    off = convert_detail(_flight_detail_envelope("OFF"))
    on = convert_detail(_flight_detail_envelope("ON"))
    db["snapshots"].docs.extend([
        {
            "flightNumber": "2057",
            "origin": "BOM",
            "departureDate": "2026-03-19",
            "snapshotType": "flight_status",
            "sequenceNumber": 1,
            "data": off,
        },
        {
            "flightNumber": "2057",
            "origin": "BOM",
            "departureDate": "2026-03-19",
            "snapshotType": "flight_status",
            "sequenceNumber": 2,
            "data": on,
        },
    ])

    compared = changes_routes.compare_snapshot_against_latest(
        "2057", snapshot_sequence=1, origin="BOM", date="2026-03-19",
    )
    flight_status = compared["types"]["flight_status"]
    assert flight_status["changed"] is True
    delta = flight_status["deltas"]["automaticOnBoard"]
    assert delta["selected"] == "OFF"
    assert delta["latest"] == "ON"
    assert delta["changed"] is True
    progress = flight_status["deltas"]["automaticBoardingInProgress"]
    assert progress["selected"] is False
    assert progress["latest"] is True


def test_status_diff_records_automatic_boarding_turning_on():
    flight_info = {"airline": "GF", "flightNumber": "2057", "origin": "BOM", "departureDate": "2026-03-19"}
    before = {"snapshotId": "snap-off", "data": convert_detail(_flight_detail_envelope("OFF"))}
    after = {"snapshotId": "snap-on", "data": convert_detail(_flight_detail_envelope("ON"))}

    changes = diff_flight_status(before, after, flight_info)
    indicator = [change for change in changes if change.get("field") == "automaticOnBoard"]

    assert len(indicator) == 1
    assert indicator[0]["oldValue"] == "OFF"
    assert indicator[0]["newValue"] == "ON"


def test_restore_writes_automatic_boarding_onto_the_current_status(db):
    doc = convert_detail(_flight_detail_envelope("ON"))
    db["snapshots"].docs.append({
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "snapshotType": "flight_status",
        "sequenceNumber": 3,
        "data": doc,
    })

    changes_routes.restore_snapshot_version(
        "2057", snapshot_sequence=3, origin="BOM", date="2026-03-19",
    )
    restored = db["flight_status"].docs[-1]
    _assert_indicator(restored, "ON", True)
    assert "_raw" not in restored

    body = flights_routes.get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
    )
    _assert_indicator(body, "ON", True)


def test_missing_flight_status_is_still_not_found(db):
    with pytest.raises(HTTPException) as caught:
        flights_routes.get_flight_status(
            "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
        )
    assert caught.value.status_code == 404
