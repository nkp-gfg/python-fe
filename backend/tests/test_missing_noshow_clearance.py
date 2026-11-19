"""Closed and departed flights must not hide manifest no-shows when the trip report is missing.

The dashboard API leaves stateSummary.others.noShow null unless a stored trip
report was compared against the manifest on a FINAL or PDC flight. The
operations card already substitutes the manifest not-checked-in count and
marks it inferred. The briefing, decision briefing, revenue, and risk views
substitute zero instead, so standby clearance stays hidden.

These tests fail until those views use the same fallback: the manifest
not-checked-in count, labeled "Inferred from manifest", only while the trip
report value is unavailable and the flight is closed or departed. A computed
trip-report zero stays zero even if passengers remain not checked in.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import (
    convert_flight_status,
    convert_passenger_list,
    convert_trip_report,
    merge_trip_reports,
)
from backend.sabre.client import SabreClient

INFERRED = "Inferred from manifest"
ORIGIN = "BAH"
DATE = "2026-04-02"
FLIGHT = "9001"


def _soap(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header/>"
        f"<soap-env:Body>{body_xml}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _sabre_body(xml_text: str, response_key: str) -> dict:
    """Parse a SOAP envelope the same way SabreClient does, without calling Sabre."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _passenger_xml(
    last: str,
    first: str,
    pnr: str,
    *,
    checked_in: bool = False,
    boarded: bool = False,
    standby: bool = False,
    seat: str = "22A",
) -> str:
    checkin = "true" if checked_in or boarded else "false"
    board = "true" if boarded else "false"
    ptype = "S" if standby else "F"
    seat_xml = f"<Seat>{seat}</Seat>" if seat else ""
    return f"""
      <PassengerInfo>
        <Name_Details>
          <LastName>{last}</LastName>
          <FirstName>{first}</FirstName>
        </Name_Details>
        <PNRLocator>{pnr}</PNRLocator>
        <PassengerID>PID{pnr}</PassengerID>
        <LineNumber>1</LineNumber>
        <BookingClass>Y</BookingClass>
        <Cabin>Y</Cabin>
        {seat_xml}
        <PassengerType>{ptype}</PassengerType>
        <CheckIn_Info><CheckInStatus>{checkin}</CheckInStatus></CheckIn_Info>
        <Boarding_Info><BoardStatus>{board}</BoardStatus></Boarding_Info>
        <Indicators><Indicator>Revenue</Indicator></Indicators>
        <EditCodeList><EditCode>DOCS</EditCode></EditCodeList>
        <VCR_Info><VCR_Data type="T"><SerialNumber>999{pnr}</SerialNumber></VCR_Data></VCR_Info>
      </PassengerInfo>
    """


def _passenger_list(passengers_xml: str):
    body = _soap(
        f"""
        <GetPassengerListRS>
          <ItineraryInfo>
            <Itinerary>
              <Airline>GF</Airline>
              <Flight>{FLIGHT}</Flight>
              <DepartureDate>{DATE}</DepartureDate>
              <Origin>{ORIGIN}</Origin>
              <Destination>DXB</Destination>
            </Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>
            {passengers_xml}
          </PassengerInfoList>
        </GetPassengerListRS>
        """
    )
    raw = _sabre_body(body, "GetPassengerListRS")
    return convert_passenger_list(raw, "GF", FLIGHT, DATE, ORIGIN)


def _flight_status(status: str | None):
    if status is None:
        return None
    body = _soap(
        f"""
        <ns3:ACS_FlightDetailRS>
          <ItineraryResponseList>
            <ItineraryInfoResponse>
              <Airline>GF</Airline>
              <Flight>{FLIGHT}</Flight>
              <Origin>{ORIGIN}</Origin>
              <DepartureDate>{DATE}</DepartureDate>
              <FlightStatus>{status}</FlightStatus>
            </ItineraryInfoResponse>
          </ItineraryResponseList>
        </ns3:ACS_FlightDetailRS>
        """
    )
    raw = _sabre_body(body, "ns3:ACS_FlightDetailRS")
    return convert_flight_status(raw, "GF", FLIGHT, ORIGIN, DATE)


