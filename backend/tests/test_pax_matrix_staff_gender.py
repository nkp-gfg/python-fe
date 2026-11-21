"""Staff document gender must appear in the passenger matrix M and F columns.

The flight tree already records staff gender on the staff nodes and badges.
PaxMatrix currently fills M and F from the revenue-passenger nodes only, so a
non-revenue traveler is counted in the Staff column and omitted from M and F.

These tests fail while that gap is present. They pass once each cabin cell and
the total row include staff male and staff female.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
RENDERER = FRONTEND / "tests" / "render-pax-matrix.ts"
TSX = FRONTEND / "node_modules" / ".bin" / "tsx"
GF2057_PASSENGERS = (
    ROOT / "sample_data" / "sample_xmlfiles" / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
GF2057_RESERVATIONS = (
    ROOT / "sample_data" / "sample_xmlfiles" / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

# Cabin Y and J travelers covering both staff rules (type E, and NonRevenue
# without type E), a female-infant document code, a revenue standby, and an
# edit code M that is a meal code rather than a gender.
SYNTHETIC_PASSENGER_LIST = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details><LastName>STAFFM</LastName><FirstName>BIZ</FirstName></Name_Details>
          <PNRLocator>BIZM01</PNRLocator>
          <Cabin>J</Cabin>
          <PassengerType>E</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>STAFFF</LastName><FirstName>BIZ</FirstName></Name_Details>
          <PNRLocator>BIZF01</PNRLocator>
          <Cabin>J</Cabin>
          <PassengerType>E</PassengerType>
          <Indicators><Indicator>NonRevenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>NONREV</LastName><FirstName>ECON</FirstName></Name_Details>
          <PNRLocator>ECONR1</PNRLocator>
          <Cabin>Y</Cabin>
          <PassengerType>F</PassengerType>
          <Indicators><Indicator>NonRevenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>REVFEM</LastName><FirstName>ECON</FirstName></Name_Details>
          <PNRLocator>ECONF1</PNRLocator>
          <Cabin>Y</Cabin>
          <PassengerType>F</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>REVMAL</LastName><FirstName>BIZ</FirstName></Name_Details>
          <PNRLocator>BIZR01</PNRLocator>
          <Cabin>J</Cabin>
          <PassengerType>F</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>STANDBY</LastName><FirstName>ECON</FirstName></Name_Details>
          <PNRLocator>ESTB01</PNRLocator>
          <Cabin>Y</Cabin>
          <PassengerType>S</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>NODOCS</LastName><FirstName>ECON</FirstName></Name_Details>
          <PNRLocator>ENOD01</PNRLocator>
          <Cabin>Y</Cabin>
          <PassengerType>E</PassengerType>
          <Indicators><Indicator>NonRevenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>MEALCODE</LastName><FirstName>ECON</FirstName></Name_Details>
          <PNRLocator>EMEAL1</PNRLocator>
          <Cabin>Y</Cabin>
          <PassengerType>F</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <EditCodeList><EditCode>M</EditCode></EditCodeList>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

SYNTHETIC_RESERVATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList>
        <Reservations>
          <Reservation Locator="BIZM01">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>STAFFM</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>M</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
          <Reservation Locator="BIZF01">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>STAFFF</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>FI</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
          <Reservation Locator="ECONR1">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>NONREV</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>F</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
          <Reservation Locator="ECONF1">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>REVFEM</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>F</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
          <Reservation Locator="BIZR01">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>REVMAL</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>M</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
          <Reservation Locator="ESTB01">
            <GetReservationRS><Reservation><PassengerReservation><Passengers>
              <Passenger><LastName>STANDBY</LastName><SpecialRequests><APISRequest>
                <DOCSEntry><Gender>M</Gender></DOCSEntry>
              </APISRequest></SpecialRequests></Passenger>
            </Passengers></PassengerReservation></Reservation></GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _extract_response(xml_text, response_key):
    """Parse SOAP the same way SabreClient returns a response body to the feeder."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _documents_from_xml(passenger_xml, reservation_xml, airline, flight_number, origin, departure_date):
    passenger_body = _extract_response(passenger_xml, "GetPassengerListRS")
    reservation_body = _extract_response(reservation_xml, "Trip_SearchRS")
    passenger_doc = convert_passenger_list(
        passenger_body, airline, flight_number, departure_date, origin
    )
    reservation_doc = convert_reservations(
        reservation_body, airline, flight_number, origin, departure_date
    )
    # Drop the archived SOAP body so a failed assertion cannot print it.
    passenger_doc.pop("_raw", None)
    reservation_doc.pop("_raw", None)
    return passenger_doc, reservation_doc


