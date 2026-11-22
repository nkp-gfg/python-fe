"""Bag-route restrictions from Sabre must survive ingestion onto the manifest.

These tests fail while GetPassengerList / GetPassengerData baggage routes are
stored without BagEmbargo, PetRestriction, BaggageRestrictionLimit,
LateCheckin, ShortCheckinReason, and BaggageRouteRequired. They pass once
those values are copied onto each stored route and returned by the passenger
APIs.

Not covered here: the React baggage table (frontend/components/dashboard/
passenger-detail-sheet.tsx) and baggage analytics, which only count routes.
Batch ingest uses the same feeder path as POST /flights/ingest.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9/falconeye"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"
os.environ.pop("POSTGRES_URI", None)

import pytest
from fastapi.testclient import TestClient

from backend.api import database as api_database
from backend.api import main as api_main
from backend.api.routes import passengers as passengers_route
from backend.feeder import storage as feeder_storage
from backend.feeder.converter import _strip_ns
from backend.sabre.client import SabreClient

FLIGHT = "4210"
ORIGIN = "BAH"
DATE = "2026-11-22"
PNR = "RSTR01"
SHORT_REASON = "Unauthorized baggage route"

LIST_RESTRICTIONS = {
    "bagEmbargo": {"1"},
    "petRestriction": {"Y"},
    "baggageRestrictionLimit": {"2"},
    "lateCheckin": {"Y"},
    "shortCheckinReason": {SHORT_REASON},
    "baggageRouteRequired": {"true", "True"},
}
DETAIL_RESTRICTIONS = {
    **LIST_RESTRICTIONS,
    "bagEmbargo": {"2"},
}


def _passenger_list_xml():
    """SOAP body shaped like a live GetPassengerListRS, plus the design fields."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DOH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>RESTRICTED</LastName>
            <FirstName>BAG</FirstName>
          </Name_Details>
          <PNRLocator>{PNR}</PNRLocator>
          <PassengerID>PAXRESTRICT01</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>DOH</Destination>
          <BagCount>1</BagCount>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:SegmentID>1</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>{FLIGHT}</ns2:Flight>
              <ns2:Origin>{ORIGIN}</ns2:Origin>
              <ns2:Destination>DOH</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAXRESTRICT01</ns2:PassengerID>
              <ns2:BagEmbargo>1</ns2:BagEmbargo>
              <ns2:PetRestriction>Y</ns2:PetRestriction>
              <ns2:BaggageRouteRequired>true</ns2:BaggageRouteRequired>
              <ns2:BaggageRestrictionLimit>2</ns2:BaggageRestrictionLimit>
              <ns2:LateCheckin>Y</ns2:LateCheckin>
              <ns2:ShortCheckinReason>{SHORT_REASON}</ns2:ShortCheckinReason>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>2</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>1000</ns2:Flight>
              <ns2:Origin>DOH</ns2:Origin>
              <ns2:Destination>MCT</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAXRESTRICT01</ns2:PassengerID>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_data_xml():
    """GetPassengerDataRS using the vendor sample embargo and late-check values."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>RESTRICTED</LastName>
          <FirstName>BAG</FirstName>
          <PassengerID>PAXRESTRICT01</PassengerID>
          <PNRLocator nameAssociationID="1">{PNR}</PNRLocator>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:SegmentID>1</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>{FLIGHT}</ns2:Flight>
              <ns2:Origin>{ORIGIN}</ns2:Origin>
              <ns2:Destination>DOH</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAXRESTRICT01</ns2:PassengerID>
              <ns2:BagEmbargo>2</ns2:BagEmbargo>
              <ns2:PetRestriction>Y</ns2:PetRestriction>
              <ns2:BaggageRouteRequired>true</ns2:BaggageRouteRequired>
              <ns2:BaggageRestrictionLimit>2</ns2:BaggageRestrictionLimit>
              <ns2:LateCheckin>Y</ns2:LateCheckin>
              <ns2:ShortCheckinReason>{SHORT_REASON}</ns2:ShortCheckinReason>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>2</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>1000</ns2:Flight>
              <ns2:Origin>DOH</ns2:Origin>
              <ns2:Destination>MCT</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAXRESTRICT01</ns2:PassengerID>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _session_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _soap_post(url, data=None, headers=None, timeout=None):
    action = (headers or {}).get("SOAPAction", "")
    if action == "SessionCreateRQ":
        return _Response(200, _session_xml())
    if action == "GetPassengerListRQ":
        return _Response(200, _passenger_list_xml())
    if action == "GetPassengerDataRQ":
        return _Response(200, _passenger_data_xml())
    if action == "SessionCloseRQ":
        return _Response(200, _session_xml())
    return _Response(500, "placeholder upstream failure")


def _matches(doc, query):
    if not isinstance(doc, dict):
        return False
    for key, expected in query.items():
        if "." in key:
            head, rest = key.split(".", 1)
            value = doc.get(head)
            items = value if isinstance(value, list) else [value]
            if not any(_matches(item, {rest: expected}) for item in items if isinstance(item, dict)):
                return False
            continue
        value = doc.get(key)
        if isinstance(expected, dict):
            if set(expected) != {"$lte"}:
                raise AssertionError(f"unsupported query {key}={expected!r}")
            if value is None or value > expected["$lte"]:
                return False
        elif value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, spec):
        for field, direction in reversed(spec):
            self.docs.sort(key=lambda doc: (doc.get(field) is None, doc.get(field)), reverse=direction < 0)
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Collection:
    def __init__(self):
        self.docs = []
        self._seq = 0

    def insert_one(self, doc):
        self._seq += 1
        doc.setdefault("_id", f"id-{self._seq}")
        self.docs.append(doc)
        return _InsertResult(doc["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def find_one(self, query=None, projection=None, sort=None):
        matched = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            matched = _Cursor(matched).sort(sort).docs
        return matched[0] if matched else None

    def find(self, query=None, projection=None, sort=None):
        matched = [doc for doc in self.docs if _matches(doc, query or {})]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, query, update, upsert=False):
        doc = self.find_one(query)
        created = doc is None
        if created:
            if not upsert:
                return
            doc = {}
            self.docs.append(doc)
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc[key] = value
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value

    def create_index(self, keys, **kwargs):
        return kwargs.get("name", "idx")

    def drop_index(self, name):
        return None

    def index_information(self):
        return {}


class _MemoryDb:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]

    def command(self, name, *args, **kwargs):
        return {"ok": 1}


class _Redis:
    def ping(self):
        return True


def _route_by_flight(routes, flight):
    found = [route for route in routes if str(route.get("flight")) == flight]
    assert found, f"no baggage route for flight {flight}: {routes}"
    return found[0]


def _assert_restrictions(route, expected):
    for key, accepted in expected.items():
        assert key in route, f"stored baggage route dropped {key}: {route}"
        value = route[key]
        rendered = {str(item) for item in accepted}
        assert value in accepted or str(value) in rendered, (
            f"{key}={value!r} lost the Sabre restriction {sorted(rendered)}"
        )
        assert value not in (False, None, "", "false", "False", "0", 0), (
            f"{key}={value!r} is stored as an ordinary unrestricted route"
        )


def _assert_ordinary(route):
    for key in LIST_RESTRICTIONS:
        value = route.get(key, "")
        assert value in ("", None, False, 0, "0", "false", "False"), (
            f"unrestricted route flight 1000 was marked {key}={value!r}"
        )


def _parsed_routes(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, response_key)
    data = _strip_ns(body)
    return data


@pytest.fixture(scope="module")
def client():
    SabreClient.API_CALL_DELAY = 0
    memory = _MemoryDb()
    api_database.get_db = lambda: memory
    api_main.get_db = lambda: memory
    passengers_route.get_db = lambda: memory
    api_main.get_redis = lambda: _Redis()
    feeder_storage._db = None
    import backend.sabre.client as sabre_client

    original_post = sabre_client.requests.post
    sabre_client.requests.post = _soap_post
    try:
        with TestClient(api_main.app) as test_client:
            response = test_client.post(
                "/flights/ingest",
                json={
                    "airline": "GF",
                    "flightNumber": FLIGHT,
                    "origin": ORIGIN,
                    "departureDate": DATE,
                    "departureDateTime": f"{DATE}T10:00:00",
                },
            )
            assert response.status_code in (200, 409), response.text
            assert response.status_code not in (401, 403)
            stored = memory["passenger_list"].find_one({"flightNumber": FLIGHT})
            assert stored is not None, response.text
            test_client.memory = memory
            test_client.ingest_response = response
            yield test_client
    finally:
        sabre_client.requests.post = original_post
        feeder_storage._db = None


def test_parser_delivers_list_bag_restrictions():
    """The product parser keeps the restriction elements the converter then drops."""
    data = _parsed_routes(_passenger_list_xml(), "GetPassengerListRS")
    routes = data["PassengerInfoList"]["PassengerInfo"]["BaggageRouteList"]["BaggageRoute"]
    restricted = routes[0]
    assert restricted["BagEmbargo"] == "1"
    assert restricted["PetRestriction"] == "Y"
    assert restricted["BaggageRestrictionLimit"] == "2"
    assert restricted["LateCheckin"] == "Y"
    assert restricted["ShortCheckinReason"] == SHORT_REASON
    assert restricted["BaggageRouteRequired"] == "true"
    assert "BagEmbargo" not in routes[1]


def test_parser_delivers_detail_bag_restrictions():
    data = _parsed_routes(_passenger_data_xml(), "GetPassengerDataRS")
    routes = data["PassengerDataResponseList"]["PassengerDataResponse"]["BaggageRouteList"]["BaggageRoute"]
    restricted = routes[0]
    assert restricted["BagEmbargo"] == "2"
    assert restricted["LateCheckin"] == "Y"
    assert restricted["PetRestriction"] == "Y"
    assert restricted["BaggageRestrictionLimit"] == "2"
    assert restricted["ShortCheckinReason"] == SHORT_REASON
    assert restricted["BaggageRouteRequired"] == "true"


def _stored_routes(client):
    doc = client.memory["passenger_list"].find_one({"flightNumber": FLIGHT})
    passenger = doc["passengers"][0]
    return passenger["baggageRoutes"]


def test_stored_manifest_keeps_each_bag_restriction(client):
    routes = _stored_routes(client)
    restricted = _route_by_flight(routes, FLIGHT)
    ordinary = _route_by_flight(routes, "1000")
    assert restricted["airline"] == "GF"
    assert restricted["origin"] == ORIGIN
    assert restricted["destination"] == "DOH"
    assert restricted["segmentStatus"] == "HK"
    _assert_restrictions(restricted, LIST_RESTRICTIONS)
    _assert_ordinary(ordinary)
    snap = client.memory["snapshots"].find_one(
        {"flightNumber": FLIGHT, "snapshotType": "passenger_list"}
    )
    snap_routes = snap["data"]["passengers"][0]["baggageRoutes"]
    _assert_restrictions(_route_by_flight(snap_routes, FLIGHT), LIST_RESTRICTIONS)


def test_manifest_api_returns_bag_restrictions_without_raw(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "_raw" not in body
    routes = body["passengers"][0]["baggageRoutes"]
    restricted = _route_by_flight(routes, FLIGHT)
    _assert_restrictions(restricted, LIST_RESTRICTIONS)
    _assert_ordinary(_route_by_flight(routes, "1000"))


def test_pnr_manifest_returns_bag_restrictions(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}",
        params={"date": DATE},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["passengers"][0]["baggageRoutes"]
    _assert_restrictions(_route_by_flight(routes, FLIGHT), LIST_RESTRICTIONS)


def test_historical_manifest_returns_bag_restrictions(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE, "snapshot_sequence": 1},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["passengers"][0]["baggageRoutes"]
    _assert_restrictions(_route_by_flight(routes, FLIGHT), LIST_RESTRICTIONS)
    _assert_ordinary(_route_by_flight(routes, "1000"))


def test_passenger_detail_keeps_vendor_embargo_and_late_checkin(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
    )
    assert response.status_code == 200, response.text
    routes = response.json()["passengers"][0]["baggageRoutes"]
    restricted = _route_by_flight(routes, FLIGHT)
    _assert_restrictions(restricted, DETAIL_RESTRICTIONS)
    _assert_ordinary(_route_by_flight(routes, "1000"))
