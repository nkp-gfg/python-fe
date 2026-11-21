"""Pax-mix totals must count manifest sex edit codes.

The operations board counts male and female from reservation passport sex
only. These tests feed Sabre responses through the parser, the passenger-list
converter, and the dashboard / phase-summary builders. They fail until a
manifest edit code of M or MI counts as male and F or FI counts as female,
including when the reservation sex is missing, blank, conflicting, or
collapsed onto another traveler with the same surname. Passport sex is the
fallback only when the manifest row has none of those four codes.

The same classification feeds:
- GET /flights/{flight_number}/dashboard via _build_dashboard_payload
- GET /flights/{flight_number}/tree via the same payload
- GET /flights/{flight_number}/phase-journey via _compute_phase_summary
"""

from functools import lru_cache
from pathlib import Path

from backend.api.routes.flights import (
    _build_dashboard_payload,
    _build_gender_lookup,
    _compute_phase_summary,
)
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

_REPO = Path(__file__).resolve().parents[2]
_SAMPLES = _REPO / "sample_data" / "sample_xmlfiles"
_PASSENGER_LIST = _SAMPLES / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
_RESERVATIONS = _SAMPLES / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"


def _unknown(total_passengers, analysis):
    """Same subtraction the pax-mix tile renders as unknown gender."""
    return max(
        0,
        total_passengers
        - analysis["totalChildren"]
        - analysis["totalMale"]
        - analysis["totalFemale"],
    )


def _badge(nodes, node_id, badge_type):
    node = next(item for item in nodes if item["id"] == node_id)
    return next(badge["value"] for badge in node["badges"] if badge["type"] == badge_type)


def _passenger(**overrides):
    row = {
        "lastName": "DOE",
        "firstName": "A",
        "pnr": "AAAAAA",
        "cabin": "Y",
        "passengerType": "F",
        "isRevenue": True,
        "editCodes": [],
        "isChild": False,
        "hasInfant": False,
        "isCheckedIn": True,
        "isBoarded": False,
    }
    row.update(overrides)
    return row


def _passenger_list(passengers):
    return {
        "passengers": passengers,
        "totalPassengers": len(passengers),
        "adultCount": sum(1 for row in passengers if not row["isChild"]),
        "childCount": sum(1 for row in passengers if row["isChild"]),
        "infantCount": sum(1 for row in passengers if row["hasInfant"]),
        "totalSouls": len(passengers) + sum(1 for row in passengers if row["hasInfant"]),
        "cabinSummary": [],
        "origin": "BOM",
        "departureDate": "2026-03-19",
    }


def _reservations(*rows):
    grouped = {}
    for pnr, last_name, gender in rows:
        grouped.setdefault(pnr, []).append({"lastName": last_name, "gender": gender})
    return {
        "reservations": [
            {"pnr": pnr, "passengers": people}
            for pnr, people in grouped.items()
        ]
    }


def _dashboard(passenger_doc, reservation_doc=None):
    return _build_dashboard_payload(
        None,
        passenger_doc,
        "BOM",
        "2026-03-19",
        {},
        reservation_doc=reservation_doc,
    )


@lru_cache(maxsize=1)
def _gf2057():
    """Parse the captured Sabre replies with the client the feeder uses."""
    passenger_xml = _PASSENGER_LIST.read_text()
    reservation_xml = _RESERVATIONS.read_text()
    passenger_body = SabreClient._extract_body(
        None, SabreClient._parse_xml(passenger_xml), "GetPassengerListRS"
    )
    reservation_body = SabreClient._extract_body(
        None, SabreClient._parse_xml(reservation_xml), "Trip_SearchRS"
    )
    passenger_doc = convert_passenger_list(
        passenger_body, "GF", "2057", "2026-03-19", "BOM"
    )
    reservation_doc = convert_reservations(
        reservation_body, "GF", "2057", "BOM", "2026-03-19"
    )
    return passenger_doc, reservation_doc


def _assert_gf2057_mix(analysis, passenger_summary, tree_nodes=None):
    assert passenger_summary["totalPassengers"] == 59
    assert analysis["totalMale"] == 40
    assert analysis["totalFemale"] == 18
    assert analysis["totalChildren"] == 1
    assert analysis["totalInfants"] == 1
    assert _unknown(passenger_summary["totalPassengers"], analysis) == 0

    economy = analysis["economy"]
    business = analysis["business"]
    assert economy["passengers"]["male"] == 39
    assert economy["passengers"]["female"] == 16
    assert economy["passengers"]["children"] == 1
    assert economy["staff"]["male"] == 0
    assert economy["staff"]["female"] == 1
    assert business["passengers"]["male"] == 1
    assert business["passengers"]["female"] == 1
    assert business["staff"]["male"] == 0
    assert business["staff"]["female"] == 0

    if tree_nodes is not None:
        assert _badge(tree_nodes, "root", "M") == 40
        assert _badge(tree_nodes, "root", "F") == 18
        assert _badge(tree_nodes, "econPassengers", "M") == 39
        assert _badge(tree_nodes, "econPassengers", "F") == 16
        assert _badge(tree_nodes, "econStaff", "F") == 1
        assert _badge(tree_nodes, "bizPassengers", "M") == 1
        assert _badge(tree_nodes, "bizPassengers", "F") == 1


