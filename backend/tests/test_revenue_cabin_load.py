"""Cabin load shown on the Revenue tab must be Sabre seats, not fare mix.

The Revenue tab receives a dashboard built from a GetPassengerListRS document.
Cabin J load is that cabin's Count divided by Authorized. The Upgrade Potential
card may offer upgrade candidates only when that load is under 70 and Authorized
still exceeds Count. Yield Mix stays the booking-class share.

These assertions fail while the tab treats the booking-class share as cabin load.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "frontend" / "components" / "dashboard" / "revenue-cabin-load.render.tsx"
SAMPLE_XML = ROOT / "sample_data" / "sample_xmlfiles" / "GetPassengerListRS_GF2006_LHR_2026-03-18.xml"
BUSINESS_CLASSES = ("J", "C", "D", "Z", "R", "F")


def js_round(value: float) -> int:
    """Match Math.round for the non-negative percentages this screen shows."""
    return int(math.floor(value + 0.5))


def _passenger_xml(index: int, booking_class: str | None, cabin: str, upgrade: bool) -> str:
    booking = (
        f"<BookingClass>{booking_class}</BookingClass>"
        if booking_class is not None
        else ""
    )
    priority = "<PriorityCode>UPG</PriorityCode>" if upgrade else ""
    return (
        "<PassengerInfo>"
        "<Name_Details><LastName>PAX</LastName>"
        f"<FirstName>N{index}</FirstName></Name_Details>"
        f"<PNRLocator>P{index:05d}</PNRLocator>"
        f"<PassengerID>ID{index}</PassengerID>"
        f"<LineNumber>{index}</LineNumber>"
        f"{booking}"
        f"<Cabin>{cabin}</Cabin>"
        "<PassengerType>P</PassengerType>"
        f"{priority}"
        "</PassengerInfo>"
    )


def _cabin_xml(cabin: str, count: int, authorized: int, destination: str) -> str:
    # Same prefixed CabinInfo shape as the captured GetPassengerListRS.
    return (
        "<ns2:CabinInfo>"
        f"<ns2:Destination>{destination}</ns2:Destination>"
        f"<ns2:Cabin>{cabin}</ns2:Cabin>"
        f"<ns2:Count>{count}</ns2:Count>"
        f"<ns2:Authorized>{authorized}</ns2:Authorized>"
        "</ns2:CabinInfo>"
    )


def _passenger_list_xml(cabins: list[tuple[str, int, int, str]], passengers: list[str]) -> str:
    cabin_xml = "".join(
        _cabin_xml(cabin, count, authorized, destination)
        for cabin, count, authorized, destination in cabins
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3">'
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>2006</Flight>"
        "<DepartureDate>2026-03-18</DepartureDate>"
        "<Origin>LHR</Origin><Destination>BAH</Destination>"
        "</Itinerary>"
        f"<CabinInfoList>{cabin_xml}</CabinInfoList>"
        "</ItineraryInfo>"
        f"<PassengerInfoList>{''.join(passengers)}</PassengerInfoList>"
        "</GetPassengerListRS>"
        "</soap-env:Body></soap-env:Envelope>"
    )


def _manifest(
    business_count: int,
    economy_count: int,
    upgrade_count: int,
    blank_booking_count: int = 0,
) -> list[str]:
    rows: list[str] = []
    index = 1
    for offset in range(business_count):
        rows.append(
            _passenger_xml(index, BUSINESS_CLASSES[offset % len(BUSINESS_CLASSES)], "J", False)
        )
        index += 1
    for offset in range(economy_count):
        rows.append(_passenger_xml(index, "Y", "Y", offset < upgrade_count))
        index += 1
    for _ in range(blank_booking_count):
        rows.append(_passenger_xml(index, None, "Y", False))
        index += 1
    return rows


def dashboard_from_passenger_list(xml_text: str) -> dict:
    """Walk the ingested passenger list into the dashboard object the tab renders."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    document = convert_passenger_list(body, "GF", "2006", "2026-03-18", "LHR")
    document.pop("_raw", None)
    return _build_dashboard_payload(None, document, "LHR", "2026-03-18", {})


