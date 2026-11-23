"""DOCS coverage must not be shown as a passport percentage.

The passenger-list guide defines DOCS as standard travel documentation and
DOCV as documentation that has been verified. PSPT is the passport edit code.
These tests feed a passenger list through the SOAP parse, the passenger-list
converter, and the dashboard payload the readiness and risk views render.
They fail while those views still title the DOCS percentage as passport
coverage.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload, router
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient
from fastapi.routing import APIRoute

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RENDER_SCRIPT = os.path.join(REPO_ROOT, "frontend", "tests", "render-docs-coverage.mjs")


def _passenger_xml(edit_codes):
    codes = "\n".join(f"            <ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>1001</Flight>
          <DepartureDate>2026-11-23</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>SAMPLE</LastName>
            <FirstName>ONE</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">AAA111</PNRLocator>
          <PassengerID>P1</PassengerID>
          <PassengerType>F</PassengerType>
          <EditCodeList>
{codes}
          </EditCodeList>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _list_from_codes(*codes):
    """Parse one passenger the same way a GetPassengerListRS response is parsed."""
    parsed = SabreClient._parse_xml(_passenger_xml(codes))
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", "1001", "2026-11-23", "BAH")


def _dashboard_for(passenger_lists):
    passengers = []
    for document in passenger_lists:
        passengers.extend(document["passengers"])
    merged = dict(passenger_lists[0])
    merged["passengers"] = passengers
    merged["totalPassengers"] = len(passengers)
    merged["adultCount"] = len(passengers)
    merged["totalSouls"] = len(passengers)
    return _build_dashboard_payload(None, merged, "BAH", "2026-11-23", {})


def _render(dashboard):
    payload = {
        "insights": dashboard["insights"],
        "totalPassengers": dashboard["passengerSummary"]["totalPassengers"],
        "dashboard": dashboard,
    }
    completed = subprocess.run(
        ["node", RENDER_SCRIPT, "-"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=os.path.join(REPO_ROOT, "frontend"),
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Rendering the dashboard views failed.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def _assert_docs_not_passport(text, view_name, docs_pct):
    folded = text.lower()
    assert "passport" not in folded, (
        f"{view_name} describes document coverage as a passport figure: {text}"
    )
    assert "standard travel document" in folded, (
        f"{view_name} does not label DOCS as standard travel documentation: {text}"
    )
    assert f"{docs_pct:g}%" in text or f"{docs_pct}%" in text, (
        f"{view_name} does not show the DOCS coverage percentage {docs_pct}: {text}"
    )


def test_dashboard_route_is_reachable_without_sign_in():
    dashboard_routes = [
        route for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/flights/{flight_number}/dashboard"
        and "GET" in route.methods
    ]
    assert dashboard_routes, "dashboard route is not registered"
    assert dashboard_routes[0].dependencies == []


def test_docs_only_manifest_is_not_passport_coverage():
    docs_only = _list_from_codes("DOCS", "ET")
    second = _list_from_codes("DOCS")
    assert docs_only["passengers"][0]["editCodes"] == ["DOCS", "ET"]
    assert "PSPT" not in docs_only["passengers"][0]["editCodes"]
    assert "DOCV" not in docs_only["passengers"][0]["editCodes"]

    passport_code_only = _list_from_codes("PSPT")
    verified_only = _list_from_codes("DOCV")
    absent_docs = _list_from_codes("XDOCS")

    covered = _dashboard_for([docs_only, second])
    insights = covered["insights"]
    assert insights["documentCompliance"]["DOCS"] == {"count": 2, "pct": 100.0}
    assert insights["documentCompliance"]["DOCV"] == {"count": 0, "pct": 0.0}
    assert insights["documentCompliance"]["DOCA"] == {"count": 0, "pct": 0.0}

    passport_dashboard = _dashboard_for([passport_code_only])
    assert passport_dashboard["insights"]["documentCompliance"]["DOCS"]["count"] == 0
    verified_dashboard = _dashboard_for([verified_only])
    assert verified_dashboard["insights"]["documentCompliance"]["DOCS"]["count"] == 0
    assert verified_dashboard["insights"]["documentCompliance"]["DOCV"]["pct"] == 100.0
    assert _dashboard_for([absent_docs])["insights"]["documentCompliance"]["DOCS"]["count"] == 0

    rendered = _render(covered)
    docs_pct = insights["documentCompliance"]["DOCS"]["pct"]
    readiness = rendered["readiness"]
    docs_summary = readiness.split("Operational Ready", 1)[1].split("Seats Assigned", 1)[0]
    document_section = readiness.split("Document Compliance", 1)[1].split("Readiness Checklist", 1)[0]

    _assert_docs_not_passport(docs_summary, "readiness DOCS summary", docs_pct)
    _assert_docs_not_passport(document_section, "readiness document compliance", docs_pct)
    assert "verified" in document_section.lower()
    assert "visa" not in document_section.lower()
    assert "passport" not in readiness.lower()

    risk = rendered["risk"]
    compliance = risk.split("Passenger Compliance", 1)[1].split("Connection Risk", 1)[0]
    _assert_docs_not_passport(compliance, "risk checklist", docs_pct)

    flight_insights = rendered["flightInsights"]
    document_block = flight_insights.split("Document Compliance", 1)[1].split("Revenue Class Mix", 1)[0]
    _assert_docs_not_passport(document_block, "flight insights document block", docs_pct)
    assert "verified" in document_block.lower()
    assert "visa" not in document_block.lower()

    for view_name in ("documentInfo", "editCodesInfo"):
        text = rendered[view_name]
        folded = text.lower()
        assert "passport" not in folded, f"{view_name} calls a document code a passport: {text}"
        assert "standard travel document" in folded, (
            f"{view_name} does not define DOCS as standard travel documentation: {text}"
        )
        assert "verified" in folded, f"{view_name} does not keep DOCV as verified documentation: {text}"
        assert "visa" not in folded, f"{view_name} describes DOCV as a visa: {text}"
