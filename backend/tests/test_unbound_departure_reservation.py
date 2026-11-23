"""Departure date and departure timestamp must name the same local day.

These tests exercise the live ingestion path: the HTTP body is validated by
SabreIngestRequest, run_feeder asks Sabre, convert_reservations stores the
Trip Search result, and the flight board reads the newest reservations
document for the calendar date.

They fail while a caller can put one calendar day in departureDate and a
different local day in departureDateTime. In that state the feeder stores the
Trip Search bookings for the timestamp under the other date, and the board
serves that document. They pass once a mismatched pair is rejected before any
Sabre call or write, and a matching pair is stored and queried as one local
departure.

Run:
    PYTHONPATH=. pytest backend/tests/test_unbound_departure_reservation.py -q
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "placeholder"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9"
os.environ["POSTGRES_URI"] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"

import re
import time
from datetime import datetime, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

from backend.api import database
from backend.api.main import app
from backend.api.routes import flights as flights_routes
from backend.api.routes import ingestion as ingestion_routes
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre import client as sabre_client
from backend.tasks.ingestion import run_batch_ingestion

_STORED_COLLECTIONS = (
    "sabre_requests",
    "snapshots",
    "changes",
    "flights",
    "flight_status",
    "passenger_list",
    "reservations",
    "trip_reports",
    "flight_schedules",
)

_TRIP_TIME = re.compile(r'<DepartureDateTime Is="([^"]*)"')


def _envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{inner}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _calendar_day(value: str) -> str:
    """Calendar day of a departure timestamp, using that value's own offset."""
    return datetime.fromisoformat(value).date().isoformat()


def _trip_search_rs(queried: str) -> str:
    """Trip_SearchRS whose booking identity is the timestamp that was queried.

    The element names are the ones convert_reservations reads after _strip_ns
    (backend/feeder/converter.py). Attributes use xmltodict's default '@' prefix.
    """
    day = _calendar_day(queried)
    if day != "2026-04-02":
        pnr, last, gender, nationality, dob = "OTHER1", "OTHER", "M", "ZZ", "1990-02-02"
        email, phone, payment, doca = (
            "other-day@example.test",
            "+15550001999",
            "CASH OTHER-DAY",
            "9 OTHER STREET",
        )
    else:
        pnr, last, gender, nationality, dob = "LOCAL1", "LOCAL", "F", "AA", "1980-01-01"
        email, phone, payment, doca = (
            "local-day@example.test",
            "+15550001001",
            "CASH LOCAL-DAY",
            "1 LOCAL STREET",
        )
    # The queried timestamp is also copied into the payment text so a stored
    # document shows which Trip Search response produced it.
    payment = f"{payment} {queried}"
    return _envelope(f"""
        <Trip_SearchRS>
          <ReservationsList NumberResults="1">
            <Reservations>
              <Reservation Locator="{pnr}">
                <GetReservationRS>
                  <Reservation numberInParty="1" numberOfInfants="0">
                    <BookingDetails>
                      <CreationTimestamp>2026-01-01T00:00:00</CreationTimestamp>
                    </BookingDetails>
                    <PassengerReservation>
                      <Passengers>
                        <Passenger>
                          <LastName>{last}</LastName>
                          <FirstName>PAX</FirstName>
                          <SpecialRequests>
                            <APISRequest>
                              <DOCSEntry>
                                <Gender>{gender}</Gender>
                                <DateOfBirth>{dob}</DateOfBirth>
                                <DocumentNationalityCountry>{nationality}</DocumentNationalityCountry>
                              </DOCSEntry>
                              <DOCAEntry><FreeText>{doca}</FreeText></DOCAEntry>
                            </APISRequest>
                          </SpecialRequests>
                        </Passenger>
                      </Passengers>
                      <EmailAddresses>
                        <EmailAddress><Address>{email}</Address></EmailAddress>
                      </EmailAddresses>
                      <PhoneNumbers>
                        <PhoneNumber><Number>{phone}</Number><Type>M</Type></PhoneNumber>
                      </PhoneNumbers>
                    </PassengerReservation>
                    <POS><Source AgentSine="WEB"/></POS>
                    <OpenReservationElements>
                      <OpenReservationElement type="FP">
                        <FormOfPayment><Other><Text>{payment}</Text></Other></FormOfPayment>
                      </OpenReservationElement>
                    </OpenReservationElements>
                  </Reservation>
                </GetReservationRS>
              </Reservation>
            </Reservations>
          </ReservationsList>
        </Trip_SearchRS>
    """)


