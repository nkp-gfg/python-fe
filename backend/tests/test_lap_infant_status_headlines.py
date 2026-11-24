"""Operations-card headlines must match the manifest rows they open.

A lap infant is an INF edit code on the accompanying adult. The converter
stores that as hasInfant on the adult and does not add a manifest row.
The status cards currently print each bucket's soul total (rows + lap
infants) as the Booked, Checked-In, Boarded, and No Show headline, and the
cabin subtotal adds those infants again. These tests fail until each
headline and cabin subtotal is the passenger-row count, with the soul
total kept on a label that says souls.

Not covered here, same soul-versus-row presentation elsewhere:
- the header "Passengers on Board" figure (flight-workbench.tsx)
- the pax-tree root labeled "Passengers on Board" (_build_tree_payload)
- the compare page Economy figure taken from economySouls
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    _build_dashboard_payload,
    _derive_flight_phase,
)
from backend.feeder.converter import convert_passenger_list  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
SAMPLE_XML = (
    REPO
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
RENDERER = FRONTEND / "tests" / "render_operation_cards.mts"

BOOKED_TITLES = {
    "CHECK_IN": "Awaiting Check-in",
    "BOARDING": "Not Checked In",
    "CLOSED": "No Show",
    "DEPARTED": "No Show",
}
STATUS_KEYS = ("booked", "checkedIn", "boarded")


def _parse_passenger_list(xml_text):
    """Same parse the Sabre client uses before conversion. No session."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerListRS")


