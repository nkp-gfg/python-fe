"""Bag-route Sabre passenger ids must survive ingestion and the passenger APIs.

These tests fail while conversion omits BaggageRoute/PassengerID. They are the
spec for the fix: every stored and returned bag route keeps the passenger id
from that route, including when the onward leg uses a different id than the
traveler record on the first flight.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from backend.api.routes import passengers as passengers_route
from backend.feeder import storage
from backend.feeder.converter import _ensure_list, _strip_ns, convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSENGER_LIST_XML = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
PASSENGER_DATA_XML = (
    REPO_ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-1_SamplePayload_4-2-4-1.xml"
)

# Connection on the committed GF2057 BOM manifest: the onward leg's Sabre id
# is not the id stored on the passenger record.
GF2057_PNR = "NDXQDR"
GF2057_PASSENGER_ID = "F32C40BE0001"
GF2057_ROUTE_IDS = ("F32C40BE0001", "F32C40BE0101")
GF2057_FLIGHTS = ("2057", "5023")

# GetPassengerData sample whose four bag routes each carry a different id.
PASSENGER_DATA_PNR = "DXXRYZ"
PASSENGER_DATA_ROUTE_IDS = (
    "43E2BE8D0001",
    "43E2BE8D0101",
    "43E2BE8D0201",
    "43E2BE8D0301",
)
PASSENGER_DATA_FLIGHTS = ("129", "632", "530", "942")


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


class _MemoryCollection:
    """In-memory stand-in for the pymongo calls the feeder and passenger routes use.

    insert stores a deep copy and does not add or remove bag-route fields.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = deepcopy(doc)
        stored.setdefault("_id", f"mem-{len(self.docs)}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def find_one(self, query=None, sort=None, projection=None):
        matches = [doc for doc in self.docs if _match(doc, query or {})]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        # Projection is only used to read sequence numbers and checksums.
        # Returning the stored document leaves every bag-route field intact.
        del projection
        return matches[0]

    def find(self, query=None, sort=None):
        matches = [doc for doc in self.docs if _match(doc, query or {})]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
        return _Cursor(matches)

    def update_one(self, query, update, upsert=False):
        doc = self.find_one(query)
        if doc is None and upsert:
            doc = {}
            doc.update(update.get("$setOnInsert") or {})
            self.docs.append(doc)
        if doc is None:
            return
        doc.update(update.get("$set") or {})
        for field, amount in (update.get("$inc") or {}).items():
            doc[field] = doc.get(field, 0) + amount


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


def _match(doc, query):
    for key, expected in query.items():
        if "." in key:
            head, rest = key.split(".", 1)
            values = doc.get(head)
            if not isinstance(values, list):
                return False
            if not any(
                _match(item, {rest: expected})
                for item in values
                if isinstance(item, dict)
            ):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _parse_response(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _response_envelope(path):
    text = path.read_text(encoding="utf-8")
    marker = "<!--Response:-->"
    start = text.find(marker)
    assert start != -1, f"{path.name} has no response envelope"
    chunk = text[start + len(marker):]
    end = chunk.rfind("</soap-env:Envelope>")
    assert end != -1, f"{path.name} response envelope is truncated"
    return chunk[: end + len("</soap-env:Envelope>")].strip()


def _source_routes(passenger):
    routes = _ensure_list(
        (passenger.get("BaggageRouteList") or {}).get("BaggageRoute", [])
    )
    return [route for route in routes if isinstance(route, dict)]


def _assert_routes_keep_source_ids(stored_passengers, source_passengers):
    assert source_passengers, "parsed passenger list was empty"
    compared = 0
    distinct_onward = 0
    for source in source_passengers:
        source_id = source.get("PassengerID", "")
        stored = next(
            passenger
            for passenger in stored_passengers
            if passenger.get("passengerId") == source_id
        )
        source_routes = _source_routes(source)
        stored_routes = stored.get("baggageRoutes") or []
        assert len(stored_routes) == len(source_routes)
        for source_route, stored_route in zip(source_routes, stored_routes):
            source_route_id = source_route.get("PassengerID", "")
            assert source_route_id, "schema-required bag-route PassengerID missing from parsed XML"
            assert stored_route.get("flight") == source_route.get("Flight", "")
            assert stored_route.get("passengerId") == source_route_id
            compared += 1
            if source_route_id != source_id:
                distinct_onward += 1
    assert compared > 0
    assert distinct_onward > 0


def _passenger(passengers, pnr, passenger_id):
    """Pick one traveler. A group PNR is shared, so the Sabre passenger id is required."""
    match = [
        passenger
        for passenger in passengers
        if passenger.get("pnr") == pnr and passenger.get("passengerId") == passenger_id
    ]
    assert len(match) == 1
    return match[0]


@pytest.fixture
def memory_db(monkeypatch):
    db = _MemoryDB()
    storage.init_db(db)
    monkeypatch.setattr(passengers_route, "get_db", lambda: db)
    return db


def test_ingested_passenger_list_returns_each_bag_route_passenger_id(memory_db):
    """Ingest the GF2057 SOAP manifest and read it back from every passenger-list API."""
    xml_text = PASSENGER_LIST_XML.read_text(encoding="utf-8")
    body = _parse_response(xml_text, "GetPassengerListRS")
    source_passengers = _ensure_list(
        _strip_ns(body).get("PassengerInfoList", {}).get("PassengerInfo", [])
    )

    flight_info = {
        "airline": "GF",
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
    }
    _process_api_call(
        "PassengerList",
        "passenger_list",
        flight_info,
        body,
        xml_text,
        {
            "requestXml": "<GetPassengerListRQ/>",
            "httpStatus": 200,
            "durationMs": 1,
            "sessionToken": None,
            "conversationId": "bag-route-test",
        },
        convert_passenger_list,
        (body, "GF", "2057", "2026-03-19", "BOM"),
    )

    current = passengers_route.get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    historical = passengers_route.get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=1
    )
    by_pnr = passengers_route.get_passenger_by_pnr(
        "2057", GF2057_PNR, date="2026-03-19"
    )

    for document in (current, historical):
        _assert_routes_keep_source_ids(document["passengers"], source_passengers)
        connecting = _passenger(
            document["passengers"], GF2057_PNR, GF2057_PASSENGER_ID
        )
        assert tuple(route.get("flight") for route in connecting["baggageRoutes"]) == GF2057_FLIGHTS
        assert tuple(
            route.get("passengerId") for route in connecting["baggageRoutes"]
        ) == GF2057_ROUTE_IDS
        assert connecting["baggageRoutes"][1]["passengerId"] != connecting["passengerId"]

    connecting = _passenger(
        by_pnr["passengers"], GF2057_PNR, GF2057_PASSENGER_ID
    )
    assert [route.get("passengerId") for route in connecting["baggageRoutes"]] == list(
        GF2057_ROUTE_IDS
    )
    stored = memory_db["passenger_list"].docs
    assert stored, "passenger list was not stored"
    _assert_routes_keep_source_ids(stored[-1]["passengers"], source_passengers)


def test_passenger_detail_returns_each_bag_route_passenger_id(memory_db, monkeypatch):
    """GetPassengerData returns each route's own passenger id, including later legs."""
    response_xml = _response_envelope(PASSENGER_DATA_XML)
    memory_db["passenger_list"].insert_one(
        {
            "flightNumber": "129",
            "origin": "ANF",
            "departureDate": "2016-07-04",
            "fetchedAt": "2016-07-04T00:00:00Z",
            "passengers": [
                {
                    "pnr": PASSENGER_DATA_PNR,
                    "lastName": "HDGLAX",
                    "firstName": "VRDJ",
                }
            ],
        }
    )

    def fake_post(self, action, body, timeout=30, endpoint=None):
        del timeout, endpoint
        if action == "SessionCreateRQ":
            xml = (
                '<?xml version="1.0"?>'
                '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
                "<soap-env:Header>"
                '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
                "<wsse:BinarySecurityToken>PLACEHOLDER-TOKEN</wsse:BinarySecurityToken>"
                "</wsse:Security>"
                "</soap-env:Header>"
                "<soap-env:Body/>"
                "</soap-env:Envelope>"
            )
            return xml, 200, 1, body
        if action == "SessionCloseRQ":
            return "<ok/>", 200, 1, body
        if action == "GetPassengerDataRQ":
            return response_xml, 200, 1, body
        raise AssertionError(f"unexpected Sabre action {action}")

    monkeypatch.setattr(SabreClient, "_post", fake_post)
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0)
    for name, value in {
        "SABRE_BASE_URL": "http://127.0.0.1:9",
        "SABRE_CPAID": "XX",
        "SABRE_USERNAME": "placeholder-user",
        "SABRE_PASSWORD": "placeholder-password",
        "SABRE_PSEUDO_CITY_CODE": "XXXX",
        "SABRE_ORGANIZATION": "XX",
        "SABRE_DOMAIN": "XX",
    }.items():
        monkeypatch.setenv(name, value)

    detail = passengers_route.get_passenger_detail(
        "129",
        PASSENGER_DATA_PNR,
        origin="ANF",
        date="2016-07-04",
        airline="LA",
    )
    passenger = detail["passengers"][0]
    routes = passenger["baggageRoutes"]
    assert tuple(route.get("flight") for route in routes) == PASSENGER_DATA_FLIGHTS
    assert tuple(route.get("passengerId") for route in routes) == PASSENGER_DATA_ROUTE_IDS
    assert routes[1]["passengerId"] != passenger["passengerId"]
    assert routes[2]["passengerId"] != passenger["passengerId"]
    assert routes[3]["passengerId"] != passenger["passengerId"]