def _is_staff(passenger):
    return passenger.get("passengerType") == "E" or not passenger.get("isRevenue", True)


def _gender_for(passenger, gender_lookup):
    return gender_lookup.get((passenger.get("pnr", ""), passenger.get("lastName", "").upper()), "")


def _expected_matrix(passengers, gender_lookup):
    """Column values the matrix owes the operator.

    M and F count every non-child traveler with a document gender, including
    staff. The Staff column still counts employees and other non-revenue
    travelers. Edit codes are not gender.
    """
    columns = {
        "economy": {"m": 0, "f": 0, "staff": 0},
        "business": {"m": 0, "f": 0, "staff": 0},
        "total": {"m": 0, "f": 0, "staff": 0},
    }
    gendered_staff = 0
    for passenger in passengers:
        cabin = "business" if passenger.get("cabin") == "J" else "economy"
        staff = _is_staff(passenger)
        if staff:
            columns[cabin]["staff"] += 1
            columns["total"]["staff"] += 1
        if passenger.get("isChild"):
            continue
        gender = _gender_for(passenger, gender_lookup)
        if staff and gender in ("M", "F", "FI"):
            gendered_staff += 1
        if gender == "M":
            columns[cabin]["m"] += 1
            columns["total"]["m"] += 1
        elif gender in ("F", "FI"):
            columns[cabin]["f"] += 1
            columns["total"]["f"] += 1
    return columns, gendered_staff