def _j_cabins(dashboard: dict) -> list[dict]:
    return [
        cabin
        for cabin in dashboard["passengerSummary"]["cabinSummary"]
        if cabin.get("cabin") == "J"
    ]


def seat_load_percent(dashboard: dict) -> int:
    cabins = _j_cabins(dashboard)
    authorized = sum(int(cabin.get("authorized") or 0) for cabin in cabins)
    count = sum(int(cabin.get("count") or 0) for cabin in cabins)
    if authorized <= 0:
        return 0
    return js_round(count / authorized * 100)


def _class_mix(dashboard: dict) -> tuple[int, int, int]:
    mix = dashboard["insights"]["revenueClassMix"]
    business = sum(count for code, count in mix.items() if code in BUSINESS_CLASSES)
    total = sum(mix.values())
    mix_percent = js_round(business / total * 100) if total else 0
    return business, total, mix_percent


def _expected_yield(dashboard: dict) -> str:
    _business, total, mix_percent = _class_mix(dashboard)
    economy_percent = 100 - mix_percent if total else 0
    return f"J:{mix_percent}% Y:{economy_percent}%"


def _expected_j_cabin_line(dashboard: dict) -> str:
    business, _total, mix_percent = _class_mix(dashboard)
    return f"{business} pax ({mix_percent}%)"


def _expected_note(dashboard: dict) -> str:
    cabins = _j_cabins(dashboard)
    authorized = sum(int(cabin.get("authorized") or 0) for cabin in cabins)
    count = sum(int(cabin.get("count") or 0) for cabin in cabins)
    load = seat_load_percent(dashboard)
    upgrades = int(dashboard["insights"]["standbyUpgrade"]["upgradeTotal"])
    downgrades = int(dashboard["insights"]["classMismatch"]["downgrades"])
    if downgrades > 0:
        noun = "passenger" if downgrades == 1 else "passengers"
        return (
            f"{downgrades} {noun} downgraded — offer complimentary upgrades for goodwill"
        )
    if load < 70 and authorized > count and upgrades > 0:
        noun = "candidate" if upgrades == 1 else "candidates"
        return f"Business cabin at {load}% — {upgrades} upgrade {noun} available"
    return "No class mismatches detected"