def _trip_report(passengers: list[tuple[str, str, str]] | None):
    """Build the stored trip-report document the dashboard query would return.

    None means the MLC ever-booked list was not usable. An empty list is a
    computed report with nobody missing.
    """
    if passengers is None:
        return {
            "cancelledPassengers": [],
            "cancelledCount": 0,
            "everBookedPassengers": None,
            "everBookedCount": 0,
        }
    passenger_xml = "".join(
        f"""
        <Passenger>
          <Name><LastName>{last}</LastName><FirstName>{first}</FirstName></Name>
          <PNRLocator>{pnr}</PNRLocator>
        </Passenger>
        """
        for last, first, pnr in passengers
    )
    def one(report_type: str):
        raw = _sabre_body(
            _soap(f"<Trip_ReportsRS><PassengerList>{passenger_xml}</PassengerList></Trip_ReportsRS>"),
            "Trip_ReportsRS",
        )
        return convert_trip_report(raw, "GF", FLIGHT, DATE, ORIGIN, report_type)

    # MLX has a passenger list and no passenger rows. xmltodict turns a
    # completely empty element into None, which convert_trip_report cannot
    # read, so the empty report keeps a sibling field the parser preserves.
    mlx = convert_trip_report(
        _sabre_body(
            _soap("<Trip_ReportsRS><PassengerList><ReportType>MLX</ReportType></PassengerList></Trip_ReportsRS>"),
            "Trip_ReportsRS",
        ),
        "GF", FLIGHT, DATE, ORIGIN, "MLX",
    )
    return merge_trip_reports(mlx, one("MLC"), "GF", FLIGHT, DATE, ORIGIN)


def _names(prefix: str, count: int, start: int = 1) -> list[tuple[str, str, str]]:
    return [(f"{prefix}{i}", "PAX", f"{prefix[:2]}{i:04d}") for i in range(start, start + count)]


def _manifest(
    *,
    boarded: int,
    not_checked_in: int,
    standby: int,
) -> str:
    parts = []
    for last, first, pnr in _names("BRD", boarded):
        parts.append(_passenger_xml(last, first, pnr, boarded=True, seat="14A"))
    for last, first, pnr in _names("NOS", not_checked_in):
        parts.append(_passenger_xml(last, first, pnr, seat=""))
    for last, first, pnr in _names("SBY", standby):
        parts.append(_passenger_xml(last, first, pnr, checked_in=True, standby=True, seat="15C"))
    return "".join(parts)


def build_dashboard(
    *,
    status: str | None,
    boarded: int,
    not_checked_in: int,
    standby: int,
    trip_passengers: list[tuple[str, str, str]] | None | bool = False,
):
    """trip_passengers False means no trip_reports document was stored."""
    pl = _passenger_list(_manifest(boarded=boarded, not_checked_in=not_checked_in, standby=standby))
    fs = _flight_status(status)
    trip = None if trip_passengers is False else _trip_report(trip_passengers)
    return _build_dashboard_payload(fs, pl, ORIGIN, DATE, {}, None, trip, None)


def _noshow_card(dashboard: dict) -> dict:
    return next(card for card in dashboard["tree"]["statusCards"] if card["id"] == "noShow")


def _kpi(html: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}</(?:p|span)>"
        rf"(?:\s*<(?:div|span)[^>]*>)*\s*<(?:p|span)[^>]*>\s*([^<]+?)\s*</(?:p|span)>",
        html,
    )
    assert match, f"KPI {label!r} not found in view"
    return match.group(1).strip()


def _render(scenarios: dict[str, dict]) -> dict:
    script = REPO / "frontend" / "tests" / "render-noshow-views.tsx"
    proc = subprocess.run(
        ["npx", "--yes", "tsx", "--tsconfig", "tsconfig.json", str(script)],
        input=json.dumps(scenarios),
        text=True,
        capture_output=True,
        cwd=REPO / "frontend",
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "view renderer failed\n"
            f"stdout:\n{proc.stdout[-4000:]}\n"
            f"stderr:\n{proc.stderr[-4000:]}"
        )
    rendered = json.loads(proc.stdout)
    for name, views in rendered.items():
        if isinstance(views, dict) and "error" in views:
            raise AssertionError(f"{name} failed to render:\n{views['error']}")
    return rendered