def _render_matrix(tree):
    if not TSX.exists():
        pytest.fail(f"tsx is not installed at {TSX}. Run npm install in frontend, then npm install --no-save tsx.")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(tree, handle)
        tree_path = handle.name
    try:
        completed = subprocess.run(
            [str(TSX), str(RENDERER), tree_path],
            cwd=FRONTEND,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(tree_path)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "renderer exited without output")[-4000:]
        pytest.fail(f"PaxMatrix renderer failed:\n{detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"PaxMatrix renderer did not return JSON: {exc}\n{completed.stdout[-2000:]}")


def _assert_gender_columns(tree, passengers, gender_lookup):
    expected, gendered_staff = _expected_matrix(passengers, gender_lookup)
    assert gendered_staff >= 1, "fixture has no staff traveler with a document gender"
    rendered = _render_matrix(tree)
    mismatches = []
    for row in ("economy", "business", "total"):
        for column in ("m", "f", "staff"):
            if rendered[row][column] != expected[row][column]:
                mismatches.append(
                    f"{row} {column} is {rendered[row][column]}, expected {expected[row][column]}"
                )
    assert not mismatches, (
        "Passenger matrix columns do not include staff document gender:\n"
        + "\n".join(mismatches)
    )
    return rendered, expected


def _assert_tree_keeps_staff_gender(tree, analysis):
    """The tree payload already publishes the counts the matrix must add."""
    by_id = {node["id"]: node for node in tree["nodes"]}
    pairs = (
        ("econStaffMale", analysis["economy"]["staff"]["male"]),
        ("econStaffFemale", analysis["economy"]["staff"]["female"]),
        ("bizStaffMale", analysis["business"]["staff"]["male"]),
        ("bizStaffFemale", analysis["business"]["staff"]["female"]),
        ("econPaxMale", analysis["economy"]["passengers"]["male"]),
        ("econPaxFemale", analysis["economy"]["passengers"]["female"]),
        ("bizPaxMale", analysis["business"]["passengers"]["male"]),
        ("bizPaxFemale", analysis["business"]["passengers"]["female"]),
    )
    for node_id, value in pairs:
        assert by_id[node_id]["value"] == value
    for node_id, male_key, female_key, staff in (
        ("econStaff", "male", "female", analysis["economy"]["staff"]),
        ("bizStaff", "male", "female", analysis["business"]["staff"]),
    ):
        badges = {badge["type"]: badge["value"] for badge in by_id[node_id]["badges"]}
        assert badges["M"] == staff[male_key]
        assert badges["F"] == staff[female_key]


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    flights._dashboard_cache.clear()
    yield
    flights._dashboard_cache.clear()


def test_committed_gf2057_list_includes_gendered_staff_in_matrix_columns():
    """The committed GF2057 passenger list has a cabin Y employee with a document gender."""
    passenger_doc, reservation_doc = _documents_from_xml(
        GF2057_PASSENGERS.read_text(encoding="utf-8"),
        GF2057_RESERVATIONS.read_text(encoding="utf-8"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    dashboard = flights._build_dashboard_payload(
        None, passenger_doc, "BOM", "2026-03-19", {}, reservation_doc
    )
    lookup = flights._build_gender_lookup(reservation_doc)
    _assert_tree_keeps_staff_gender(dashboard["tree"], dashboard["analysis"])
    _assert_gender_columns(dashboard["tree"], passenger_doc["passengers"], lookup)


def test_synthetic_staff_rules_are_included_for_both_cabins():
    """Type E with a Revenue indicator, NonRevenue without type E, and gender FI."""
    passenger_doc, reservation_doc = _documents_from_xml(
        SYNTHETIC_PASSENGER_LIST,
        SYNTHETIC_RESERVATIONS,
        "GF",
        "1",
        "BAH",
        "2026-03-19",
    )
    lookup = flights._build_gender_lookup(reservation_doc)
    by_pnr = {passenger["pnr"]: passenger for passenger in passenger_doc["passengers"]}

    assert by_pnr["BIZM01"]["passengerType"] == "E"
    assert by_pnr["BIZM01"]["isRevenue"] is True
    assert by_pnr["BIZM01"]["cabin"] == "J"
    assert _gender_for(by_pnr["BIZM01"], lookup) == "M"

    assert by_pnr["BIZF01"]["passengerType"] == "E"
    assert by_pnr["BIZF01"]["isRevenue"] is False
    assert _gender_for(by_pnr["BIZF01"], lookup) == "FI"

    assert by_pnr["ECONR1"]["passengerType"] == "F"
    assert by_pnr["ECONR1"]["isRevenue"] is False
    assert by_pnr["ECONR1"]["cabin"] == "Y"
    assert _gender_for(by_pnr["ECONR1"], lookup) == "F"

    assert by_pnr["ESTB01"]["passengerType"] == "S"
    assert by_pnr["ESTB01"]["isRevenue"] is True
    assert _is_staff(by_pnr["ESTB01"]) is False

    assert by_pnr["EMEAL1"]["editCodes"] == ["M"]
    assert _gender_for(by_pnr["EMEAL1"], lookup) == ""

    dashboard = flights._build_dashboard_payload(
        None, passenger_doc, "BAH", "2026-03-19", {}, reservation_doc
    )
    analysis = dashboard["analysis"]
    assert analysis["business"]["staff"]["male"] == 1
    assert analysis["business"]["staff"]["female"] == 1
    assert analysis["economy"]["staff"]["female"] == 1
    assert analysis["economy"]["staff"]["male"] == 0
    assert analysis["economy"]["passengers"]["male"] == 1
    assert analysis["economy"]["passengers"]["female"] == 1
    _assert_tree_keeps_staff_gender(dashboard["tree"], analysis)
    _assert_gender_columns(dashboard["tree"], passenger_doc["passengers"], lookup)


def test_dashboard_and_tree_routes_include_staff_gender_in_matrix(monkeypatch):
    """Both flight endpoints serve the tree the matrix screen and the compare screen render."""
    passenger_doc, reservation_doc = _documents_from_xml(
        SYNTHETIC_PASSENGER_LIST,
        SYNTHETIC_RESERVATIONS,
        "GF",
        "1",
        "BAH",
        "2026-03-19",
    )

    def fake_fetch(db, flight_number, origin, date, snapshot_sequence):
        assert flight_number == "1"
        assert origin == "BAH"
        assert date == "2026-03-19"
        assert snapshot_sequence is None
        return {
            "flight_status": None,
            "passenger_list": passenger_doc,
            "reservations": reservation_doc,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": {"flightSequenceNumber": 1},
        }

    monkeypatch.setattr(flights, "_fetch_dashboard_data_parallel", fake_fetch)
    monkeypatch.setattr(flights, "get_db", lambda: object())
    lookup = flights._build_gender_lookup(reservation_doc)

    dashboard = flights.get_flight_dashboard(
        "1", origin="BAH", date="2026-03-19", snapshot_sequence=None
    )
    tree = flights.get_flight_tree(
        "1", origin="BAH", date="2026-03-19", snapshot_sequence=None
    )

    problems = []
    for label, payload in (("dashboard", dashboard["tree"]), ("tree", tree)):
        try:
            _assert_gender_columns(payload, passenger_doc["passengers"], lookup)
        except AssertionError as exc:
            problems.append(f"{label} endpoint:\n{exc}")
    assert not problems, "\n".join(problems)
