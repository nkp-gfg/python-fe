"""Destination reroutes must be stored and shown on the activity views.

A traveler's arrival city is taken from the Sabre passenger-list ``Destination``
element and saved on the manifest snapshot. When a later manifest for the same
traveler carries a different arrival city, change detection must record that
reroute and the passenger timeline, flight activity timeline, and activity feed
must show the previous and new city.

These tests fail while that comparison is missing.
"""

from contextlib import contextmanager
import copy

from backend.api import database
from backend.api.routes.changes import (
    get_activity_feed,
    get_flight_timeline,
    get_passenger_timeline,
)
from backend.feeder import storage
from backend.feeder.runner import _process_api_call
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient


FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


def _passenger_xml(passenger):
    """One PassengerInfo row shaped like a GetPassengerListRS manifest."""
    pnr = passenger.get("pnr")
    if pnr:
        pnr_xml = f'<PNRLocator nameAssociationID="1">{pnr}</PNRLocator>'
    else:
        pnr_xml = "<PNRLocator></PNRLocator>"
    destination = passenger.get("destination")
    destination_xml = (
        f"<Destination>{destination}</Destination>" if destination is not None else ""
    )
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{passenger["lastName"]}</LastName>
            <FirstName>{passenger["firstName"]}</FirstName>
          </Name_Details>
          {pnr_xml}
          <PassengerID>{passenger["passengerId"]}</PassengerID>
          <LineNumber>{passenger["lineNumber"]}</LineNumber>
          <BookingClass>V</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{passenger["seat"]}</Seat>
          {destination_xml}
          <PassengerType>F</PassengerType>
          <BagCount>1</BagCount>
          <CheckIn_Info>
            <CheckInStatus>false</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
    """


def _manifest_xml(passengers):
    """SOAP envelope the passenger-list client parses. No credentials."""
    rows = "".join(_passenger_xml(passenger) for passenger in passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {rows}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text):
    """Same parse and body extraction the Sabre client uses after a response."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _ingest(raw_data):
    """Run the passenger-list storage and change-detection pipeline."""
    meta = {
        "requestXml": "<GetPassengerListRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": None,
    }
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        FLIGHT,
        raw_data,
        "<GetPassengerListRS/>",
        meta,
        convert_passenger_list,
        (raw_data, FLIGHT["airline"], FLIGHT["flightNumber"],
         FLIGHT["departureDate"], FLIGHT["origin"]),
    )


