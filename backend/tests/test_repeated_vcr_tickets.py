"""Repeated Sabre VCR_Data records must keep the in-use ticket on the manifest.

These tests fail while passenger-list conversion stores a blank serial, coupon,
and airline number whenever GetPassengerListRS repeats VCR_Data for one traveler.
They pass once that in-use record is kept on the stored row and on the passenger
list, record-locator, snapshot, and dashboard ticket reads.

The SOAP bodies are parsed by SabreClient and stored by the ingestion pipeline.
MongoDB is an in-memory stand-in of the collection methods the pipeline and
routes call. It does not rewrite passenger ticket fields.
"""

import copy
import os
import threading
import uuid

os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "ORG"
os.environ["SABRE_DOMAIN"] = "DOMAIN"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ.pop("MONGODB_URI", None)
os.environ.pop("POSTGRES_URI", None)

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import flights as flights_route
from backend.api.routes import passengers as passengers_route
from backend.api import database
from backend.feeder import runner
from backend.feeder import storage
from backend.sabre.client import SabreClient, SabreError


IN_USE_SECOND = {
    "pnr": "ABC123",
    "last": "ALI",
    "first": "ADULT",
    "pid": "PAXSECOND",
    "serial": "5438287621",
    "coupon": "1",
    "airline": "072",
    "vcr_type": "adult",
    "unused_serial": "9999999999",
}
IN_USE_FIRST = {
    "pnr": "DEF456",
    "last": "SARA",
    "first": "ADULT",
    "pid": "PAXFIRST",
    "serial": "7777777777",
    "coupon": "3",
    "airline": "065",
    "vcr_type": "adult",
    "unused_serial": "2222222222",
}
SINGLE = {
    "pnr": "GHI789",
    "last": "OMAR",
    "first": "SOLO",
    "pid": "PAXSOLO",
    "serial": "2136399399",
    "coupon": "1",
    "airline": "072",
    "vcr_type": "adult",
}
NO_TICKET = {
    "pnr": "JKL012",
    "last": "NOOR",
    "first": "NONE",
    "pid": "PAXNONE",
}

_MISSING = object()


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return _Cursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


def _match_dotted(value, parts, expected):
    if not parts:
        return value == expected
    if isinstance(value, list):
        return any(_match_dotted(item, parts, expected) for item in value)
    if not isinstance(value, dict) or parts[0] not in value:
        return False
    return _match_dotted(value[parts[0]], parts[1:], expected)


def _match(doc, query):
    for key, expected in (query or {}).items():
        if "." in key:
            if not _match_dotted(doc, key.split("."), expected):
                return False
            continue
        actual = doc.get(key, _MISSING)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is _MISSING or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _sort_docs(docs, sort):
    ordered = list(docs)
    if not sort:
        return ordered
    for field, direction in reversed(list(sort)):
        ordered.sort(
            key=lambda doc: (doc.get(field) is None, doc.get(field)),
            reverse=direction == -1,
        )
    return ordered


class MemoryCollection:
    """Collection methods used by storage.py and the passenger/dashboard reads."""

    def __init__(self):
        self.docs = []
        self._lock = threading.Lock()

    def insert_one(self, doc):
        with self._lock:
            stored = copy.deepcopy(doc)
            stored["_id"] = stored.get("_id") or uuid.uuid4().hex
            self.docs.append(stored)
            doc["_id"] = stored["_id"]
            return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        sort = kwargs.get("sort", sort)
        with self._lock:
            matches = _sort_docs(
                [doc for doc in self.docs if _match(doc, query or {})],
                sort,
            )
            if not matches:
                return None
            return copy.deepcopy(matches[0])

    def find(self, query=None, projection=None, sort=None, **kwargs):
        sort = kwargs.get("sort", sort)
        with self._lock:
            matches = _sort_docs(
                [copy.deepcopy(doc) for doc in self.docs if _match(doc, query or {})],
                sort,
            )
        return _Cursor(matches)

    def update_one(self, query, update, upsert=False):
        with self._lock:
            for doc in self.docs:
                if _match(doc, query):
                    _apply_update(doc, update, on_insert=False)
                    return
            if upsert:
                created = {}
                _apply_update(created, update, on_insert=True)
                created["_id"] = uuid.uuid4().hex
                self.docs.append(created)

    def index_information(self):
        return {}

    def create_index(self, keys, **kwargs):
        return kwargs.get("name", "index")

    def drop_index(self, name):
        return None


