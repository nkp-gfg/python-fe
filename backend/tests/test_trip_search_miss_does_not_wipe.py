"""A Trip Search miss must not replace the live reservation list.

Flight ingestion copies the caller-supplied departureDateTime into
Trip_SearchRQ as an exact DepartureDateTime Is match (SearchType ACTIVE).
The Trip Search user guide says a search that matches no PNRs returns an
empty list, and a successful sample response is a Trip_SearchRS body with
Success plus ReservationsList. These tests stand in for Sabre at the HTTP
boundary with that empty success body (and with a shorter list) and require
the live reservations, the dashboard, and the change history to keep the
bookings that were already stored.

They fail while an empty or shorter success is saved as the newest
reservations document and recorded as RESERVATION_REMOVED.
"""

import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# Placeholders only. SabreClient reads these when a session is opened.
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "BAH"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"

import mongomock

from backend.api.main import app
from backend.api.routes import flights as flights_routes
from backend.celery_app import app as celery_app
from backend.feeder import storage
from backend.sabre.client import SabreClient

DATE = "2026-11-18"
# Local departure carried on the stored segment. Caller timestamps below are
# not this value: the Today page sends scheduledDepartureUtc (or midnight
# when that field is missing), and the manual ingest form sends a typed time.
LOCAL_DEPARTURE = "2026-11-18T07:00:00"
FETCHED_AT = "2020-01-01T00:00:00Z"

SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <wsse:BinarySecurityToken xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">placeholder-session-token</wsse:BinarySecurityToken>
  </soap-env:Body>