def _passenger_xml(
    *,
    last,
    first,
    pnr,
    pid,
    line,
    cabin,
    seat,
    checked_in,
    boarded,
    edit_codes,
    passenger_type="F",
    revenue=True,
):
    edits = "".join(f"<ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
    indicators = ["Revenue"] if revenue else ["NonRevenue"]
    if checked_in:
        indicators.append("CheckedIn")
    if boarded:
        indicators.append("Boarded")
    indicator_xml = "".join(f"<Indicator>{item}</Indicator>" for item in indicators)
    check_in = "true" if checked_in or boarded else "false"
    board = "true" if boarded else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{pid}</PassengerID>
          <LineNumber>{line}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>{cabin}</Cabin>
          <Seat>{seat}</Seat>
          <Destination>DMM</Destination>
          <PassengerType>{passenger_type}</PassengerType>
          <BagCount>0</BagCount>
          <BoardingPassFlag>{board}</BoardingPassFlag>
          <Indicators>{indicator_xml}</Indicators>
          <CheckIn_Info>
            <CheckInStatus>{check_in}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{board}</BoardStatus>
          </Boarding_Info>
          <EditCodeList>{edits}</EditCodeList>
        </PassengerInfo>
    """


def _envelope(passengers_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>9001</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _status_manifest():
    """One INF adult in each status, plus a seated child and a staff adult."""
    return _envelope(
        _passenger_xml(
            last="BOOKED", first="ADULT", pnr="BKD001", pid="P1", line=1,
            cabin="Y", seat="10A", checked_in=False, boarded=False,
            edit_codes=["INF"],
        )
        + _passenger_xml(
            last="STAFF", first="ADULT", pnr="BKD002", pid="P2", line=2,
            cabin="J", seat="1A", checked_in=False, boarded=False,
            edit_codes=["INF"], passenger_type="E",
        )
        + _passenger_xml(
            last="CHECKED", first="ADULT", pnr="CHK001", pid="P3", line=3,
            cabin="J", seat="2A", checked_in=True, boarded=False,
            edit_codes=["INF"],
        )
        + _passenger_xml(
            last="BOARDED", first="ADULT", pnr="BRD001", pid="P4", line=4,
            cabin="Y", seat="20A", checked_in=True, boarded=True,
            edit_codes=["INF"],
        )
        + _passenger_xml(
            last="BOARDED", first="CHILD", pnr="BRD002", pid="P5", line=5,
            cabin="Y", seat="20B", checked_in=True, boarded=True,
            edit_codes=["CHD"],
        )
    )


def _flight_status(status):
    return {
        "status": status,
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "boarding": {"indicator": ""},
        "gate": "",
        "jumpSeat": {"cockpit": 0, "cabin": 0},
        "aircraft": {},
    }


def _load_manifest(xml_text, flight_number, origin, date):
    body = _parse_passenger_list(xml_text)
    document = convert_passenger_list(body, "GF", flight_number, date, origin)
    dashboard = _build_dashboard_payload(
        _flight_status("PDC"), document, origin, date, {},
    )
    passengers = []
    for person in document["passengers"]:
        row = dict(person)
        row.pop("_raw", None)
        passengers.append(row)
    return document, dashboard, passengers


def _row_status(person):
    if person.get("isBoarded"):
        return "boarded"
    if person.get("isCheckedIn"):
        return "checkedIn"
    return "booked"


def _manifest_rows(passengers):
    """Rows the opened list can show: one per converted passenger, no lap infant."""
    counts = {key: 0 for key in STATUS_KEYS}
    cabins = {
        key: {"Y": 0, "J": 0} for key in STATUS_KEYS
    }
    for person in passengers:
        status = _row_status(person)
        counts[status] += 1
        cabin = "J" if person.get("cabin") == "J" else "Y"
        cabins[status][cabin] += 1
    return counts, cabins


def _phases_under_test():
    """Phase codes the API actually returns for each Sabre flight status."""
    boarded = {
        "boarded": 1,
        "checkedIn": 1,
        "notCheckedIn": 1,
        "stateBreakdown": {
            "booked": {"totalPassengers": 1},
            "checkedIn": {"totalPassengers": 0},
            "boarded": {"totalPassengers": 1},
        },
    }
    quiet = {
        "boarded": 0,
        "checkedIn": 0,
        "notCheckedIn": 1,
        "stateBreakdown": {
            "booked": {"totalPassengers": 1},
            "checkedIn": {"totalPassengers": 0},
            "boarded": {"totalPassengers": 0},
        },
    }
    return [
        _derive_flight_phase(_flight_status("OPENCI"), quiet)["phase"],
        _derive_flight_phase(
            {
                **_flight_status("OPENCI"),
                "boarding": {"indicator": "BDG"},
            },
            boarded,
        )["phase"],
        _derive_flight_phase(_flight_status("FINAL"), boarded)["phase"],
        _derive_flight_phase(_flight_status("PDC"), boarded)["phase"],
    ]


def _render(scenario):
    binary = FRONTEND / "node_modules" / ".bin" / "tsx"
    command = [str(binary)] if binary.exists() else ["npx", "--yes", "tsx"]
    command += ["--tsconfig", "tsconfig.json", str(RENDERER)]
    completed = subprocess.run(
        command,
        cwd=FRONTEND,
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "card renderer failed\n"
            + completed.stdout[-2000:]
            + "\n"
            + completed.stderr[-4000:]
        )
    return json.loads(completed.stdout)


def _card_by_title(cards, title):
    # The closed-flight "Other Passengers" card also prints a No Show row.
    # The status card is the one with economy and business subtotals.
    matches = [
        card for card in cards
        if card["title"] == title and card["cabins"]["Economy"] is not None
    ]
    assert matches, f"no operations card titled {title!r} in {[c['title'] for c in cards]}"
    return matches[0]


def _assert_cards_match_rows(rendered, passengers, phases):
    rows, cabins = _manifest_rows(passengers)
    problems = []
    for phase, snapshot in rendered["phases"].items():
        assert phase in phases
        for key in STATUS_KEYS:
            title = BOOKED_TITLES[phase] if key == "booked" else (
                "Checked-In" if key == "checkedIn" else "Boarded"
            )
            card = _card_by_title(snapshot["cards"], title)
            souls = _bucket_souls(rendered, key)
            if card["headline"] != rows[key]:
                problems.append(
                    f"{phase} {title} headline is {card['headline']}, "
                    f"manifest rows are {rows[key]}, soul total is {souls}"
                )
            if card["cabins"]["Economy"] != cabins[key]["Y"]:
                problems.append(
                    f"{phase} {title} Economy subtotal is {card['cabins']['Economy']}, "
                    f"economy manifest rows are {cabins[key]['Y']}"
                )
            if card["cabins"]["Business"] != cabins[key]["J"]:
                problems.append(
                    f"{phase} {title} Business subtotal is {card['cabins']['Business']}, "
                    f"business manifest rows are {cabins[key]['J']}"
                )
            if souls != rows[key] and card["soulsLabel"] != souls:
                problems.append(
                    f"{phase} {title} soul total {souls} is missing a souls label "
                    f"(label read as {card['soulsLabel']})"
                )
    for key in STATUS_KEYS:
        detail_rows = rendered["detail"][key]
        if detail_rows != rows[key]:
            problems.append(
                f"card list for {key} shows {detail_rows} rows, manifest has {rows[key]}"
            )
        table_rows = rendered["table"][f"{key}:all"]
        if table_rows != rows[key]:
            problems.append(
                f"passenger table for {key} shows {table_rows} rows, manifest has {rows[key]}"
            )
        if rendered["table"][f"{key}:Y"] != cabins[key]["Y"]:
            problems.append(
                f"passenger table {key} economy shows {rendered['table'][f'{key}:Y']}, "
                f"manifest has {cabins[key]['Y']}"
            )
        if rendered["table"][f"{key}:J"] != cabins[key]["J"]:
            problems.append(
                f"passenger table {key} business shows {rendered['table'][f'{key}:J']}, "
                f"manifest has {cabins[key]['J']}"
            )
    assert not problems, "status headlines do not match the manifest lists:\n" + "\n".join(problems)


def _bucket_souls(rendered, key):
    return rendered["buckets"][key]["totalSouls"]


def _scenario(name, dashboard, passengers, flight_number, origin, date, phases):
    buckets = {}
    for key in STATUS_KEYS:
        bucket = dashboard["stateSummary"][key]
        buckets[key] = {
            "totalPassengers": bucket["totalPassengers"],
            "totalSouls": bucket["totalSouls"],
        }
    return {
        "id": name,
        "flightNumber": flight_number,
        "origin": origin,
        "date": date,
        "phases": phases,
        "stateSummary": dashboard["stateSummary"],
        "passengers": passengers,
        "buckets": buckets,
    }


def test_sample_flight_boarded_headline_counts_manifest_rows():
    """GF2057's captured list has one boarded lap infant and 58 boarded rows."""
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    document, dashboard, passengers = _load_manifest(
        xml_text, "2057", "BOM", "2026-03-19",
    )
    assert document["infantCount"] == 1
    assert document["totalPassengers"] == 59
    assert document["totalSouls"] == document["totalPassengers"] + document["infantCount"]
    boarded = dashboard["stateSummary"]["boarded"]
    assert boarded["totalSouls"] == boarded["totalPassengers"] + 1
    assert dashboard["flightPhase"]["phase"] == "DEPARTED"

    phases = _phases_under_test()
    assert phases == ["CHECK_IN", "BOARDING", "CLOSED", "DEPARTED"]
    scenario = _scenario(
        "gf2057", dashboard, passengers, "2057", "BOM", "2026-03-19", phases,
    )
    rendered = _render(scenario)
    rendered["buckets"] = scenario["buckets"]
    _assert_cards_match_rows(rendered, passengers, phases)


def test_each_status_headline_excludes_lap_infants():
    """Booked, checked-in, and boarded each include an INF adult."""
    document, dashboard, passengers = _load_manifest(
        _status_manifest(), "9001", "BOM", "2026-03-19",
    )
    assert document["infantCount"] == 4
    assert sum(1 for person in passengers if person["hasInfant"]) == 4
    for key in STATUS_KEYS:
        bucket = dashboard["stateSummary"][key]
        assert bucket["totalSouls"] > bucket["totalPassengers"], key
        assert bucket["infants"] >= 1, key

    phases = _phases_under_test()
    scenario = _scenario(
        "each-status", dashboard, passengers, "9001", "BOM", "2026-03-19", phases,
    )
    rendered = _render(scenario)
    rendered["buckets"] = scenario["buckets"]
    _assert_cards_match_rows(rendered, passengers, phases)