def _apply_update(doc, update, on_insert):
    if on_insert:
        for key, value in update.get("$setOnInsert", {}).items():
            doc[key] = copy.deepcopy(value)
    for key, value in update.get("$set", {}).items():
        doc[key] = copy.deepcopy(value)
    for key, value in update.get("$inc", {}).items():
        doc[key] = doc.get(key, 0) + value


class MemoryDB:
    def __init__(self):
        self._collections = {}
        self._lock = threading.Lock()

    def __getitem__(self, name):
        with self._lock:
            if name not in self._collections:
                self._collections[name] = MemoryCollection()
            return self._collections[name]

    def command(self, command_name, *args, **kwargs):
        return {"ok": 1}


def _tag(prefix, name):
    return f"{prefix}{name}"


def _vcr(prefix, in_use, vcr_type, airline, coupon, serial):
    data = _tag(prefix, "VCR_Data")
    airline_tag = _tag(prefix, "AirlineNumber")
    coupon_tag = _tag(prefix, "CouponNumber")
    serial_tag = _tag(prefix, "SerialNumber")
    return (
        f'<{data} inUse="{in_use}" type="{vcr_type}">'
        f"<{airline_tag}>{airline}</{airline_tag}>"
        f"<{coupon_tag}>{coupon}</{coupon_tag}>"
        f"<{serial_tag}>{serial}</{serial_tag}>"
        f"</{data}>"
    )


def _passenger(prefix, last, first, pnr, pid, vcr_blocks):
    def t(name):
        return _tag(prefix, name)

    vcr_xml = ""
    if vcr_blocks is not None:
        info = t("VCR_Info")
        vcr_xml = f"<{info}>{''.join(vcr_blocks)}</{info}>"
    return (
        f"<{t('PassengerInfo')}>"
        f"<{t('Name_Details')}>"
        f"<{t('LastName')}>{last}</{t('LastName')}>"
        f"<{t('FirstName')}>{first}</{t('FirstName')}>"
        f"</{t('Name_Details')}>"
        f'<{t("PNRLocator")} nameAssociationID="1">{pnr}</{t("PNRLocator")}>'
        f"<{t('PassengerID')}>{pid}</{t('PassengerID')}>"
        f"<{t('LineNumber')}>1</{t('LineNumber')}>"
        f"<{t('BookingClass')}>Y</{t('BookingClass')}>"
        f"<{t('Cabin')}>Y</{t('Cabin')}>"
        f"<{t('Destination')}>KHI</{t('Destination')}>"
        f"<{t('PassengerType')}>F</{t('PassengerType')}>"
        f"<{t('BagCount')}>0</{t('BagCount')}>"
        f"<{t('CheckIn_Info')}><{t('CheckInStatus')}>false</{t('CheckInStatus')}></{t('CheckIn_Info')}>"
        f"<{t('Boarding_Info')}><{t('BoardStatus')}>false</{t('BoardStatus')}></{t('Boarding_Info')}>"
        f"{vcr_xml}"
        f"</{t('PassengerInfo')}>"
    )


