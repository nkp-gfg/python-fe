"""Offload and no-show figures must come from this flight's airline.

The dashboard and tree load the newest trip_reports document by flight
number, origin, and date. Ingestion accepts any two-character airline and
stores that call's trip report under it. These tests send the unauthenticated
ingest route a second airline for the same flight number, origin, and date,
then read the Gulf Air dashboard.

Sabre is replaced at the HTTP boundary with SOAP envelopes. Flight status and
the passenger list follow the fields the converters read. Trip reports follow
Trip_ReportsRS (Report/Details/Entry/PaxEntry and GroupEntry). A second case
also includes PassengerList/Passenger, which is the only body shape
convert_trip_report copies into the stored cancelled and ever-booked lists.

The tests fail while a newer document for another airline is allowed to set
Offloaded and the no-show count. The historical dashboard query parameter is
not covered: that path calls get_snapshot_data_as_of with positional
arguments, the call raises, and the route returns 404 before it builds
figures.
"""

import os
import re
import time
from types import SimpleNamespace

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("SABRE_API_DELAY_SECONDS", "0")
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XXXX")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.database import get_db
from backend.api.main import app
from backend.api.routes import flights as flights_routes


FLIGHT_DATE = "2026-03-19"
ORIGIN = "BAH"
DEPARTURE_AT = "2026-03-19T08:00:00"

# Manifest: two checked-in and not boarded, one still booked.
# On a FINAL flight those are the inferred offload and no-show counts.
MANIFEST = [
    ("ALI", "AAA111", True, False),
    ("BAKER", "BBB222", True, False),
    ("CHEN", "CCC333", False, False),
]
INFERRED_OFFLOADED = 2
INFERRED_NO_SHOW = 1

# Gulf Air trip report, using the PassengerList shape the converter stores.
GF_CANCELLED = [("GFONLY", "GFO001")]
GF_EVER_BOOKED = [("GFBOOK", "GFB001")]

# Other airline, same shape, different people.
XX_CANCELLED = [
    ("XXONE", "XX0001"),
    ("XXTWO", "XX0002"),
    ("XXTHREE", "XX0003"),
    ("XXFOUR", "XX0004"),
]
XX_EVER_BOOKED = [
    ("XXBOOK1", "XXB001"),
    ("XXBOOK2", "XXB002"),
    ("XXBOOK3", "XXB003"),
]


