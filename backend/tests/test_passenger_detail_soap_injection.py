"""Spec for GetPassengerData request construction on the passenger detail route.

These tests call the production FastAPI app. They fail while a caller-controlled
airline value, or a stored given name or surname, is interpolated into the SOAP
body as markup. They pass when that value is rejected before any GetPassengerData
request is prepared, or when the prepared body keeps the looked-up passenger as
the only passenger in the document.

The record locator is not covered here. It is a single path segment, and a closing
tag contains a slash, so that value is not delivered to this handler.

Run from the repository root:

    PYTHONPATH=. python3 -m pytest backend/tests/test_passenger_detail_soap_injection.py -q
"""

import os
from urllib.parse import quote
from xml.etree import ElementTree

import pytest
import requests
from fastapi.testclient import TestClient

# Placeholders only. Set before the app import so a local env file cannot
# override them into the process that builds the SOAP request.
os.environ["MONGODB_URI"] = "mongodb://placeholder-mongo.invalid:27017"
os.environ["REDIS_URL"] = "redis://placeholder-redis.invalid:6379/0"
os.environ["SABRE_BASE_URL"] = "http://placeholder-sabre.invalid"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder-pcc"
os.environ["SABRE_ORGANIZATION"] = "placeholder-org"
os.environ["SABRE_DOMAIN"] = "placeholder-domain"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"

from backend.api.main import app  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

STORED_FLIGHT = "2057"
STORED_DATE = "2026-03-19"
STORED_ORIGIN = "BAH"
STORED_PNR = "ABC123"
STORED_LAST = "DOE"
STORED_FIRST = "JANE"
SESSION_TOKEN = "placeholder-session-token"
SABRE_ENDPOINT = "http://placeholder-sabre.invalid/GF"

# Closes the airline element and appends a second itinerary and passenger,
# then reopens the original elements so the rest of the template stays well formed.
AIRLINE_BREAKOUT = (
    "GF</Airline></Itinerary></ItineraryAndPassengerInfo>"
    "<ItineraryAndPassengerInfo><Itinerary>"
    "<Airline>GF</Airline><Flight>100</Flight>"
    "<DepartureDate>2026-04-01</DepartureDate><Origin>LHR</Origin>"
    "</Itinerary><PassengerList><Passenger>"
    "<LastName>TARGET</LastName><PNRLocator>ZZZZZZ</PNRLocator>"
    "</Passenger></PassengerList></ItineraryAndPassengerInfo>"
    "<ItineraryAndPassengerInfo><Itinerary><Airline>GF"
)

LAST_NAME_BREAKOUT = (
    "DOE</LastName></Passenger><Passenger>"
    "<LastName>TARGET</LastName><PNRLocator>ZZZZZZ</PNRLocator>"
    "</Passenger><Passenger><LastName>DOE"
)

FIRST_NAME_BREAKOUT = (
    "JANE</FirstName></Passenger><Passenger>"
    "<LastName>TARGET</LastName><PNRLocator>ZZZZZZ</PNRLocator>"
    "</Passenger><Passenger><FirstName>JANE"
)

SESSION_RESPONSE = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>{SESSION_TOKEN}</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""

PASSENGER_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerDataRS Status="Success" CompletionStatus="Complete">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LastName>DOE</LastName>
          <FirstName>JANE</FirstName>
          <PNRLocator>ABC123</PNRLocator>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""

CLOSE_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body/>
</soap-env:Envelope>"""


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None):
        document = self._document
        if document is None:
            return None
        if query.get("flightNumber") not in (None, document["flightNumber"]):
            return None
        if query.get("departureDate") not in (None, document["departureDate"]):
            return None
        if query.get("passengers.pnr") not in (
            None,
            document["passengers"][0]["pnr"],
        ):
            return None
        return document

    def index_information(self):
        return {"flight_lookup": {}}


class _Database:
    def __init__(self, passenger_list):
        self._passenger_list = passenger_list

    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        if name == "passenger_list":
            return self._passenger_list
        return _Collection(None)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _texts(root, name):
    return [
        (element.text or "")
        for element in root.iter()
        if _local(element.tag) == name
    ]


def _response(request, content):
    response = requests.Response()
    response.status_code = 200
    response.url = request.url
    response.request = request
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "text/xml; charset=utf-8"
    response._content = content.encode("utf-8")
    response.raw = None
    return response


def _stored_passenger(pnr, last_name, first_name):
    return {
        "flightNumber": STORED_FLIGHT,
        "departureDate": STORED_DATE,
        "origin": STORED_ORIGIN,
        "passengers": [
            {
                "pnr": pnr,
                "lastName": last_name,
                "firstName": first_name,
            }
        ],
    }


@pytest.fixture
def sabre_transport(monkeypatch):
    """Run requests' request preparation, and stop at the adapter send."""
    prepared = []

    def fake_send(self, request, **kwargs):
        prepared.append(request)
        action = request.headers.get("SOAPAction", "")
        if action == "SessionCreateRQ":
            content = SESSION_RESPONSE
        elif action == "GetPassengerDataRQ":
            content = PASSENGER_RESPONSE
        else:
            content = CLOSE_RESPONSE
        return _response(request, content)

    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)
    monkeypatch.setattr("requests.adapters.HTTPAdapter.send", fake_send)
    return prepared


