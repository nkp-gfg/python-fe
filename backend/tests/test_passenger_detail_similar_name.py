"""Live passenger detail must not return other travelers from a Sabre multi-match.

GET /flights/{flight_number}/passengers/{pnr}/detail looks up one stored
traveler and asks Sabre GetPassengerData for that name and record locator.
GetPassengerDataRS may contain every similar-name match. These tests run that
route with the real SOAP parse and converter. They fail while every
PassengerDataResponse is copied into the HTTP body.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Placeholders only. Set before importing the feeder, which loads a dotenv file
# without overriding variables that are already present.
os.environ["SABRE_BASE_URL"] = "http://sabre.invalid"
os.environ["SABRE_CPAID"] = "placeholder-cpaid"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-secret"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder-pcc"
os.environ["SABRE_ORGANIZATION"] = "placeholder-org"
os.environ["SABRE_DOMAIN"] = "placeholder-domain"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.sabre.client import SabreClient


FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
REQUESTED_PNR = "ABCDEF"
REQUESTED_LAST = "ALFA"
REQUESTED_FIRST = "NINA"
REQUESTED_TICKET = "REQUESTEDTKT1001"
REQUESTED_DOC = "REQUESTEDDOC111"
REQUESTED_DEST = "DEST-REQUESTED"
REQUESTED_ID = "PAXID-REQUESTED"

OTHER_PNR = "ZZZZZZ"
OTHER_LAST = "BRAVO"
OTHER_FIRST = "OTTO"
OTHER_TICKET = "OTHERTKT2002"
OTHER_DOC = "OTHERDOC999"
OTHER_DEST = "DEST-OTHER"
OTHER_ID = "PAXID-OTHER"

SECOND_TICKET = "SECONDTKT3003"
SECOND_ID = "PAXID-SECOND"


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _PassengerList:
    def __init__(self, document):
        self.document = document
        self.queries = []

    def find_one(self, query, sort=None):
        self.queries.append((query, sort))
        if query.get("flightNumber") != self.document.get("flightNumber"):
            return None
        if "departureDate" in query and query["departureDate"] != self.document.get("departureDate"):
            return None
        wanted = query.get("passengers.pnr")
        passengers = self.document.get("passengers", [])
        if wanted is not None and not any(p.get("pnr") == wanted for p in passengers):
            return None
        return self.document


class _Database:
    def __init__(self, document):
        self.passenger_list = _PassengerList(document)

    def __getitem__(self, name):
        if name != "passenger_list":
            raise KeyError(name)
        return self.passenger_list


class _SabreHttp:
    """Stand in for requests.post. Session creation and the data call still go
    through SabreClient's XML builder, xmltodict parse, and body extractor.
    """

    def __init__(self, passenger_data_xml):
        self.passenger_data_xml = passenger_data_xml
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        self.calls.append({"action": action, "data": data, "url": url})
        if action == "SessionCreateRQ":
            body = "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        elif action == "GetPassengerDataRQ":
            body = self.passenger_data_xml
        else:
            body = "<ok/>"
        return _Response(body)


def _passenger_xml(last, first, passenger_id, pnr, ticket, destination, document):
    return f"""<PassengerDataResponse>
      <LineNumber>1</LineNumber>
      <LastName>{last}</LastName>
      <FirstName>{first}</FirstName>
      <PassengerID>{passenger_id}</PassengerID>
      <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
      <VCRNumber couponNumber="1">{ticket}</VCRNumber>
      <PassengerItineraryList>
        <PassengerItinerary>
          <Airline>GF</Airline>
          <Flight>{FLIGHT}</Flight>
          <Origin>{ORIGIN}</Origin>
          <Destination>{destination}</Destination>
          <DepartureDate>{DATE}</DepartureDate>
        </PassengerItinerary>
      </PassengerItineraryList>
      <FreeTextInfoList>
        <FreeTextInfo>
          <EditCode>DOCS</EditCode>
          <Text>P/XX/{document}/XX/01JAN80/F/01JAN30/{last}/{first}</Text>
        </FreeTextInfo>
      </FreeTextInfoList>
    </PassengerDataResponse>"""


def _passenger_data_response(entries, similar_name_found=None):
    flag = ""
    if similar_name_found is True:
        flag = ' similarNameFound="true"'
    elif similar_name_found is False:
        flag = ' similarNameFound="false"'
    inner = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList{flag}>
        {inner}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _stored_manifest():
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-03-19T00:00:00Z",
        "passengers": [
            {
                "pnr": REQUESTED_PNR,
                "lastName": REQUESTED_LAST,
                "firstName": REQUESTED_FIRST,
            },
            {
                "pnr": "YYYYYY",
                "lastName": "KEPT",
                "firstName": "ASIDE",
            },
        ],
    }


def _requested_entry():
    return _passenger_xml(
        REQUESTED_LAST, REQUESTED_FIRST, REQUESTED_ID, REQUESTED_PNR,
        REQUESTED_TICKET, REQUESTED_DEST, REQUESTED_DOC,
    )


def _other_entry(pnr=OTHER_PNR, last=OTHER_LAST, first=OTHER_FIRST,
                 passenger_id=OTHER_ID, ticket=OTHER_TICKET,
                 destination=OTHER_DEST, document=OTHER_DOC):
    return _passenger_xml(
        last, first, passenger_id, pnr, ticket, destination, document,
    )


def _client(monkeypatch, sabre_xml):
    database = _Database(_stored_manifest())
    http = _SabreHttp(sabre_xml)
    monkeypatch.setattr(passengers, "get_db", lambda: database)
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0)
    monkeypatch.setattr("backend.sabre.client.requests.post", http)
    app = FastAPI()
    app.include_router(passengers.router)
    return TestClient(app), http, database


def _get_detail(client):
    return client.get(
        f"/flights/{FLIGHT}/passengers/{REQUESTED_PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
    )


def _assert_asked_for_one_traveler(http):
    data_calls = [call for call in http.calls if call["action"] == "GetPassengerDataRQ"]
    assert len(data_calls) == 1
    request_xml = data_calls[0]["data"]
    assert f"<LastName>{REQUESTED_LAST}</LastName>" in request_xml
    assert f"<FirstName>{REQUESTED_FIRST}</FirstName>" in request_xml
    assert f"<PNRLocator>{REQUESTED_PNR}</PNRLocator>" in request_xml
    assert OTHER_LAST not in request_xml
    assert OTHER_TICKET not in request_xml
    assert "KEPT" not in request_xml


def _assert_only_requested_traveler(response):
    assert response.status_code == 200, response.text
    body = response.json()
    returned = body["passengers"]
    assert body["totalPassengers"] == 1
    assert len(returned) == 1
    passenger = returned[0]
    assert passenger["pnr"] == REQUESTED_PNR
    assert passenger["lastName"] == REQUESTED_LAST
    assert passenger["firstName"] == REQUESTED_FIRST
    assert passenger["vcrNumber"] == REQUESTED_TICKET
    assert passenger["passengerId"] == REQUESTED_ID
    assert passenger["docsData"]["documentNumber"] == REQUESTED_DOC
    assert [segment["destination"] for segment in passenger["itinerary"]] == [REQUESTED_DEST]
    leaked = (
        OTHER_PNR, OTHER_LAST, OTHER_FIRST, OTHER_TICKET, OTHER_DOC,
        OTHER_DEST, OTHER_ID, SECOND_TICKET, SECOND_ID, "KEPT",
    )
    raw = response.text
    for token in leaked:
        assert token not in raw


def _assert_conflict_without_other_travelers(response):
    assert response.status_code == 409, response.text
    raw = response.text
    for token in (
        OTHER_PNR, OTHER_LAST, OTHER_FIRST, OTHER_TICKET, OTHER_DOC,
        OTHER_DEST, OTHER_ID, SECOND_TICKET, SECOND_ID, REQUESTED_TICKET,
        REQUESTED_DOC, REQUESTED_ID, "CHARLIE", "CHARTKT4004",
    ):
        assert token not in raw


def test_similar_name_list_does_not_return_other_locator(monkeypatch):
    xml = _passenger_data_response(
        [_requested_entry(), _other_entry()],
        similar_name_found=True,
    )
    client, http, database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    assert database.passenger_list.queries
    assert database.passenger_list.queries[0][0]["passengers.pnr"] == REQUESTED_PNR
    _assert_asked_for_one_traveler(http)
    _assert_only_requested_traveler(response)


def test_same_locator_different_name_is_not_returned(monkeypatch):
    """Vendor similar-name samples put two travelers on one record locator."""
    xml = _passenger_data_response(
        [
            _requested_entry(),
            _other_entry(pnr=REQUESTED_PNR),
        ],
        similar_name_found=True,
    )
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_only_requested_traveler(response)


def test_same_name_different_locator_is_not_returned(monkeypatch):
    xml = _passenger_data_response(
        [
            _requested_entry(),
            _other_entry(last=REQUESTED_LAST, first=REQUESTED_FIRST),
        ],
        similar_name_found=True,
    )
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_only_requested_traveler(response)


def test_multi_match_without_similar_name_flag_is_filtered(monkeypatch):
    xml = _passenger_data_response(
        [_requested_entry(), _other_entry()],
        similar_name_found=None,
    )
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_only_requested_traveler(response)


def test_similar_name_list_with_no_unique_match_is_conflict(monkeypatch):
    xml = _passenger_data_response(
        [
            _other_entry(),
            _other_entry(
                pnr="YYYYYY", last="CHARLIE", first="QUINN",
                passenger_id="PAXID-CHARLIE", ticket="CHARTKT4004",
                destination="DEST-CHARLIE", document="CHARLIEDOC444",
            ),
        ],
        similar_name_found=True,
    )
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_conflict_without_other_travelers(response)
    assert "YYYYYY" not in response.text
    assert "CHARLIEDOC444" not in response.text
    assert "DEST-CHARLIE" not in response.text


def test_two_entries_matching_name_and_locator_is_conflict(monkeypatch):
    xml = _passenger_data_response(
        [
            _requested_entry(),
            _other_entry(
                pnr=REQUESTED_PNR, last=REQUESTED_LAST, first=REQUESTED_FIRST,
                passenger_id=SECOND_ID, ticket=SECOND_TICKET,
                destination="DEST-SECOND", document="SECONDDOC333",
            ),
        ],
        similar_name_found=True,
    )
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_conflict_without_other_travelers(response)
    assert "SECONDDOC333" not in response.text
    assert "DEST-SECOND" not in response.text


def test_exact_single_match_is_returned(monkeypatch):
    xml = _passenger_data_response([_requested_entry()], similar_name_found=False)
    client, http, _database = _client(monkeypatch, xml)
    with client:
        response = _get_detail(client)
    _assert_asked_for_one_traveler(http)
    _assert_only_requested_traveler(response)
