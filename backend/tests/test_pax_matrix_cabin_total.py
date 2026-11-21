"""The passenger-matrix Total column must equal the cabin rows.

Economy and business are manifest rows in each cabin. The Total cell of that
column is the tree root: every manifest passenger, plus one lap infant for
each INF edit code, plus any jump-seat counts the flight-status document
stored. These tests drive the Sabre parse, the converters, and the dashboard
tree, then render the matrix the operations board and the comparison page
mount. They fail while that Total cell is the root.
"""

import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.api.routes.flights import _build_dashboard_payload, _strip_id  # noqa: E402
from backend.feeder.converter import convert_flight_status, convert_passenger_list  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_PAX = os.path.join(
    REPO, "sample_data", "sample_xmlfiles",
    "GetPassengerListRS_GF2057_BOM_2026-03-19.xml",
)
SAMPLE_STATUS = os.path.join(
    REPO, "sample_data", "sample_xmlfiles",
    "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml",
)
RENDERER = os.path.join(os.path.dirname(__file__), "render_pax_matrix.mjs")
SURFACES = ("matrix", "board", "compare")


def _ensure_esbuild():
    module = os.environ.get("ESBUILD_MODULE", "/tmp/esbuild-tool/node_modules/esbuild")
    if os.path.isfile(os.path.join(module, "package.json")):
        return module
    prefix = os.path.dirname(os.path.dirname(module))
    subprocess.check_call(
        ["npm", "install", "--prefix", prefix, "esbuild@0.25.0"],
        stdout=subprocess.DEVNULL,
    )
    return module


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _tree_from_responses(pax_xml, status_xml, pax_key, status_key, flight_number, origin, departure_date):
    """Same transforms as ingestion plus the dashboard and tree routes."""
    passenger_doc = convert_passenger_list(
        _parse_body(pax_xml, pax_key), "GF", flight_number, departure_date, origin,
    )
    status_doc = convert_flight_status(
        _parse_body(status_xml, status_key), "GF", flight_number, origin, departure_date,
    )
    # The dashboard and tree routes strip _id/_raw, then build one payload.
    passenger_doc = _strip_id(passenger_doc)
    status_doc = _strip_id(status_doc)
    dashboard = _build_dashboard_payload(
        status_doc, passenger_doc, origin, departure_date, {},
    )
    return passenger_doc, status_doc, dashboard["tree"]


