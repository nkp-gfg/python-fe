"""Phase-journey identity tests.

These tests fail while travelers who share a record locator and name are
collapsed into one boarding flow. They pass once each stored passenger id
(or name-association id, when the passenger id is empty) keeps its own slot
in the transition map, including unnamed Sabre group rows whose last name
is the placeholder PAX.

Run:
    python3 -m pytest backend/tests/test_phase_journey_shared_name_key.py -q
"""

import os
import sys

os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import flights
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
AIRLINE = "GF"
GROUP_PNR = "GRPLOC"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query):
        return _Cursor(doc for doc in self._docs if _matches(doc, query))

    def find_one(self, query, sort=None):
        matched = [doc for doc in self._docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matched.sort(
                    key=lambda doc: doc.get(field) or "",
                    reverse=direction < 0,
                )
        return matched[0] if matched else None


class _Db:
    """In-memory stand-in for the snapshot reads the journey route performs.

    It applies the equality filters and sequence sort that route uses, and
    returns the documents the converters produced. It does not key passengers.
    """

    def __init__(self, snapshots):
        self._collections = {
            "snapshots": _Collection(snapshots),
            "reservations": _Collection([]),
            "passenger_list": _Collection([]),
            "flight_status": _Collection([]),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _matches(doc, query):
    return all(doc.get(key) == expected for key, expected in query.items())


def _passenger_xml(row):
    first = row.get("first")
    first_xml = f"<FirstName>{first}</FirstName>" if first else ""
    passenger_id = row.get("passenger_id", "")
    association = row.get("association")
    association_attr = (
        f' nameAssociationID="{association}"' if association else ""
    )
    group = row.get("group", "")
    group_xml = f"<GroupCode>{group}</GroupCode>" if group else ""
    checked = "true" if row.get("checked_in") else "false"
    boarded = "true" if row.get("boarded") else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{row["last"]}</LastName>
            {first_xml}
          </Name_Details>
          <PNRLocator{association_attr}>{row["pnr"]}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>{row["line"]}</LineNumber>
          {group_xml}
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>BAH</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>{checked}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>{boarded}</BoardStatus></Boarding_Info>
        </PassengerInfo>
    """


def _parse_passenger_list(rows):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(_passenger_xml(row) for row in rows)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, AIRLINE, FLIGHT, DATE, ORIGIN)


def _parse_flight_status(boarding):
    boarding_xml = (
        '<BoardingTime boardingIndicator="BDG">05:00PM</BoardingTime>'
        if boarding
        else "<BoardingTime>04:00PM</BoardingTime>"
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <Origin>{ORIGIN}</Origin>
          <DepartureDate>{DATE}</DepartureDate>
          <FlightStatus>OPENCI</FlightStatus>
          <DepartureGate>A1</DepartureGate>
          {boarding_xml}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")
    return convert_flight_status(body, AIRLINE, FLIGHT, ORIGIN, DATE)


def _snapshot(snapshot_type, sequence, data):
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": f"2026-03-19T0{sequence}:00:00Z",
        "data": data,
    }


def _group_row(index, **overrides):
    row = {
        "last": "PAX",
        "first": "",
        "pnr": GROUP_PNR,
        "association": str(index),
        "passenger_id": f"PAXID{index:04d}",
        "line": index,
        "group": "GRP",
        "checked_in": False,
        "boarded": False,
    }
    row.update(overrides)
    return row


def _named_row(**overrides):
    row = {
        "last": "SMITH",
        "first": "JOHN",
        "pnr": "SMITH1",
        "association": "1",
        "passenger_id": "NAMED0001",
        "line": 1,
        "group": "",
        "checked_in": False,
        "boarded": False,
    }
    row.update(overrides)
    return row


def _assert_converted_identities(doc, rows):
    passengers = doc["passengers"]
    assert len(passengers) == len(rows)
    for passenger, row in zip(passengers, rows):
        assert passenger["pnr"] == row["pnr"]
        assert passenger["lastName"] == row["last"]
        assert passenger["lineNumber"] == row["line"]
        assert (passenger.get("passengerId") or "") == row["passenger_id"]
        assert (passenger.get("nameAssociationId") or "") == (
            row["association"] or ""
        )
        if row["last"] == "PAX" and not row["first"] and row["group"]:
            assert passenger["isUnnamedGroup"] is True


def _flow_count(transition, from_state, to_state):
    return sum(
        flow["count"]
        for flow in transition["flows"]
        if flow["fromState"] == from_state and flow["toState"] == to_state
    )


def _journey(checkin_rows, boarding_rows):
    checkin = _parse_passenger_list(checkin_rows)
    boarding = _parse_passenger_list(boarding_rows)
    _assert_converted_identities(checkin, checkin_rows)
    _assert_converted_identities(boarding, boarding_rows)
    checkin_status = _parse_flight_status(boarding=False)
    boarding_status = _parse_flight_status(boarding=True)
    assert checkin_status["status"] == "OPENCI"
    assert checkin_status["boarding"]["indicator"] == ""
    assert boarding_status["boarding"]["indicator"] == "BDG"
    db = _Db([
        _snapshot("flight_status", 1, checkin_status),
        _snapshot("flight_status", 2, boarding_status),
        _snapshot("passenger_list", 1, checkin),
        _snapshot("passenger_list", 2, boarding),
    ])
    original = flights.get_db
    flights.get_db = lambda: db
    try:
        payload = flights.get_phase_journey(
            FLIGHT, origin=ORIGIN, date=DATE)
    finally:
        flights.get_db = original
    phases = [phase["phase"] for phase in payload["phases"]]
    assert phases == ["CHECK_IN", "BOARDING"], phases
    assert payload["phases"][0]["summary"]["totalPassengers"] == len(checkin_rows)
    assert payload["phases"][1]["summary"]["totalPassengers"] == len(boarding_rows)
    assert len(payload["transitions"]) == 1
    return payload["transitions"][0], checkin, boarding


def _changes(before_doc, after_doc):
    return detect_changes(
        {"snapshotId": "before", "data": before_doc},
        {"snapshotId": "after", "data": after_doc},
        "passenger_list",
        FLIGHT_INFO,
    )


def test_unnamed_group_added_count_keeps_each_traveler():
    transition, _, _ = _journey(
        [_named_row()],
        [
            _named_row(),
            _group_row(2),
            _group_row(3),
            _group_row(4),
        ],
    )
    assert transition["addedCount"] == 3
    assert transition["removedCount"] == 0
    assert _flow_count(transition, "new", "booked") == 3


def test_unnamed_group_removed_count_keeps_each_traveler():
    transition, _, _ = _journey(
        [_group_row(2), _group_row(3), _group_row(4), _named_row()],
        [_named_row()],
    )
    assert transition["removedCount"] == 3
    assert transition["addedCount"] == 0
    assert _flow_count(transition, "booked", "removed") == 3


def test_unnamed_group_boarded_flow_keeps_each_traveler():
    transition, _, _ = _journey(
        [
            _group_row(2, checked_in=True),
            _group_row(3, checked_in=True),
            _group_row(4, checked_in=True),
        ],
        [
            _group_row(2, checked_in=True, boarded=True),
            _group_row(3, checked_in=True, boarded=True),
            _group_row(4, checked_in=True, boarded=True),
        ],
    )
    assert transition["addedCount"] == 0
    assert transition["removedCount"] == 0
    assert _flow_count(transition, "checkedIn", "boarded") == 3


def test_later_group_row_does_not_replace_earlier_boarding_states():
    """The last placeholder must not decide the state of the earlier ones."""
    transition, _, _ = _journey(
        [
            _group_row(2, checked_in=True),
            _group_row(3, checked_in=True),
            _group_row(4, checked_in=True),
        ],
        [
            _group_row(2, checked_in=True, boarded=True),
            _group_row(3, checked_in=True, boarded=True),
            _group_row(4, checked_in=True),
        ],
    )
    assert _flow_count(transition, "checkedIn", "boarded") == 2
    assert _flow_count(transition, "checkedIn", "checkedIn") == 1
    assert transition["addedCount"] == 0
    assert transition["removedCount"] == 0


def test_same_real_name_with_distinct_passenger_ids_stays_separate():
    transition, _, _ = _journey(
        [_named_row(passenger_id="NAMED0001", line=1, association="1")],
        [
            _named_row(passenger_id="NAMED0001", line=1, association="1"),
            _named_row(
                passenger_id="NAMED0002",
                line=2,
                association="2",
                pnr="SMITH1",
            ),
        ],
    )
    assert transition["addedCount"] == 1
    assert _flow_count(transition, "new", "booked") == 1
    assert _flow_count(transition, "booked", "booked") == 1


def test_empty_passenger_id_falls_back_to_name_association_id():
    rows_before = [_named_row()]
    rows_after = [
        _named_row(),
        _group_row(2, passenger_id="", association="2"),
        _group_row(3, passenger_id="", association="3"),
        _group_row(4, passenger_id="", association="4"),
    ]
    transition, _, after = _journey(rows_before, rows_after)
    group = [p for p in after["passengers"] if p["isUnnamedGroup"]]
    assert [p.get("passengerId") or "" for p in group] == ["", "", ""]
    assert [p["nameAssociationId"] for p in group] == ["2", "3", "4"]
    assert transition["addedCount"] == 3
    assert _flow_count(transition, "new", "booked") == 3


def test_placeholder_rows_without_ids_are_not_collapsed():
    rows_before = [_named_row()]
    rows_after = [
        _named_row(),
        _group_row(2, passenger_id="", association=""),
        _group_row(3, passenger_id="", association=""),
        _group_row(4, passenger_id="", association=""),
    ]
    transition, _, after = _journey(rows_before, rows_after)
    group = [p for p in after["passengers"] if p["isUnnamedGroup"]]
    assert len(group) == 3
    assert {p["lineNumber"] for p in group} == {2, 3, 4}
    assert transition["addedCount"] == 3


def test_partial_group_overlap_counts_each_traveler_once():
    transition, _, _ = _journey(
        [
            _group_row(2),
            _group_row(3),
            _group_row(4),
        ],
        [
            _group_row(3, checked_in=True, boarded=True),
            _group_row(4),
            _group_row(5),
        ],
    )
    assert transition["removedCount"] == 1
    assert transition["addedCount"] == 1
    assert _flow_count(transition, "booked", "removed") == 1
    assert _flow_count(transition, "booked", "boarded") == 1
    assert _flow_count(transition, "booked", "booked") == 1
    assert _flow_count(transition, "new", "booked") == 1


def test_change_detection_counts_each_added_removed_and_boarded_traveler():
    before = _parse_passenger_list([
        _group_row(2, checked_in=True),
        _group_row(3, checked_in=True),
        _group_row(4, checked_in=True),
    ])
    boarded = _parse_passenger_list([
        _group_row(2, checked_in=True, boarded=True),
        _group_row(3, checked_in=True, boarded=True),
        _group_row(4, checked_in=True, boarded=True),
    ])
    added = _parse_passenger_list([
        _group_row(2),
        _group_row(3),
        _group_row(4),
    ])
    empty = _parse_passenger_list([_named_row()])
    boarded_changes = _changes(before, boarded)
    assert sum(1 for change in boarded_changes if change["changeType"] == "BOARDED") == 3
    added_changes = _changes(
        _parse_passenger_list([_named_row()]),
        _parse_passenger_list([
            _named_row(),
            _group_row(2),
            _group_row(3),
            _group_row(4),
        ]),
    )
    assert sum(1 for change in added_changes if change["changeType"] == "PASSENGER_ADDED") == 3
    removed_changes = _changes(added, empty)
    assert sum(1 for change in removed_changes if change["changeType"] == "PASSENGER_REMOVED") == 3


def test_unauthenticated_phase_journey_returns_each_added_traveler():
    checkin_rows = [_named_row()]
    boarding_rows = [_named_row(), _group_row(2), _group_row(3), _group_row(4)]
    checkin = _parse_passenger_list(checkin_rows)
    boarding = _parse_passenger_list(boarding_rows)
    db = _Db([
        _snapshot("flight_status", 1, _parse_flight_status(boarding=False)),
        _snapshot("flight_status", 2, _parse_flight_status(boarding=True)),
        _snapshot("passenger_list", 1, checkin),
        _snapshot("passenger_list", 2, boarding),
    ])
    original = flights.get_db
    flights.get_db = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/flights/{FLIGHT}/phase-journey",
                params={"origin": ORIGIN, "date": DATE},
            )
    finally:
        flights.get_db = original
    assert response.status_code == 200, response.text
    body = response.json()
    assert "authorization" not in {name.lower() for name in response.request.headers}
    transition = body["transitions"][0]
    assert transition["addedCount"] == 3
    assert _flow_count(transition, "new", "booked") == 3