def _list_xml(prefix, passenger_xml, flight_number):
    def t(name):
        return _tag(prefix, name)

    body = (
        f"<{t('GetPassengerListRS')}>"
        f"<{t('ItineraryInfo')}><{t('Itinerary')}>"
        f"<{t('Airline')}>GF</{t('Airline')}>"
        f"<{t('Flight')}>{flight_number}</{t('Flight')}>"
        f"<{t('Origin')}>DMM</{t('Origin')}>"
        f"<{t('Destination')}>KHI</{t('Destination')}>"
        f"</{t('Itinerary')}></{t('ItineraryInfo')}>"
        f"<{t('PassengerInfoList')}>{passenger_xml}</{t('PassengerInfoList')}>"
        f"</{t('GetPassengerListRS')}>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _manifest_xml(prefix, flight_number):
    """Booked call has one ticket. The fourth call adds repeated ticket blocks."""
    single = _passenger(
        prefix, SINGLE["last"], SINGLE["first"], SINGLE["pnr"], SINGLE["pid"],
        [_vcr(prefix, "true", SINGLE["vcr_type"], SINGLE["airline"], SINGLE["coupon"], SINGLE["serial"])],
    )
    repeated = "".join([
        _passenger(
            prefix, IN_USE_SECOND["last"], IN_USE_SECOND["first"],
            IN_USE_SECOND["pnr"], IN_USE_SECOND["pid"],
            [
                _vcr(prefix, "false", "infant", "999", "4", IN_USE_SECOND["unused_serial"]),
                _vcr(
                    prefix, "true", IN_USE_SECOND["vcr_type"],
                    IN_USE_SECOND["airline"], IN_USE_SECOND["coupon"],
                    IN_USE_SECOND["serial"],
                ),
            ],
        ),
        _passenger(
            prefix, IN_USE_FIRST["last"], IN_USE_FIRST["first"],
            IN_USE_FIRST["pnr"], IN_USE_FIRST["pid"],
            [
                _vcr(
                    prefix, "true", IN_USE_FIRST["vcr_type"],
                    IN_USE_FIRST["airline"], IN_USE_FIRST["coupon"],
                    IN_USE_FIRST["serial"],
                ),
                _vcr(prefix, "false", "infant", "111", "2", IN_USE_FIRST["unused_serial"]),
            ],
        ),
        _passenger(
            prefix, NO_TICKET["last"], NO_TICKET["first"],
            NO_TICKET["pnr"], NO_TICKET["pid"], None,
        ),
    ])
    return _list_xml(prefix, single, flight_number), _list_xml(prefix, repeated, flight_number)


def _assert_ticket(passenger, expected):
    assert passenger["ticketNumber"] == expected["serial"]
    assert passenger["vcrCouponNumber"] == expected["coupon"]
    assert passenger["vcrAirlineNumber"] == expected["airline"]
    assert passenger["vcrInUse"] is True
    assert passenger["vcrType"] == expected["vcr_type"]
    unused = expected.get("unused_serial")
    if unused:
        assert passenger["ticketNumber"] != unused


def _by_pnr(passengers, pnr):
    matches = [pax for pax in passengers if pax.get("pnr") == pnr]
    assert len(matches) == 1
    return matches[0]


def test_single_vcr_record_remains_on_the_manifest():
    """One VCR_Data record is stored. Repeated records must not change this."""
    booked, _later = _manifest_xml("", "3001")
    db = MemoryDB()
    with _patched(db, {"3001": (booked, booked)}):
        with TestClient(app) as client:
            status = _ingest(client, "3001")
    assert status not in (401, 403)
    stored = _stored_passengers(db, "3001")
    _assert_ticket(_by_pnr(stored, SINGLE["pnr"]), SINGLE)


def test_repeated_vcr_records_keep_in_use_ticket_through_ingest_and_reads():
    """Unprefixed GetPassengerListRS, the shape captured from Sabre."""
    _exercise_manifest("", "2754")


def test_namespaced_repeated_vcr_records_keep_in_use_ticket():
    """v4-prefixed GetPassengerListRS, the shape in the vendor design XML."""
    _exercise_manifest("v4:", "2755")


def _exercise_manifest(prefix, flight_number):
    booked_xml, repeated_xml = _manifest_xml(prefix, flight_number)
    db = MemoryDB()
    # Calls 1-3 are the booked list. Call 4 is the display that repeats VCR_Data.
    responses = {
        flight_number: (booked_xml, repeated_xml),
    }
    with _patched(db, responses):
        with TestClient(app) as client:
            ingest_status = _ingest(client, flight_number)
            listed = client.get(
                f"/flights/{flight_number}/passengers",
                params={"origin": "DMM", "date": "2026-03-18"},
            )
            second = client.get(
                f"/flights/{flight_number}/passengers/{IN_USE_SECOND['pnr']}",
                params={"date": "2026-03-18"},
            )
            first = client.get(
                f"/flights/{flight_number}/passengers/{IN_USE_FIRST['pnr']}",
                params={"date": "2026-03-18"},
            )
            historical = client.get(
                f"/flights/{flight_number}/passengers",
                params={"origin": "DMM", "date": "2026-03-18", "snapshot_sequence": 1},
            )
            dashboard = client.get(
                f"/flights/{flight_number}/dashboard",
                params={"origin": "DMM", "date": "2026-03-18"},
            )

    assert ingest_status not in (401, 403)
    assert listed.status_code == 200
    assert second.status_code == 200
    assert first.status_code == 200
    assert historical.status_code == 200
    assert dashboard.status_code == 200

    _assert_ticket(second.json()["passengers"][0], IN_USE_SECOND)
    _assert_ticket(first.json()["passengers"][0], IN_USE_FIRST)

    stored = _stored_passengers(db, flight_number)
    for source in (
        listed.json()["passengers"],
        historical.json()["passengers"],
        stored,
    ):
        _assert_ticket(_by_pnr(source, IN_USE_SECOND["pnr"]), IN_USE_SECOND)
        _assert_ticket(_by_pnr(source, IN_USE_FIRST["pnr"]), IN_USE_FIRST)
        _assert_ticket(_by_pnr(source, SINGLE["pnr"]), SINGLE)
        assert _by_pnr(source, NO_TICKET["pnr"])["ticketNumber"] == ""

    ticket_status = dashboard.json()["insights"]["ticketStatus"]
    assert ticket_status["withTicket"] == 3
    assert ticket_status["withoutTicket"] == 1


def _stored_passengers(db, flight_number):
    matches = [
        doc for doc in db["passenger_list"].docs
        if doc.get("flightNumber") == flight_number
    ]
    assert len(matches) == 1
    return matches[0]["passengers"]


def _ingest(client, flight_number):
    response = client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": "DMM",
            "departureDate": "2026-03-18",
            "departureDateTime": "2026-03-18T17:00:00",
        },
    )
    return response.status_code