def _render(trees):
    proc = subprocess.run(
        ["node", RENDERER, _ensure_esbuild()],
        input=json.dumps(trees),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def _manifest_counts(passenger_doc):
    economy = business = 0
    for passenger in passenger_doc["passengers"]:
        if passenger.get("cabin") == "J":
            business += 1
        else:
            economy += 1
    return economy, business


def _cell(row, index):
    try:
        return int(row[index])
    except (TypeError, ValueError, IndexError) as exc:
        raise AssertionError(f"expected a count in column {index}, got {row!r}") from exc


def _assert_total_column_matches_cabins(rows, economy_rows, business_rows, surface):
    assert len(rows) == 3, rows
    economy, business, total = rows
    assert economy[0] == "Economy", economy
    assert business[0] == "Business", business
    assert total[0] == "Total", total
    assert _cell(economy, 1) == economy_rows, (
        f"{surface} economy total {economy[1]} is not the {economy_rows} manifest rows"
    )
    assert _cell(business, 1) == business_rows, (
        f"{surface} business total {business[1]} is not the {business_rows} manifest rows"
    )
    # Pax, staff, and demographic columns already add the two cabin cells.
    for column in range(2, 8):
        assert _cell(total, column) == _cell(economy, column) + _cell(business, column), (
            f"{surface} column {column} total {total[column]} != "
            f"{economy[column]} + {business[column]}"
        )
    expected = economy_rows + business_rows
    actual = _cell(total, 1)
    assert actual == expected, (
        f"{surface} Total column is {actual}, economy {economy_rows} + business {business_rows} = {expected}"
    )


def _assert_every_surface(rendered, economy_rows, business_rows):
    mismatches = []
    for surface in SURFACES:
        try:
            _assert_total_column_matches_cabins(
                rendered[surface], economy_rows, business_rows, surface,
            )
        except AssertionError as exc:
            mismatches.append(str(exc))
    if mismatches:
        raise AssertionError(" | ".join(mismatches))


def _soap(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger(last_name, cabin, edit_codes, revenue):
    codes = "".join(f"<EditCode>{code}</EditCode>" for code in edit_codes)
    indicator = "<Indicator>Revenue</Indicator>" if revenue else ""
    return f"""
      <PassengerInfo>
        <Name_Details><LastName>{last_name}</LastName><FirstName>TEST</FirstName></Name_Details>
        <PNRLocator>TESTPN</PNRLocator>
        <PassengerID>{last_name}</PassengerID>
        <Cabin>{cabin}</Cabin>
        <PassengerType>F</PassengerType>
        <Indicators>{indicator}</Indicators>
        <EditCodeList>{codes}</EditCodeList>
      </PassengerInfo>
    """


def _passenger_list_xml(passengers):
    body = "".join(passengers)
    return _soap(f"""
      <GetPassengerListRS>
        <ItineraryInfo>
          <Itinerary>
            <Airline>GF</Airline>
            <Flight>1001</Flight>
            <DepartureDate>2026-03-19</DepartureDate>
            <Origin>BAH</Origin>
            <Destination>DXB</Destination>
          </Itinerary>
        </ItineraryInfo>
        <PassengerInfoList>{body}</PassengerInfoList>
      </GetPassengerListRS>
    """)


def _flight_status_xml(cockpit, cabin):
    jump = ""
    if cockpit is not None:
        jump = f"<JumpSeat><Cockpit>{cockpit}</Cockpit><Cabin>{cabin}</Cabin></JumpSeat>"
    return _soap(f"""
      <ACS_FlightDetailRS>
        <ItineraryResponseList>
          <ItineraryInfoResponse><FlightStatus>OPEN</FlightStatus></ItineraryInfoResponse>
        </ItineraryResponseList>
        {jump}
      </ACS_FlightDetailRS>
    """)


def test_gf2057_matrix_total_matches_cabin_rows():
    """Committed GF2057 list: an INF edit code makes the Total cell one higher."""
    pax_xml = open(SAMPLE_PAX, encoding="utf-8").read()
    status_xml = open(SAMPLE_STATUS, encoding="utf-8").read()
    passenger_doc, _status_doc, tree = _tree_from_responses(
        pax_xml, status_xml,
        "GetPassengerListRS", "ns3:ACS_FlightDetailRS",
        "2057", "BOM", "2026-03-19",
    )
    assert passenger_doc["infantCount"] >= 1
    assert any(
        "INF" in (passenger.get("editCodes") or [])
        for passenger in passenger_doc["passengers"]
    )
    economy_rows, business_rows = _manifest_counts(passenger_doc)
    rendered = _render({"gf2057": tree})["gf2057"]
    _assert_every_surface(rendered, economy_rows, business_rows)


def test_infants_and_stored_jump_seats_stay_out_of_the_total_column():
    """Lap infants in both cabins, including a non-revenue row, plus stored jump seats."""
    pax_xml = _passenger_list_xml([
        _passenger("REVENUE", "Y", [], True),
        _passenger("LAPY", "Y", ["INF"], True),
        _passenger("STAFFINF", "Y", ["INF"], False),
        _passenger("LAPJ", "J", ["INF"], True),
    ])
    status_xml = _flight_status_xml(2, 1)
    passenger_doc, status_doc, tree = _tree_from_responses(
        pax_xml, status_xml,
        "GetPassengerListRS", "ACS_FlightDetailRS",
        "1001", "BAH", "2026-03-19",
    )
    stored_jump = status_doc["jumpSeat"]["cockpit"] + status_doc["jumpSeat"]["cabin"]
    assert passenger_doc["infantCount"] == 3
    assert stored_jump == 3
    economy_rows, business_rows = _manifest_counts(passenger_doc)
    assert (economy_rows, business_rows) == (3, 1)
    rendered = _render({"mixed": tree})["mixed"]
    _assert_every_surface(rendered, economy_rows, business_rows)


def test_manifest_without_infants_or_jump_seats_reconciles():
    pax_xml = _passenger_list_xml([
        _passenger("ONEY", "Y", [], True),
        _passenger("ONEJ", "J", ["ET"], True),
    ])
    status_xml = _flight_status_xml(None, None)
    passenger_doc, status_doc, tree = _tree_from_responses(
        pax_xml, status_xml,
        "GetPassengerListRS", "ACS_FlightDetailRS",
        "1001", "BAH", "2026-03-19",
    )
    assert passenger_doc["infantCount"] == 0
    assert status_doc["jumpSeat"]["cockpit"] == 0
    assert status_doc["jumpSeat"]["cabin"] == 0
    economy_rows, business_rows = _manifest_counts(passenger_doc)
    rendered = _render({"plain": tree})["plain"]
    _assert_every_surface(rendered, economy_rows, business_rows)


if __name__ == "__main__":
    tests = [
        test_gf2057_matrix_total_matches_cabin_rows,
        test_infants_and_stored_jump_seats_stay_out_of_the_total_column,
        test_manifest_without_infants_or_jump_seats_reconciles,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} test(s) failed")
        sys.exit(1)
    print("ALL TESTS PASS")