def _assert_inferred_clearance(views: dict, count: int, standby: int):
    clearable = min(count, standby)
    seat_word = "seat" if count == 1 else "seats"
    for view_name in ("briefing", "decision", "revenue", "risk"):
        html = views[view_name]
        assert INFERRED in html, (
            f"{view_name} hid the inferred no-show label while {count} "
            "manifest passengers never checked in"
        )
    briefing = views["briefing"]
    decision = views["decision"]
    revenue = views["revenue"]
    risk = views["risk"]
    assert "No-Show Passengers" in briefing
    assert "No-Show Passengers" in decision
    assert f"{count} pax" in briefing
    assert f"{count} pax" in decision
    assert _kpi(briefing, "No-Shows") == str(count)
    assert _kpi(decision, "No-Shows") == str(count)
    assert f"Clear standby — {clearable} passengers can be accommodated." in briefing
    assert (
        f"Clear standby queue — {clearable} standby passengers can be accommodated."
        in decision
    )
    assert "No clearance opportunity" not in revenue
    assert f"{count} no-show {seat_word} available" in revenue
    assert f"{clearable} can be cleared immediately." in revenue
    assert f"Clearance possible: {count} no-show {seat_word} available" in risk


def _assert_no_inferred_clearance(views: dict, shown_count: int):
    for view_name in ("briefing", "decision", "revenue", "risk"):
        assert INFERRED not in views[view_name], view_name
    assert _kpi(views["briefing"], "No-Shows") == str(shown_count)
    assert _kpi(views["decision"], "No-Shows") == str(shown_count)
    if shown_count == 0:
        assert "No-Show Passengers" not in views["briefing"]
        assert "No-Show Passengers" not in views["decision"]
        assert "No clearance opportunity" in views["revenue"]
        assert "Clearance possible" not in views["risk"]
    else:
        seat_word = "seat" if shown_count == 1 else "seats"
        assert f"{shown_count} no-show {seat_word} available" in views["revenue"]
        assert "No clearance opportunity" not in views["revenue"]


def _assert_operations_shows_noshow(views: dict, count: int):
    operations = views["operations"]
    assert re.search(
        rf"No Show</span>\s*<span[^>]*>\s*<button[^>]*>{count}</button>",
        operations,
    ), "operations card did not show the manifest count under No Show"
    assert "Inferred from manifest data" in operations


SCENARIOS: dict | None = None
RENDERED: dict | None = None


def _views():
    global SCENARIOS, RENDERED
    if RENDERED is None:
        on_manifest = (
            _names("BRD", 6) + _names("NOS", 4) + _names("SBY", 3)
        )
        missing = _names("GON", 2)
        SCENARIOS = {
            "final_missing_report": build_dashboard(
                status="FINAL", boarded=6, not_checked_in=4, standby=3,
            ),
            "final_unusable_mlc": build_dashboard(
                status="FINAL", boarded=6, not_checked_in=4, standby=3,
                trip_passengers=None,
            ),
            "pdc_missing_report": build_dashboard(
                status="PDC", boarded=6, not_checked_in=4, standby=3,
            ),
            "departed_inferred": build_dashboard(
                status=None, boarded=9, not_checked_in=1, standby=1,
            ),
            "final_computed_zero": build_dashboard(
                status="FINAL", boarded=6, not_checked_in=4, standby=3,
                trip_passengers=on_manifest,
            ),
            "final_computed_two": build_dashboard(
                status="FINAL", boarded=6, not_checked_in=4, standby=3,
                trip_passengers=on_manifest + missing,
            ),
            "checkin_open": build_dashboard(
                status="OPENCI", boarded=0, not_checked_in=4, standby=3,
            ),
        }
        RENDERED = _render(SCENARIOS)
    return SCENARIOS, RENDERED


def test_final_without_trip_report_keeps_manifest_noshows():
    scenarios, rendered = _views()
    dashboard = scenarios["final_missing_report"]
    others = dashboard["stateSummary"]["others"]
    assert others["noShow"] is None
    assert others["noShowAvailable"] is False
    assert others["notCheckedIn"] == 4
    assert others["flightClosed"] is True
    assert dashboard["flightPhase"]["phase"] == "CLOSED"
    assert dashboard["insights"]["standbyUpgrade"]["standbyTotal"] == 3
    card = _noshow_card(dashboard)
    assert card["value"] == 4
    assert card["subLabel"] == INFERRED
    _assert_operations_shows_noshow(rendered["final_missing_report"], 4)
    _assert_inferred_clearance(rendered["final_missing_report"], 4, 3)


