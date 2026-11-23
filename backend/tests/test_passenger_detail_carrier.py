"""Live passenger detail must use the airline stored on the passenger list.

The live detail client calls
``GET /flights/{flight}/passengers/{pnr}/detail`` without a carrier, and
``fetchPassengerDetail`` fills in airline GF. The detail route forwards that
query value to Sabre GetPassengerData and to ``convert_passenger_data``.

These tests fail while that happens. They pass once the route uses the
airline saved on the matching passenger-list document and does not assume GF
when that field is missing, including when the client sends GF explicitly.

Not executed here (same bug, covered by the client call the sheet makes):
the flight workbench opens one passenger sheet from several buttons and never
passes a carrier into it. Dashboard aggregations that default a missing
airline to GF for display keys do not call GetPassengerData. The ingestion
form defaults its airline field to GF and disables editing, so that form
cannot submit another carrier.
"""

import copy
import os
import re
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import passengers
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

FLIGHT = "512"
ORIGIN = "BAH"
DATE = "2026-04-01"
PNR = "ABC123"
LAST = "HADDAD"
FIRST = "LAYLA"
STORED_AIRLINE = "WY"

# Fictional document strings. The second is the Gulf Air booking that shares
# this name and record locator; it must not be returned for the stored carrier.
SELECTED_DOCS = "P/BH/WY1000001/BH/15MAR90/F/01JAN30/HADDAD/LAYLA"
OTHER_DOCS = "P/GB/GF2000002/GB/02FEB80/M/01JUN28/HADDAD/LAYLA"

SELECTED_DOCUMENT = {
    "documentType": "P",
    "documentCountry": "BH",
    "documentNumber": "WY1000001",
    "nationality": "BH",
    "dateOfBirth": "15MAR90",
    "gender": "F",
    "expiryDate": "01JAN30",
    "lastName": LAST,
    "firstName": FIRST,
}

_TAG_RE = re.compile(r"<([A-Za-z0-9]+)>([^<]*)</\1>")


def _tag(body, name):
    """Return the text of the first element with this local name."""
    for tag, text in _TAG_RE.findall(body):
        if tag == name:
            return text
    return ""


def _session_rs():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""


def _passenger_data_rs(docs_text, last, first, pnr):
    """SOAP body shaped the way ``_extract_body`` and ``convert_passenger_data`` read it."""
    if docs_text:
        passenger = f"""
          <PassengerDataResponse>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
            <PNRLocator>{pnr}</PNRLocator>
            <FreeTextInfoList>
              <FreeTextInfo>
                <EditCode>DOCS</EditCode>
                <Text>{docs_text}</Text>
              </FreeTextInfo>
            </FreeTextInfoList>
          </PassengerDataResponse>"""
    else:
        passenger = ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerDataRS Status="Success" CompletionStatus="Complete">
      <PassengerDataResponseList>{passenger}</PassengerDataResponseList>
    </GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


class _PassengerListCollection:
    """In-memory stand-in for the passenger_list collection.

    Supports the equality and dotted filters the detail route uses
    (``flightNumber``, ``departureDate``, ``passengers.pnr``) plus an
    ``airline`` or ``origin`` equality a fix may add.
    """

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None, sort=None, **kwargs):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=field: doc.get(field) or "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


def _matches(doc, query):
    return all(_path_equals(doc, key.split("."), expected) for key, expected in query.items())


def _path_equals(value, parts, expected):
    if isinstance(value, list):
        return any(_path_equals(item, parts, expected) for item in value)
    if not parts:
        return value == expected
    if not isinstance(value, dict) or parts[0] not in value:
        return False
    return _path_equals(value[parts[0]], parts[1:], expected)


class _FakeDb:
    def __init__(self, docs):
        self._passenger_list = _PassengerListCollection(docs)

    def __getitem__(self, name):
        if name != "passenger_list":
            raise AssertionError(f"detail lookup touched unexpected collection {name}")
        return self._passenger_list


