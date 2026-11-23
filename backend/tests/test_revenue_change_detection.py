"""Revenue reclassification must be recorded as a passenger change.

These tests follow a Sabre passenger list from the SOAP body the ingest
pipeline stores through change detection and the activity, timeline, and
change-history readers. They fail while a revenue-indicator flip between
two manifests for the same traveler produces no change.
"""

import os
import sys
import time

# Set before importing the app or the Sabre client. Placeholders only.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["POSTGRES_URI"] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes.changes import (
    get_activity_feed,
    get_changes,
    get_flight_timeline,
    get_passenger_timeline,
)
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes, diff_flight_status
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

FLIGHT_INFO = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


def _soap(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger_list_xml(
    *,
    revenue: bool,
    pnr: str | None = "ESMMQK",
    last_name: str = "BABOO",
    first_name: str = "MOHIT MR",
    line_number: str = "1",
    seat: str = "22A",
    bag_count: str = "1",
    passenger_id: str = "CFBAAF430001",
) -> str:
    indicators = ["CheckedIn", "Boarded"]
    if revenue:
        indicators.append("Revenue")
    indicator_xml = "".join(f"<Indicator>{name}</Indicator>" for name in indicators)
    pnr_xml = ""
    if pnr is not None:
        pnr_xml = f'<PNRLocator nameAssociationID="1">{pnr}</PNRLocator>'
    body = f"""
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          {pnr_xml}
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>{line_number}</LineNumber>
          <BookingClass>V</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <PassengerType>F</PassengerType>
          <BagCount>{bag_count}</BagCount>
          <Indicators>{indicator_xml}</Indicators>
          <CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>true</BoardStatus></Boarding_Info>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
    """
    return _soap(body)


def _parse_passenger_body(xml_text: str) -> dict:
    client = SabreClient()
    parsed = client._parse_xml(xml_text)
    return client._extract_body(parsed, "GetPassengerListRS")


def _converted(xml_text: str, flight_info: dict | None = None) -> dict:
    info = flight_info or FLIGHT_INFO
    raw = _parse_passenger_body(xml_text)
    return convert_passenger_list(
        raw,
        info["airline"],
        info["flightNumber"],
        info["departureDate"],
        info["origin"],
    )


def _snapshots(before_doc: dict, after_doc: dict) -> tuple[dict, dict]:
    return (
        {"snapshotId": "snap-before", "data": before_doc},
        {"snapshotId": "snap-after", "data": after_doc},
    )


def _revenue_changes(changes: list[dict]) -> list[dict]:
    return [change for change in changes if change.get("field") == "isRevenue"]


def _assert_revenue_transition(changes: list[dict], old: bool, new: bool, pnr: str | None):
    matches = _revenue_changes(changes)
    assert matches, changes
    wanted_old = str(old)
    wanted_new = str(new)
    assert any(
        str(change.get("oldValue")) == wanted_old and str(change.get("newValue")) == wanted_new
        for change in matches
    ), matches
    if pnr is not None:
        assert any((change.get("passenger") or {}).get("pnr") == pnr for change in matches), matches


def _empty_sabre_body(tag: str) -> str:
    # A child element keeps xmltodict from turning the body into None.
    return _soap(f"<{tag}><Status>Complete</Status></{tag}>")


class _ScriptedSabre:
    """Return local SOAP bodies from SabreClient._post. No network call."""

    def __init__(self):
        self.passenger_xml = _passenger_list_xml(revenue=True)

    def __call__(self, action, body, timeout=30, endpoint=None):
        if action == "SessionCreateRQ":
            xml = (
                '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
                '<soap-env:Header><wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
                "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
                "</wsse:Security></soap-env:Header></soap-env:Envelope>"
            )
            return xml, 200, 1, body
        if action == "SessionCloseRQ":
            return "<closed/>", 200, 1, body
        if action == "GetPassengerListRQ":
            return self.passenger_xml, 200, 1, "<request/>"
        if action == "ACS_FlightDetailRQ":
            return _empty_sabre_body("ns3:ACS_FlightDetailRS"), 200, 1, "<request/>"
        if action == "Trip_SearchRQ":
            return _empty_sabre_body("Trip_SearchRS"), 200, 1, "<request/>"
        if action == "Trip_ReportsRQ":
            return _empty_sabre_body("Trip_ReportsRS"), 200, 1, "<request/>"
        if action == "VerifyFlightDetailsLLSRQ":
            return _empty_sabre_body("VerifyFlightDetailsRS"), 200, 1, "<request/>"
        raise AssertionError(f"unexpected Sabre action {action}")


def _reset_db_handles():
    """Drop clients closed by the API lifespan so the next test can reconnect."""
    import backend.api.database as database
    import backend.feeder.storage as storage

    database._client = None
    database._db = None
    storage._client = None
    storage._db = None
    storage._owns_connection = False


def _clear_flight(flight_number: str, origin: str, departure_date: str):
    from backend.api.database import get_db

    _reset_db_handles()

    db = get_db()
    query = {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
    }
    for name in (
        "snapshots",
        "changes",
        "flights",
        "passenger_list",
        "flight_status",
        "reservations",
        "trip_reports",
        "flight_schedules",
    ):
        db[name].delete_many({"flightNumber": flight_number})
    db["sabre_requests"].delete_many({"flight.flightNumber": flight_number})
    db["snapshots"].delete_many(query)


def test_revenue_indicator_removed_is_a_passenger_change():
    before = _converted(_passenger_list_xml(revenue=True))
    after = _converted(_passenger_list_xml(revenue=False))
    assert before["passengers"][0]["isRevenue"] is True
    assert after["passengers"][0]["isRevenue"] is False
    before_snap, after_snap = _snapshots(before, after)
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)
    _assert_revenue_transition(changes, True, False, "ESMMQK")


