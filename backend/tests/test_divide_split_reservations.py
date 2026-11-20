"""Split-booking link dropped when Trip Search reservations are ingested.

These tests are the spec for the missing DivideSplitDetails link. They fail
while conversion stores only the post-split ``numberInParty`` and the
reservations API, party-size audit, and reservation diff never surface the
other record.

Each stored reservation must include:

* ``divideSplit.splitTo`` and ``divideSplit.splitFrom`` — lists in document
  order, empty when that direction is absent. Each item has
  ``recordLocator``, ``divideTimestamp``, ``originalPartySize``,
  ``currentPartySize``, and ``passengerNames`` taken from that
  SplitToRecord or SplitFromRecord.
* ``originalPartySize`` — the largest ``originalPartySize`` on those links,
  or ``numberInParty`` when both lists are empty.
* ``currentPartySize`` — the smallest ``currentPartySize`` on those links,
  or ``numberInParty`` when both lists are empty.
* ``numberInParty`` — the reservation ``@numberInParty`` value, left as the
  post-split count. It must not be rewritten to the original party size.

``run_audit`` must emit ``DIVIDED_BOOKING`` for every reservation that has
a split link, including when the manifest count equals ``numberInParty``.
The alert ``details`` are ``originalPartySize``, ``currentPartySize``,
``splitToRecordLocators``, and ``splitFromRecordLocators``.

``diff_reservations`` must record the other record locator when a split
link appears, including when ``numberInParty`` does not change.

Not covered here: the React reservation card and insight panel, the flight
insight party-size distribution, and the analytics party-size aggregation.
Those callers also read ``numberInParty`` and do not render a split link.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.api.audit_rules import run_audit
from backend.api.routes.audit import get_flight_audit
from backend.api.routes.reservations import get_reservation_by_pnr, get_reservations
from backend.feeder.converter import convert_reservations
from backend.feeder.differ import diff_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


def _link(locator, timestamp, original, current, names):
    return {
        "recordLocator": locator,
        "divideTimestamp": timestamp,
        "originalPartySize": original,
        "currentPartySize": current,
        "passengerNames": names,
    }


# Split records read from the captured Trip Search response.
EXPECTED_DIVIDES = {
    "KMZWCI": {
        "numberInParty": 1,
        "splitTo": [
            _link("DPHUDD", "2026-03-18T02:32:15", 2, 1, "LEWIS/WILLIAM"),
        ],
        "splitFrom": [],
    },
    "ELUMFB": {
        "numberInParty": 1,
        "splitTo": [
            _link("CCLEUG", "2026-03-18T11:40:57", 1, 0, "I/1KHAN/HAMNA"),
        ],
        "splitFrom": [],
    },
    "NWBNQD": {
        "numberInParty": 1,
        "splitTo": [
            _link("LWSEJZ", "2026-02-25T23:22:01", 3, 2, "SHINE/RENIMOL"),
        ],
        "splitFrom": [
            _link("IRDFST", "2026-02-26T00:30:24", 2, 1, "BABY/OOMMEN"),
        ],
    },
    "IRDFST": {
        "numberInParty": 1,
        "splitTo": [
            _link("LWSEJZ", "2026-02-25T23:22:01", 3, 2, "SHINE/RENIMOL"),
            _link("NWBNQD", "2026-02-26T00:30:24", 2, 1, "SHINE/RENIMOL"),
        ],
        "splitFrom": [],
    },
    "DPHUDD": {
        "numberInParty": 1,
        "splitTo": [],
        "splitFrom": [
            _link("KMZWCI", "2026-03-18T02:32:15", 2, 1, "PANDIT/RUDOLPH"),
        ],
    },
}


def convert_trip_search_response(xml_text, airline="GF", flight_number="2057",
                                 origin="BOM", departure_date="2026-03-19"):
    """Run the same parse the reservations client uses, then convert.

    ``SabreClient.get_reservations`` parses the SOAP body and returns it
    unchanged. Building a client would read Sabre credentials, so this
    calls the same two methods on the response text.
    """
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(object(), parsed, "Trip_SearchRS")
    return convert_reservations(body, airline, flight_number, origin, departure_date)


def _party_sizes(split_to, split_from, number_in_party):
    links = list(split_to) + list(split_from)
    if not links:
        return number_in_party, number_in_party
    original = max(item["originalPartySize"] for item in links)
    current = min(item["currentPartySize"] for item in links)
    return original, current


def _assert_divide(reservation, expected):
    split_to = expected["splitTo"]
    split_from = expected["splitFrom"]
    original, current = _party_sizes(
        split_to, split_from, expected["numberInParty"])
    assert reservation["numberInParty"] == expected["numberInParty"]
    assert reservation["originalPartySize"] == original
    assert reservation["currentPartySize"] == current
    assert reservation["divideSplit"] == {
        "splitTo": split_to,
        "splitFrom": split_from,
    }
    if split_to or split_from:
        assert reservation["originalPartySize"] != reservation["currentPartySize"] or (
            reservation["currentPartySize"] != reservation["numberInParty"]
        )


def _public_reservations(doc):
    """Apply the list endpoint's response model, which drops unknown fields."""
    stored = copy.deepcopy(doc)
    stored.pop("_id", None)
    stored.pop("_raw", None)
    return ReservationsResponse.model_validate(stored).model_dump()["reservations"]