class _InsertOne:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertMany:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            specs = list(key)
        else:
            specs = [(key, direction if direction is not None else 1)]
        for field, order in reversed(specs):
            self._docs.sort(
                key=lambda doc, field=field: (
                    _path(doc, field) is None,
                    _path(doc, field),
                ),
                reverse=order < 0,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _path(doc, field):
    current = doc
    for part in str(field).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(doc, field, value):
    parts = str(field).split(".")
    current = doc
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _matches(doc, query):
    for key, expected in query.items():
        actual = _path(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$in" not in expected and "$lte" not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    excluding = any(
        value == 0 and key != "_id" for key, value in projection.items()
    )
    if excluding:
        projected = copy.deepcopy(doc)
        for key, value in projection.items():
            if value == 0 and key in projected:
                projected.pop(key)
        return projected
    projected = {}
    if projection.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, value in projection.items():
        if key == "_id" or value == 0:
            continue
        found = _path(doc, key)
        if found is not None or key in doc:
            projected[key] = copy.deepcopy(found)
    return projected


class _Collection:
    def __init__(self):
        self._docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = self._next_id
        self._next_id += 1
        self._docs.append(stored)
        return _InsertOne(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertMany(ids)

    def find(self, query=None, projection=None, sort=None):
        query = query or {}
        docs = [
            _project(doc, projection)
            for doc in self._docs
            if _matches(doc, query)
        ]
        cursor = _Cursor(docs)
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, projection=None, sort=None):
        docs = list(self.find(query, projection, sort).limit(1))
        return docs[0] if docs else None

    def update_one(self, query, update, upsert=False):
        live = next((doc for doc in self._docs if _matches(doc, query)), None)
        created = False
        if live is None:
            if not upsert:
                return
            live = {}
            for key, value in query.items():
                if not str(key).startswith("$") and not isinstance(value, dict):
                    _set_path(live, key, copy.deepcopy(value))
            self._docs.append(live)
            live["_id"] = self._next_id
            self._next_id += 1
            created = True
        for key, value in update.get("$set", {}).items():
            _set_path(live, key, copy.deepcopy(value))
        for key, value in update.get("$inc", {}).items():
            _set_path(live, key, (_path(live, key) or 0) + value)
        if created:
            for key, value in update.get("$setOnInsert", {}).items():
                _set_path(live, key, copy.deepcopy(value))


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


@contextmanager
def _bound_database():
    """Point both storage and the read API at one in-memory database."""
    previous_storage = storage._db
    previous_api = database._db
    mem = _Database()
    storage.init_db(mem)
    database._db = mem
    try:
        yield mem
    finally:
        storage._db = previous_storage
        database._db = previous_api


def _stored_destination(raw_data, pnr=None, last_name=None):
    converted = convert_passenger_list(
        raw_data, FLIGHT["airline"], FLIGHT["flightNumber"],
        FLIGHT["departureDate"], FLIGHT["origin"],
    )
    for passenger in converted["passengers"]:
        if pnr is not None and passenger.get("pnr") == pnr:
            return passenger.get("destination")
        if last_name is not None and passenger.get("lastName") == last_name:
            return passenger.get("destination")
    raise AssertionError("passenger missing from converted manifest")


def _assert_reroute_visible(previous, new, pnr):
    timeline = get_passenger_timeline(
        FLIGHT["flightNumber"], pnr, date=FLIGHT["departureDate"], origin=FLIGHT["origin"],
    )
    passenger_hits = [
        event for event in timeline["events"]
        if previous in (event.get("description") or "")
        and new in (event.get("description") or "")
        and (event.get("details") or {}).get("field") == "destination"
        and (event.get("details") or {}).get("oldValue") == previous
        and (event.get("details") or {}).get("newValue") == new
    ]
    assert passenger_hits, timeline["events"]

    feed = get_activity_feed(
        date=FLIGHT["departureDate"], limit=50, categories=None,
    )
    feed_hits = [
        event for event in feed["events"]
        if previous in (event.get("description") or "")
        and new in (event.get("description") or "")
        and (event.get("passenger") or {}).get("pnr") == pnr
    ]
    assert feed_hits, feed["events"]

    flight_timeline = get_flight_timeline(
        FLIGHT["flightNumber"], origin=FLIGHT["origin"], date=FLIGHT["departureDate"],
    )
    flight_hits = [
        event for event in flight_timeline["events"]
        if previous in (event.get("description") or "")
        and new in (event.get("description") or "")
        and (event.get("details") or {}).get("field") == "destination"
        and (event.get("passenger") or {}).get("pnr") == pnr
    ]
    assert flight_hits, flight_timeline["events"]


def test_changed_manifest_destination_is_a_visible_reroute():
    """Two ingested manifests that differ only by arrival city emit a reroute."""
    before_rows = [
        {
            "pnr": "ESMMQK",
            "lastName": "BABOO",
            "firstName": "MOHIT",
            "passengerId": "P1",
            "lineNumber": 1,
            "seat": "22A",
            "destination": "DMM",
        },
        {
            "pnr": "STAY01",
            "lastName": "NAIR",
            "firstName": "ANITA",
            "passengerId": "P2",
            "lineNumber": 2,
            "seat": "22B",
            "destination": "DMM",
        },
        {
            "pnr": None,
            "lastName": "CREW",
            "firstName": "SAM",
            "passengerId": "P3",
            "lineNumber": 3,
            "seat": "2A",
            "destination": "BAH",
        },
    ]
    after_rows = [
        {**before_rows[0], "destination": "BAH"},
        before_rows[1],
        {**before_rows[2], "destination": "DOH"},
    ]

    before_raw = _parse_passenger_list(_manifest_xml(before_rows))
    after_raw = _parse_passenger_list(_manifest_xml(after_rows))
    assert _stored_destination(before_raw, pnr="ESMMQK") == "DMM"
    assert _stored_destination(after_raw, pnr="ESMMQK") == "BAH"
    assert _stored_destination(before_raw, last_name="CREW") == "BAH"
    assert _stored_destination(after_raw, last_name="CREW") == "DOH"

    with _bound_database() as db:
        first = _ingest(before_raw)
        second = _ingest(after_raw)
        assert first["isDuplicate"] is False
        assert second["isDuplicate"] is False

        stored = list(db["changes"].find({}))
        booked = [
            change for change in stored
            if change.get("field") == "destination"
            and (change.get("passenger") or {}).get("pnr") == "ESMMQK"
            and change.get("oldValue") == "DMM"
            and change.get("newValue") == "BAH"
        ]
        assert booked, stored
        unchanged = [
            change for change in stored
            if change.get("field") == "destination"
            and (change.get("passenger") or {}).get("pnr") == "STAY01"
        ]
        assert unchanged == []
        staff = [
            change for change in stored
            if change.get("field") == "destination"
            and (change.get("passenger") or {}).get("lastName") == "CREW"
            and change.get("oldValue") == "BAH"
            and change.get("newValue") == "DOH"
        ]
        assert staff, stored

        _assert_reroute_visible("DMM", "BAH", "ESMMQK")

        crew_timeline = get_passenger_timeline(
            FLIGHT["flightNumber"],
            "",
            date=FLIGHT["departureDate"],
            origin=FLIGHT["origin"],
        )
        # Staff with no record locator are stored with an empty pnr. The
        # timeline query is by pnr, so an empty locator matches every such
        # change. Require the crew reroute itself to be present.
        crew_hits = [
            event for event in crew_timeline["events"]
            if "BAH" in (event.get("description") or "")
            and "DOH" in (event.get("description") or "")
            and (event.get("details") or {}).get("field") == "destination"
            and (event.get("details") or {}).get("oldValue") == "BAH"
            and (event.get("details") or {}).get("newValue") == "DOH"
        ]
        assert crew_hits, crew_timeline["events"]


def test_same_destination_does_not_emit_a_reroute():
    """A seat change with the same arrival city is only a seat change."""
    before_rows = [
        {
            "pnr": "ESMMQK",
            "lastName": "BABOO",
            "firstName": "MOHIT",
            "passengerId": "P1",
            "lineNumber": 1,
            "seat": "22A",
            "destination": "DMM",
        },
    ]
    after_rows = [{**before_rows[0], "seat": "14C"}]
    before_raw = _parse_passenger_list(_manifest_xml(before_rows))
    after_raw = _parse_passenger_list(_manifest_xml(after_rows))

    with _bound_database() as db:
        _ingest(before_raw)
        _ingest(after_raw)
        stored = list(db["changes"].find({}))
        assert any(change.get("field") == "seat" for change in stored), stored
        assert not any(change.get("field") == "destination" for change in stored)

        timeline = get_passenger_timeline(
            FLIGHT["flightNumber"], "ESMMQK",
            date=FLIGHT["departureDate"], origin=FLIGHT["origin"],
        )
        assert not any(
            (event.get("details") or {}).get("field") == "destination"
            for event in timeline["events"]
        )
        assert any(
            (event.get("details") or {}).get("field") == "seat"
            for event in timeline["events"]
        )


def _main():
    test_changed_manifest_destination_is_a_visible_reroute()
    test_same_destination_does_not_emit_a_reroute()


if __name__ == "__main__":
    _main()
