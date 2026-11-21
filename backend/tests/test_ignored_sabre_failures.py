"""Spec tests for ignored Sabre failures during flight refresh.

These tests exercise POST /flights/ingest and the batch worker with the real
feeder, Sabre client, converters, storage writes, and job-status code. Sabre's
HTTP response and MongoDB/Redis are replaced by stand-ins at the network
boundary. The stand-ins do not decide success; the product code does.

The tests are expected to fail until trip-report and schedule failures clear
the flight success flag. A refresh must not be reported as completed, and the
previous offload, no-show, or published schedule document must remain the one
the operations board reads.
"""

import copy
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder import storage
from backend.sabre.client import SabreClient

PREVIOUS_FETCHED_AT = "2026-03-18T00:00:00Z"
PREVIOUS_DEPARTURE = "2026-03-19T21:50"
# VerifyFlightDetails stores Sabre's DepartureDateTime as returned (MM-DDTHH:MM).
REPLACEMENT_DEPARTURE = "03-19T23:15"
SEEDED_CANCELLED = ("CANCELLED", "OFFLOAD")
SEEDED_EVER_BOOKED = "GHOST"


class MemoryCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return MemoryCursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


class MemoryCollection:
    def __init__(self):
        self._docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = uuid.uuid4().hex
        self._docs.append(stored)
        doc["_id"] = stored["_id"]
        return SimpleNamespace(inserted_id=stored["_id"])

    def insert_many(self, docs):
        inserted = [self.insert_one(doc).inserted_id for doc in docs]
        return SimpleNamespace(inserted_ids=inserted)

    def _matches(self, doc, filt):
        for key, expected in (filt or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    def _sorted(self, docs, sort):
        ordered = list(docs)
        for field, direction in reversed(sort or []):
            ordered.sort(
                key=lambda doc: (
                    doc.get(field) is None,
                    "" if doc.get(field) is None else doc.get(field),
                ),
                reverse=direction < 0,
            )
        return ordered

    def find(self, filt=None, projection=None, sort=None):
        del projection
        return MemoryCursor(self._sorted(
            [copy.deepcopy(doc) for doc in self._docs if self._matches(doc, filt)],
            sort,
        ))

    def find_one(self, filt=None, sort=None, projection=None):
        matches = list(self.find(filt, projection, sort))
        return matches[0] if matches else None

    def update_one(self, filt, update, upsert=False):
        for doc in self._docs:
            if self._matches(doc, filt):
                self._apply(doc, update, inserted=False)
                return
        if not upsert:
            return
        created = copy.deepcopy(filt)
        self._apply(created, update, inserted=True)
        created.setdefault("_id", uuid.uuid4().hex)
        self._docs.append(created)

    def _apply(self, doc, update, inserted):
        if inserted:
            doc.update(copy.deepcopy(update.get("$setOnInsert", {})))
        doc.update(copy.deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount

    def create_index(self, keys, **kwargs):
        return kwargs.get("name", "index")

    def drop_index(self, name):
        del name

    def index_information(self):
        return {"flight_lookup": {"key": [("flightNumber", 1)]}}


class MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        self._collections.setdefault(name, MemoryCollection())
        return self._collections[name]

    def command(self, name):
        if name == "ping":
            return {"ok": 1}
        raise AssertionError(f"unexpected database command {name}")


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        del ex
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += 1 if self.values.pop(key, None) is not None else 0
        return removed

    def ping(self):
        return True

    def publish(self, channel, message):
        del channel, message
        return 1

    def scan(self, cursor=0, match=None, count=None):
        del cursor, match, count
        return 0, []


class SabreResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _envelope(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _session_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        "<soap-env:Body/>"
        "</soap-env:Envelope>"
    )


def _flight_status_xml():
    return _envelope(
        "<ns3:ACS_FlightDetailRS>"
        "<ItineraryResponseList><ItineraryInfoResponse>"
        "<Airline>GF</Airline><Flight>2057</Flight><Origin>BAH</Origin>"
        "<DepartureDate>2026-03-19</DepartureDate>"
        "<FlightStatus>FINAL</FlightStatus>"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )


def _passenger_list_xml():
    return _envelope(
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>2057</Flight><Origin>BAH</Origin>"
        "</Itinerary></ItineraryInfo>"
        "</GetPassengerListRS>"
    )


def _reservations_xml():
    return _envelope(
        "<Trip_SearchRS>"
        '<ReservationsList NumberResults="0" TotalResults="0"/>'
        "</Trip_SearchRS>"
    )


def _trip_report_xml(last_name, pnr):
    return _envelope(
        "<Trip_ReportsRS><PassengerList><Passenger>"
        f"<LastName>{last_name}</LastName><FirstName>ANN</FirstName>"
        f"<PNRLocator>{pnr}</PNRLocator>"
        "</Passenger></PassengerList></Trip_ReportsRS>"
    )


def _schedule_complete_xml():
    return _envelope(
        "<VerifyFlightDetailsRS>"
        '<ApplicationResults status="Complete"/>'
        "<OriginDestinationOptions>"
        '<OriginDestinationOption OriginTimeZone="AST" DestinationTimeZone="GMT">'
        '<FlightSegment DepartureDateTime="03-19T23:15" ArrivalDateTime="03-20T07:40"'
        ' AirMilesFlown="3200" ElapsedTime="06.25">'
        '<OriginLocation LocationCode="BAH" Terminal="1"/>'
        '<DestinationLocation LocationCode="LHR" Terminal="4"/>'
        '<Equipment AirEquipType="32N"/>'
        '<MarketingAirline Code="GF" FlightNumber="2057"/>'
        "</FlightSegment>"
        "</OriginDestinationOption>"
        "</OriginDestinationOptions>"
        "</VerifyFlightDetailsRS>"
    )


def _schedule_incomplete_xml():
    return _envelope(
        "<VerifyFlightDetailsRS>"
        '<ApplicationResults status="Incomplete">'
        "<Error><SystemSpecificResults>"
        "<Message>UNABLE TO VERIFY FLIGHT</Message>"
        "</SystemSpecificResults></Error>"
        "</ApplicationResults>"
        "</VerifyFlightDetailsRS>"
    )


def _fault_xml():
    return _envelope(
        "<soap-env:Fault><faultstring>Trip report rejected</faultstring></soap-env:Fault>"
    )


class SabreScript:
    """HTTP stand-in for Sabre. Outcomes are selected by the test; parsing is real."""

    def __init__(self):
        self.mlx = "ok"
        self.mlc = "ok"
        self.schedule = "ok"
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        del url, timeout
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode() if isinstance(data, bytes) else (data or "")
        self.calls.append(action)
        if action == "SessionCreateRQ":
            return SabreResponse(200, _session_xml())
        if action == "SessionCloseRQ":
            return SabreResponse(200, _envelope(""))
        if action == "ACS_FlightDetailRQ":
            return SabreResponse(200, _flight_status_xml())
        if action == "GetPassengerListRQ":
            return SabreResponse(200, _passenger_list_xml())
        if action == "Trip_SearchRQ":
            return SabreResponse(200, _reservations_xml())
        if action == "Trip_ReportsRQ":
            report = "MLX" if "<ReportName>MLX</ReportName>" in body else "MLC"
            outcome = self.mlx if report == "MLX" else self.mlc
            return self._outcome(outcome, _trip_report_xml("NEWPAX", "NEW999"))
        if action == "VerifyFlightDetailsLLSRQ":
            if self.schedule == "incomplete":
                return SabreResponse(200, _schedule_incomplete_xml())
            return self._outcome(self.schedule, _schedule_complete_xml())
        return SabreResponse(500, f"unexpected SOAPAction {action}")

    @staticmethod
    def _outcome(outcome, success_xml):
        if outcome == "http_error":
            return SabreResponse(500, "Sabre service failed")
        if outcome == "missing_body":
            return SabreResponse(200, _fault_xml())
        if outcome == "ok":
            return SabreResponse(200, success_xml)
        raise AssertionError(f"unknown Sabre outcome {outcome}")


def _flight(number):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T21:50:00",
        "flightSequenceNumber": 4242,
    }


def _seed_previous_board(db, flight_number):
    db["trip_reports"].insert_one({
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "fetchedAt": PREVIOUS_FETCHED_AT,
        "cancelledPassengers": [
            {"pnr": "CAN001", "lastName": SEEDED_CANCELLED[0]},
            {"pnr": "CAN002", "lastName": SEEDED_CANCELLED[1]},
        ],
        "cancelledCount": 2,
        "everBookedPassengers": [
            {"pnr": "NS001", "lastName": SEEDED_EVER_BOOKED},
        ],
        "everBookedCount": 1,
    })
    db["flight_schedules"].insert_one({
        "airline": "GF",
        "flightNumber": flight_number,
        "departureDate": "2026-03-19",
        "fetchedAt": PREVIOUS_FETCHED_AT,
        "success": True,
        "error": None,
        "origin": "BAH",
        "destination": "LHR",
        "scheduledDeparture": PREVIOUS_DEPARTURE,
        "scheduledArrival": "2026-03-20T07:20",
        "aircraftType": "789",
    })


def _latest(db, collection, query):
    return db[collection].find_one(query, sort=[("fetchedAt", -1)])


def _board(db, flight_number):
    """Read the documents the dashboard reads and build the board payload."""
    flight_query = {
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
    }
    schedule_query = {
        "flightNumber": flight_number,
        "departureDate": "2026-03-19",
    }
    flight_status = _latest(db, "flight_status", flight_query)
    passenger_list = _latest(db, "passenger_list", flight_query)
    trip_report = _latest(db, "trip_reports", flight_query)
    schedule = _latest(db, "flight_schedules", schedule_query)
    return _build_dashboard_payload(
        flight_status,
        passenger_list,
        "BAH",
        "2026-03-19",
        {},
        None,
        trip_report,
        schedule,
    )


def _card(board, card_id):
    cards = (board.get("tree") or {}).get("statusCards") or []
    return next(card for card in cards if card["id"] == card_id)


@pytest.fixture(scope="module")
def refreshed_api(monkeypatch_module):
    db = MemoryDatabase()
    redis = MemoryRedis()
    monkeypatch_module.setenv("SABRE_BASE_URL", "http://sabre.invalid")
    monkeypatch_module.setenv("SABRE_CPAID", "GF")
    monkeypatch_module.setenv("SABRE_USERNAME", "placeholder-user")
    monkeypatch_module.setenv("SABRE_PASSWORD", "placeholder-password")
    monkeypatch_module.setenv("SABRE_PSEUDO_CITY_CODE", "BAH")
    monkeypatch_module.setenv("SABRE_ORGANIZATION", "GF")
    monkeypatch_module.setenv("SABRE_DOMAIN", "GF")
    monkeypatch_module.setenv("MONGODB_URI", "mongodb://placeholder.invalid/falconeye")
    monkeypatch_module.setenv("REDIS_URL", "redis://placeholder.invalid/0")
    monkeypatch_module.setattr(SabreClient, "API_CALL_DELAY", 0)

    import backend.api.main as main
    import backend.api.redis_client as redis_client

    monkeypatch_module.setattr(main, "get_db", lambda: db)
    monkeypatch_module.setattr(main, "get_redis", lambda: redis)
    monkeypatch_module.setattr(redis_client, "get_redis", lambda: redis)

    with TestClient(main.app) as client:
        storage.init_db(db)
        yield SimpleNamespace(client=client, db=db, redis=redis)


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    yield patcher
    patcher.undo()


@pytest.fixture
def sabre(monkeypatch):
    script = SabreScript()
    monkeypatch.setattr("backend.sabre.client.requests.post", script)
    return script


def _post_ingest(api, flight_number):
    return api.client.post(
        "/flights/ingest",
        json=_flight(flight_number),
        headers={},
    )


def _assert_refresh_rejected(response):
    assert response.status_code >= 400
    assert response.status_code not in (401, 403)
    assert "Sabre ingestion completed" not in response.text


def test_successful_refresh_reports_completed_and_replaces_board_documents(refreshed_api, sabre):
    """A refresh whose required Sabre calls succeed is completed and stored."""
    flight_number = "3101"
    _seed_previous_board(refreshed_api.db, flight_number)

    response = _post_ingest(refreshed_api, flight_number)

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Sabre ingestion completed"
    assert body["result"]["success"] is True
    assert body["result"]["apis"]["tripReports"]["status"] == "success"
    assert body["result"]["apis"]["schedule"]["status"] == "success"
    board = _board(refreshed_api.db, flight_number)
    assert _card(board, "offloaded")["value"] == 1
    assert _card(board, "offloaded")["subLabel"] == "MLX report"
    assert board["schedule"]["scheduledDeparture"] == REPLACEMENT_DEPARTURE
    assert "SessionCreateRQ" in sabre.calls
    assert sabre.calls.count("Trip_ReportsRQ") == 2


def test_cancelled_passenger_report_http_error_is_not_a_completed_refresh(refreshed_api, sabre):
    flight_number = "3102"
    _seed_previous_board(refreshed_api.db, flight_number)
    sabre.mlx = "http_error"

    response = _post_ingest(refreshed_api, flight_number)

    board = _board(refreshed_api.db, flight_number)
    offloaded = _card(board, "offloaded")
    no_show = _card(board, "noShow")
    assert offloaded["value"] == 2
    assert offloaded["subLabel"] == "MLX report"
    assert [pax["lastName"] for pax in _latest(
        refreshed_api.db, "trip_reports", {
            "flightNumber": flight_number,
            "origin": "BAH",
            "departureDate": "2026-03-19",
        },
    )["cancelledPassengers"]] == list(SEEDED_CANCELLED)
    assert no_show["value"] == 1
    assert no_show["subLabel"] == "MLC vs manifest"
    assert board["schedule"]["scheduledDeparture"] == REPLACEMENT_DEPARTURE
    _assert_refresh_rejected(response)


def test_ever_booked_report_fault_is_not_a_completed_refresh(refreshed_api, sabre):
    flight_number = "3103"
    _seed_previous_board(refreshed_api.db, flight_number)
    sabre.mlc = "missing_body"

    response = _post_ingest(refreshed_api, flight_number)

    latest_trip = _latest(refreshed_api.db, "trip_reports", {
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
    })
    assert latest_trip["fetchedAt"] == PREVIOUS_FETCHED_AT
    assert latest_trip["everBookedPassengers"][0]["lastName"] == SEEDED_EVER_BOOKED
    board = _board(refreshed_api.db, flight_number)
    assert _card(board, "offloaded")["value"] == 2
    assert _card(board, "noShow")["value"] == 1
    assert board["schedule"]["scheduledDeparture"] == REPLACEMENT_DEPARTURE
    _assert_refresh_rejected(response)


def test_schedule_http_error_leaves_published_schedule_and_is_not_completed(refreshed_api, sabre):
    flight_number = "3104"
    _seed_previous_board(refreshed_api.db, flight_number)
    sabre.schedule = "http_error"

    response = _post_ingest(refreshed_api, flight_number)

    latest_schedule = _latest(refreshed_api.db, "flight_schedules", {
        "flightNumber": flight_number,
        "departureDate": "2026-03-19",
    })
    assert latest_schedule["fetchedAt"] == PREVIOUS_FETCHED_AT
    assert latest_schedule["scheduledDeparture"] == PREVIOUS_DEPARTURE
    board = _board(refreshed_api.db, flight_number)
    assert board["schedule"]["scheduledDeparture"] == PREVIOUS_DEPARTURE
    assert board["schedule"]["aircraftType"] == "789"
    assert _card(board, "offloaded")["value"] == 1
    _assert_refresh_rejected(response)


def test_incomplete_schedule_body_is_not_a_completed_refresh(refreshed_api, sabre):
    flight_number = "3105"
    _seed_previous_board(refreshed_api.db, flight_number)
    sabre.schedule = "incomplete"

    response = _post_ingest(refreshed_api, flight_number)

    latest_schedule = _latest(refreshed_api.db, "flight_schedules", {
        "flightNumber": flight_number,
        "departureDate": "2026-03-19",
    })
    assert latest_schedule["fetchedAt"] == PREVIOUS_FETCHED_AT
    assert latest_schedule["scheduledDeparture"] == PREVIOUS_DEPARTURE
    assert latest_schedule["aircraftType"] == "789"
    board = _board(refreshed_api.db, flight_number)
    assert board["schedule"]["scheduledDeparture"] == PREVIOUS_DEPARTURE
    _assert_refresh_rejected(response)


def _run_batch(api, flights):
    from backend.tasks.ingestion import run_batch_ingestion

    def run_inline(*args, **kwargs):
        run_batch_ingestion.run(*args, **kwargs)
        return SimpleNamespace(id="inline")

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "backend.tasks.ingestion.run_batch_ingestion.delay",
            run_inline,
        )
        accepted = api.client.post(
            "/flights/ingest/batch",
            json={"flights": flights},
            headers={},
        )
    assert accepted.status_code == 202
    job = api.client.get(accepted.json()["pollUrl"])
    assert job.status_code == 200
    return job.json()