def test_revenue_indicator_added_is_a_passenger_change():
    before = _converted(_passenger_list_xml(revenue=False))
    after = _converted(_passenger_list_xml(revenue=True))
    before_snap, after_snap = _snapshots(before, after)
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)
    _assert_revenue_transition(changes, False, True, "ESMMQK")


def test_staff_without_pnr_revenue_flip_is_a_passenger_change():
    before = _converted(_passenger_list_xml(revenue=True, pnr=None, last_name="CREW", first_name="ALEX"))
    after = _converted(_passenger_list_xml(revenue=False, pnr=None, last_name="CREW", first_name="ALEX"))
    assert before["passengers"][0]["pnr"] == ""
    assert before["passengers"][0]["isRevenue"] is True
    assert after["passengers"][0]["isRevenue"] is False
    before_snap, after_snap = _snapshots(before, after)
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)
    _assert_revenue_transition(changes, True, False, "")


def test_revenue_flip_alongside_seat_change_records_both():
    before = _converted(_passenger_list_xml(revenue=True, seat="22A"))
    after = _converted(_passenger_list_xml(revenue=False, seat="22B"))
    before_snap, after_snap = _snapshots(before, after)
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)
    assert any(change.get("field") == "seat" for change in changes), changes
    _assert_revenue_transition(changes, True, False, "ESMMQK")


def test_unchanged_revenue_flag_does_not_emit_a_revenue_change():
    before = _converted(_passenger_list_xml(revenue=True, seat="22A"))
    after = _converted(_passenger_list_xml(revenue=True, seat="31C"))
    before_snap, after_snap = _snapshots(before, after)
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)
    assert _revenue_changes(changes) == []
    assert any(change.get("field") == "seat" for change in changes)


def test_flight_status_revenue_count_change_is_recorded():
    """Flight-status class counts store revenue and non-revenue and the diff skips both."""
    flight_info = {"flightNumber": "2057", "origin": "BOM", "departureDate": "2026-03-19"}
    before_counts = {
        "booked": 10,
        "onBoard": 8,
        "boardingPasses": 8,
        "revenue": 10,
        "nonRevenue": 0,
    }
    after_counts = {
        "booked": 10,
        "onBoard": 8,
        "boardingPasses": 8,
        "revenue": 9,
        "nonRevenue": 1,
    }
    before = {"snapshotId": "snap-1", "data": {"status": "OPENCI", "passengerCounts": {"Y": dict(before_counts)}}}
    after = {"snapshotId": "snap-2", "data": {"status": "OPENCI", "passengerCounts": {"Y": dict(after_counts)}}}
    changes = diff_flight_status(before, after, flight_info)
    fields = {change.get("field"): change for change in changes}
    assert "passengerCounts.Y.revenue" in fields, changes
    assert "passengerCounts.Y.nonRevenue" in fields, changes
    assert str(fields["passengerCounts.Y.revenue"]["oldValue"]) == "10"
    assert str(fields["passengerCounts.Y.revenue"]["newValue"]) == "9"
    assert str(fields["passengerCounts.Y.nonRevenue"]["oldValue"]) == "0"
    assert str(fields["passengerCounts.Y.nonRevenue"]["newValue"]) == "1"