class _SabreTransport:
    """Replace ``SabreClient._post`` after the request XML has been built.

    The airline element in that XML selects which traveler's DOCS string is
    returned, which is the itinerary key ``PASSENGER_DATA`` sends to Sabre.
    """

    def __init__(self, docs_by_airline):
        self.docs_by_airline = docs_by_airline
        self.passenger_bodies = []

    def __call__(self, action, body, timeout=30, endpoint=None):
        if action == "SessionCreateRQ":
            return _session_rs(), 200, 1, body
        if action == "SessionCloseRQ":
            return _session_rs(), 200, 1, body
        if action != "GetPassengerDataRQ":
            raise AssertionError(f"unexpected Sabre action {action}")
        self.passenger_bodies.append(body)
        docs = self.docs_by_airline.get(_tag(body, "Airline"))
        xml = _passenger_data_rs(
            docs,
            _tag(body, "LastName"),
            _tag(body, "FirstName"),
            _tag(body, "PNRLocator"),
        )
        return xml, 200, 1, body


def _stored_list(airline):
    """Build the passenger_list document the feeder stores for one carrier."""
    raw = {
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR"}},
        "PassengerInfoList": {
            "PassengerInfo": {
                "Name_Details": {"LastName": LAST, "FirstName": FIRST},
                "PNRLocator": PNR,
            }
        },
    }
    return convert_passenger_list(raw, airline, FLIGHT, DATE, ORIGIN)


def _live_client_path(airline):
    """Run the real ``fetchPassengerDetail`` and return its request path.

    ``airline`` is ``None`` when the passenger sheet calls the function with
    only flight number, record locator, origin, and date.
    """
    script = """
import { fetchPassengerDetail } from "./frontend/lib/api.ts";
const calls = [];
globalThis.fetch = async (url) => {
  calls.push(String(url));
  return { ok: true, json: async () => ({ passengers: [] }) };
};
const airline = process.env.DETAIL_AIRLINE;
if (airline === "OMIT") {
  await fetchPassengerDetail(
    process.env.DETAIL_FLIGHT,
    process.env.DETAIL_PNR,
    process.env.DETAIL_ORIGIN,
    process.env.DETAIL_DATE,
  );
} else {
  await fetchPassengerDetail(
    process.env.DETAIL_FLIGHT,
    process.env.DETAIL_PNR,
    process.env.DETAIL_ORIGIN,
    process.env.DETAIL_DATE,
    airline,
  );
}
const url = new URL(calls[0]);
process.stdout.write(url.pathname + url.search);
"""
    env = os.environ.copy()
    env.update({
        "DETAIL_AIRLINE": "OMIT" if airline is None else airline,
        "DETAIL_FLIGHT": FLIGHT,
        "DETAIL_PNR": PNR,
        "DETAIL_ORIGIN": ORIGIN,
        "DETAIL_DATE": DATE,
        "NODE_NO_WARNINGS": "1",
    })
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


@pytest.fixture()
def sabre_env(monkeypatch):
    monkeypatch.setenv("SABRE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SABRE_CPAID", "PLACEHOLDER")
    monkeypatch.setenv("SABRE_USERNAME", "placeholder-user")
    monkeypatch.setenv("SABRE_PASSWORD", "placeholder-password")
    monkeypatch.setenv("SABRE_PSEUDO_CITY_CODE", "placeholder-pcc")
    monkeypatch.setenv("SABRE_ORGANIZATION", "placeholder-org")
    monkeypatch.setenv("SABRE_DOMAIN", "placeholder-domain")


def _client(monkeypatch, docs, docs_by_airline):
    transport = _SabreTransport(docs_by_airline)
    monkeypatch.setattr(passengers, "get_db", lambda: _FakeDb(docs))
    monkeypatch.setattr(SabreClient, "_post", lambda self, action, body, timeout=30, endpoint=None: transport(action, body, timeout, endpoint))
    app = FastAPI()
    app.include_router(passengers.router)
    return TestClient(app), transport


def _assert_selected_documents(payload, airline):
    assert payload["airline"] == airline
    assert payload["flightNumber"] == FLIGHT
    assert payload["origin"] == ORIGIN
    assert payload["departureDate"] == DATE
    passenger = payload["passengers"][0]
    assert passenger["lastName"] == LAST
    assert passenger["firstName"] == FIRST
    assert passenger["pnr"] == PNR
    assert passenger["docsData"] == SELECTED_DOCUMENT
    assert passenger["docsData"]["documentNumber"] != "GF2000002"