def test_batch_job_is_failed_when_only_flight_trip_report_fails(refreshed_api, sabre):
    flight_number = "3106"
    _seed_previous_board(refreshed_api.db, flight_number)
    sabre.mlx = "http_error"

    job = _run_batch(refreshed_api, [_flight(flight_number)])

    assert job["status"] == "failed"
    assert job["results"][0]["success"] is False
    assert job["results"][0]["apis"]["tripReports"]["status"] == "error"
    board = _board(refreshed_api.db, flight_number)
    assert _card(board, "offloaded")["value"] == 2
    assert _card(board, "noShow")["value"] == 1


def test_batch_job_is_partial_when_one_schedule_body_is_incomplete(refreshed_api, sabre):
    failed_flight = "3107"
    successful_flight = "3108"
    _seed_previous_board(refreshed_api.db, failed_flight)
    _seed_previous_board(refreshed_api.db, successful_flight)

    def post(url, data=None, headers=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else (data or "")
        if (
            (headers or {}).get("SOAPAction") == "VerifyFlightDetailsLLSRQ"
            and "FlightNumber=\"3107\"" in body
        ):
            return SabreResponse(200, _schedule_incomplete_xml())
        return sabre(url, data=data, headers=headers, timeout=timeout)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("backend.sabre.client.requests.post", post)
        job = _run_batch(refreshed_api, [_flight(failed_flight), _flight(successful_flight)])

    assert job["status"] == "partial"
    by_flight = {item["flight"]["flightNumber"]: item for item in job["results"]}
    assert by_flight[failed_flight]["success"] is False
    assert by_flight[failed_flight]["apis"]["schedule"]["status"] == "error"
    assert by_flight[successful_flight]["success"] is True
    failed_schedule = _latest(refreshed_api.db, "flight_schedules", {
        "flightNumber": failed_flight,
        "departureDate": "2026-03-19",
    })
    assert failed_schedule["scheduledDeparture"] == PREVIOUS_DEPARTURE
    successful_schedule = _latest(refreshed_api.db, "flight_schedules", {
        "flightNumber": successful_flight,
        "departureDate": "2026-03-19",
    })
    assert successful_schedule["scheduledDeparture"] == REPLACEMENT_DEPARTURE