@pytest.fixture(scope="module")
def ingested():
    xml = SAMPLE.read_text(encoding="utf-8")
    return convert_trip_search_response(xml)


def _by_pnr(reservations):
    return {reservation["pnr"]: reservation for reservation in reservations}


def test_parser_delivers_divide_details_that_conversion_drops(ingested):
    """The captured response reaches conversion with every split shape."""
    stored = _by_pnr(ingested["reservations"])
    assert set(EXPECTED_DIVIDES) <= set(stored)
    for pnr, expected in EXPECTED_DIVIDES.items():
        _assert_divide(stored[pnr], expected)


def test_undivided_reservations_keep_party_size_and_no_link(ingested):
    stored = _by_pnr(ingested["reservations"])
    undivided = [pnr for pnr in stored if pnr not in EXPECTED_DIVIDES]
    assert len(undivided) == len(stored) - len(EXPECTED_DIVIDES)
    for pnr in undivided:
        reservation = stored[pnr]
        assert reservation["divideSplit"] == {"splitTo": [], "splitFrom": []}
        assert reservation["originalPartySize"] == reservation["numberInParty"]
        assert reservation["currentPartySize"] == reservation["numberInParty"]


def test_reservations_list_api_returns_split_link(ingested):
    public = _by_pnr(_public_reservations(ingested))
    for pnr, expected in EXPECTED_DIVIDES.items():
        _assert_divide(public[pnr], expected)
    intact = public["DQGZWU"]
    assert intact["numberInParty"] == 2
    assert intact["divideSplit"] == {"splitTo": [], "splitFrom": []}
    assert intact["originalPartySize"] == 2
    assert intact["currentPartySize"] == 2


class _Collection:
    def __init__(self, document):
        self.document = document

    def find_one(self, query, sort=None):
        document = copy.deepcopy(self.document)
        if query.get("flightNumber") not in (None, document.get("flightNumber")):
            return None
        wanted = query.get("reservations.pnr")
        if wanted is not None:
            matches = [
                reservation for reservation in document.get("reservations", [])
                if reservation.get("pnr") == wanted
            ]
            if not matches:
                return None
        if query.get("departureDate") not in (None, document.get("departureDate")):
            return None
        return document

    def find(self, query, projection=None):
        return _Cursor([])


class _Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.documents)


class _Database:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


def test_reservation_by_pnr_returns_split_to_and_split_from(ingested, monkeypatch):
    database = _Database({"reservations": _Collection(ingested)})
    monkeypatch.setattr(
        "backend.api.routes.reservations.get_db", lambda: database)

    split_to = get_reservation_by_pnr("2057", "KMZWCI", date=None)
    record = split_to["reservations"][0]
    _assert_divide(record, EXPECTED_DIVIDES["KMZWCI"])

    split_from = get_reservation_by_pnr("2057", "DPHUDD", date=None)
    record = split_from["reservations"][0]
    _assert_divide(record, EXPECTED_DIVIDES["DPHUDD"])

    both = get_reservation_by_pnr("2057", "NWBNQD", date=None)
    record = both["reservations"][0]
    _assert_divide(record, EXPECTED_DIVIDES["NWBNQD"])

    repeated = get_reservation_by_pnr("2057", "IRDFST", date=None)
    record = repeated["reservations"][0]
    _assert_divide(record, EXPECTED_DIVIDES["IRDFST"])