def _ingest_meta():
    return {
        "requestXml": "<request/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": None,
    }


def _run_passenger_pipeline(xml_text: str, flight_info: dict) -> dict:
    raw = _parse_passenger_body(xml_text)
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        flight_info,
        raw,
        xml_text,
        _ingest_meta(),
        convert_passenger_list,
        (
            raw,
            flight_info["airline"],
            flight_info["flightNumber"],
            flight_info["departureDate"],
            flight_info["origin"],
        ),
    )


def test_pipeline_records_revenue_added_for_history_readers():
    flight_info = {
        "airline": "GF",
        "flightNumber": "3199",
        "origin": "BAH",
        "departureDate": "2026-04-02",
    }
    _clear_flight("3199", "BAH", "2026-04-02")
    first = _passenger_list_xml(revenue=False, pnr="STAFF1", last_name="KHAN", first_name="SARA")
    second = _passenger_list_xml(revenue=True, pnr="STAFF1", last_name="KHAN", first_name="SARA")
    first_result = _run_passenger_pipeline(first, flight_info)
    second_result = _run_passenger_pipeline(second, flight_info)
    assert first_result["changesStored"] == 0
    assert second_result["isDuplicate"] is False
    from backend.api.database import get_db

    latest = get_db()["snapshots"].find_one(
        {"flightNumber": "3199", "snapshotType": "passenger_list"},
        sort=[("sequenceNumber", -1)],
    )
    assert latest["data"]["passengers"][0]["isRevenue"] is True
    assert second_result["changesStored"] >= 1

    changes = get_changes("3199", origin="BAH", date="2026-04-02", change_type=None, limit=100)
    _assert_revenue_transition(changes, False, True, "STAFF1")

    feed = get_activity_feed(date="2026-04-02", limit=50, categories=None)
    feed_hits = [
        event for event in feed["events"]
        if event.get("flightNumber") == "3199"
        and (
            "revenue" in str(event.get("eventType", "")).lower()
            or "revenue" in str(event.get("description", "")).lower()
            or "isrevenue" in str(event.get("description", "")).lower()
        )
    ]
    assert feed_hits, feed

    timeline = get_flight_timeline("3199", origin="BAH", date="2026-04-02")
    timeline_hits = [
        event for event in timeline["events"]
        if (event.get("details") or {}).get("field") == "isRevenue"
    ]
    assert timeline_hits, timeline
    assert str(timeline_hits[0]["details"]["oldValue"]) == "False"
    assert str(timeline_hits[0]["details"]["newValue"]) == "True"

    passenger_timeline = get_passenger_timeline("3199", "STAFF1", date="2026-04-02", origin="BAH")
    passenger_hits = [
        event for event in passenger_timeline["events"]
        if (event.get("details") or {}).get("field") == "isRevenue"
    ]
    assert passenger_hits, passenger_timeline


def test_pipeline_records_staff_revenue_removed_without_pnr():
    flight_info = {
        "airline": "GF",
        "flightNumber": "3200",
        "origin": "BAH",
        "departureDate": "2026-04-03",
    }
    _clear_flight("3200", "BAH", "2026-04-03")
    common = dict(pnr=None, last_name="CREW", first_name="NOOR", line_number="4", passenger_id="STAFF0004")
    first_result = _run_passenger_pipeline(_passenger_list_xml(revenue=True, **common), flight_info)
    second_result = _run_passenger_pipeline(_passenger_list_xml(revenue=False, **common), flight_info)
    assert second_result["isDuplicate"] is False
    assert first_result["changesStored"] == 0
    from backend.api.database import get_db

    latest = get_db()["snapshots"].find_one(
        {"flightNumber": "3200", "snapshotType": "passenger_list"},
        sort=[("sequenceNumber", -1)],
    )
    assert latest["data"]["passengers"][0]["isRevenue"] is False
    assert latest["data"]["passengers"][0]["pnr"] == ""
    assert second_result["changesStored"] >= 1

    changes = get_changes("3200", origin="BAH", date="2026-04-03", change_type=None, limit=100)
    _assert_revenue_transition(changes, True, False, "")
    timeline = get_flight_timeline("3200", origin="BAH", date="2026-04-03")
    assert any((event.get("details") or {}).get("field") == "isRevenue" for event in timeline["events"]), timeline


