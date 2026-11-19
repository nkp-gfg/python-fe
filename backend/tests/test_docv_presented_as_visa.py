"""DOCV is Sabre's verified-document marker, and the dashboard must not call it visa coverage.

These tests follow a GetPassengerListRS payload through the same parse and convert
steps as SabreClient.get_passenger_list, then through _build_insights and the
dashboard surfaces that render documentCompliance.DOCV.

They fail while those surfaces label that count as a visa. A fix should keep the
DOCV count equal to passengers whose edit codes include DOCV, and label that
count as documents verified. Passengers who only have DOCO (other documentation)
or TWOV (travelling without visa) stay out of that count.

Run from the repository root:

    python backend/tests/test_docv_presented_as_visa.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes.flights import _build_dashboard_payload, _build_insights
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

SAMPLE_LIST = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
FRONTEND = REPO_ROOT / "frontend"
RENDER_TEST = FRONTEND / "tests" / "docv-verified-label.test.tsx"


def _passenger_xml(last_name, first_name, passenger_id, edit_codes):
    codes = "\n".join(f"        <ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
    edit_list = f"<EditCodeList>\n{codes}\n      </EditCodeList>" if edit_codes else "<EditCodeList/>"
    return f"""
    <PassengerInfo>
      <Name_Details>
        <LastName>{last_name}</LastName>
        <FirstName>{first_name}</FirstName>
      </Name_Details>
      <PNRLocator nameAssociationID="1">{last_name}</PNRLocator>
      <PassengerID>{passenger_id}</PassengerID>
      <ThruIndicator>false</ThruIndicator>
      <LineNumber>1</LineNumber>
      <BookingClass>Q</BookingClass>
      <Cabin>Y</Cabin>
      <Destination>DMM</Destination>
      <PassengerType>F</PassengerType>
      <BagCount>0</BagCount>
      <CheckIn_Info>
        <CheckInStatus>false</CheckInStatus>
      </CheckIn_Info>
      <Boarding_Info>
        <BoardStatus>false</BoardStatus>
      </Boarding_Info>
      {edit_list}
    </PassengerInfo>"""


def _envelope(passengers_xml):
    """SOAP body shaped like a GetPassengerListRS, with no security header."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                        xmlns:ns2="http://services.sabre.com/STL/v3"
                        version="4.0.0">
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
        {passengers_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def ingest_passenger_list(xml_text, airline="GF", flight_number="2057", departure_date="2026-03-19", origin="BOM"):
    """Parse and convert the way get_passenger_list hands data to the feeder.

    SabreClient.__init__ reads session settings before any call. Parsing does
    not use them, so the same _parse_xml and _extract_body methods run on an
    uninitialized instance and never open a session.
    """
    client = SabreClient.__new__(SabreClient)
    parsed = client._parse_xml(xml_text)
    body = client._extract_body(parsed, "GetPassengerListRS")
    return convert_passenger_list(body, airline, flight_number, departure_date, origin)


def _manifest_with_known_codes():
    """Three DOCV passengers, plus DOCO-only, TWOV-only, DOCS-only, and DOCA-only."""
    passengers = [
        ("VERIFIED1", "A", "P1", ["DOCV", "DOCS"]),
        ("VERIFIED2", "B", "P2", ["DOCV"]),
        ("VERIFIED3", "C", "P3", ["DOCV", "DOCO"]),
        ("OTHERDOC", "D", "P4", ["DOCO"]),
        ("WITHOUTVISA", "E", "P5", ["TWOV"]),
        ("PASSPORT", "F", "P6", ["DOCS"]),
        ("ADDRESS", "G", "P7", ["DOCA"]),
        ("NONE", "H", "P8", []),
    ]
    xml = _envelope("".join(_passenger_xml(*row) for row in passengers))
    document = ingest_passenger_list(xml)
    by_id = {pax["passengerId"]: pax["editCodes"] for pax in document["passengers"]}
    assert by_id["P1"] == ["DOCV", "DOCS"]
    assert by_id["P2"] == ["DOCV"]
    assert by_id["P4"] == ["DOCO"]
    assert by_id["P5"] == ["TWOV"]
    assert by_id["P8"] == []
    return document


def _counts(passengers):
    def has(code):
        return sum(1 for pax in passengers if code in pax.get("editCodes", []))

    return {"DOCS": has("DOCS"), "DOCV": has("DOCV"), "DOCA": has("DOCA"), "DOCO": has("DOCO"), "TWOV": has("TWOV")}


def test_edit_codes_survive_parse_and_docv_count_ignores_doco_and_twov():
    document = _manifest_with_known_codes()
    passengers = document["passengers"]
    counts = _counts(passengers)
    assert counts == {"DOCS": 2, "DOCV": 3, "DOCA": 1, "DOCO": 2, "TWOV": 1}

    insights = _build_insights(passengers, None, None, document, {})
    compliance = insights["documentCompliance"]
    assert set(compliance) == {"DOCS", "DOCV", "DOCA"}
    assert compliance["DOCV"]["count"] == counts["DOCV"]
    assert compliance["DOCS"]["count"] == counts["DOCS"]
    assert compliance["DOCA"]["count"] == counts["DOCA"]
    assert compliance["DOCV"]["count"] != counts["DOCO"]
    assert compliance["DOCV"]["count"] != counts["TWOV"]
    # A DOCO-only passenger and a TWOV-only passenger are not verified-document hits.
    assert compliance["DOCV"]["pct"] == round(counts["DOCV"] / len(passengers) * 100, 1)

    payload = _build_dashboard_payload(None, document, "BOM", "2026-03-19", {})
    assert payload["insights"]["documentCompliance"] == compliance
    assert payload["passengerSummary"]["totalPassengers"] == len(passengers)
    print("  PASS: test_edit_codes_survive_parse_and_docv_count_ignores_doco_and_twov")
    return document, insights


def test_captured_passenger_list_counts_docv_edit_codes():
    """The committed GetPassengerListRS sample takes the same path as a live response."""
    xml_text = SAMPLE_LIST.read_text(encoding="utf-8")
    document = ingest_passenger_list(xml_text)
    passengers = document["passengers"]
    counts = _counts(passengers)
    assert counts["DOCV"] > 0
    insights = _build_insights(passengers, None, None, document, {})
    assert insights["documentCompliance"]["DOCV"]["count"] == counts["DOCV"]
    assert insights["documentCompliance"]["DOCS"]["count"] == counts["DOCS"]
    assert insights["documentCompliance"]["DOCA"]["count"] == counts["DOCA"]
    print("  PASS: test_captured_passenger_list_counts_docv_edit_codes")


def test_dashboard_labels_docv_as_documents_verified(document, insights):
    """Render the live dashboard components with the insights the API returns."""
    payload = {
        "insights": insights,
        "totalPassengers": document["totalPassengers"],
        "docvCount": insights["documentCompliance"]["DOCV"]["count"],
        "docoCount": _counts(document["passengers"])["DOCO"],
        "twovCount": _counts(document["passengers"])["TWOV"],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        payload_path = handle.name
    try:
        env = os.environ.copy()
        env["NODE_PATH"] = str(FRONTEND / "node_modules")
        completed = subprocess.run(
            ["npx", "--yes", "tsx", "--tsconfig", "tsconfig.json", str(RENDER_TEST), payload_path],
            cwd=FRONTEND,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(payload_path)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise AssertionError(
            "dashboard still presents the DOCV count as visa coverage, or the renderer failed:\n" + output
        )
    print(output.strip())
    print("  PASS: test_dashboard_labels_docv_as_documents_verified")


if __name__ == "__main__":
    print("Running DOCV label tests...")
    manifest, built = test_edit_codes_survive_parse_and_docv_count_ignores_doco_and_twov()
    test_captured_passenger_list_counts_docv_edit_codes()
    test_dashboard_labels_docv_as_documents_verified(manifest, built)
    print("ALL TESTS PASS")