def test_reservations_route_publishes_split_link(ingested, monkeypatch):
    database = _Database({"reservations": _Collection(ingested)})
    monkeypatch.setattr(
        "backend.api.routes.reservations.get_db", lambda: database)

    payload = get_reservations(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None)
    assert "_raw" not in payload
    public = _by_pnr(_public_reservations(payload))
    _assert_divide(public["KMZWCI"], EXPECTED_DIVIDES["KMZWCI"])
    _assert_divide(public["ELUMFB"], EXPECTED_DIVIDES["ELUMFB"])


def _divide_alert(result, pnr):
    alerts = [
        alert for alert in result["alerts"]
        if alert.get("pnr") == pnr and alert.get("ruleId") == "DIVIDED_BOOKING"
    ]
    assert len(alerts) == 1
    return alerts[0]


def _expected_alert_details(expected):
    return {
        "originalPartySize": _party_sizes(
            expected["splitTo"], expected["splitFrom"], expected["numberInParty"]
        )[0],
        "currentPartySize": _party_sizes(
            expected["splitTo"], expected["splitFrom"], expected["numberInParty"]
        )[1],
        "splitToRecordLocators": [
            item["recordLocator"] for item in expected["splitTo"]
        ],
        "splitFromRecordLocators": [
            item["recordLocator"] for item in expected["splitFrom"]
        ],
    }


def test_party_size_audit_links_divided_bookings_when_manifest_matches(ingested):
    """A remaining party that matches the manifest is still a divided booking."""
    passengers = []
    for pnr, expected in EXPECTED_DIVIDES.items():
        # One manifest row per post-split numberInParty, so the existing
        # mismatch rule stays quiet. The infant split has current size 0
        # while numberInParty stays 1, which is the case that must not be
        # reported as an intact party of 1.
        for index in range(expected["numberInParty"]):
            passengers.append({
                "pnr": pnr,
                "lastName": "PAX",
                "firstName": str(index),
            })
    passengers.extend([
        {"pnr": "DQGZWU", "lastName": "ONE", "firstName": "A"},
        {"pnr": "DQGZWU", "lastName": "TWO", "firstName": "B"},
    ])

    result = run_audit({"passengers": passengers}, ingested, [])

    for pnr, expected in EXPECTED_DIVIDES.items():
        alert = _divide_alert(result, pnr)
        assert alert["details"] == _expected_alert_details(expected)
        assert alert["details"]["originalPartySize"] != alert["details"]["currentPartySize"] or (
            alert["details"]["currentPartySize"] != expected["numberInParty"]
        )

    intact_alerts = [
        alert for alert in result["alerts"] if alert.get("pnr") == "DQGZWU"
    ]
    assert intact_alerts == []


def test_undivided_party_mismatch_still_alerts():
    xml = _soap_reservation(
        "INTACT",
        number_in_party=2,
        passengers=[("ONE", "A"), ("TWO", "B")],
        divide_xml="<stl19:DivideSplitDetails />",
    )
    doc = convert_trip_search_response(xml)
    result = run_audit(
        {"passengers": [{"pnr": "INTACT", "lastName": "ONE", "firstName": "A"}]},
        doc,
        [],
    )
    mismatches = [
        alert for alert in result["alerts"]
        if alert.get("ruleId") == "PARTY_SIZE_MISMATCH" and alert.get("pnr") == "INTACT"
    ]
    assert len(mismatches) == 1
    assert mismatches[0]["details"]["declaredPartySize"] == 2
    assert mismatches[0]["details"]["manifestCount"] == 1
    assert not any(alert.get("ruleId") == "DIVIDED_BOOKING" for alert in result["alerts"])