def test_unusable_mlc_on_final_flight_uses_manifest_fallback():
    scenarios, rendered = _views()
    others = scenarios["final_unusable_mlc"]["stateSummary"]["others"]
    assert others["noShow"] is None
    assert others["noShowAvailable"] is False
    assert others["flightClosed"] is True
    _assert_inferred_clearance(rendered["final_unusable_mlc"], 4, 3)


def test_pdc_without_trip_report_keeps_manifest_noshows():
    scenarios, rendered = _views()
    dashboard = scenarios["pdc_missing_report"]
    assert dashboard["flightPhase"]["phase"] == "DEPARTED"
    assert dashboard["stateSummary"]["others"]["flightClosed"] is True
    assert dashboard["stateSummary"]["others"]["noShow"] is None
    assert dashboard["stateSummary"]["others"]["notCheckedIn"] == 4
    _assert_operations_shows_noshow(rendered["pdc_missing_report"], 4)
    _assert_inferred_clearance(rendered["pdc_missing_report"], 4, 3)


def test_inferred_departed_without_sabre_status_keeps_manifest_noshows():
    scenarios, rendered = _views()
    dashboard = scenarios["departed_inferred"]
    assert dashboard["flightPhase"]["phase"] == "DEPARTED"
    assert dashboard["stateSummary"]["others"]["flightClosed"] is False
    assert dashboard["stateSummary"]["others"]["noShow"] is None
    assert dashboard["stateSummary"]["others"]["notCheckedIn"] == 1
    operations = rendered["departed_inferred"]["operations"]
    assert "No Show" in operations
    _assert_inferred_clearance(rendered["departed_inferred"], 1, 1)


def test_computed_zero_is_not_replaced_by_manifest_count():
    scenarios, rendered = _views()
    others = scenarios["final_computed_zero"]["stateSummary"]["others"]
    assert others["noShow"] == 0
    assert others["noShowAvailable"] is True
    assert others["notCheckedIn"] == 4
    assert others["flightClosed"] is True
    _assert_no_inferred_clearance(rendered["final_computed_zero"], 0)


def test_computed_trip_report_count_is_not_replaced_by_manifest_count():
    scenarios, rendered = _views()
    others = scenarios["final_computed_two"]["stateSummary"]["others"]
    assert others["noShow"] == 2
    assert others["noShowAvailable"] is True
    assert others["notCheckedIn"] == 4
    _assert_no_inferred_clearance(rendered["final_computed_two"], 2)
    assert "4 no-show" not in rendered["final_computed_two"]["revenue"]
    assert "Clearance possible: 2 no-show seats available" in rendered["final_computed_two"]["risk"]


def test_open_checkin_does_not_treat_pending_passengers_as_noshows():
    scenarios, rendered = _views()
    dashboard = scenarios["checkin_open"]
    assert dashboard["flightPhase"]["phase"] == "CHECK_IN"
    assert dashboard["stateSummary"]["others"]["flightClosed"] is False
    assert dashboard["stateSummary"]["others"]["noShow"] is None
    assert dashboard["stateSummary"]["others"]["notCheckedIn"] == 4
    operations = rendered["checkin_open"]["operations"]
    assert "Not Checked In" in operations
    assert "No Show" not in operations
    _assert_no_inferred_clearance(rendered["checkin_open"], 0)


if __name__ == "__main__":
    tests = [
        test_final_without_trip_report_keeps_manifest_noshows,
        test_unusable_mlc_on_final_flight_uses_manifest_fallback,
        test_pdc_without_trip_report_keeps_manifest_noshows,
        test_inferred_departed_without_sabre_status_keeps_manifest_noshows,
        test_computed_zero_is_not_replaced_by_manifest_count,
        test_computed_trip_report_count_is_not_replaced_by_manifest_count,
        test_open_checkin_does_not_treat_pending_passengers_as_noshows,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
        else:
            print(f"PASS: {test.__name__}")
    if failed:
        print(f"{failed} test(s) failed")
        sys.exit(1)
    print("ALL TESTS PASS")