class SabreStandIn:
    """HTTP stand-in for Sabre SOAP calls made by SabreClient._post."""

    def __init__(self):
        self.gf_trip_reports = False
        self.foreign_passenger_list = False

    def __call__(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        body = data if isinstance(data, str) else ""
        airline = _request_airline(body)
        if action == "SessionCreateRQ":
            return _response(200, _session_xml())
        if action == "SessionCloseRQ":
            return _response(200, "<ok/>")
        if action == "ACS_FlightDetailRQ" and airline == "GF":
            return _response(200, _flight_status_xml())
        if action == "GetPassengerListRQ" and airline == "GF":
            return _response(200, _passenger_list_xml())
        if action == "Trip_ReportsRQ" and airline == "GF" and self.gf_trip_reports:
            report = _request_report(body)
            people = GF_CANCELLED if report == "MLX" else GF_EVER_BOOKED
            return _response(200, _trip_report_xml(report, people, include_passenger_list=True))
        if action == "Trip_ReportsRQ" and airline == "XX":
            report = _request_report(body)
            people = XX_CANCELLED if report == "MLX" else XX_EVER_BOOKED
            return _response(
                200,
                _trip_report_xml(
                    report,
                    people,
                    include_passenger_list=self.foreign_passenger_list,
                ),
            )
        return _response(500, "sabre fault")


def _response(status, text):
    return SimpleNamespace(status_code=status, text=text)


def _request_airline(body):
    match = re.search(r"<AirlineCode>([^<]+)</AirlineCode>", body)
    if match:
        return match.group(1)
    match = re.search(r"<[^>]*Airline>([^<]+)</[^>]*Airline>", body)
    return match.group(1) if match else ""


def _request_report(body):
    match = re.search(r"<ReportName>([^<]+)</ReportName>", body)
    return match.group(1) if match else ""


def _session_xml():
    return (
        "<soap-env:Envelope><soap-env:Body>"
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</soap-env:Body></soap-env:Envelope>"
    )


def _envelope(inner):
    return f"<soap-env:Envelope><soap-env:Body>{inner}</soap-env:Body></soap-env:Envelope>"


def _flight_status_xml():
    return _envelope(
        "<ns3:ACS_FlightDetailRS>"
        "<ItineraryResponseList><ItineraryInfoResponse>"
        "<FlightStatus>FINAL</FlightStatus>"
        f"<ScheduledDepartureDate>{FLIGHT_DATE}</ScheduledDepartureDate>"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )


def _passenger_xml(last, pnr, checked_in, boarded):
    return (
        "<PassengerInfo>"
        f"<Name_Details><LastName>{last}</LastName><FirstName>A</FirstName></Name_Details>"
        f"<PNRLocator>{pnr}</PNRLocator>"
        "<PassengerID>1</PassengerID><LineNumber>1</LineNumber>"
        "<BookingClass>Y</BookingClass><Cabin>Y</Cabin><PassengerType>F</PassengerType>"
        f"<CheckIn_Info><CheckInStatus>{str(checked_in).lower()}</CheckInStatus></CheckIn_Info>"
        f"<Boarding_Info><BoardStatus>{str(boarded).lower()}</BoardStatus></Boarding_Info>"
        "</PassengerInfo>"
    )


def _passenger_list_xml():
    rows = "".join(_passenger_xml(*row) for row in MANIFEST)
    return _envelope(
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary><Destination>LHR</Destination></Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{rows}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )


def _people_entries(people):
    """Schema-shaped report body: one PaxEntry and, when repeated, GroupEntry."""
    chunks = []
    for last, pnr in people:
        chunks.append(
            "<Entry><PaxEntry>"
            f"<FirstName>A</FirstName><LastName>{last}</LastName><Locator>{pnr}</Locator>"
            "</PaxEntry></Entry>"
        )
        chunks.append(
            "<Entry><GroupEntry>"
            "<PnrIndicatorType>G</PnrIndicatorType>"
            f"<Name>{last}</Name><Locator>{pnr}</Locator>"
            "</GroupEntry></Entry>"
        )
    return "".join(chunks)


def _passenger_list_entries(people):
    rows = []
    for last, pnr in people:
        rows.append(
            "<Passenger>"
            f"<Name><LastName>{last}</LastName><FirstName>A</FirstName></Name>"
            f"<PNRLocator>{pnr}</PNRLocator>"
            "</Passenger>"
        )
    return "<PassengerList>" + "".join(rows) + "</PassengerList>"


def _trip_report_xml(report_name, people, include_passenger_list):
    passenger_list = _passenger_list_entries(people) if include_passenger_list else ""
    return _envelope(
        "<Trip_ReportsRS>"
        "<Report>"
        f"<ReportName>{report_name}</ReportName>"
        "<ReportFormat>X</ReportFormat>"
        "<ReportResult>"
        "<Header>"
        "<AirlineCode>XX</AirlineCode>"
        "<FlightNumber>0000</FlightNumber>"
        f"<DepartureAirport>{ORIGIN}</DepartureAirport>"
        "</Header>"
        f"<Details>{_people_entries(people)}</Details>"
        "</ReportResult>"
        "</Report>"
        f"{passenger_list}"
        "</Trip_ReportsRS>"
    )


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_db():
    db = get_db()
    for name in (
        "flight_status",
        "passenger_list",
        "reservations",
        "trip_reports",
        "flight_schedules",
        "snapshots",
        "changes",
        "flights",
        "sabre_requests",
    ):
        db[name].delete_many({})
    flights_routes._dashboard_cache.clear()
    yield
    flights_routes._dashboard_cache.clear()


def _ingest(client, stand_in, airline, flight_number):
    with patch("backend.sabre.client.requests.post", side_effect=stand_in):
        response = client.post(
            "/flights/ingest",
            json={
                "airline": airline,
                "flightNumber": flight_number,
                "origin": ORIGIN,
                "departureDate": FLIGHT_DATE,
                "departureDateTime": DEPARTURE_AT,
            },
        )
    assert response.status_code not in (401, 403), response.text
    assert response.status_code in (200, 409), response.text
    return response


def _card(payload, card_id):
    cards = payload["tree"]["statusCards"] if "tree" in payload else payload["statusCards"]
    return next(card for card in cards if card["id"] == card_id)


def _board_offloaded(others):
    """Value the flight board renders for Offloaded."""
    if others["offloadedAvailable"]:
        return others["offloaded"]
    return others["checkedInNotBoarded"]


def _board_no_show(others):
    """Value the flight board renders for No-Show on a closed flight."""
    if others["noShowAvailable"]:
        return others["noShow"]
    return others["notCheckedIn"]


def _assert_figures(payload, offloaded, no_show, offloaded_source, no_show_source):
    others = payload["stateSummary"]["others"]
    assert payload["flightStatus"]["airline"] == "GF"
    assert payload["flightStatus"]["status"] == "FINAL"
    assert _board_offloaded(others) == offloaded
    assert _board_no_show(others) == no_show
    assert others["offloadedAvailable"] is (offloaded_source == "report")
    assert others["noShowAvailable"] is (no_show_source == "report")
    if offloaded_source == "report":
        assert others["offloaded"] == offloaded
    if no_show_source == "report":
        assert others["noShow"] == no_show
    offloaded_card = _card(payload, "offloaded")
    no_show_card = _card(payload, "noShow")
    assert offloaded_card["value"] == offloaded
    assert no_show_card["value"] == no_show
    if offloaded_source == "inferred":
        assert offloaded_card["subLabel"] == "Inferred from manifest"
    else:
        assert offloaded_card["subLabel"] == "MLX report"
    if no_show_source == "inferred":
        assert no_show_card["subLabel"] == "Inferred from manifest"
    else:
        assert no_show_card["subLabel"] == "MLC vs manifest"


def test_schema_trip_report_for_other_airline_does_not_replace_manifest_counts(client):
    """A schema-shaped Trip_ReportsRS for airline XX must not zero the board.

    The converter does not read PaxEntry or GroupEntry, so the stored lists are
    empty. Selecting that newer document still marks offload and no-show as
    available and publishes zero instead of the Gulf Air manifest figures.
    """
    flight_number = "2057"
    stand_in = SabreStandIn()
    _ingest(client, stand_in, "GF", flight_number)

    flights_routes._dashboard_cache.clear()
    before = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert before.status_code == 200, before.text
    _assert_figures(before.json(), INFERRED_OFFLOADED, INFERRED_NO_SHOW, "inferred", "inferred")

    time.sleep(1.1)
    _ingest(client, stand_in, "XX", flight_number)

    stored = list(get_db()["trip_reports"].find({"flightNumber": flight_number}))
    assert len(stored) == 1
    assert stored[0]["airline"] == "XX"
    assert stored[0]["cancelledPassengers"] == []
    assert stored[0]["everBookedPassengers"] == []
    assert stored[0]["origin"] == ORIGIN
    assert stored[0]["departureDate"] == FLIGHT_DATE

    flights_routes._dashboard_cache.clear()
    dashboard = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert dashboard.status_code == 200, dashboard.text
    _assert_figures(
        dashboard.json(), INFERRED_OFFLOADED, INFERRED_NO_SHOW, "inferred", "inferred"
    )

    flights_routes._dashboard_cache.clear()
    tree = client.get(
        f"/flights/{flight_number}/tree",
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert tree.status_code == 200, tree.text
    assert _card(tree.json(), "offloaded")["value"] == INFERRED_OFFLOADED
    assert _card(tree.json(), "offloaded")["subLabel"] == "Inferred from manifest"
    assert _card(tree.json(), "noShow")["value"] == INFERRED_NO_SHOW
    assert _card(tree.json(), "noShow")["subLabel"] == "Inferred from manifest"


def test_other_airline_parsed_lists_do_not_replace_this_flight(client):
    """When the converter stores names, the board must keep this carrier's lists.

    PassengerList is the body convert_trip_report reads. A newer XX document
    with a longer cancelled list and a different ever-booked list must not
    become the Gulf Air offload or no-show count on the dashboard, the tree,
    or a snapshot view.
    """
    flight_number = "2058"
    stand_in = SabreStandIn()
    stand_in.gf_trip_reports = True
    stand_in.foreign_passenger_list = True
    _ingest(client, stand_in, "GF", flight_number)

    time.sleep(1.1)
    _ingest(client, stand_in, "XX", flight_number)

    reports = list(
        get_db()["trip_reports"].find({"flightNumber": flight_number}).sort("fetchedAt", 1)
    )
    assert [doc["airline"] for doc in reports] == ["GF", "XX"]
    assert reports[0]["fetchedAt"] < reports[1]["fetchedAt"]
    assert len(reports[0]["cancelledPassengers"]) == len(GF_CANCELLED)
    assert len(reports[0]["everBookedPassengers"]) == len(GF_EVER_BOOKED)
    assert len(reports[1]["cancelledPassengers"]) == len(XX_CANCELLED)
    assert len(reports[1]["everBookedPassengers"]) == len(XX_EVER_BOOKED)

    flights_routes._dashboard_cache.clear()
    dashboard = client.get(
        f"/flights/{flight_number}/dashboard",
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert dashboard.status_code == 200, dashboard.text
    _assert_figures(
        dashboard.json(),
        len(GF_CANCELLED),
        len(GF_EVER_BOOKED),
        "report",
        "report",
    )

    flights_routes._dashboard_cache.clear()
    tree = client.get(
        f"/flights/{flight_number}/tree",
        params={"origin": ORIGIN, "date": FLIGHT_DATE},
    )
    assert tree.status_code == 200, tree.text
    assert _card(tree.json(), "offloaded")["value"] == len(GF_CANCELLED)
    assert _card(tree.json(), "offloaded")["subLabel"] == "MLX report"
    assert _card(tree.json(), "noShow")["value"] == len(GF_EVER_BOOKED)
    assert _card(tree.json(), "noShow")["subLabel"] == "MLC vs manifest"