def _response_for(action: str, request_xml: str) -> str:
    if action == "SessionCreateRQ":
        return _envelope(
            "<wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>"
        )
    if action == "SessionCloseRQ":
        return _envelope("<SessionCloseRS/>")
    if action == "ACS_FlightDetailRQ":
        return _envelope(
            "<ns3:ACS_FlightDetailRS>"
            "<ItineraryResponseList><ItineraryInfoResponse>"
            "<FlightStatus>OPEN</FlightStatus>"
            "</ItineraryInfoResponse></ItineraryResponseList>"
            "</ns3:ACS_FlightDetailRS>"
        )
    if action == "GetPassengerListRQ":
        return _envelope(
            "<GetPassengerListRS>"
            "<PassengerInfoList><PassengerInfo>"
            "<Name_Details><LastName>LOCAL</LastName><FirstName>PAX</FirstName></Name_Details>"
            "<PNRLocator>LOCAL1</PNRLocator>"
            "<PassengerID>1</PassengerID>"
            "<Cabin>Y</Cabin>"
            "<PassengerType>F</PassengerType>"
            "</PassengerInfo></PassengerInfoList>"
            "</GetPassengerListRS>"
        )
    if action == "Trip_SearchRQ":
        match = _TRIP_TIME.search(request_xml)
        assert match, request_xml
        return _trip_search_rs(match.group(1))
    if action == "Trip_ReportsRQ":
        # An empty element parses as None. A child element keeps PassengerList
        # a dict so convert_trip_report can read Passenger.
        return _envelope(
            "<Trip_ReportsRS><PassengerList><Unused>0</Unused></PassengerList></Trip_ReportsRS>"
        )
    if action == "VerifyFlightDetailsLLSRQ":
        return _envelope(
            "<VerifyFlightDetailsRS>"
            '<ApplicationResults status="Complete"/>'
            "<OriginDestinationOptions><OriginDestinationOption>"
            '<FlightSegment DepartureDateTime="2026-04-02T08:00" ArrivalDateTime="2026-04-02T12:00">'
            '<OriginLocation LocationCode="BAH"/>'
            '<DestinationLocation LocationCode="DXB"/>'
            '<MarketingAirline Code="GF" FlightNumber="9101"/>'
            "</FlightSegment>"
            "</OriginDestinationOption></OriginDestinationOptions>"
            "</VerifyFlightDetailsRS>"
        )
    raise AssertionError(f"unexpected Sabre action {action}")


class _SabreHttpResponse:
    def __init__(self, text: str):
        self.status_code = 200
        self.text = text


@pytest.fixture()
def world(monkeypatch):
    posts: list[tuple[str, str]] = []

    def fake_post(url, data=None, headers=None, timeout=30, **kwargs):
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        posts.append((action, body))
        return _SabreHttpResponse(_response_for(action, body))

    mongo = mongomock.MongoClient()
    db = mongo["falconeye"]
    database._client = mongo
    database._db = db
    storage.init_db(db)
    flights_routes._dashboard_cache.clear()
    monkeypatch.setattr(sabre_client.requests, "post", fake_post)

    with TestClient(app) as http:
        yield {"http": http, "db": db, "posts": posts}

    flights_routes._dashboard_cache.clear()
    database._client = None
    database._db = None
    storage._db = None
    storage._client = None


def _wait_for_next_stored_second() -> None:
    """fetchedAt is stored at one-second resolution, so the next ingest must
    start on a later second for the board's newest-document sort to be decided."""
    current = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    while datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") == current:
        time.sleep(0.05)


def _payload(flight_number: str, departure_date: str, departure_datetime: str) -> dict:
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": departure_date,
        "departureDateTime": departure_datetime,
    }


def _counts(db) -> dict[str, int]:
    return {name: len(list(db[name].find())) for name in _STORED_COLLECTIONS}