</soap-env:Envelope>
"""

FLIGHT_STATUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <FlightStatus>OPEN</FlightStatus>
          <ScheduledDepartureDate>2026-11-18</ScheduledDepartureDate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

PASSENGER_LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>SEED</LastName>
            <FirstName>PAX</FirstName>
          </Name_Details>
          <PNRLocator>BOOK01</PNRLocator>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

# Shape from sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-19.xml
# (soap-env:Envelope, Trip_SearchRS, Success, ReservationsList) with
# NumberResults="0". xmltodict 0.13.0 keeps that element as a dict of
# attributes and no Reservation children.
EMPTY_TRIP_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="0" NumberPages="0" TotalResults="0"/>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

SHORTER_TRIP_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="1" NumberPages="1" TotalResults="1">
        <Reservations>
          <Reservation Locator="KEEP01">
            <GetReservationRS>
              <Reservation numberInParty="1" numberOfInfants="0">
                <BookingDetails>
                  <CreationTimestamp>2026-11-01T00:00:00</CreationTimestamp>
                </BookingDetails>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>KEPT</LastName>
                      <FirstName>ONE</FirstName>
                    </Passenger>
                  </Passengers>
                  <EmailAddresses>
                    <EmailAddress><Address>kept@example.com</Address></EmailAddress>
                  </EmailAddresses>
                  <PhoneNumbers>
                    <PhoneNumber><Number>5550101</Number></PhoneNumber>
                  </PhoneNumbers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    def publish(self, channel, message):
        return 1

    def scan(self, cursor=0, match=None, count=None):
        return 0, []


class SabreStandIn:
    """HTTP stand-in for Sabre. Only Trip_SearchRS varies; the other actions
    return the minimal success bodies the feeder already converts, or HTTP 500
    for the calls that are not part of this finding (trip reports, schedule).
    """

    def __init__(self, trip_search_xml):
        self.trip_search_xml = trip_search_xml
        self.trip_requests = []
        self.actions = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        self.actions.append(action)
        body = data.decode() if isinstance(data, bytes) else (data or "")
        if action == "SessionCreateRQ":
            return _Response(SESSION_XML)
        if action == "SessionCloseRQ":
            return _Response("<ok/>")
        if action == "ACS_FlightDetailRQ":
            return _Response(FLIGHT_STATUS_XML)
        if action == "GetPassengerListRQ":
            return _Response(PASSENGER_LIST_XML)
        if action == "Trip_SearchRQ":
            self.trip_requests.append(body)
            return _Response(self.trip_search_xml)
        return _Response("unavailable", status_code=500)


def _booking(pnr, phone, email, party, form_of_payment):
    return {
        "pnr": pnr,
        "numberInParty": party,
        "numberOfInfants": 0,
        "createdAt": "2026-11-01T00:00:00",
        "updatedAt": "2026-11-01T00:00:00",
        "formOfPayment": form_of_payment,
        "passengers": [
            {"lastName": "PASSENGER", "firstName": "TEST", "nameId": "01.01"}
        ],
        "segments": [
            {
                "departureAirport": "BAH",
                "arrivalAirport": "DXB",
                "departureDate": LOCAL_DEPARTURE,
                "flightNumber": "4242",
            }
        ],
        "emails": [email],
        "phones": [{"number": phone, "type": "M"}],
    }


def _seed(db, flight_number, bookings):
    reservation_doc = {
        "airline": "GF",
        "flightNumber": flight_number,
        "departureAirport": "BAH",
        "departureDate": DATE,
        "fetchedAt": FETCHED_AT,
        "totalResults": len(bookings),
        "reservations": bookings,
    }
    db["reservations"].insert_one(dict(reservation_doc))
    db["snapshots"].insert_one(
        {
            "snapshotId": f"seed-{flight_number}",
            "requestId": f"seed-req-{flight_number}",
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": "BAH",
            "departureDate": DATE,
            "snapshotType": "reservations",
            "capturedAt": FETCHED_AT,
            "sequenceNumber": 1,
            "checksum": "seed",
            "data": reservation_doc,
        }
    )


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient()["falconeye"]
    redis = _FakeRedis()
    flights_routes._dashboard_cache.clear()
    storage.init_db(db)
    monkeypatch.setattr("backend.api.database.get_db", lambda: db)
    monkeypatch.setattr("backend.api.main.get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.reservations.get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: db)
    monkeypatch.setattr("backend.api.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("backend.api.main.get_redis", lambda: redis)
    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0.0)
    celery_app.conf.task_ignore_result = True
    celery_app.conf.task_store_eager_result = False

    stand_in = SabreStandIn(EMPTY_TRIP_SEARCH_XML)
    monkeypatch.setattr("backend.sabre.client.requests.post", stand_in)

    def sync_delay(job_id, payloads):
        from backend.tasks.ingestion import run_batch_ingestion

        run_batch_ingestion.apply(args=(job_id, payloads), throw=True)

    monkeypatch.setattr(
        "backend.tasks.ingestion.run_batch_ingestion.delay", sync_delay
    )
    return db, stand_in


def _ingest_body(flight_number, departure_datetime):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": DATE,
        "departureDateTime": departure_datetime,
    }


def _assert_bookings_remain(client, db, flight_number, expected):
    """expected: list of dicts with pnr, phone, email, party, formOfPayment."""
    listed = client.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": "BAH", "date": DATE},
    )
    assert listed.status_code == 200, listed.text
    by_pnr = {row["pnr"]: row for row in listed.json()["reservations"]}

    dashboard = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": "BAH", "date": DATE},
    )
    assert dashboard.status_code == 200, dashboard.text
    dash = dashboard.json()
    payments = (dash.get("insights") or {}).get("paymentMethods") or {}
    recency = (dash.get("insights") or {}).get("reservationRecency") or {}
    change_summary = dash.get("changeSummary") or {}

    removed = list(
        db["changes"].find(
            {"flightNumber": flight_number, "changeType": "RESERVATION_REMOVED"}
        )
    )
    problems = []
    for booking in expected:
        live = by_pnr.get(booking["pnr"])
        if live is None:
            problems.append(f"{booking['pnr']} missing from reservations API")
            continue
        phones = [p.get("number") for p in live.get("phones") or []]
        if booking["phone"] not in phones:
            problems.append(f"{booking['pnr']} phone lost: {phones}")
        if booking["email"] not in (live.get("emails") or []):
            problems.append(f"{booking['pnr']} email lost: {live.get('emails')}")
        if live.get("numberInParty") != booking["party"]:
            problems.append(
                f"{booking['pnr']} party {live.get('numberInParty')} != {booking['party']}"
            )
        if payments.get(booking["formOfPayment"]) != 1:
            problems.append(
                f"{booking['pnr']} form of payment {booking['formOfPayment']} "
                f"not on dashboard: {payments}"
            )
    if recency.get("totalReservations") != len(expected):
        problems.append(
            f"dashboard totalReservations={recency.get('totalReservations')} "
            f"expected {len(expected)}"
        )
    if change_summary.get("RESERVATION_REMOVED", 0) != 0 or removed:
        problems.append(
            f"removal events recorded: summary={change_summary.get('RESERVATION_REMOVED', 0)} "
            f"docs={[doc.get('oldValue') for doc in removed]}"
        )

    newest = db["reservations"].find_one(
        {"flightNumber": flight_number, "departureAirport": "BAH", "departureDate": DATE},
        sort=[("fetchedAt", -1)],
    )
    if newest and newest.get("fetchedAt") != FETCHED_AT and problems:
        problems.append(f"live fetchedAt advanced to {newest.get('fetchedAt')}")

    assert not problems, "; ".join(problems)


def _run_client():
    return TestClient(app)


@pytest.mark.parametrize(
    "flight_number,departure_datetime",
    [
        ("4242", "2026-11-18T04:00:00Z"),
        ("4243", "2026-11-18T00:00:00"),
        ("4244", "2026-11-18T09:30:00"),
    ],
)
def test_unauthenticated_ingest_keeps_reservations_when_trip_search_is_empty(
    world, flight_number, departure_datetime
):
    """POST /flights/ingest with no credentials.

    4242 uses a UTC timestamp, the value Today sends as scheduledDepartureUtc.
    4243 uses midnight on the departure date, the Today fallback when that
    field is absent. 4244 uses a typed time from the manual ingest form.
    None of them is the stored local departure. Sabre answers with Success
    and an empty ReservationsList.
    """
    db, stand_in = world
    expected = [
        {
            "pnr": "BOOK01",
            "phone": "5550100",
            "email": "passenger@example.com",
            "party": 2,
            "formOfPayment": "CASH",
        }
    ]
    _seed(
        db,
        flight_number,
        [
            _booking(
                "BOOK01", "5550100", "passenger@example.com", 2, "CASH"
            )
        ],
    )
    body = _ingest_body(flight_number, departure_datetime)
    with _run_client() as client:
        response = client.post("/flights/ingest", json=body)
        assert response.status_code not in (401, 403), response.text
        assert stand_in.trip_requests, (
            f"Trip_SearchRQ was not sent; ingest status {response.status_code}: {response.text}"
        )
        try:
            _assert_bookings_remain(client, db, flight_number, expected)
        except AssertionError as exc:
            sent = stand_in.trip_requests[0]
            marker = sent.find("DepartureDateTime")
            snippet = sent[max(0, marker - 10): marker + 90]
            raise AssertionError(f"{exc}; Trip_SearchRQ had: {snippet}") from exc


def test_batch_ingest_keeps_reservations_when_trip_search_is_empty(world):
    """POST /flights/ingest/batch runs the same feeder through the worker task."""
    db, stand_in = world
    flight_number = "4245"
    expected = [
        {
            "pnr": "BOOK01",
            "phone": "5550100",
            "email": "passenger@example.com",
            "party": 2,
            "formOfPayment": "CASH",
        }
    ]
    _seed(
        db,
        flight_number,
        [_booking("BOOK01", "5550100", "passenger@example.com", 2, "CASH")],
    )
    with _run_client() as client:
        response = client.post(
            "/flights/ingest/batch",
            json={"flights": [_ingest_body(flight_number, "2026-11-18T04:00:00Z")]},
        )
        assert response.status_code not in (401, 403), response.text
        assert stand_in.trip_requests, response.text
        _assert_bookings_remain(client, db, flight_number, expected)


def test_shorter_trip_search_does_not_drop_missing_bookings(world):
    """A success body with fewer PNRs than the previous snapshot must not
    record the missing booking as removed or drop it from the live list.
    """
    db, stand_in = world
    stand_in.trip_search_xml = SHORTER_TRIP_SEARCH_XML
    flight_number = "4246"
    expected = [
        {
            "pnr": "KEEP01",
            "phone": "5550101",
            "email": "kept@example.com",
            "party": 1,
            "formOfPayment": "VISA",
        },
        {
            "pnr": "GONE02",
            "phone": "5550102",
            "email": "gone@example.com",
            "party": 3,
            "formOfPayment": "CASH",
        },
    ]
    _seed(
        db,
        flight_number,
        [
            _booking("KEEP01", "5550101", "kept@example.com", 1, "VISA"),
            _booking("GONE02", "5550102", "gone@example.com", 3, "CASH"),
        ],
    )
    with _run_client() as client:
        response = client.post(
            "/flights/ingest",
            json=_ingest_body(flight_number, "2026-11-18T04:00:00Z"),
        )
        assert response.status_code not in (401, 403), response.text
        assert stand_in.trip_requests, response.text
        _assert_bookings_remain(client, db, flight_number, expected)


def test_trip_search_http_error_keeps_reservations(world, monkeypatch):
    """A Trip Search transport failure is already not a successful snapshot."""
    db, stand_in = world
    original = stand_in.__call__

    def routed(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        if action == "Trip_SearchRQ":
            body = data.decode() if isinstance(data, bytes) else (data or "")
            stand_in.trip_requests.append(body)
            stand_in.actions.append(action)
            return _Response("unavailable", status_code=500)
        return original(url, data=data, headers=headers, timeout=timeout)

    monkeypatch.setattr("backend.sabre.client.requests.post", routed)

    flight_number = "4247"
    expected = [
        {
            "pnr": "BOOK01",
            "phone": "5550100",
            "email": "passenger@example.com",
            "party": 2,
            "formOfPayment": "CASH",
        }
    ]
    _seed(
        db,
        flight_number,
        [_booking("BOOK01", "5550100", "passenger@example.com", 2, "CASH")],
    )
    with _run_client() as client:
        client.post(
            "/flights/ingest",
            json=_ingest_body(flight_number, "2026-11-18T04:00:00Z"),
        )
        assert stand_in.trip_requests
        _assert_bookings_remain(client, db, flight_number, expected)


def test_direct_feeder_today_payload_keeps_reservations(world):
    """The Today page builds departureDateTime from scheduledDepartureUtc.

    Run the feeder with that payload directly so a later authentication
    wrapper on the route cannot hide the wipe.
    """
    db, stand_in = world
    from backend.feeder.runner import run_feeder

    flight_number = "4248"
    # datetime.isoformat() of an aware UTC value, which is what otp._ts returns
    # for scheduled_departure_utc and what Today puts in departureDateTime.
    scheduled_departure_utc = datetime(
        2026, 11, 18, 4, 0, tzinfo=timezone.utc
    ).isoformat()
    assert scheduled_departure_utc != LOCAL_DEPARTURE
    expected = [
        {
            "pnr": "BOOK01",
            "phone": "5550100",
            "email": "passenger@example.com",
            "party": 2,
            "formOfPayment": "CASH",
        }
    ]
    _seed(
        db,
        flight_number,
        [_booking("BOOK01", "5550100", "passenger@example.com", 2, "CASH")],
    )
    run_feeder(
        [
            {
                "airline": "GF",
                "flightNumber": flight_number,
                "origin": "BAH",
                "departureDate": DATE,
                "departureDateTime": scheduled_departure_utc,
            }
        ]
    )
    assert stand_in.trip_requests
    sent = stand_in.trip_requests[0]
    with _run_client() as client:
        try:
            _assert_bookings_remain(client, db, flight_number, expected)
        except AssertionError as exc:
            raise AssertionError(f"{exc}; request was: {sent[sent.find('DepartureDateTime')-20:sent.find('DepartureDateTime')+80]}") from exc