def test_audit_route_reports_divided_booking(ingested, monkeypatch):
    passengers = {
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "fetchedAt": ingested["fetchedAt"],
        "passengers": [
            {"pnr": "KMZWCI", "lastName": "LEWIS", "firstName": "WILLIAM"},
        ],
    }
    database = _Database({
        "passenger_list": _Collection(passengers),
        "reservations": _Collection(ingested),
        "changes": _Collection(None),
    })
    monkeypatch.setattr("backend.api.routes.audit.get_db", lambda: database)

    result = get_flight_audit("2057", origin="BOM", date="2026-03-19")
    alert = _divide_alert(result, "KMZWCI")
    assert alert["details"] == _expected_alert_details(EXPECTED_DIVIDES["KMZWCI"])
    mismatch = [
        item for item in result["alerts"]
        if item.get("pnr") == "KMZWCI" and item.get("ruleId") == "PARTY_SIZE_MISMATCH"
    ]
    assert mismatch == []


def test_diff_records_split_link_when_party_size_is_unchanged():
    before = convert_trip_search_response(_soap_reservation(
        "DPHUDD",
        number_in_party=1,
        passengers=[("PANDIT", "RUDOLPH")],
        divide_xml="<stl19:DivideSplitDetails />",
    ))
    after = convert_trip_search_response(_soap_reservation(
        "DPHUDD",
        number_in_party=1,
        passengers=[("PANDIT", "RUDOLPH")],
        divide_xml="""
        <stl19:DivideSplitDetails>
          <stl19:SplitFromRecord>
            <stl19:DivideTimestamp>2026-03-18T02:32:15</stl19:DivideTimestamp>
            <stl19:RecordLocator>KMZWCI</stl19:RecordLocator>
            <stl19:OriginalNumberOfPax>02</stl19:OriginalNumberOfPax>
            <stl19:CurrentNumberOfPax>01</stl19:CurrentNumberOfPax>
            <stl19:CurrentPassengerNames>PANDIT/RUDOLPH</stl19:CurrentPassengerNames>
          </stl19:SplitFromRecord>
        </stl19:DivideSplitDetails>
        """,
    ))
    changes = diff_reservations(
        {"snapshotId": "before", "data": before},
        {"snapshotId": "after", "data": after},
        FLIGHT,
    )
    assert changes, "a new split-from link is a reservation change"
    assert any("KMZWCI" in json.dumps(change) for change in changes)


def test_diff_records_other_locator_when_party_shrinks():
    before = convert_trip_search_response(_soap_reservation(
        "KMZWCI",
        number_in_party=2,
        passengers=[("LEWIS", "WILLIAM"), ("PANDIT", "RUDOLPH")],
        divide_xml="<stl19:DivideSplitDetails />",
    ))
    after = convert_trip_search_response(_soap_reservation(
        "KMZWCI",
        number_in_party=1,
        passengers=[("LEWIS", "WILLIAM")],
        divide_xml="""
        <stl19:DivideSplitDetails>
          <stl19:SplitToRecord>
            <stl19:DivideTimestamp>2026-03-18T02:32:15</stl19:DivideTimestamp>
            <stl19:RecordLocator>DPHUDD</stl19:RecordLocator>
            <stl19:OriginalNumberOfPax>02</stl19:OriginalNumberOfPax>
            <stl19:CurrentNumberOfPax>01</stl19:CurrentNumberOfPax>
            <stl19:CurrentPassengerNames>LEWIS/WILLIAM</stl19:CurrentPassengerNames>
          </stl19:SplitToRecord>
        </stl19:DivideSplitDetails>
        """,
    ))
    changes = diff_reservations(
        {"snapshotId": "before", "data": before},
        {"snapshotId": "after", "data": after},
        FLIGHT,
    )
    assert any("DPHUDD" in json.dumps(change) for change in changes)
    party_changes = [
        change for change in changes
        if change.get("changeType") == "RESERVATION_PARTY_CHANGE"
    ]
    assert party_changes
    assert party_changes[0]["oldValue"] == "2"
    assert party_changes[0]["newValue"] == "1"


def _soap_reservation(locator, number_in_party, passengers, divide_xml):
    passenger_xml = "".join(
        f"""
        <stl19:Passenger>
          <stl19:LastName>{last}</stl19:LastName>
          <stl19:FirstName>{first}</stl19:FirstName>
        </stl19:Passenger>
        """
        for last, first in passengers
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="{number_in_party}" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                  <stl19:CreationTimestamp>2026-02-26T09:05:00</stl19:CreationTimestamp>
                  <stl19:UpdateTimestamp>2026-03-19T04:01:17</stl19:UpdateTimestamp>
                  {divide_xml}
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    {passenger_xml}
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
