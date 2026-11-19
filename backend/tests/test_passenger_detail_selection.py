"""Live passenger detail must describe the traveler who was selected.

A shared record locator stores several people. The detail request identifies
one of them with ``passenger_id`` or with ``first_name`` and ``last_name``,
and the manifest row is chosen from the requested origin. The Sabre call and
``passengers[0]`` in the JSON must be that person: their travel-document
number, check-in requirement, ticket, and seat come back from GetPassengerData,
not from the first row stored for the locator.

These tests fail while the detail route asks Sabre for the first stored name
and returns that response unchanged.

Run from the repository root:

    python -m pytest backend/tests/test_passenger_detail_selection.py -q
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://sabre.test")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")
os.environ.setdefault("MONGODB_URI", "mongodb://placeholder.invalid")

import copy
from xml.sax.saxutils import escape

import pytest
import requests
import xmltodict
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.audit_rules import run_audit
from backend.api.routes import passengers as passengers_route

# GetPassengerDataRQ is a choice of a top-level record locator, which the
# published sample answers with one PassengerDataResponse per person on the
# booking, or ItineraryAndPassengerInfo. A PassengerList entry carries
# LastName plus optional FirstName and PassengerID. The schema text says an
# exact match returns that passenger, and a last name or part of one returns
# the matching list. See sabre_docs/GetPassengerDataRQ_v4-0-0/
# GetPassengerDataRQRS_v4-0-0.xsd and
# sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/
# GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_1.xml.

FLIGHT = "2057"
DATE = "2026-03-19"
PNR = "ABCPQR"
ORIGIN = "LHR"

IDRIS = {
    "lastName": "HALE",
    "firstName": "IDRIS",
    "passengerId": "HALEIDRIS001",
    "pnr": PNR,
    "documentNumber": "100000001",
    "docsText": "P/BH/100000001/BH/01JAN80/M/01JAN30/HALE/IDRIS",
    "requirement": "DOCS OK FOR IDRIS HALE",
    "seat": "14A",
    "vcr": "0000000000001",
    "nameAssociationId": "1",
}
SARA = {
    "lastName": "HALE",
    "firstName": "SARA",
    "passengerId": "HALESARA0002",
    "pnr": PNR,
    "documentNumber": "200000002",
    "docsText": "P/BH/200000002/BH/02FEB81/F/02FEB31/HALE/SARA",
    "requirement": "DOCS FAILED FOR SARA HALE",
    "seat": "14B",
    "vcr": "0000000000002",
    "nameAssociationId": "2",
}
TAREK = {
    "lastName": "HALE",
    "firstName": "TAREK",
    "passengerId": "HALETAREK003",
    "pnr": PNR,
    "documentNumber": "300000003",
    "docsText": "P/BH/300000003/BH/03MAR82/M/03MAR32/HALE/TAREK",
    "requirement": "DOCS OK FOR TAREK HALE",
    "seat": "14C",
    "vcr": "0000000000003",
    "nameAssociationId": "3",
}
OMAR = {
    "lastName": "NASSER",
    "firstName": "OMAR",
    "passengerId": "OMARNASSER01",
    "pnr": PNR,
    "documentNumber": "400000004",
    "docsText": "P/BH/400000004/BH/04APR83/M/04APR33/NASSER/OMAR",
    "requirement": "DOCS OK FOR OMAR NASSER",
    "seat": "1A",
    "vcr": "0000000000004",
    "nameAssociationId": "1",
}

FAMILY = [IDRIS, SARA, TAREK]
POSTED_REQUESTS = []


def _local(tag):
    return tag.split(":")[-1]


def _children(node, name):
    if not isinstance(node, dict):
        return []
    found = []
    for key, value in node.items():
        if key.startswith("@"):
            continue
        if _local(key) == name:
            found.append(value)
    return found


def _as_list(node):
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


def _text(node):
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict):
        if "#text" in node:
            return str(node["#text"]).strip()
        return ""
    return str(node).strip()


def _find_request(parsed):
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if not key.startswith("@") and _local(key) == "GetPassengerDataRQ":
                return value
            found = _find_request(value)
            if found is not None:
                return found
    elif isinstance(parsed, list):
        for item in parsed:
            found = _find_request(item)
            if found is not None:
                return found
    return None


def matched_passengers(request_xml, inventory):
    """Apply the GetPassengerData request rules to the inventory."""
    parsed = xmltodict.parse(request_xml)
    request = _find_request(parsed)
    assert request is not None, "GetPassengerDataRQ missing from posted SOAP"

    top_level_locator = _children(request, "PNRLocator")
    itinerary_blocks = _children(request, "ItineraryAndPassengerInfo")
    if top_level_locator and not itinerary_blocks:
        locator = _text(top_level_locator[0])
        return [person for person in inventory if person["pnr"] == locator]

    assert itinerary_blocks, "request used neither a record locator nor an itinerary"
    block = itinerary_blocks[0]
    lists = _children(block, "PassengerList")
    if not lists:
        return []

    matched = []
    seen = set()
    passenger_nodes = []
    for child in _children(lists[0], "Passenger"):
        passenger_nodes.extend(_as_list(child))

    for node in passenger_nodes:
        last_name = _text(_children(node, "LastName")[0]) if _children(node, "LastName") else ""
        first_name = _text(_children(node, "FirstName")[0]) if _children(node, "FirstName") else ""
        passenger_id = _text(_children(node, "PassengerID")[0]) if _children(node, "PassengerID") else ""
        locator = _text(_children(node, "PNRLocator")[0]) if _children(node, "PNRLocator") else ""
        candidates = inventory
        if locator:
            candidates = [person for person in candidates if person["pnr"] == locator]
        if passenger_id:
            candidates = [person for person in candidates if person["passengerId"] == passenger_id]
        elif first_name:
            candidates = [
                person for person in candidates
                if person["lastName"] == last_name and person["firstName"] == first_name
            ]
        elif last_name:
            candidates = [
                person for person in candidates
                if person["lastName"].startswith(last_name)
            ]
        else:
            candidates = []
        for person in candidates:
            if person["passengerId"] not in seen:
                seen.add(person["passengerId"])
                matched.append(person)
    return matched


def _passenger_xml(person, line_number):
    return f"""
        <PassengerDataResponse>
          <LineNumber>{line_number}</LineNumber>
          <LastName>{escape(person["lastName"])}</LastName>
          <FirstName>{escape(person["firstName"])}</FirstName>
          <PassengerID>{escape(person["passengerId"])}</PassengerID>
          <PNRLocator nameAssociationID="{escape(person["nameAssociationId"])}">{escape(person["pnr"])}</PNRLocator>
          <VCRNumber>{escape(person["vcr"])}</VCRNumber>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <Airline>GF</Airline>
              <Flight>{FLIGHT}</Flight>
              <Origin>{ORIGIN}</Origin>
              <Destination>BAH</Destination>
              <DepartureDate>{DATE}</DepartureDate>
              <BookingClass>Y</BookingClass>
              <Cabin>Y</Cabin>
              <Seat>{escape(person["seat"])}</Seat>
              <RequiredInfoList>
                <RequiredInfo>
                  <Code>DOCS</Code>
                  <DetailStatus>ValidationRequired</DetailStatus>
                  <FreeText>{escape(person["requirement"])}</FreeText>
                </RequiredInfo>
              </RequiredInfoList>
            </PassengerItinerary>
          </PassengerItineraryList>
          <RequiredInfoSumList>
            <RequiredInfo>
              <Code>DOCS</Code>
              <DetailStatus>ValidationRequired</DetailStatus>
              <FreeText>{escape(person["requirement"])}</FreeText>
            </RequiredInfo>
          </RequiredInfoSumList>
          <FreeTextInfoList>
            <FreeTextInfo>
              <EditCode>DOCS</EditCode>
              <Text>{escape(person["docsText"])}</Text>
            </FreeTextInfo>
          </FreeTextInfoList>
        </PassengerDataResponse>
    """


def _response_xml(people):
    rows = "\n".join(_passenger_xml(person, index) for index, person in enumerate(people, start=1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        {rows}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _post(url, data=None, headers=None, timeout=None):
    action = (headers or {}).get("SOAPAction")
    if action == "SessionCreateRQ":
        return _Response(SESSION_XML)
    if action == "SessionCloseRQ":
        return _Response(SESSION_XML)
    if action == "GetPassengerDataRQ":
        POSTED_REQUESTS.append(data)
        people = matched_passengers(data, _post.inventory)
        return _Response(_response_xml(people))
    raise AssertionError(f"unexpected Sabre action {action}")


_post.inventory = []


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if _doc_matches(doc, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        if not matches:
            return None
        return copy.deepcopy(matches[0])


def _doc_matches(doc, query):
    for key, expected in query.items():
        if "." in key:
            head, tail = key.split(".", 1)
            rows = doc.get(head) or []
            if not any(isinstance(row, dict) and row.get(tail) == expected for row in rows):
                return False
        elif doc.get(key) != expected:
            return False
    return True


class _Database:
    def __init__(self, manifests):
        self._collections = {"passenger_list": _Collection(manifests)}

    def __getitem__(self, name):
        return self._collections[name]


def _stored_row(person, seat="00A"):
    return {
        "passengerId": person["passengerId"],
        "firstName": person["firstName"],
        "lastName": person["lastName"],
        "pnr": person["pnr"],
        "seat": seat,
    }


def _manifest(origin, fetched_at, people):
    return {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": DATE,
        "fetchedAt": fetched_at,
        "passengers": [_stored_row(person) for person in people],
    }


def _client(manifests, inventory):
    app = FastAPI()
    app.include_router(passengers_route.router)
    _post.inventory = inventory
    POSTED_REQUESTS.clear()
    passengers_route.get_db = lambda: _Database(manifests)
    requests.post = _post
    return TestClient(app)


def _assert_selected(body, request_xml, person, inventory):
    matched = matched_passengers(request_xml, inventory)
    matched_ids = [item["passengerId"] for item in matched]
    assert person["passengerId"] in matched_ids, (
        f"Sabre was asked for {matched_ids or 'nobody'}, not {person['passengerId']}"
    )
    assert body["passengers"], "detail response had no passengers"
    shown = body["passengers"][0]
    assert shown["passengerId"] == person["passengerId"]
    assert shown["firstName"] == person["firstName"]
    assert shown["lastName"] == person["lastName"]
    assert shown["docsData"]["documentNumber"] == person["documentNumber"]
    assert shown["vcrNumber"] == person["vcr"]
    assert shown["itinerary"][0]["seat"] == person["seat"]
    assert any(item.get("freeText") == person["requirement"] for item in shown["requiredInfo"])
    assert shown["itinerary"][0]["seat"] != "00A"


@pytest.fixture(autouse=True)
def _restore_collaborators():
    original_get_db = passengers_route.get_db
    original_post = requests.post
    yield
    passengers_route.get_db = original_get_db
    requests.post = original_post
    POSTED_REQUESTS.clear()


@pytest.mark.parametrize("selected", [SARA, TAREK])
def test_detail_returns_the_selected_person_on_a_shared_locator(selected):
    manifests = [_manifest(ORIGIN, "2026-03-19T08:00:00Z", FAMILY)]
    client = _client(manifests, FAMILY)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={
            "origin": ORIGIN,
            "date": DATE,
            "airline": "GF",
            "passenger_id": selected["passengerId"],
        },
    )
    assert response.status_code == 200, response.text
    assert POSTED_REQUESTS, "detail route did not call GetPassengerData"
    _assert_selected(response.json(), POSTED_REQUESTS[-1], selected, FAMILY)


def test_detail_name_selects_the_person_who_shares_a_last_name():
    manifests = [_manifest(ORIGIN, "2026-03-19T08:00:00Z", FAMILY)]
    client = _client(manifests, FAMILY)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={
            "origin": ORIGIN,
            "date": DATE,
            "airline": "GF",
            "first_name": SARA["firstName"],
            "last_name": SARA["lastName"],
        },
    )
    assert response.status_code == 200, response.text
    assert POSTED_REQUESTS, "detail route did not call GetPassengerData"
    _assert_selected(response.json(), POSTED_REQUESTS[-1], SARA, FAMILY)


def test_detail_uses_the_requested_origin_manifest():
    manifests = [
        _manifest("BAH", "2026-03-20T12:00:00Z", [OMAR]),
        _manifest(ORIGIN, "2026-03-19T08:00:00Z", FAMILY),
    ]
    inventory = FAMILY + [OMAR]
    client = _client(manifests, inventory)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={
            "origin": ORIGIN,
            "date": DATE,
            "airline": "GF",
            "passenger_id": SARA["passengerId"],
        },
    )
    assert response.status_code == 200, response.text
    assert POSTED_REQUESTS, "detail route did not call GetPassengerData"
    _assert_selected(response.json(), POSTED_REQUESTS[-1], SARA, inventory)
    shown = response.json()["passengers"][0]
    assert shown["docsData"]["documentNumber"] != OMAR["documentNumber"]


def test_named_audit_alerts_carry_the_passenger_id():
    """Alerts that name a traveler have to carry that traveler's id.

    The audit panel opens live detail from the alert. A shared locator is
    not enough to tell the two boarded travelers apart.
    """
    passengers = []
    for person in (IDRIS, SARA):
        passengers.append({
            "passengerId": person["passengerId"],
            "firstName": person["firstName"],
            "lastName": person["lastName"],
            "pnr": person["pnr"],
            "isBoarded": True,
            "isCheckedIn": True,
            "boardingPassIssued": True,
            "editCodes": [],
            "seat": person["seat"],
            "cabin": "Y",
        })
    result = run_audit(
        {"passengers": passengers, "cabinSummary": []},
        {"reservations": []},
        [],
    )
    named = [alert for alert in result["alerts"] if alert.get("passengerName")]
    assert named, "expected a per-passenger alert"
    ids = {alert.get("passengerId") for alert in named}
    assert IDRIS["passengerId"] in ids
    assert SARA["passengerId"] in ids