def render_revenue_tabs(dashboards: dict[str, dict]) -> dict[str, dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(dashboards, handle)
        payload_path = handle.name
    try:
        completed = subprocess.run(
            ["npx", "--yes", "tsx@4.23.15", str(RENDERER), payload_path],
            cwd=ROOT / "frontend",
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        os.unlink(payload_path)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise AssertionError(f"Revenue tab render failed:\n{detail[-4000:]}")
    return json.loads(completed.stdout)


def _revenue_tab_failures(name: str, dashboard: dict, rendered: dict) -> list[str]:
    view = rendered[name]
    load = seat_load_percent(dashboard)
    _business, _total, mix_percent = _class_mix(dashboard)
    failures = []
    if mix_percent == load:
        failures.append(
            f"{name}: fixture does not separate fare mix ({mix_percent}%) from seat load ({load}%)"
        )
    if view["yieldMix"] != _expected_yield(dashboard):
        failures.append(f"{name}: Yield Mix should stay the booking-class share")
    if view["jCabinLine"] != _expected_j_cabin_line(dashboard):
        failures.append(
            f"{name}: revenue class distribution should stay the booking-class share"
        )
    if view["businessLoad"] != load:
        failures.append(
            f"{name}: Business Load is {view['businessLoad']}% "
            f"(fare-mix share {mix_percent}%), seat load is {load}%"
        )
    expected_note = _expected_note(dashboard)
    if view["upgradeNote"] != expected_note:
        failures.append(
            f"{name}: upgrade text is {view['upgradeNote']!r}, expected {expected_note!r}"
        )
    return failures


def _scenario(
    name: str,
    cabins: list[tuple[str, int, int, str]],
    business_count: int,
    economy_count: int,
    upgrade_count: int,
    blank_booking_count: int = 0,
) -> tuple[str, dict]:
    xml_text = _passenger_list_xml(
        cabins,
        _manifest(business_count, economy_count, upgrade_count, blank_booking_count),
    )
    dashboard = dashboard_from_passenger_list(xml_text)
    j_cabins = _j_cabins(dashboard)
    assert [(c["count"], c["authorized"]) for c in j_cabins] == [
        (count, authorized) for cabin, count, authorized, _destination in cabins if cabin == "J"
    ], name
    assert dashboard["insights"]["classMismatch"]["downgrades"] == 0, name
    assert dashboard["insights"]["standbyUpgrade"]["upgradeTotal"] == upgrade_count, name
    return name, dashboard


def test_revenue_tab_uses_cabin_seat_load_for_upgrade_potential():
    scenarios = [
        # 24 of 24 business seats taken, 24/200 fare mix looks 12% full.
        _scenario(
            "full_cabin",
            [("J", 24, 24, "BAH"), ("Y", 176, 200, "BAH")],
            business_count=24,
            economy_count=176,
            upgrade_count=4,
        ),
        # Finding example: 20 of 24 seats on a 200-passenger flight.
        _scenario(
            "nearly_full_cabin",
            [("J", 20, 24, "BAH"), ("Y", 180, 200, "BAH")],
            business_count=20,
            economy_count=180,
            upgrade_count=3,
        ),
        # Seats remain and seat load is under 70, but the fare mix is lower.
        _scenario(
            "open_cabin",
            [("J", 3, 26, "BAH"), ("Y", 43, 256, "BAH")],
            business_count=3,
            economy_count=43,
            upgrade_count=1,
            blank_booking_count=1,
        ),
        # Count above Authorized. Fare mix still looks sparse.
        _scenario(
            "overbooked_cabin",
            [("J", 26, 24, "BAH"), ("Y", 90, 200, "BAH")],
            business_count=10,
            economy_count=90,
            upgrade_count=2,
        ),
        # No authorized business seats. A 0 load must not be treated as room to upgrade.
        _scenario(
            "zero_authorized",
            [("J", 5, 0, "BAH"), ("Y", 90, 200, "BAH")],
            business_count=10,
            economy_count=90,
            upgrade_count=2,
        ),
        # Two J rows (different destinations) combine into one seat load.
        _scenario(
            "split_j_cabins",
            [("J", 20, 20, "BAH"), ("J", 4, 6, "DMM"), ("Y", 180, 220, "BAH")],
            business_count=20,
            economy_count=180,
            upgrade_count=2,
        ),
        # Sabre count is 0 while fare classes still list business passengers.
        _scenario(
            "empty_authorized_cabin",
            [("J", 0, 26, "BAH"), ("Y", 90, 200, "BAH")],
            business_count=10,
            economy_count=90,
            upgrade_count=2,
        ),
    ]
    captured = dashboard_from_passenger_list(SAMPLE_XML.read_text(encoding="utf-8"))
    scenarios.append(("captured_gf2006", captured))
    dashboards = dict(scenarios)
    rendered = render_revenue_tabs(dashboards)
    failures = [
        failure
        for name, dashboard in dashboards.items()
        for failure in _revenue_tab_failures(name, dashboard, rendered)
    ]
    assert not failures, "\n".join(failures)


def test_sample_passenger_list_keeps_cabin_count_and_authorized():
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    dashboard = dashboard_from_passenger_list(xml_text)
    j_cabins = _j_cabins(dashboard)
    assert [(cabin["count"], cabin["authorized"]) for cabin in j_cabins] == [(3, 26)]
    assert "revenueClassMix" in dashboard["insights"]
    assert seat_load_percent(dashboard) == js_round(3 / 26 * 100)


if __name__ == "__main__":
    test_sample_passenger_list_keeps_cabin_count_and_authorized()
    print("  PASS: test_sample_passenger_list_keeps_cabin_count_and_authorized")
    test_revenue_tab_uses_cabin_seat_load_for_upgrade_potential()
    print("  PASS: test_revenue_tab_uses_cabin_seat_load_for_upgrade_potential")
    print("ALL TESTS PASS")
