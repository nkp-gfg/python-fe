"""Bag coverage must count checked bags, not the presence of a number.

These tests fail while readiness, risk, and briefing treat
``baggage.dataAvailablePct`` as proof that bags are tracked. Passenger-list
ingestion stores a number for every row, including zero when Sabre sends 0,
``NB``, or omits ``BagCount``. A baggage route that only repeats the flight
leg is not a checked bag.

They are expected to fail until that display is fixed. Run:

    python -m pytest backend/tests/test_baggage_coverage.py -q
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
FRONTEND = os.path.join(REPO_ROOT, "frontend")
VIEW_TEST = os.path.join(FRONTEND, "tests", "baggage-coverage-views.tsx")


def _passenger_xml(
    last_name,
    *,
    ready=False,
    bag=None,
    route=False,
):
    """One GetPassengerListRS PassengerInfo element.

    ``bag=None`` omits BagCount. Otherwise the value is written as given,
    including ``0`` and ``NB``.
    """
    parts = [
        "<PassengerInfo>",
        "<Name_Details>",
        f"<LastName>{last_name}</LastName>",
        "<FirstName>TEST</FirstName>",
        "</Name_Details>",
        f"<PassengerID>{last_name}</PassengerID>",
        "<LineNumber>1</LineNumber>",
        "<BookingClass>Y</BookingClass>",
        "<Cabin>Y</Cabin>",
        "<PassengerType>F</PassengerType>",
        "<Destination>LHR</Destination>",
    ]
    if ready:
        parts.append("<Seat>12A</Seat>")
        parts.append("<BoardingPassFlag>true</BoardingPassFlag>")
        parts.append("<EditCodeList><EditCode>DOCS</EditCode></EditCodeList>")
        parts.append(
            "<VCR_Info><VCR_Data><SerialNumber>0000000001</SerialNumber></VCR_Data></VCR_Info>"
        )
    else:
        parts.append("<BoardingPassFlag>false</BoardingPassFlag>")
    if bag is not None:
        parts.append(f"<BagCount>{bag}</BagCount>")
    if route:
        parts.append(
            "<BaggageRouteList><BaggageRoute>"
            "<Airline>GF</Airline><Flight>2006</Flight>"
            "<Origin>BAH</Origin><Destination>LHR</Destination>"
            "<SegmentStatus>HK</SegmentStatus>"
            "</BaggageRoute></BaggageRouteList>"
        )
    parts.append("</PassengerInfo>")
    return "".join(parts)


def _soap(passenger_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">'
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>2006</Flight>"
        "<DepartureDate>2026-03-20</DepartureDate>"
        "<Origin>BAH</Origin><Destination>LHR</Destination>"
        "</Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{passenger_xml}</PassengerInfoList>"
        "</GetPassengerListRS>"
        "</soap-env:Body></soap-env:Envelope>"
    )


def _ingest(passenger_xml):
    """Parse, convert, and build the dashboard payload the board renders."""
    parsed = SabreClient._parse_xml(_soap(passenger_xml))
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    listed = convert_passenger_list(body, "GF", "2006", "2026-03-20", "BAH")
    dashboard = _build_dashboard_payload(
        None, listed, "BAH", "2026-03-20", {},
    )
    return listed, dashboard


def _checked_bag_pct(passengers):
    total = len(passengers)
    checked = sum(1 for pax in passengers if (pax.get("bagCount") or 0) > 0)
    return round(checked / total * 100, 1) if total else 0


def _data_presence_pct(passengers):
    total = len(passengers)
    present = sum(
        1 for pax in passengers
        if isinstance(pax.get("bagCount"), int) and pax["bagCount"] >= 0
    )
    return round(present / total * 100, 1) if total else 0


def _scenario(name, passenger_xml, *, max_overall=None):
    listed, dashboard = _ingest(passenger_xml)
    return {
        "name": name,
        "dashboard": dashboard,
        "checkedBagPct": _checked_bag_pct(listed["passengers"]),
        "dataPresencePct": _data_presence_pct(listed["passengers"]),
        "maxOverall": max_overall,
    }


def _views():
    tsx = os.path.join(FRONTEND, "node_modules", ".bin", "tsx")
    if not os.path.exists(tsx):
        pytest.fail(f"tsx is not installed at {tsx}")
    return tsx


def test_no_checked_bags_are_not_full_coverage_or_a_passing_check():
    """Zero, NB, and omitted bag counts, with a flight-leg baggage route, stay at 0%."""
    passengers = "".join([
        _passenger_xml("OMITTED", ready=True, bag=None, route=True),
        _passenger_xml("ZERO", ready=True, bag="0", route=True),
        _passenger_xml("NONE", ready=False, bag="NB", route=True),
    ])
    scenario = _scenario("no-checked-bags", passengers, max_overall=70)
    assert scenario["checkedBagPct"] == 0
    assert scenario["dataPresencePct"] == 100

    _assert_views([scenario])


def test_checked_bag_share_is_not_replaced_by_data_presence():
    """Two checked bags out of four is 50 percent, including when the others have routes."""
    passengers = "".join([
        _passenger_xml("BAG1", bag="1", route=False),
        _passenger_xml("BAG2", bag="2", route=True),
        _passenger_xml("ROUTEONLY", bag="0", route=True),
        _passenger_xml("EMPTY", bag="0", route=False),
    ])
    scenario = _scenario("half-checked", passengers)
    assert scenario["checkedBagPct"] == 50
    assert scenario["dataPresencePct"] == 100
    _assert_views([scenario])


def test_baggage_weight_does_not_turn_a_weak_flight_green():
    """Nine passengers, eight otherwise ready, nobody checked a bag."""
    rows = []
    for index in range(9):
        rows.append(_passenger_xml(
            f"P{index}",
            ready=index < 8,
            bag="0",
            route=True,
        ))
    scenario = _scenario("near-green", "".join(rows), max_overall=90)
    assert scenario["checkedBagPct"] == 0
    assert scenario["dataPresencePct"] == 100
    _assert_views([scenario])


def test_every_checked_bag_still_passes_tracking():
    """A list where every passenger has a bag above zero remains a real pass."""
    passengers = "".join(
        _passenger_xml(f"BAG{index}", bag="1", route=False)
        for index in range(4)
    )
    scenario = _scenario("all-checked", passengers)
    assert scenario["checkedBagPct"] == 100
    _assert_views([scenario])


def _assert_views(scenarios):
    completed = subprocess.run(
        [_views(), VIEW_TEST],
        input=json.dumps(scenarios),
        text=True,
        capture_output=True,
        cwd=FRONTEND,
        check=False,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stderr or completed.stdout
