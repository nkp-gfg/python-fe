"""Positive-space company travelers (passenger type P) must not be revenue.

Sabre GetPassengerListRS rows with PassengerType P, a seniority date, and a
Revenue indicator are company travel. The dashboard, passenger summary,
data-audit comparison, and ingestion flight-state summary still count those
rows as paying passengers. These tests fail until that classification is fixed.

They do not cover the Mongo revenue aggregations in
backend/feeder/aggregations.py (passenger_status_distribution) or
backend/api/routes/analytics.py (operational_rates), or the client filters in
frontend/components/dashboard/bottom-detail-panel.tsx and passenger-table.tsx.
Those share the isRevenue-only split but are not executed here.
"""

import copy
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes.data_audit import (  # noqa: E402
    _aggregate_mongo_pax,
    compare_passenger_data,
)
from backend.api.routes.flights import (  # noqa: E402
    _compute_phase_summary,
    _dashboard_cache,
    get_flight_dashboard,
)
from backend.api.routes.passengers import get_passenger_summary  # noqa: E402
from backend.feeder import runner as feeder_runner  # noqa: E402
from backend.feeder.converter import convert_passenger_list  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_XML = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
))

# Minimal SOAP body in the shape SabreClient._extract_body returns to the
# converter. No session header, so this fixture carries no credential.
SYNTHETIC_SOAP = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary><Destination>DMM</Destination></Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details><LastName>FARE</LastName><FirstName>FULL</FirstName></Name_Details>
          <PNRLocator>ABC123</PNRLocator>
          <PassengerType>F</PassengerType>
          <Cabin>Y</Cabin>
          <Indicators><Indicator>NotBoarded</Indicator><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>POS</LastName><FirstName>SPACE</FirstName></Name_Details>
          <PNRLocator>DEF456</PNRLocator>
          <PassengerType>P</PassengerType>
          <Cabin>J</Cabin>
          <SeniorityDate>2001-01-01</SeniorityDate>
          <Indicators><Indicator>NotBoarded</Indicator><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>EMP</LastName><FirstName>LOYEE</FirstName></Name_Details>
          <PNRLocator>GHI789</PNRLocator>
          <PassengerType>E</PassengerType>
          <Cabin>Y</Cabin>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>STANDBY</LastName><FirstName>REV</FirstName></Name_Details>
          <PNRLocator>JKL012</PNRLocator>
          <PassengerType>S</PassengerType>
          <Cabin>Y</Cabin>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>FARE</LastName><FirstName>NONREV</FirstName></Name_Details>
          <PNRLocator>MNO345</PNRLocator>
          <PassengerType>F</PassengerType>
          <Cabin>Y</Cabin>
          <Indicators><Indicator>NotBoarded</Indicator></Indicators>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text, flight_number, departure_date, origin):
    """Run the same parse and convert steps as get_passenger_list → ingestion."""
    client = SabreClient.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(client, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", flight_number, departure_date, origin)


def _revenue_flag(passenger):
    return bool(passenger.get("isRevenue", True))


def _expected_revenue(passengers):
    """Paying passengers. Type P is company travel even when Revenue is set."""
    return sum(
        1
        for p in passengers
        if _revenue_flag(p) and (p.get("passengerType") or "").upper() != "P"
    )


def _expected_non_revenue(passengers):
    return len(passengers) - _expected_revenue(passengers)


def _expected_dashboard_staff(passengers):
    """Dashboard staff: employee, positive space, or a row without Revenue."""
    return sum(
        1
        for p in passengers
        if (p.get("passengerType") or "").upper() in ("E", "P") or not _revenue_flag(p)
    )


def _expected_audit_staff(passengers):
    """Data-audit staff keeps standby (S) and adds positive space (P)."""
    return sum(
        1
        for p in passengers
        if (p.get("passengerType") or "").upper() in ("E", "S", "P") or not _revenue_flag(p)
    )


def _cabin_key(passenger):
    return "business" if passenger.get("cabin") == "J" else "economy"


def _expected_cabin_staff(passengers, cabin_key):
    return sum(
        1
        for p in passengers
        if _cabin_key(p) == cabin_key
        and ((p.get("passengerType") or "").upper() in ("E", "P") or not _revenue_flag(p))
    )


def _expected_cabin_revenue_passengers(passengers, cabin_key):
    return sum(
        1
        for p in passengers
        if _cabin_key(p) == cabin_key
        and not (
            (p.get("passengerType") or "").upper() in ("E", "P") or not _revenue_flag(p)
        )
    )


class _PassengerListDb:
    def __init__(self, doc):
        self._doc = doc

    def __getitem__(self, name):
        assert name == "passenger_list"
        return self

    def find_one(self, query, sort=None):
        if query.get("flightNumber") != self._doc.get("flightNumber"):
            return None
        if query.get("origin") and query["origin"] != self._doc.get("origin"):
            return None
        if query.get("departureDate") and query["departureDate"] != self._doc.get("departureDate"):
            return None
        return copy.deepcopy(self._doc)


def _load_views(doc):
    """Call the three read APIs and the phase summary on one stored manifest."""
    stored = copy.deepcopy(doc)
    stored.pop("_raw", None)
    flight_number = stored["flightNumber"]
    origin = stored["origin"]
    departure_date = stored["departureDate"]
    _dashboard_cache.clear()
    with patch("backend.api.routes.flights.get_db", return_value=object()), patch(
        "backend.api.routes.flights._fetch_dashboard_data_parallel",
        return_value={
            "flight_status": None,
            "passenger_list": copy.deepcopy(stored),
            "reservations": None,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": None,
        },
    ):
        dashboard = get_flight_dashboard(
            flight_number, origin=origin, date=departure_date, snapshot_sequence=None
        )
    with patch(
        "backend.api.routes.passengers.get_db",
        return_value=_PassengerListDb(stored),
    ):
        summary = get_passenger_summary(
            flight_number, origin=origin, date=departure_date
        )
    with patch("backend.api.routes.data_audit._fetch_pg", return_value=None), patch(
        "backend.api.routes.data_audit._fetch_mongo_pax",
        return_value=copy.deepcopy(stored),
    ):
        audit = compare_passenger_data(
            flight_number, origin=origin, date=departure_date, seq=None
        )
    phase = _compute_phase_summary(stored["passengers"])
    audit_stats = _aggregate_mongo_pax(stored)
    return dashboard, summary, audit, phase, audit_stats


def _assert_counts(passengers, dashboard, summary, audit, phase, audit_stats, ingestion_revenue):
    revenue = _expected_revenue(passengers)
    non_revenue = _expected_non_revenue(passengers)
    dashboard_staff = _expected_dashboard_staff(passengers)
    economy_staff = _expected_cabin_staff(passengers, "economy")
    business_staff = _expected_cabin_staff(passengers, "business")
    analysis = dashboard["analysis"]
    staff_in_states = 0
    for bucket in analysis["stateBreakdown"].values():
        staff_in_states += bucket["economyDetail"]["staff"]
        staff_in_states += bucket["businessDetail"]["staff"]
    revenue_card = next(card for card in dashboard["tree"]["statusCards"] if card["id"] == "revenue")
    tree_nodes = {node["id"]: node["value"] for node in dashboard["tree"]["nodes"]}
    rows = {row.field: row.mongoValue for row in audit.rows}
    actual = {
        "dashboard.revenue": analysis["revenue"],
        "dashboard.nonRevenue": analysis["nonRevenue"],
        "dashboard.staff": analysis["economy"]["staff"]["total"] + analysis["business"]["staff"]["total"],
        "dashboard.economyStaff": analysis["economy"]["staff"]["total"],
        "dashboard.businessStaff": analysis["business"]["staff"]["total"],
        "dashboard.economyPassengers": analysis["economy"]["passengers"]["total"],
        "dashboard.businessPassengers": analysis["business"]["passengers"]["total"],
        "dashboard.stateStaff": staff_in_states,
        "dashboard.treeRevenue": revenue_card["value"],
        "dashboard.stateSummary.nonRevenue": dashboard["stateSummary"]["others"]["nonRevenue"],
        "dashboard.tree.econStaff": tree_nodes["econStaff"],
        "dashboard.tree.bizStaff": tree_nodes["bizStaff"],
        "phase.revenue": phase["revenue"],
        "phase.nonRevenue": phase["nonRevenue"],
        "summary.revenue": summary["revenue"],
        "summary.nonRevenue": summary["nonRevenue"],
        "audit.Revenue Passengers": rows["Revenue Passengers"],
        "audit.Non-Revenue / Staff": rows["Non-Revenue / Staff"],
        "audit.revenue": audit_stats["revenue"],
        "audit.nonRevenue": audit_stats["nonRevenue"],
        "audit.staff": audit_stats["staff"],
        "ingestion.revenue": ingestion_revenue,
    }
    expected = {
        "dashboard.revenue": revenue,
        "dashboard.nonRevenue": non_revenue,
        "dashboard.staff": dashboard_staff,
        "dashboard.economyStaff": economy_staff,
        "dashboard.businessStaff": business_staff,
        "dashboard.economyPassengers": _expected_cabin_revenue_passengers(passengers, "economy"),
        "dashboard.businessPassengers": _expected_cabin_revenue_passengers(passengers, "business"),
        "dashboard.stateStaff": dashboard_staff,
        "dashboard.treeRevenue": revenue,
        "dashboard.stateSummary.nonRevenue": non_revenue,
        "dashboard.tree.econStaff": economy_staff,
        "dashboard.tree.bizStaff": business_staff,
        "phase.revenue": revenue,
        "phase.nonRevenue": non_revenue,
        "summary.revenue": revenue,
        "summary.nonRevenue": non_revenue,
        "audit.Revenue Passengers": str(revenue),
        "audit.Non-Revenue / Staff": str(non_revenue),
        "audit.revenue": revenue,
        "audit.nonRevenue": non_revenue,
        "audit.staff": _expected_audit_staff(passengers),
        "ingestion.revenue": revenue,
    }
    mismatches = {key: (actual[key], expected[key]) for key in expected if actual[key] != expected[key]}
    assert mismatches == {}
    assert analysis["revenue"] + analysis["nonRevenue"] == len(passengers)
    assert summary["revenue"] + summary["nonRevenue"] == summary["totalPassengers"]


def _ingestion_revenue(doc):
    """Revenue written onto the flight state during passenger-list ingestion."""
    captured = {}

    def _capture_state(*args, **kwargs):
        captured["summary"] = args[6] if len(args) > 6 else kwargs.get("summary")

    stored = copy.deepcopy(doc)
    with patch.object(feeder_runner.storage, "store_raw_request", return_value="req"), patch.object(
        feeder_runner.storage, "store_snapshot", return_value=("snap", "checksum", False)
    ), patch.object(feeder_runner.storage, "store_passenger_list", return_value="id"), patch.object(
        feeder_runner.storage, "get_previous_snapshot", return_value=None
    ), patch.object(
        feeder_runner.storage, "update_flight_state", side_effect=_capture_state
    ):
        feeder_runner._process_api_call(
            "PassengerList",
            "passenger_list",
            {
                "airline": stored["airline"],
                "flightNumber": stored["flightNumber"],
                "origin": stored["origin"],
                "departureDate": stored["departureDate"],
            },
            {},
            "",
            {"requestXml": "", "httpStatus": 200, "durationMs": 0},
            lambda *_args, **_kwargs: copy.deepcopy(stored),
            (),
        )
    return captured["summary"]["revenue"]


def test_stored_positive_space_rows_are_company_travel_on_every_read_path():
    """Type P with the Revenue indicator set is staff, not a paying passenger.

    Type F with Revenue stays revenue. Type E stays staff. Type S with Revenue
    stays a revenue passenger on the dashboard and stays in the data-audit
    staff count. A full-fare row without Revenue stays non-revenue.
    """
    doc = _parse_passenger_list(SYNTHETIC_SOAP, "2006", "2026-03-18", "LHR")
    passengers = doc["passengers"]
    by_type = {}
    for passenger in passengers:
        by_type.setdefault(passenger["passengerType"], []).append(passenger)

    positive = by_type["P"][0]
    assert positive["isRevenue"] is True
    assert positive["seniorityDate"] == "2001-01-01"
    assert by_type["F"][0]["isRevenue"] is True
    assert by_type["F"][1]["isRevenue"] is False
    assert by_type["E"][0]["isRevenue"] is True
    assert by_type["S"][0]["isRevenue"] is True

    dashboard, summary, audit, phase, audit_stats = _load_views(doc)
    _assert_counts(
        passengers, dashboard, summary, audit, phase, audit_stats, _ingestion_revenue(doc)
    )


def test_gf2006_positive_space_travelers_are_not_revenue_passengers():
    """The committed GF2006 LHR manifest's 15 type P rows are company travel."""
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    doc = _parse_passenger_list(xml_text, "2006", "2026-03-18", "LHR")
    passengers = doc["passengers"]
    positive = [
        p
        for p in passengers
        if p.get("passengerType") == "P" and p.get("seniorityDate") and p.get("isRevenue") is True
    ]
    full_fare = [p for p in passengers if p.get("passengerType") == "F" and p.get("isRevenue") is True]
    assert len(positive) == 15
    assert len(full_fare) == 31
    assert all(p.get("cabin") == "Y" for p in positive)

    dashboard, summary, audit, phase, audit_stats = _load_views(doc)
    _assert_counts(
        passengers, dashboard, summary, audit, phase, audit_stats, _ingestion_revenue(doc)
    )