def _trip_queries(posts) -> list[str]:
    found = []
    for action, body in posts:
        if action != "Trip_SearchRQ":
            continue
        match = _TRIP_TIME.search(body)
        assert match, body
        found.append(match.group(1))
    return found


def _board(http, flight_number: str, departure_date: str) -> tuple[dict, dict, dict]:
    flights_routes._dashboard_cache.clear()
    reservations = http.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": "BAH", "date": departure_date},
    )
    assert reservations.status_code == 200, reservations.text
    one = http.get(
        f"/flights/{flight_number}/reservations/LOCAL1",
        params={"date": departure_date},
    )
    dashboard = http.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": "BAH", "date": departure_date},
    )
    assert dashboard.status_code == 200, dashboard.text
    journey = http.get(
        f"/flights/{flight_number}/phase-journey",
        params={"origin": "BAH", "date": departure_date},
    )
    assert journey.status_code == 200, journey.text
    return reservations.json(), one.json(), dashboard.json() | {"_journey": journey.json()}


def test_matching_local_departure_is_the_booking_list(world):
    """A timestamp on departureDate is queried, stored, and shown as that departure."""
    http, posts = world["http"], world["posts"]
    departure_date = "2026-04-02"
    departure_datetime = "2026-04-02T08:00:00"
    response = http.post(
        "/flights/ingest",
        json=_payload("9101", departure_date, departure_datetime),
    )
    assert response.status_code == 200, response.text

    queried = _trip_queries(posts)
    assert queried == [departure_datetime]
    assert all(_calendar_day(value) == departure_date for value in queried)

    reservations, by_pnr, dashboard = _board(http, "9101", departure_date)
    assert reservations["departureDate"] == departure_date
    booking = reservations["reservations"][0]
    assert booking["emails"] == ["local-day@example.test"]
    assert booking["phones"][0]["number"] == "+15550001001"
    passenger = booking["passengers"][0]
    assert passenger["nationality"] == "AA"
    assert passenger["dateOfBirth"] == "1980-01-01"
    assert passenger["gender"] == "F"
    assert by_pnr["departureDate"] == departure_date
    assert by_pnr["reservations"][0]["emails"] == ["local-day@example.test"]
    assert "CASH LOCAL-DAY 2026-04-02T08:00:00" in dashboard["insights"]["paymentMethods"]
    assert dashboard["analysis"]["nationalityCounts"] == {"AA": 1}
    assert dashboard["_journey"]["phases"][0]["summary"]["totalFemale"] == 1

    stored = world["db"]["reservations"].find_one(
        {"flightNumber": "9101", "departureDate": departure_date},
        sort=[("fetchedAt", -1)],
    )
    assert stored["departureDate"] == departure_date
    assert stored["reservations"][0]["passengers"][0]["docaAddress"] == "1 LOCAL STREET"
    assert _calendar_day(queried[0]) == stored["departureDate"]


def test_same_local_day_with_an_offset_is_one_departure(world):
    """01:00 at +03:00 is still that calendar day, including when UTC is the day before."""
    http, posts = world["http"], world["posts"]
    departure_date = "2026-04-02"
    departure_datetime = "2026-04-02T01:00:00+03:00"
    response = http.post(
        "/flights/ingest",
        json=_payload("9104", departure_date, departure_datetime),
    )
    assert response.status_code == 200, response.text
    queried = _trip_queries(posts)
    assert len(queried) == 1
    assert _calendar_day(queried[0]) == departure_date
    reservations, _, dashboard = _board(http, "9104", departure_date)
    assert reservations["departureDate"] == departure_date
    assert _calendar_day(queried[0]) == reservations["departureDate"]
    assert any(departure_date in key for key in dashboard["insights"]["paymentMethods"])


