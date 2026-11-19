"""Spec: a missing or unrecognized Sabre flight status must not look departed.

These tests follow the product path. A scripted Sabre client either fails
ACS_FlightDetailRQ or returns a status other than PDC, FINAL, or OPENCI.
GetPassengerListRS is real SOAP XML parsed by SabreClient and stored by the
feeder. The dashboard, flight list, phase journey, and both list summarizers
then read those stored documents.

The tests assert the safe behavior, so they fail while boarded percentage can
still be reported as departure. They are expected to pass once a missing or
unrecognized status stays unknown or boarding, says flight detail is
unavailable, and leaves the boarding card in focus.

Known Sabre statuses are covered too: PDC stays departed, FINAL stays closed,
and OPENCI with boarded passengers stays boarding.

Not asserted here: GET /flights omits a flight that has no flight_status
document at all (it iterates flight-status keys only), and GET
/{flight}/tree returns the passenger tree without flightPhase. Neither
response is how this false departure is shown.
"""

import os
import sys

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes import flights as flights_api
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError

MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
TEST_DB = "falconeye_missing_status_phase_tests"
KNOWN_STATUSES = {"PDC", "FINAL", "OPENCI"}
SAFE_ACTIVE_PHASES = {"BOARDING", "UNKNOWN"}


class ScriptedSabreClient:
    """Stand-in for the SOAP transport only.

    Flight detail is either a SabreError or an ACS_FlightDetailRS document.
    Passenger list XML is parsed with SabreClient._parse_xml and
    SabreClient._extract_body, the same functions the live client uses after
    a successful HTTP response. Later Sabre calls fail so only the flight-detail
    and passenger-list outcomes decide what is stored.
    """

    def __init__(self, flight_status, boarded, total):
        self.flight_status = flight_status
        self.boarded = boarded
        self.total = total
        self.flight_status_calls = 0
        self.passenger_list_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        self.flight_status_calls += 1
        if self.flight_status == "FAIL":
            raise SabreError(
                "ACS_FlightDetailRQ returned HTTP 503: flight detail unavailable"
            )
        xml_text = _flight_status_xml(
            airline, flight_number, origin, departure_date, self.flight_status
        )
        parsed = SabreClient._parse_xml(xml_text)
        data = SabreClient._extract_body(self, parsed, "ns3:ACS_FlightDetailRS")
        return data, xml_text, _meta()

    def get_passenger_list(
        self, airline, flight_number, departure_date, origin, display_codes=None
    ):
        self.passenger_list_calls += 1
        xml_text = _passenger_list_xml(
            airline,
            flight_number,
            departure_date,
            origin,
            self.boarded,
            self.total,
        )
        parsed = SabreClient._parse_xml(xml_text)
        data = SabreClient._extract_body(self, parsed, "GetPassengerListRS")
        return data, xml_text, _meta()

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        raise SabreError("Trip_SearchRQ failed")

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        raise SabreError("trip report failed")

    def verify_flight_details(
        self, airline, flight_number, departure_date, origin, destination=""
    ):
        raise SabreError("VerifyFlightDetailsLLSRQ failed")


@pytest.fixture()
def db(monkeypatch):
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    client.drop_database(TEST_DB)
    database = client[TEST_DB]
    storage.init_db(database)
    monkeypatch.setattr(flights_api, "get_db", lambda: database)
    flights_api._dashboard_cache.clear()
    yield database
    flights_api._dashboard_cache.clear()
    storage.close()
    client.drop_database(TEST_DB)
    client.close()


def _meta():
    return {
        "requestXml": "<GetPassengerListRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": None,
    }