def _assert_itinerary(body, airline):
    assert _tag(body, "Airline") == airline
    assert _tag(body, "Flight") == FLIGHT
    assert _tag(body, "DepartureDate") == DATE
    assert _tag(body, "Origin") == ORIGIN
    assert _tag(body, "LastName") == LAST
    assert _tag(body, "FirstName") == FIRST
    assert _tag(body, "PNRLocator") == PNR


def test_sheet_client_queries_stored_carrier_not_gulf_air(monkeypatch, sabre_env):
    """The passenger sheet's detail request must return the stored carrier's documents."""
    stored = _stored_list(STORED_AIRLINE)
    assert stored["airline"] == STORED_AIRLINE
    client, transport = _client(
        monkeypatch,
        [stored],
        {STORED_AIRLINE: SELECTED_DOCS, "GF": OTHER_DOCS},
    )
    path = _live_client_path(None)
    response = client.get(path)

    assert response.status_code == 200, response.text
    assert len(transport.passenger_bodies) == 1
    _assert_itinerary(transport.passenger_bodies[0], STORED_AIRLINE)
    _assert_selected_documents(response.json(), STORED_AIRLINE)


def test_explicit_gulf_air_query_cannot_override_stored_carrier(monkeypatch, sabre_env):
    """A caller who sends airline=GF still gets the carrier saved on the passenger list."""
    stored = _stored_list(STORED_AIRLINE)
    client, transport = _client(
        monkeypatch,
        [stored],
        {STORED_AIRLINE: SELECTED_DOCS, "GF": OTHER_DOCS},
    )
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
    )

    assert response.status_code == 200, response.text
    assert len(transport.passenger_bodies) == 1
    _assert_itinerary(transport.passenger_bodies[0], STORED_AIRLINE)
    _assert_selected_documents(response.json(), STORED_AIRLINE)


def test_client_supplied_other_carrier_cannot_override_stored_carrier(monkeypatch, sabre_env):
    stored = _stored_list(STORED_AIRLINE)
    client, transport = _client(
        monkeypatch,
        [stored],
        {STORED_AIRLINE: SELECTED_DOCS, "GF": OTHER_DOCS, "BA": OTHER_DOCS},
    )
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "BA"},
    )

    assert response.status_code == 200, response.text
    assert len(transport.passenger_bodies) == 1
    _assert_itinerary(transport.passenger_bodies[0], STORED_AIRLINE)
    _assert_selected_documents(response.json(), STORED_AIRLINE)


def test_omitted_airline_query_uses_stored_carrier(monkeypatch, sabre_env):
    stored = _stored_list(STORED_AIRLINE)
    client, transport = _client(
        monkeypatch,
        [stored],
        {STORED_AIRLINE: SELECTED_DOCS, "GF": OTHER_DOCS},
    )
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE},
    )

    assert response.status_code == 200, response.text
    assert len(transport.passenger_bodies) == 1
    _assert_itinerary(transport.passenger_bodies[0], STORED_AIRLINE)
    _assert_selected_documents(response.json(), STORED_AIRLINE)


def test_gulf_air_passenger_list_still_queries_gulf_air(monkeypatch, sabre_env):
    stored = _stored_list("GF")
    assert stored["airline"] == "GF"
    client, transport = _client(
        monkeypatch,
        [stored],
        {"GF": SELECTED_DOCS},
    )
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE},
    )

    assert response.status_code == 200, response.text
    assert len(transport.passenger_bodies) == 1
    _assert_itinerary(transport.passenger_bodies[0], "GF")
    _assert_selected_documents(response.json(), "GF")


@pytest.mark.parametrize("blank", ["missing", "empty"])
def test_missing_stored_airline_does_not_query_gulf_air(monkeypatch, sabre_env, blank):
    stored = _stored_list(STORED_AIRLINE)
    if blank == "missing":
        stored.pop("airline")
    else:
        stored["airline"] = ""
    client, transport = _client(
        monkeypatch,
        [stored],
        {"GF": OTHER_DOCS, STORED_AIRLINE: SELECTED_DOCS},
    )
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
    )

    assert response.status_code >= 400, response.text
    assert transport.passenger_bodies == []
    assert "GF2000002" not in response.text
    assert "WY1000001" not in response.text