def test_gf2057_dashboard_without_reservation_counts_manifest_sex():
    passenger_doc, _reservation_doc = _gf2057()
    dashboard = _dashboard(passenger_doc, reservation_doc=None)
    _assert_gf2057_mix(
        dashboard["analysis"],
        dashboard["passengerSummary"],
        dashboard["tree"]["nodes"],
    )


def test_gf2057_dashboard_with_reservation_keeps_manifest_sex():
    passenger_doc, reservation_doc = _gf2057()
    dashboard = _dashboard(passenger_doc, reservation_doc=reservation_doc)
    _assert_gf2057_mix(
        dashboard["analysis"],
        dashboard["passengerSummary"],
        dashboard["tree"]["nodes"],
    )


def test_gf2057_phase_summary_without_reservation_counts_manifest_sex():
    passenger_doc, _reservation_doc = _gf2057()
    summary = _compute_phase_summary(passenger_doc["passengers"], None)
    _assert_gf2057_mix(summary, summary)


def test_gf2057_phase_summary_with_reservation_keeps_manifest_sex():
    passenger_doc, reservation_doc = _gf2057()
    lookup = _build_gender_lookup(reservation_doc)
    summary = _compute_phase_summary(passenger_doc["passengers"], lookup)
    _assert_gf2057_mix(summary, summary)


def test_manifest_sex_overrides_conflicting_passport_sex():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["M"], cabin="J"),
        _passenger(pnr="PNR002", lastName="ALI", editCodes=["F"]),
    ])
    reservation_doc = _reservations(
        ("PNR001", "KHAN", "F"),
        ("PNR002", "ALI", "M"),
    )
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 1
    assert analysis["business"]["passengers"]["male"] == 1
    assert analysis["economy"]["passengers"]["female"] == 1
    assert _unknown(2, analysis) == 0


def test_shared_surname_keeps_each_manifest_letter():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", firstName="A", editCodes=["M"]),
        _passenger(
            pnr="PNR001",
            lastName="KHAN",
            firstName="B",
            editCodes=["F"],
            passengerType="E",
            isRevenue=False,
        ),
    ])
    reservation_doc = _reservations(("PNR001", "KHAN", "F"))
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 1
    assert analysis["economy"]["passengers"]["male"] == 1
    assert analysis["economy"]["staff"]["female"] == 1
    assert _unknown(2, analysis) == 0


def test_blank_passport_sex_uses_manifest_letter():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["M", "DOCS"]),
    ])
    reservation_doc = _reservations(("PNR001", "KHAN", ""))
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 0
    assert _unknown(1, analysis) == 0


def test_manifest_infant_sex_codes_count():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["MI"]),
        _passenger(pnr="PNR002", lastName="ALI", editCodes=["FI"], cabin="J"),
    ])
    reservation_doc = _reservations(
        ("PNR001", "KHAN", "F"),
        ("PNR002", "ALI", "M"),
    )
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 1
    assert analysis["economy"]["passengers"]["male"] == 1
    assert analysis["business"]["passengers"]["female"] == 1
    assert _unknown(2, analysis) == 0

    summary = _compute_phase_summary(
        passenger_doc["passengers"], _build_gender_lookup(reservation_doc)
    )
    assert summary["totalMale"] == 1
    assert summary["totalFemale"] == 1


def test_reservation_infant_sex_used_when_manifest_has_no_sex_code():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["ET", "DOCS"]),
        _passenger(pnr="PNR002", lastName="ALI", editCodes=["ET", "DOCS"]),
    ])
    reservation_doc = _reservations(
        ("PNR001", "KHAN", "MI"),
        ("PNR002", "ALI", "FI"),
    )
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 1
    assert _unknown(2, analysis) == 0


def test_meal_code_does_not_replace_manifest_male():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["M", "AVML", "CM", "SM"]),
    ])
    dashboard = _dashboard(passenger_doc, reservation_doc=None)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 0
    assert analysis["economy"]["passengers"]["male"] == 1
    assert _unknown(1, analysis) == 0


def test_reservation_sex_used_only_when_manifest_has_no_sex_code():
    passenger_doc = _passenger_list([
        _passenger(pnr="PNR001", lastName="KHAN", editCodes=["ET", "CM", "SM"]),
        _passenger(pnr="PNR002", lastName="ALI", editCodes=["DOCS"]),
    ])
    reservation_doc = _reservations(
        ("PNR001", "KHAN", "M"),
        ("PNR002", "ALI", "F"),
    )
    dashboard = _dashboard(passenger_doc, reservation_doc)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 1
    assert analysis["totalFemale"] == 1
    assert _unknown(2, analysis) == 0


def test_passenger_type_and_embedded_letters_are_not_sex():
    passenger_doc = _passenger_list([
        _passenger(
            pnr="PNR001",
            lastName="KHAN",
            passengerType="F",
            editCodes=["ET", "CM", "SM", "FF"],
        ),
        _passenger(
            pnr="PNR002",
            lastName="ALI",
            editCodes=["CHD", "CM"],
            isChild=True,
        ),
    ])
    dashboard = _dashboard(passenger_doc, reservation_doc=None)
    analysis = dashboard["analysis"]
    assert analysis["totalMale"] == 0
    assert analysis["totalFemale"] == 0
    assert analysis["totalChildren"] == 1
    assert _unknown(2, analysis) == 1
