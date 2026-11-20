"""DesiredBookingClass is an upgrade request, not the class originally booked.

Sabre GetPassengerList defines DesiredBookingClass as the class the passenger
asked to move into. The flight dashboard currently treats that letter as the
original booking and bookingClass as the seat actually held, so a Y booking
with a J request is reported as a downgrade.

These tests follow the product path: SOAP response parsed the way
SabreClient parses it, converted by convert_passenger_list, then returned by
get_flight_dashboard. They fail while that miscount is still served.
"""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes import flights  # noqa: E402
from backend.feeder.converter import convert_passenger_list  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

VENDOR_SAMPLE = (
    REPO_ROOT
    / "sabre_docs/GetPassengerListRQ-v4/GetPassengerListRQ_v4-0-0_Samples"
    / "GetPassengerListRQ_v4_SamplePayload_4_2_c.xml"
)
RENDER_SCRIPT = Path(__file__).resolve().parent / "render_class_mismatch_screens.mjs"
FRONTEND_ROOT = REPO_ROOT / "frontend"

# Booking letters the insight code already treats as business.
BUSINESS_BOOKING_CLASSES = {"J", "C", "D", "I", "R"}
BUSINESS_CABIN = "J"
ECONOMY_CABIN = "Y"


def _response_xml_from_vendor_sample():
    """Return the SOAP response document inside the vendor sample file.

    The sample file wraps a request and a response. SabreClient parses the
    response envelope it receives from GetPassengerList.
    """
    text = VENDOR_SAMPLE.read_text()
    response = text.split("<!--RESPONSE-->", 1)[1]
    start = response.find("<soap-env:Envelope")
    end = response.find("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    if start < 0 or end < len("</soap-env:Envelope>"):
        raise AssertionError("vendor sample has no SOAP response envelope")
    return response[start:end]


def _passenger_list_from_soap(xml_text, airline, flight_number, departure_date, origin):
    """Parse a GetPassengerList SOAP response the way SabreClient does."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not read client credentials. Skip SabreClient.__init__,
    # which requires the live SABRE_* environment.
    client = SabreClient.__new__(SabreClient)
    body = SabreClient._extract_body(client, parsed, "GetPassengerListRS")
    return convert_passenger_list(
        body, airline, flight_number, departure_date, origin,
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        if not self._docs:
            return None
        return copy.deepcopy(self._docs[0])

    def find(self, query, projection=None):
        return _Cursor([copy.deepcopy(doc) for doc in self._docs])


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _dashboard_from_stored_list(passenger_list, flight_number, origin, date):
    """Call the dashboard route against the stored passenger-list document."""
    database = _Database({
        "passenger_list": _Collection([passenger_list]),
        "flight_status": _Collection([]),
        "reservations": _Collection([]),
        "trip_reports": _Collection([]),
        "flight_schedules": _Collection([]),
        "changes": _Collection([]),
        "flights": _Collection([]),
    })
    flights._dashboard_cache.clear()
    original_get_db = flights.get_db
    flights.get_db = lambda: database
    try:
        return flights.get_flight_dashboard(
            flight_number,
            origin=origin,
            date=date,
            snapshot_sequence=None,
        )
    finally:
        flights.get_db = original_get_db
        flights._dashboard_cache.clear()


def _vendor_dashboard():
    passenger_list = _passenger_list_from_soap(
        _response_xml_from_vendor_sample(),
        "EY", "888", "2017-10-25", "AUH",
    )
    dashboard = _dashboard_from_stored_list(
        passenger_list, "888", "AUH", "2017-10-25",
    )
    return passenger_list, dashboard


def _occupied_cabin_xml():
    """Manifest where the cabin field, not the requested class, decides mismatch.

    Cabin is the booked/held cabin stored by convert_passenger_list. A business
    booking letter held in cabin Y is a downgrade. A Y booking held in cabin J
    is an upgrade. DesiredBookingClass with priority UPG is only a request,
    including when the held cabin already matches the booking letter.
    """
    passengers = [
        ("LOWERJ", "J", ECONOMY_CABIN, "", ""),
        ("LOWERC", "C", ECONOMY_CABIN, "", ""),
        ("LOWERI", "I", ECONOMY_CABIN, "", ""),
        ("LOWERR", "R", ECONOMY_CABIN, "", ""),
        ("SAMEBIZ", "D", BUSINESS_CABIN, "", ""),
        ("RAISED", "Y", BUSINESS_CABIN, "", ""),
        ("ASKJ", "Y", ECONOMY_CABIN, "J", "UPG"),
        ("ASKM", "M", ECONOMY_CABIN, "J", "UPG"),
        ("ASKHELD", "J", BUSINESS_CABIN, "F", "UPG"),
        ("SEATONLY", "J", "", "", ""),
    ]
    blocks = []
    for index, (last, booked, cabin, desired, priority) in enumerate(passengers, start=1):
        cabin_xml = f"<Cabin>{cabin}</Cabin>" if cabin else ""
        desired_xml = f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
        priority_xml = f"<PriorityCode>{priority}</PriorityCode>" if priority else ""
        seat_xml = "<Seat>21A</Seat>" if last == "SEATONLY" else ""
        blocks.append(
            f"""<PassengerInfo>
              <Name_Details><LastName>{last}</LastName><FirstName>TEST</FirstName></Name_Details>
              <PassengerID>P{index}</PassengerID>
              <BookingClass>{booked}</BookingClass>
              {cabin_xml}
              {desired_xml}
              {priority_xml}
              {seat_xml}
            </PassengerInfo>"""
        )
    return f"""<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
      <soap-env:Body>
        <GetPassengerListRS>
          <ItineraryInfo>
            <Itinerary>
              <Airline>GF</Airline>
              <Flight>2057</Flight>
              <DepartureDate>2026-11-20</DepartureDate>
              <Origin>BAH</Origin>
            </Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>
            {''.join(blocks)}
          </PassengerInfoList>
        </GetPassengerListRS>
      </soap-env:Body>
    </soap-env:Envelope>"""


def _occupied_cabin_dashboard():
    passenger_list = _passenger_list_from_soap(
        _occupied_cabin_xml(), "GF", "2057", "2026-11-20", "BAH",
    )
    dashboard = _dashboard_from_stored_list(
        passenger_list, "2057", "BAH", "2026-11-20",
    )
    return passenger_list, dashboard


def _risk_score_without_class_mismatch(insights, total):
    """Score from risk-tab computeRisk with the class-mismatch factor forced to zero.

    The component adds (classMismatch.total / passengers) * 10. An upgrade
    request must not contribute. Weight 10 is the factor in the component;
    it is not a flat 15 points.
    """
    connecting = insights["connectingPassengers"]["connecting"]
    factors = [
        (insights["operationalReadiness"]["notCheckedIn"] / total * 100 if total else 0, 25),
        (insights["operationalReadiness"]["noSeat"] / total * 100 if total else 0, 20),
        (insights["ticketStatus"]["withoutTicket"] / total * 100 if total else 0, 20),
        (insights["connectionRisk"]["atRiskCount"] / max(connecting, 1) * 100, 15),
        (insights["operationalReadiness"]["checkedInNoBP"] / total * 100 if total else 0, 10),
        (0, 10),
    ]
    raw = sum(score * weight / 100 for score, weight in factors)
    clamped = min(100, max(0, raw))
    return round(clamped * 10) / 10


def _render_screens(dashboards):
    esbuild_path = FRONTEND_ROOT / "node_modules" / "esbuild"
    if not esbuild_path.exists():
        pytest.fail(
            "frontend dependencies are missing. From frontend run "
            "`npm install` and `npm install --no-save esbuild`."
        )
    payload_path = REPO_ROOT / "backend" / "tests" / ".class-mismatch-dashboards.json"
    payload_path.write_text(json.dumps(dashboards))
    try:
        completed = subprocess.run(
            ["node", str(RENDER_SCRIPT), str(payload_path)],
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        payload_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        pytest.fail(
            "screen render failed\n"
            f"stdout:\n{completed.stdout[-2000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    return json.loads(completed.stdout)


def _assert_no_render_error(html, surface):
    if html.startswith("RENDER_ERROR:"):
        pytest.fail(f"{surface} failed to render:\n{html}")


def _class_mismatch_raw(risk_html):
    marker = "Class Mismatch</span>"
    index = risk_html.find(marker)
    assert index >= 0, risk_html
    tail = risk_html[index + len(marker):]
    start = tail.find(">")
    end = tail.find("</span>", start)
    return int(tail[start + 1:end].strip())


def _rendered_risk_score(risk_html):
    match = re.search(r"text-6xl[^>]*>(\d+\.\d+)<", risk_html)
    assert match, risk_html[:500]
    return float(match.group(1))


@pytest.fixture(scope="module")
def vendor_case():
    return _vendor_dashboard()


@pytest.fixture(scope="module")
def occupied_case():
    return _occupied_cabin_dashboard()


@pytest.fixture(scope="module")
def screens(vendor_case, occupied_case):
    _, vendor_dashboard = vendor_case
    _, occupied_dashboard = occupied_case
    return _render_screens({
        "vendor": vendor_dashboard,
        "occupied": occupied_dashboard,
    })


def test_vendor_priority_sample_stores_request_separately_from_booking_class(vendor_case):
    passenger_list, _dashboard = vendor_case
    passengers = passenger_list["passengers"]
    requests = [
        passenger for passenger in passengers
        if passenger["desiredBookingClass"]
    ]
    assert {(p["bookingClass"], p["desiredBookingClass"], p["priorityCode"]) for p in requests} == {
        ("Y", "F", "UPG"),
        ("Y", "J", "UPG"),
    }
    assert all(passenger["cabin"] == "" for passenger in passengers)
    assert all(passenger["seat"] == "" for passenger in passengers)


def test_dashboard_does_not_count_upgrade_request_as_downgrade(vendor_case):
    _passenger_list, dashboard = vendor_case
    mismatch = dashboard["insights"]["classMismatch"]
    assert mismatch["downgrades"] == 0
    assert mismatch["upgrades"] == 0
    assert mismatch["total"] == 0
    # The request stays on the upgrade-priority counter.
    assert dashboard["insights"]["standbyUpgrade"]["upgradeTotal"] == 3


def test_dashboard_counts_held_cabin_against_booking_class(occupied_case):
    passenger_list, dashboard = occupied_case
    by_name = {passenger["lastName"]: passenger for passenger in passenger_list["passengers"]}
    assert by_name["LOWERJ"]["bookingClass"] == "J"
    assert by_name["LOWERJ"]["cabin"] == ECONOMY_CABIN
    assert by_name["ASKJ"]["desiredBookingClass"] == "J"
    assert by_name["ASKJ"]["bookingClass"] == "Y"
    assert by_name["ASKJ"]["cabin"] == ECONOMY_CABIN
    assert by_name["SEATONLY"]["seat"] == "21A"
    assert by_name["SEATONLY"]["cabin"] == ""

    mismatch = dashboard["insights"]["classMismatch"]
    # J, C, I, and R held in Y are downgrades. D held in J is the same cabin.
    # Y held in J is an upgrade. Requests and a seat with no cabin are not.
    assert mismatch["downgrades"] == 4
    assert mismatch["upgrades"] == 1
    assert mismatch["total"] == 5
    assert dashboard["insights"]["standbyUpgrade"]["upgradeTotal"] == 3
    assert BUSINESS_BOOKING_CLASSES == {"J", "C", "D", "I", "R"}


def test_request_only_screens_do_not_recommend_goodwill_upgrades(screens):
    vendor = screens["vendor"]
    for surface in ("decisionBriefing", "briefingTab", "revenueTab"):
        html = vendor[surface]
        _assert_no_render_error(html, surface)
        lowered = html.lower()
        assert "complimentary" not in lowered, surface
        assert "re-seating" not in lowered, surface
        assert "reseating" not in lowered, surface
        assert "downgraded" not in lowered, surface
        assert "below their booked" not in lowered, surface
        assert "below booked" not in lowered, surface
        assert "seated above" not in lowered, surface

    insights_html = vendor["flightInsights"]
    _assert_no_render_error(insights_html, "flightInsights")
    assert ">Downgrades</p><p class=\"text-lg font-bold text-rose-500\">0</p>" in insights_html

    exceptions_html = vendor["exceptionsTab"]
    _assert_no_render_error(exceptions_html, "exceptionsTab")
    assert "0 upgrades, 0 downgrades" in exceptions_html

    risk_html = vendor["riskTab"]
    _assert_no_render_error(risk_html, "riskTab")
    assert _class_mismatch_raw(risk_html) == 0


def test_request_only_risk_score_ignores_desired_class(vendor_case, screens):
    _passenger_list, dashboard = vendor_case
    insights = dashboard["insights"]
    total = dashboard["passengerSummary"]["totalPassengers"]
    expected = _risk_score_without_class_mismatch(insights, total)
    rendered = _rendered_risk_score(screens["vendor"]["riskTab"])
    assert rendered == expected


def test_real_cabin_downgrade_still_recommends_reseat(screens):
    occupied = screens["occupied"]
    for surface in ("decisionBriefing", "briefingTab", "revenueTab"):
        html = occupied[surface]
        _assert_no_render_error(html, surface)
        lowered = html.lower()
        assert "complimentary" in lowered, surface
        assert "downgraded" in lowered or "below" in lowered, surface

    insights_html = occupied["flightInsights"]
    _assert_no_render_error(insights_html, "flightInsights")
    assert ">Downgrades</p><p class=\"text-lg font-bold text-rose-500\">4</p>" in insights_html

    exceptions_html = occupied["exceptionsTab"]
    _assert_no_render_error(exceptions_html, "exceptionsTab")
    assert "1 upgrades, 4 downgrades" in exceptions_html

    risk_html = occupied["riskTab"]
    _assert_no_render_error(risk_html, "riskTab")
    assert _class_mismatch_raw(risk_html) == 5


def test_class_mismatch_help_does_not_call_desired_class_the_original(screens):
    html = screens["vendor"]["infoPanel"]
    _assert_no_render_error(html, "infoPanel")
    lowered = html.lower()
    assert "(original)" not in lowered
    assert "desired in bizclasses" not in lowered
    assert "request" in lowered
    assert "cabin" in lowered