@pytest.fixture
def detail_client(monkeypatch, sabre_transport):
    database = _Database(_Collection(None))

    def get_db():
        return database

    class _Redis:
        def ping(self):
            return True

    monkeypatch.setattr("backend.api.database.get_db", get_db)
    monkeypatch.setattr("backend.api.main.get_db", get_db)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", get_db)
    monkeypatch.setattr("backend.api.main.get_redis", lambda: _Redis())

    def install(document):
        database._passenger_list = _Collection(document)

    with TestClient(app) as client:
        yield client, install, sabre_transport


def _passenger_data_requests(prepared):
    return [
        request
        for request in prepared
        if request.headers.get("SOAPAction") == "GetPassengerDataRQ"
    ]


def _elements(root, name):
    return [element for element in root.iter() if _local(element.tag) == name]


def _assert_detail_request_stays_on_stored_passenger(
    response, prepared, pnr, last_name, first_name
):
    """Reject markup before send, or send only the looked-up passenger.

    The passenger row loaded for this request is the only passenger the SOAP
    body may name. Field text must be that stored value, so a later check that
    re-reads a different value still fails.
    """
    data_requests = _passenger_data_requests(prepared)
    if not data_requests:
        assert 400 <= response.status_code < 500, response.status_code
        detail = response.json().get("detail", "")
        assert "not found" not in str(detail).lower()
        return

    assert len(data_requests) == 1
    request = data_requests[0]
    assert request.url == SABRE_ENDPOINT
    body = request.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    root = ElementTree.fromstring(body)
    assert _texts(root, "BinarySecurityToken") == [SESSION_TOKEN]
    assert len(_elements(root, "ItineraryAndPassengerInfo")) == 1
    assert len(_elements(root, "Passenger")) == 1
    assert len(_elements(root, "Airline")) == 1
    assert _texts(root, "PNRLocator") == [pnr]
    assert _texts(root, "LastName") == [last_name]
    assert _texts(root, "FirstName") == [first_name]
    assert _texts(root, "Flight") == [STORED_FLIGHT]
    assert _texts(root, "Origin") == [STORED_ORIGIN]
    assert _texts(root, "DepartureDate") == [STORED_DATE]
    assert response.status_code == 200
    returned = response.json()
    assert returned["passengers"][0]["lastName"] == "DOE"
    assert returned["passengers"][0]["pnr"] == "ABC123"


def _get_detail(client, pnr, airline):
    return client.get(
        f"/flights/{STORED_FLIGHT}/passengers/{quote(pnr, safe='')}/detail",
        params={
            "origin": STORED_ORIGIN,
            "date": STORED_DATE,
            "airline": airline,
        },
    )


def test_airline_query_cannot_add_a_passenger_to_the_sabre_request(detail_client):
    client, install, prepared = detail_client
    install(_stored_passenger(STORED_PNR, STORED_LAST, STORED_FIRST))

    response = _get_detail(client, STORED_PNR, AIRLINE_BREAKOUT)

    _assert_detail_request_stays_on_stored_passenger(
        response, prepared, STORED_PNR, STORED_LAST, STORED_FIRST
    )


def test_stored_last_name_cannot_add_a_passenger_to_the_sabre_request(detail_client):
    client, install, prepared = detail_client
    install(_stored_passenger(STORED_PNR, LAST_NAME_BREAKOUT, STORED_FIRST))

    response = _get_detail(client, STORED_PNR, "GF")

    _assert_detail_request_stays_on_stored_passenger(
        response, prepared, STORED_PNR, LAST_NAME_BREAKOUT, STORED_FIRST
    )


def test_stored_first_name_cannot_add_a_passenger_to_the_sabre_request(detail_client):
    client, install, prepared = detail_client
    install(_stored_passenger(STORED_PNR, STORED_LAST, FIRST_NAME_BREAKOUT))

    response = _get_detail(client, STORED_PNR, "GF")

    _assert_detail_request_stays_on_stored_passenger(
        response, prepared, STORED_PNR, STORED_LAST, FIRST_NAME_BREAKOUT
    )