def _flight_status_xml(airline, flight_number, origin, departure_date, status):
    # An empty element becomes null and crashes the dashboard tree before
    # phase is returned. Omit the element so a blank Sabre status is "".
    status_element = f"<FlightStatus>{status}</FlightStatus>" if status else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>{airline}</Airline>
          <Flight>{flight_number}</Flight>
          <Origin>{origin}</Origin>
          <ScheduledDepartureDate>{departure_date}</ScheduledDepartureDate>
          {status_element}
          <DepartureGate>A1</DepartureGate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_xml(index, boarded):
    flag = "true" if boarded else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>PAX</LastName>
            <FirstName>TEST{index:02d}</FirstName>
          </Name_Details>
          <PNRLocator>T{index:05d}</PNRLocator>
          <PassengerID>ID{index:04d}</PassengerID>
          <LineNumber>{index}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>DXB</Destination>
          <PassengerType>P</PassengerType>
          <BagCount>0</BagCount>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>{flag}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>{flag}</BoardStatus></Boarding_Info>
        </PassengerInfo>
    """


def _passenger_list_xml(airline, flight_number, departure_date, origin, boarded, total):
    passengers = "".join(
        _passenger_xml(i, i <= boarded) for i in range(1, total + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{airline}</Airline>
          <Flight>{flight_number}</Flight>
          <DepartureDate>{departure_date}</DepartureDate>
          <Origin>{origin}</Origin>
          <Destination>DXB</Destination>
          <AircraftType>32N</AircraftType>
        </Itinerary>
        <DepartureGate>A1</DepartureGate>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _flight(flight_number, departure_date):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": departure_date,
        "departureDateTime": f"{departure_date}T10:00:00",
    }


def _ingest(monkeypatch, database, flight_status, boarded, total, flight_number, departure_date):
    scripted = ScriptedSabreClient(flight_status, boarded, total)
    monkeypatch.setattr("backend.feeder.runner.SabreClient", lambda: scripted)
    result = run_feeder([_flight(flight_number, departure_date)])
    stored_status = database["flight_status"].find_one({"flightNumber": flight_number})
    stored_passengers = database["passenger_list"].find_one(
        {"flightNumber": flight_number}
    )
    return result, scripted, stored_status, stored_passengers


def _manifest_counts(analysis):
    state = analysis["stateBreakdown"]
    total = (
        state["booked"]["totalPassengers"]
        + state["checkedIn"]["totalPassengers"]
        + state["boarded"]["totalPassengers"]
    )
    return analysis["boarded"], total


def _assert_ratio(analysis, boarded, total):
    got_boarded, got_total = _manifest_counts(analysis)
    assert got_boarded == boarded
    assert got_total == total
    assert got_total > 0
    assert got_boarded / got_total > 0.8


def _says_flight_detail_unavailable(description):
    text = (description or "").lower()
    if "unavailable" not in text:
        return False
    return any(
        phrase in text
        for phrase in ("flight detail", "flight status", "sabre status", "status")
    )


def _assert_not_inferred_departure(phase, *, boarding_open):
    """Phase shown by the workbench banner, timeline, and state panels."""
    assert phase["phase"] != "DEPARTED"
    assert phase["phase"] != "CLOSED"
    assert "depart" not in phase["label"].lower()
    assert "depart" not in phase["description"].lower()
    assert _says_flight_detail_unavailable(phase["description"])
    if boarding_open:
        assert phase["phase"] in SAFE_ACTIVE_PHASES
        assert phase["focusCard"] == "boarded"
    else:
        assert phase["focusCard"] != "others"


def _assert_banner_text(phase):
    # PhaseAlertBanner prints label, then description, with no extra status logic.
    shown = f"{phase['label']} — {phase['description']}"
    assert "depart" not in shown.lower()
    assert "unavailable" in shown.lower()


@pytest.mark.parametrize("boarded", [9, 10])
def test_failed_flight_detail_mostly_boarded_dashboard_stays_open(
    db, monkeypatch, boarded
):
    flight_number = f"91{boarded:02d}"
    departure_date = "2026-11-19"
    result, scripted, stored_status, stored_passengers = _ingest(
        monkeypatch, db, "FAIL", boarded, 10, flight_number, departure_date
    )

    apis = result["results"][0]["apis"]
    assert scripted.flight_status_calls == 1
    assert scripted.passenger_list_calls == 4
    assert apis["flightStatus"]["status"] == "error"
    assert apis["passengerList"]["status"] == "success"
    assert stored_status is None
    assert stored_passengers is not None
    assert db["flight_status"].count_documents({"flightNumber": flight_number}) == 0

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    assert dashboard["flightStatus"] is None
    _assert_ratio(dashboard["analysis"], boarded, 10)
    _assert_not_inferred_departure(dashboard["flightPhase"], boarding_open=True)
    _assert_banner_text(dashboard["flightPhase"])

    # The same stored manifest feeds both list summarizers.
    summary = flights_api._summarize_flight_for_list(None, stored_passengers)
    fast = flights_api._summarize_flight_for_list_fast(None, stored_passengers)
    _assert_not_inferred_departure(summary["flightPhase"], boarding_open=True)
    _assert_not_inferred_departure(fast["flightPhase"], boarding_open=True)

    journey = flights_api.get_phase_journey(
        flight_number, origin="BAH", date=departure_date
    )
    assert journey["phases"]
    assert all(item["phase"] != "DEPARTED" for item in journey["phases"])
    assert all("depart" not in item["label"].lower() for item in journey["phases"])


@pytest.mark.parametrize("status", ["CKIN", ""])
def test_unrecognized_or_blank_status_mostly_boarded_does_not_look_departed(
    db, monkeypatch, status
):
    suffix = "blank" if status == "" else status.lower()
    flight_number = "9201" if status == "" else "9202"
    departure_date = "2026-11-20" if status == "" else "2026-11-21"
    result, scripted, stored_status, stored_passengers = _ingest(
        monkeypatch, db, status, 9, 10, flight_number, departure_date
    )

    apis = result["results"][0]["apis"]
    assert scripted.flight_status_calls == 1
    assert apis["flightStatus"]["status"] == "success"
    assert apis["passengerList"]["status"] == "success"
    assert stored_status is not None
    assert (stored_status.get("status") or "") not in KNOWN_STATUSES
    assert stored_passengers is not None

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    assert (dashboard["flightStatus"] or {}).get("status") or "" == (status or "")
    _assert_ratio(dashboard["analysis"], 9, 10)
    _assert_not_inferred_departure(dashboard["flightPhase"], boarding_open=True)
    _assert_banner_text(dashboard["flightPhase"])

    listed = flights_api.list_flights(date=departure_date)
    assert len(listed) == 1
    assert listed[0]["flightNumber"] == flight_number
    assert (listed[0].get("status") or "") not in KNOWN_STATUSES
    _assert_not_inferred_departure(listed[0]["flightPhase"], boarding_open=True)
    _assert_banner_text(listed[0]["flightPhase"])

    summary = flights_api._summarize_flight_for_list(stored_status, stored_passengers)
    _assert_not_inferred_departure(summary["flightPhase"], boarding_open=True)

    journey = flights_api.get_phase_journey(
        flight_number, origin="BAH", date=departure_date
    )
    assert journey["phases"]
    assert all(item["phase"] != "DEPARTED" for item in journey["phases"])
    assert all("depart" not in item["label"].lower() for item in journey["phases"])


def test_missing_status_at_eighty_percent_does_not_infer_departure(db, monkeypatch):
    flight_number = "9308"
    departure_date = "2026-11-22"
    _ingest(monkeypatch, db, "FAIL", 8, 10, flight_number, departure_date)

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    boarded, total = _manifest_counts(dashboard["analysis"])
    assert boarded == 8
    assert total == 10
    assert boarded / total == 0.8
    _assert_not_inferred_departure(dashboard["flightPhase"], boarding_open=True)
    _assert_banner_text(dashboard["flightPhase"])


def test_missing_status_checked_in_not_boarded_says_detail_unavailable(
    db, monkeypatch
):
    flight_number = "9305"
    departure_date = "2026-11-24"
    scripted = _CheckedInNotBoardedClient()
    monkeypatch.setattr("backend.feeder.runner.SabreClient", lambda: scripted)
    result = run_feeder([_flight(flight_number, departure_date)])
    assert result["results"][0]["apis"]["flightStatus"]["status"] == "error"
    assert result["results"][0]["apis"]["passengerList"]["status"] == "success"

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    boarded, total = _manifest_counts(dashboard["analysis"])
    assert boarded == 0
    assert dashboard["analysis"]["checkedIn"] == 4
    assert total == 10
    _assert_not_inferred_departure(dashboard["flightPhase"], boarding_open=False)
    _assert_banner_text(dashboard["flightPhase"])
    assert dashboard["flightPhase"]["phase"] in {"CHECK_IN", "BOARDING", "UNKNOWN"}
    assert dashboard["flightPhase"]["focusCard"] in {"checkedIn", "boarded"}


def test_missing_status_with_no_activity_says_detail_unavailable(db, monkeypatch):
    flight_number = "9300"
    departure_date = "2026-11-25"
    _ingest(monkeypatch, db, "FAIL", 0, 10, flight_number, departure_date)

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    boarded, total = _manifest_counts(dashboard["analysis"])
    assert boarded == 0
    assert dashboard["analysis"]["checkedIn"] == 0
    assert total == 10
    _assert_not_inferred_departure(dashboard["flightPhase"], boarding_open=False)
    _assert_banner_text(dashboard["flightPhase"])


def test_pdc_status_still_reports_departed(db, monkeypatch):
    flight_number = "9401"
    departure_date = "2026-11-26"
    _ingest(monkeypatch, db, "PDC", 9, 10, flight_number, departure_date)

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    _assert_ratio(dashboard["analysis"], 9, 10)
    assert dashboard["flightStatus"]["status"] == "PDC"
    assert dashboard["flightPhase"]["phase"] == "DEPARTED"
    assert dashboard["flightPhase"]["focusCard"] == "others"

    listed = flights_api.list_flights(date=departure_date)
    assert listed[0]["flightPhase"]["phase"] == "DEPARTED"


def test_openci_with_boarded_passengers_stays_boarding(db, monkeypatch):
    flight_number = "9402"
    departure_date = "2026-11-27"
    _ingest(monkeypatch, db, "OPENCI", 9, 10, flight_number, departure_date)

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    _assert_ratio(dashboard["analysis"], 9, 10)
    assert dashboard["flightStatus"]["status"] == "OPENCI"
    assert dashboard["flightPhase"]["phase"] == "BOARDING"
    assert dashboard["flightPhase"]["focusCard"] == "boarded"
    assert "depart" not in dashboard["flightPhase"]["label"].lower()
    assert "depart" not in dashboard["flightPhase"]["description"].lower()


def test_final_status_stays_closed(db, monkeypatch):
    flight_number = "9403"
    departure_date = "2026-11-28"
    _ingest(monkeypatch, db, "FINAL", 9, 10, flight_number, departure_date)

    dashboard = flights_api.get_flight_dashboard(
        flight_number, origin="BAH", date=departure_date, snapshot_sequence=None
    )
    _assert_ratio(dashboard["analysis"], 9, 10)
    assert dashboard["flightStatus"]["status"] == "FINAL"
    assert dashboard["flightPhase"]["phase"] == "CLOSED"
    assert dashboard["flightPhase"]["phase"] != "DEPARTED"


class _CheckedInNotBoardedClient(ScriptedSabreClient):
    def __init__(self):
        super().__init__("FAIL", 0, 10)

    def get_passenger_list(
        self, airline, flight_number, departure_date, origin, display_codes=None
    ):
        self.passenger_list_calls += 1
        passengers = []
        for i in range(1, 11):
            checked_in = i <= 4
            flag = "true" if checked_in else "false"
            passengers.append(
                f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>PAX</LastName>
            <FirstName>HOLD{i:02d}</FirstName>
          </Name_Details>
          <PNRLocator>H{i:05d}</PNRLocator>
          <PassengerID>HD{i:04d}</PassengerID>
          <LineNumber>{i}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>DXB</Destination>
          <PassengerType>P</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>{flag}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>
                """
            )
        xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{airline}</Airline>
          <Flight>{flight_number}</Flight>
          <Origin>{origin}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(passengers)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        parsed = SabreClient._parse_xml(xml_text)
        data = SabreClient._extract_body(self, parsed, "GetPassengerListRS")
        return data, xml_text, _meta()
