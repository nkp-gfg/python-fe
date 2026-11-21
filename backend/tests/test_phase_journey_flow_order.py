"""Phase-journey flows must follow capture order, not lifecycle rank.

These tests fail while ``get_phase_journey`` sorts the first snapshot of each
phase into SCHEDULED → CHECK_IN → BOARDING → CLOSED → DEPARTED and then diffs
passenger lists between those rank neighbors. They pass once transitions are
computed from those same first-of-phase snapshots ordered by sequence number
(capture order). Card layout may stay in lifecycle order; the passenger lists
compared for adds, removals, and boardings must not.

Not executed here: ``frontend/components/dashboard/phase-snapshot-cards.tsx``
and ``frontend/components/dashboard/phase-transition-chart.tsx``. Both render
``phases`` and ``transitions`` in the array order this endpoint returns and
do not derive phases or recompute flows.

Run:
    python3 -m pytest backend/tests/test_phase_journey_flow_order.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import flights as flights_routes
from backend.feeder import storage
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.sabre.client import SabreClient

AIRLINE = "GF"
FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"


class MemoryCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            specs = list(key_or_list)
        else:
            specs = [(key_or_list, 1 if direction is None else direction)]
        for key, direct in reversed(specs):
            reverse = direct in (-1, "desc", "descending")
            self._docs.sort(key=lambda doc, field=key: doc.get(field), reverse=reverse)
        return self

    def __iter__(self):
        return iter(self._docs)


class MemoryCollection:
    """Equality filter and field sort used by snapshot storage and the journey query.

    ``Cursor.sort`` in pymongo 4.20.1 only records the spec (cursor_shared.py).
    The server applies it. These tests sort the same documents with the same
    direction constants the product passes: 1 ascending, -1 descending.
    """

    def __init__(self):
        self._docs = []

    def insert_one(self, doc):
        self._docs.append(doc)
        return None

    def find(self, query, projection=None):
        return MemoryCursor([doc for doc in self._docs if _matches(doc, query)])

    def find_one(self, query, sort=None, projection=None):
        matched = [doc for doc in self._docs if _matches(doc, query)]
        if sort:
            MemoryCursor(matched).sort(list(sort))
        return matched[0] if matched else None


class MemoryDatabase(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = MemoryCollection()
        return dict.__getitem__(self, name)


def _matches(doc, query):
    return all(doc.get(key) == expected for key, expected in query.items())


@pytest.fixture
def client(monkeypatch):
    previous = storage._db
    database = MemoryDatabase()
    storage.init_db(database)
    monkeypatch.setattr(flights_routes, "get_db", lambda: database)
    counter = {"n": 0}

    def clock():
        counter["n"] += 1
        return f"2026-03-19T{counter['n']:02d}:00:00Z"

    monkeypatch.setattr(storage, "_now_iso", clock)
    app = FastAPI()
    app.include_router(flights_routes.router)
    yield TestClient(app)
    storage._db = previous


def _flight_status_xml(status=None, boarding_indicator=None):
    status_xml = f"<FlightStatus>{status}</FlightStatus>" if status is not None else ""
    if boarding_indicator:
        boarding = (
            f'<BoardingTime boardingIndicator="{boarding_indicator}">08:00AM</BoardingTime>'
        )
    else:
        boarding = "<BoardingTime>08:00AM</BoardingTime>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <Origin>{ORIGIN}</Origin>
          <DepartureDate>{DATE}</DepartureDate>
          {status_xml}
          {boarding}
          <DepartureGate>A1</DepartureGate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _passenger_xml(passengers):
    blocks = []
    for index, passenger in enumerate(passengers, 1):
        checked = "true" if passenger.get("checked_in") else "false"
        boarded = "true" if passenger.get("boarded") else "false"
        blocks.append(
            f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{passenger["last"]}</LastName>
            <FirstName>{passenger["first"]}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="{index}">{passenger["pnr"]}</PNRLocator>
          <PassengerType>F</PassengerType>
          <Cabin>Y</Cabin>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>{checked}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>{boarded}</BoardStatus></Boarding_Info>
        </PassengerInfo>"""
        )
    joined = "".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>{joined}</PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _parse_response(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def ingest(status_xml, passenger_xml):
    """Store one flight-status snapshot and one passenger-list snapshot.

    Same pairing the feeder uses: each successful poll writes one document of
    each type, so their sequence numbers stay aligned and the journey handler
    binds a passenger list to the flight status of that poll.
    """
    status_doc = convert_flight_status(
        _parse_response(status_xml, "ns3:ACS_FlightDetailRS"),
        AIRLINE,
        FLIGHT,
        ORIGIN,
        DATE,
    )
    passenger_doc = convert_passenger_list(
        _parse_response(passenger_xml, "GetPassengerListRS"),
        AIRLINE,
        FLIGHT,
        DATE,
        ORIGIN,
    )
    storage.store_snapshot("req-fs", AIRLINE, FLIGHT, ORIGIN, DATE, "flight_status", status_doc)
    storage.store_snapshot(
        "req-pl", AIRLINE, FLIGHT, ORIGIN, DATE, "passenger_list", passenger_doc
    )


def fetch_journey(client):
    response = client.get(
        f"/flights/{FLIGHT}/phase-journey",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _flow_map(transition):
    return {(item["fromState"], item["toState"]): item["count"] for item in transition["flows"]}


def assert_time_ordered_transitions(body, expected):
    actual_pairs = [(item["fromPhase"], item["toPhase"]) for item in body["transitions"]]
    expected_pairs = [(item["from"], item["to"]) for item in expected]
    assert actual_pairs == expected_pairs, body
    phases = {phase["phase"]: phase for phase in body["phases"]}
    for transition, spec in zip(body["transitions"], expected):
        earlier = phases[transition["fromPhase"]]
        later = phases[transition["toPhase"]]
        assert earlier["sequenceNumber"] < later["sequenceNumber"], body
        assert earlier["capturedAt"] < later["capturedAt"], body
        assert _flow_map(transition) == spec["flows"], body
        added = sum(count for (src, _dst), count in spec["flows"].items() if src == "new")
        removed = sum(count for (_src, dst), count in spec["flows"].items() if dst == "removed")
        assert transition["addedCount"] == added
        assert transition["removedCount"] == removed


def test_boarding_then_missing_status_flows_forward_in_time(client):
    """A poll that starts at boarding, then a later poll with no Sabre status.

    The later manifest is check-in: Alice has moved booked → checked-in, Bob
    has left, and Carol has been added. Counts must run in that direction.
    """
    ingest(
        _flight_status_xml(status="OPENCI", boarding_indicator="BDG"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A"},
            {"pnr": "BBB222", "last": "BOB", "first": "B", "checked_in": True},
        ]),
    )
    ingest(
        _flight_status_xml(status=None),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True},
            {"pnr": "CCC333", "last": "CAROL", "first": "C"},
        ]),
    )

    body = fetch_journey(client)
    assert_time_ordered_transitions(body, [
        {
            "from": "BOARDING",
            "to": "CHECK_IN",
            "flows": {
                ("booked", "checkedIn"): 1,
                ("checkedIn", "removed"): 1,
                ("new", "booked"): 1,
            },
        },
    ])