def test_mismatched_naive_timestamp_is_rejected_before_any_write(world):
    """The other day's bookings must not become the newest document for this date."""
    http, db, posts = world["http"], world["db"], world["posts"]
    departure_date = "2026-04-02"
    aligned = "2026-04-02T08:00:00"
    foreign = "2026-04-01T08:00:00"

    first = http.post("/flights/ingest", json=_payload("9102", departure_date, aligned))
    assert first.status_code == 200, first.text
    after_aligned = _counts(db)
    posts_after_aligned = len(posts)
    _wait_for_next_stored_second()

    second = http.post("/flights/ingest", json=_payload("9102", departure_date, foreign))

    reservations, by_pnr, dashboard = _board(http, "9102", departure_date)
    assert reservations["reservations"][0]["emails"] == ["local-day@example.test"]
    assert reservations["reservations"][0]["phones"][0]["number"] == "+15550001001"
    assert reservations["reservations"][0]["passengers"][0]["nationality"] == "AA"
    assert reservations["reservations"][0]["passengers"][0]["dateOfBirth"] == "1980-01-01"
    foreign_pnr = http.get(
        f"/flights/9102/reservations/OTHER1",
        params={"date": departure_date},
    )
    assert foreign_pnr.status_code == 404
    assert by_pnr["reservations"][0]["emails"] == ["local-day@example.test"]
    payment_keys = " ".join(dashboard["insights"]["paymentMethods"])
    assert "CASH LOCAL-DAY" in payment_keys
    assert "CASH OTHER-DAY" not in payment_keys
    assert "2026-04-01" not in payment_keys
    assert dashboard["analysis"]["nationalityCounts"] == {"AA": 1}
    assert dashboard["_journey"]["phases"][0]["summary"]["totalFemale"] == 1

    newest = db["reservations"].find_one(
        {"flightNumber": "9102", "departureDate": departure_date},
        sort=[("fetchedAt", -1)],
    )
    assert newest["reservations"][0]["emails"] == ["local-day@example.test"]
    assert newest["reservations"][0]["passengers"][0]["docaAddress"] == "1 LOCAL STREET"
    assert second.status_code in (400, 422), second.text
    assert len(posts) == posts_after_aligned
    assert all(foreign not in body for _action, body in posts)
    assert _trip_queries(posts) == [aligned]
    assert _counts(db) == after_aligned


def test_mismatched_offset_timestamp_is_rejected_before_any_call(world):
    http, db, posts = world["http"], world["db"], world["posts"]
    before = _counts(db)
    response = http.post(
        "/flights/ingest",
        json=_payload("9103", "2026-04-02", "2026-04-01T23:00:00+03:00"),
    )
    assert response.status_code in (400, 422), response.text
    assert posts == []
    assert _counts(db) == before
    missing = http.get(
        "/flights/9103/reservations",
        params={"origin": "BAH", "date": "2026-04-02"},
    )
    assert missing.status_code == 404


def test_feeder_rejects_mismatched_dict_before_sabre_or_storage(world):
    """Batch workers and the CLI call run_feeder with the dumped dict, not the model."""
    db, posts = world["db"], world["posts"]
    before = _counts(db)
    payload = _payload("9105", "2026-04-02", "2026-04-01T08:00:00")
    outcome = None
    try:
        outcome = run_feeder([payload])
    except Exception:
        outcome = None
    assert posts == []
    assert _counts(db) == before
    if outcome is not None:
        assert outcome["results"][0]["success"] is False
        assert "reservations" not in outcome["results"][0].get("apis", {}) or (
            outcome["results"][0]["apis"]["reservations"].get("status") != "success"
        )


def test_batch_rejects_one_mismatched_flight_before_queueing(world, monkeypatch):
    queued: list[str] = []

    def reject_job(*_args, **_kwargs):
        queued.append("create_job")
        return {"jobId": "not-queued"}

    def reject_delay(*_args, **_kwargs):
        queued.append("delay")
        return None

    monkeypatch.setattr(ingestion_routes, "create_job", reject_job)
    monkeypatch.setattr(run_batch_ingestion, "delay", reject_delay)

    response = world["http"].post(
        "/flights/ingest/batch",
        json={
            "flights": [
                _payload("9106", "2026-04-02", "2026-04-02T08:00:00"),
                _payload("9107", "2026-04-02", "2026-04-01T08:00:00"),
            ]
        },
    )
    assert response.status_code in (400, 422), response.text
    assert queued == []
    assert world["posts"] == []
    assert _counts(world["db"]) == {name: 0 for name in _STORED_COLLECTIONS}