def test_unauthenticated_ingest_keeps_revenue_flip_out_of_activity_and_history(monkeypatch):
    """Two ingest calls whose Sabre manifests differ only by the Revenue indicator."""
    from fastapi.routing import APIRoute
    from backend.api.routes.ingestion import router as ingestion_router

    ingest_routes = [
        route for route in ingestion_router.routes
        if isinstance(route, APIRoute) and route.path == "/flights/ingest" and "POST" in route.methods
    ]
    assert len(ingest_routes) == 1
    assert list(ingest_routes[0].dependencies) == []
    assert list(ingestion_router.dependencies) == []

    script = _ScriptedSabre()

    def _fake_post(self, action, body, timeout=30, endpoint=None):
        return script(action, body, timeout=timeout, endpoint=endpoint)

    monkeypatch.setattr(SabreClient, "_post", _fake_post)

    flight_number = "9057"
    origin = "BOM"
    departure_date = "2026-03-19"
    _clear_flight(flight_number, origin, departure_date)

    payload = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "departureDateTime": "2026-03-19T17:40:00",
    }
    with TestClient(app) as client:
        script.passenger_xml = _passenger_list_xml(revenue=True)
        first = client.post("/flights/ingest", json=payload)
        assert first.status_code == 200, first.text
        first_pax = first.json()["result"]["apis"]["passengerList"]
        assert first_pax["status"] == "success"
        assert first_pax["changesStored"] == 0

        script.passenger_xml = _passenger_list_xml(revenue=False)
        time.sleep(1.1)
        second = client.post("/flights/ingest", json=payload)
        assert second.status_code == 200, second.text
        second_pax = second.json()["result"]["apis"]["passengerList"]
        assert second_pax["status"] == "success"
        assert second_pax["isDuplicate"] is False

        manifest = client.get(
            f"/flights/{flight_number}/passengers",
            params={"origin": origin, "date": departure_date},
        )
        assert manifest.status_code == 200, manifest.text
        passengers = manifest.json()["passengers"]
        traveler = next(pax for pax in passengers if pax["pnr"] == "ESMMQK")
        assert traveler["isCheckedIn"] is True
        assert traveler["isBoarded"] is True
        assert traveler["cabin"] == "Y"
        assert traveler["bookingClass"] == "V"
        assert traveler["seat"] == "22A"
        assert traveler["bagCount"] == 1
        assert traveler["isRevenue"] is False
        assert second_pax["changesStored"] >= 1

        changes = client.get(
            f"/flights/{flight_number}/changes",
            params={"origin": origin, "date": departure_date},
        )
        assert changes.status_code == 200, changes.text
        _assert_revenue_transition(changes.json(), True, False, "ESMMQK")

        feed = client.get("/activity/feed", params={"date": departure_date})
        assert feed.status_code == 200, feed.text
        feed_hits = [
            event for event in feed.json()["events"]
            if event.get("flightNumber") == flight_number
            and (event.get("passenger") or {}).get("pnr") == "ESMMQK"
            and (
                "revenue" in str(event.get("eventType", "")).lower()
                or "isrevenue" in str(event.get("description", "")).lower()
                or "revenue" in str(event.get("description", "")).lower()
            )
        ]
        assert feed_hits, feed.json()

        timeline = client.get(
            f"/flights/{flight_number}/timeline",
            params={"origin": origin, "date": departure_date},
        )
        assert timeline.status_code == 200, timeline.text
        timeline_hits = [
            event for event in timeline.json()["events"]
            if (event.get("details") or {}).get("field") == "isRevenue"
        ]
        assert timeline_hits, timeline.json()
        assert str(timeline_hits[0]["details"]["oldValue"]) == "True"
        assert str(timeline_hits[0]["details"]["newValue"]) == "False"

        history = client.get(
            f"/flights/{flight_number}/passengers/ESMMQK/timeline",
            params={"origin": origin, "date": departure_date},
        )
        assert history.status_code == 200, history.text
        history_hits = [
            event for event in history.json()["events"]
            if (event.get("details") or {}).get("field") == "isRevenue"
        ]
        assert history_hits, history.json()
        assert str(history_hits[0]["details"]["oldValue"]) == "True"
        assert str(history_hits[0]["details"]["newValue"]) == "False"