def test_status_regression_does_not_count_an_offload_as_a_boarding(client):
    """Sabre status FINAL, then a later OPENCI poll where Alice is no longer boarded."""
    ingest(
        _flight_status_xml(status="FINAL"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True, "boarded": True},
        ]),
    )
    ingest(
        _flight_status_xml(status="OPENCI"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True},
        ]),
    )

    body = fetch_journey(client)
    assert_time_ordered_transitions(body, [
        {
            "from": "CLOSED",
            "to": "CHECK_IN",
            "flows": {
                ("boarded", "checkedIn"): 1,
            },
        },
    ])


def test_phase_rank_does_not_change_which_snapshots_are_neighbors(client):
    """Three first-seen phases whose lifecycle order is not their capture order.

    Time neighbors are check-in → boarding, then boarding → scheduled.
    Rank order would instead pair the scheduled manifest with check-in.
    """
    ingest(
        _flight_status_xml(status="OPENCI"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True},
        ]),
    )
    ingest(
        _flight_status_xml(status="OPENCI", boarding_indicator="BDG"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True, "boarded": True},
            {"pnr": "BBB222", "last": "BOB", "first": "B", "checked_in": True, "boarded": True},
        ]),
    )
    ingest(
        _flight_status_xml(status=None),
        _passenger_xml([
            {"pnr": "CCC333", "last": "CAROL", "first": "C"},
        ]),
    )

    body = fetch_journey(client)
    assert_time_ordered_transitions(body, [
        {
            "from": "CHECK_IN",
            "to": "BOARDING",
            "flows": {
                ("checkedIn", "boarded"): 1,
                ("new", "boarded"): 1,
            },
        },
        {
            "from": "BOARDING",
            "to": "SCHEDULED",
            "flows": {
                ("boarded", "removed"): 2,
                ("new", "booked"): 1,
            },
        },
    ])


def test_first_snapshot_of_a_phase_is_compared_in_capture_order(client):
    """A second boarding poll must not replace the first, and must not be the flow target.

    Alice is booked on the first boarding snapshot and boarded on a later
    boarding snapshot. The check-in snapshot captured after both is the only
    other phase. The flow uses the first boarding manifest and points at the
    later check-in manifest.
    """
    ingest(
        _flight_status_xml(status="OPENCI", boarding_indicator="BDG"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A"},
        ]),
    )
    ingest(
        _flight_status_xml(status="OPENCI", boarding_indicator="BDG"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True, "boarded": True},
        ]),
    )
    ingest(
        _flight_status_xml(status=None),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True},
            {"pnr": "BBB222", "last": "BOB", "first": "B"},
        ]),
    )

    body = fetch_journey(client)
    assert_time_ordered_transitions(body, [
        {
            "from": "BOARDING",
            "to": "CHECK_IN",
            "flows": {
                ("booked", "checkedIn"): 1,
                ("new", "booked"): 1,
            },
        },
    ])
    boarding = next(phase for phase in body["phases"] if phase["phase"] == "BOARDING")
    check_in = next(phase for phase in body["phases"] if phase["phase"] == "CHECK_IN")
    assert boarding["sequenceNumber"] == 1
    assert check_in["sequenceNumber"] == 3


def test_lifecycle_order_that_matches_capture_order_stays_forward(client):
    """Guard: when the first snapshots already follow the lifecycle, flows stay forward."""
    ingest(
        _flight_status_xml(status=None),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A"},
        ]),
    )
    ingest(
        _flight_status_xml(status="OPENCI"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True},
        ]),
    )
    ingest(
        _flight_status_xml(status="OPENCI", boarding_indicator="BDG"),
        _passenger_xml([
            {"pnr": "AAA111", "last": "ALICE", "first": "A", "checked_in": True, "boarded": True},
        ]),
    )

    body = fetch_journey(client)
    assert_time_ordered_transitions(body, [
        {
            "from": "SCHEDULED",
            "to": "CHECK_IN",
            "flows": {
                ("booked", "checkedIn"): 1,
            },
        },
        {
            "from": "CHECK_IN",
            "to": "BOARDING",
            "flows": {
                ("checkedIn", "boarded"): 1,
            },
        },
    ])
