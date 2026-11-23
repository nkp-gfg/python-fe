"""Carrier labels must follow the airline stored for a flight.

These tests fail while the comparison column, the comparison passenger-tree
heading, and the operations workbench title print a hardcoded GF prefix for a
departure whose stored airline is something else. They pass once those
headings use the airline on the loaded flight.

The analytics filter chip at frontend/app/analytics/page.tsx also hardcodes GF,
but that page passes fetchFlights straight to useQuery. React Query then calls
it with a query context object, so the request is /flights?date=[object Object]
and the stored flight never reaches the chip. That surface is not covered here.

Run:
    python3 -m pip install -r requirements.txt mongomock httpx
    cd frontend && npm install
    python3 backend/tests/test_comparison_carrier_label.py

The Sabre HTTP client is replaced with responses parsed by the real SOAP
parser. Placeholder credentials are names only; no live service is contacted.
"""

import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "HDQ")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:9")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:9/0")

import mongomock
import uvicorn
from mongomock.collection import Collection

import backend.api.database as database
import backend.sabre.client as sabre_client

API_PORT = 8765
API_URL = f"http://127.0.0.1:{API_PORT}"

# mongomock 4.3.0 lists $reduce but raises NotImplementedError for it
# (mongomock/aggregate.py _handle_array_operator). list_flights uses $reduce
# only to count passengers. The carrier under test comes from the flight_status
# aggregation, which is left intact. Dropping that one stage lets the list
# endpoint return the stored airline.
_ORIGINAL_AGGREGATE = Collection.aggregate


def _aggregate_without_unimplemented_reduce(self, pipeline, session=None, **kwargs):
    if getattr(self, "name", None) == "passenger_list":
        pipeline = [stage for stage in pipeline if "$addFields" not in stage]
    return _ORIGINAL_AGGREGATE(self, pipeline, session=session, **kwargs)


Collection.aggregate = _aggregate_without_unimplemented_reduce

MEMORY_DB = mongomock.MongoClient().get_database("falconeye")
database._db = MEMORY_DB
database.get_db = lambda: MEMORY_DB

_FLIGHT_STATUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>XX</Airline>
          <Flight>0000</Flight>
          <Origin>XXX</Origin>
          <DepartureDate>2026-11-20</DepartureDate>
          <ScheduledDepartureDate>2026-11-20</ScheduledDepartureDate>
          <FlightStatus>OPEN</FlightStatus>
          <AircraftType>32N</AircraftType>
          <AircraftRegistration>A7-TEST</AircraftRegistration>
          <DepartureGate>12</DepartureGate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""

_PASSENGER_LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Destination>BAH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <PNRLocator>ABC123</PNRLocator>
          <Name_Details>
            <LastName>DOE</LastName>
            <FirstName>JANE</FirstName>
          </Name_Details>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""

_SESSION_XML = (
    "<wsse:BinarySecurityToken>placeholder-session</wsse:BinarySecurityToken>"
)
_EMPTY_ENVELOPE = (
    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
    "<soap-env:Envelope xmlns:soap-env=\"http://schemas.xmlsoap.org/soap/envelope/\">"
    "<soap-env:Body/></soap-env:Envelope>"
)


class _SoapResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _soap_post(url, data=None, headers=None, timeout=None):
    """Stand in for the remote Sabre socket. Parsing stays in SabreClient."""
    action = (headers or {}).get("SOAPAction", "")
    if action == "SessionCreateRQ":
        return _SoapResponse(_SESSION_XML)
    if action == "ACS_FlightDetailRQ":
        return _SoapResponse(_FLIGHT_STATUS_XML)
    if action == "GetPassengerListRQ":
        return _SoapResponse(_PASSENGER_LIST_XML)
    if action == "SessionCloseRQ":
        return _SoapResponse(_EMPTY_ENVELOPE)
    return _SoapResponse("unavailable", 500)


sabre_client.requests.post = _soap_post

from backend.api.main import app  # noqa: E402


def _request(method, path, body=None):
    data = None if body is None else body.encode()
    request = urllib.request.Request(
        API_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _wait_until_ready():
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            status, _ = _request("GET", "/flights")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("API did not become ready")


def _ingest(airline, flight_number, origin):
    import json

    payload = json.dumps({
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": "2026-11-20",
        "departureDateTime": "2026-11-20T08:00:00",
    })
    return _request("POST", "/flights/ingest", payload)


def _assert_dashboard_airline(flight_number, origin, expected_airline):
    import json

    status, raw = _request(
        "GET",
        f"/flights/{flight_number}/dashboard?origin={origin}&date=2026-11-20",
    )
    assert status == 200, raw
    body = json.loads(raw)
    airline = body["flightStatus"]["airline"]
    assert airline == expected_airline, body["flightStatus"]
    # The sample SOAP body says XX. The stored airline is the caller's.
    assert airline != "XX"
    return body


def main():
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_ready()
        # Later Sabre calls are refused, so ingest reports a conflict, but the
        # flight-status and passenger-list documents are already stored.
        ba_status, ba_body = _ingest("BA", "2057", "LHR")
        gf_status, gf_body = _ingest("GF", "3001", "BAH")
        assert ba_status in (200, 409), ba_body
        assert gf_status in (200, 409), gf_body

        _assert_dashboard_airline("2057", "LHR", "BA")
        _assert_dashboard_airline("3001", "BAH", "GF")

        import json
        list_status, list_raw = _request("GET", "/flights")
        assert list_status == 200, list_raw
        listed = {
            (item["airline"], item["flightNumber"], item["origin"])
            for item in json.loads(list_raw)
        }
        assert ("BA", "2057", "LHR") in listed, listed
        assert ("GF", "3001", "BAH") in listed, listed

        env = os.environ.copy()
        env["NEXT_PUBLIC_API_URL"] = API_URL
        import subprocess

        completed = subprocess.run(
            ["npx", "vitest", "run", "tests/carrier-label.test.tsx"],
            cwd=os.path.join(ROOT, "frontend"),
            env=env,
        )
        if completed.returncode != 0:
            sys.exit(completed.returncode)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