class _patched:
    def __init__(self, db, responses_by_flight):
        self._db = db
        self._responses = responses_by_flight
        self._saved = []

    def __enter__(self):
        flights_route._dashboard_cache.clear()
        self._saved.append((database, "get_db", database.get_db))
        database.get_db = lambda: self._db
        # lifespan looks up get_db on the main module global.
        import backend.api.main as main
        self._saved.append((main, "get_db", main.get_db))
        main.get_db = lambda: self._db
        storage.init_db(self._db)
        self._saved.append((passengers_route, "get_db", passengers_route.get_db))
        self._saved.append((flights_route, "get_db", flights_route.get_db))
        passengers_route.get_db = lambda: self._db
        flights_route.get_db = lambda: self._db
        self._saved.append((runner, "_lookup_flight_sequence_number", runner._lookup_flight_sequence_number))
        runner._lookup_flight_sequence_number = lambda *args, **kwargs: None
        self._saved.append((SabreClient, "_post", SabreClient._post))
        self._saved.append((SabreClient, "API_CALL_DELAY", SabreClient.API_CALL_DELAY))
        SabreClient.API_CALL_DELAY = 0
        calls = {"n": 0}

        def fake_post(client, action, body, timeout=30, endpoint=None):
            if action == "SessionCreateRQ":
                xml = "<wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>"
                return xml, 200, 1, body
            if action == "SessionCloseRQ":
                return "<closed/>", 200, 1, body
            if action == "GetPassengerListRQ":
                calls["n"] += 1
                flight_number = _flight_number_from_request(body)
                booked_xml, repeated_xml = self._responses[flight_number]
                # The runner issues four passenger-list calls. The repeating
                # ticket block arrives on the last call and is merged in.
                xml = repeated_xml if calls["n"] % 4 == 0 else booked_xml
                return xml, 200, 1, body
            raise SabreError(f"{action} returned HTTP 500: downstream api unavailable")

        SabreClient._post = fake_post
        return self

    def __exit__(self, exc_type, exc, tb):
        SabreClient._post = self._pop(SabreClient, "_post")
        SabreClient.API_CALL_DELAY = self._pop(SabreClient, "API_CALL_DELAY")
        runner._lookup_flight_sequence_number = self._pop(runner, "_lookup_flight_sequence_number")
        flights_route.get_db = self._pop(flights_route, "get_db")
        passengers_route.get_db = self._pop(passengers_route, "get_db")
        import backend.api.main as main
        main.get_db = self._pop(main, "get_db")
        database.get_db = self._pop(database, "get_db")
        storage._db = None
        flights_route._dashboard_cache.clear()
        return False

    def _pop(self, owner, name):
        for index in range(len(self._saved) - 1, -1, -1):
            saved_owner, saved_name, saved_value = self._saved[index]
            if saved_owner is owner and saved_name == name:
                self._saved.pop(index)
                return saved_value
        raise AssertionError(f"missing saved {name}")


def _flight_number_from_request(body):
    marker = "<v4:Flight>"
    end = "</v4:Flight>"
    start = body.find(marker)
    if start == -1:
        raise AssertionError("passenger list request has no flight number")
    start += len(marker)
    return body[start:body.find(end, start)]
