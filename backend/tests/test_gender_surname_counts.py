"""Gender totals must follow each traveler, not the last surname on the booking.

Sabre Trip Search stores a DOCS gender on every traveler. The live-ops
dashboard and the phase summary then classify each manifest row. These tests
parse captured SOAP responses with the product's XML parser and converters,
then run the same dashboard and phase-summary functions the API calls.

They fail while one gender is stored per record locator and last name.
"""

import os
import sys
from xml.sax.saxutils import escape

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    _analyze_passengers,
    _build_dashboard_payload,
    _build_gender_lookup,
    _compute_phase_summary,
)
from backend.feeder.converter import (  # noqa: E402
    convert_passenger_list,
    convert_reservations,
)
from backend.sabre.client import SabreClient  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE = os.path.join(ROOT, "sample_data", "sample_xmlfiles")
TRIP_2274 = os.path.join(SAMPLE, "Trip_SearchRS_GF2274_DMM_2026-03-19.xml")
LISTS_2274 = (
    "GetPassengerListRS_GF2274_DMM_2026-03-19.xml",
    "GetPassengerListRS_GF2274_DMM_2026-03-19_2.xml",
)
TRIP_2057 = os.path.join(SAMPLE, "Trip_SearchRS_GF2057_BOM_2026-03-18.xml")


def _load_sabre_body(path, response_key):
    """Parse a captured SOAP envelope the way SabreClient does."""
    with open(path, encoding="utf-8") as handle:
        parsed = SabreClient._parse_xml(handle.read())
    # _extract_body does not use instance state.
    return SabreClient._extract_body(None, parsed, response_key)


def _gf2274_documents(passenger_list_name):
    trip_body = _load_sabre_body(TRIP_2274, "Trip_SearchRS")
    list_body = _load_sabre_body(
        os.path.join(SAMPLE, passenger_list_name), "GetPassengerListRS")
    reservation_doc = convert_reservations(
        trip_body, "GF", "2274", "DMM", "2026-03-19")
    passenger_doc = convert_passenger_list(
        list_body, "GF", "2274", "2026-03-19", "DMM")
    return reservation_doc, passenger_doc


def _traveler_genders(reservation_doc):
    """DOCS gender keyed by record locator, last name, and first name."""
    index = {}
    for reservation in reservation_doc.get("reservations", []):
        pnr = reservation.get("pnr", "")
        for traveler in reservation.get("passengers", []):
            key = (
                pnr,
                (traveler.get("lastName") or "").upper(),
                (traveler.get("firstName") or "").upper(),
            )
            index[key] = traveler.get("gender") or ""
    return index


def _expected_counts(passengers, reservation_doc):
    """Count each non-child manifest row with that traveler's own DOCS gender.

    A row whose first name does not match a traveler on the booking is not
    given another traveler's gender. Children stay in the child total.
    """
    genders = _traveler_genders(reservation_doc)
    expected = {
        "totalMale": 0,
        "totalFemale": 0,
        "totalChildren": 0,
        "economy": {
            "passengers": {"male": 0, "female": 0},
            "staff": {"male": 0, "female": 0},
        },
        "business": {
            "passengers": {"male": 0, "female": 0},
            "staff": {"male": 0, "female": 0},
        },
    }
    for passenger in passengers:
        if passenger.get("isChild"):
            expected["totalChildren"] += 1
            continue
        key = (
            passenger.get("pnr") or "",
            (passenger.get("lastName") or "").upper(),
            (passenger.get("firstName") or "").upper(),
        )
        gender = genders.get(key, "")
        is_male = gender == "M"
        is_female = gender in ("F", "FI")
        if is_male:
            expected["totalMale"] += 1
        elif is_female:
            expected["totalFemale"] += 1
        cabin = "business" if passenger.get("cabin") == "J" else "economy"
        is_staff = passenger.get("passengerType") == "E" or not passenger.get(
            "isRevenue", True)
        category = "staff" if is_staff else "passengers"
        if is_male:
            expected[cabin][category]["male"] += 1
        elif is_female:
            expected[cabin][category]["female"] += 1
    return expected


def _assert_analysis_matches(analysis, expected):
    assert analysis["totalMale"] == expected["totalMale"]
    assert analysis["totalFemale"] == expected["totalFemale"]
    assert analysis["totalChildren"] == expected["totalChildren"]
    for cabin in ("economy", "business"):
        for category in ("passengers", "staff"):
            assert analysis[cabin][category]["male"] == expected[cabin][category]["male"]
            assert analysis[cabin][category]["female"] == expected[cabin][category]["female"]


def _badge_value(node, badge_type):
    for badge in node.get("badges", []):
        if badge["type"] == badge_type:
            return badge["value"]
    raise AssertionError(f"badge {badge_type} missing on {node.get('id')}")


@pytest.mark.parametrize("passenger_list_name", LISTS_2274)
def test_gf2274_dashboard_and_phase_keep_each_travelers_gender(passenger_list_name):
    """Captured GF2274 Trip Search and passenger lists, same flight and date."""
    reservation_doc, passenger_doc = _gf2274_documents(passenger_list_name)
    passengers = passenger_doc["passengers"]
    expected = _expected_counts(passengers, reservation_doc)
    # The captured responses do contain mixed-gender surname groups.
    assert expected["totalMale"] > 0 and expected["totalFemale"] > 0

    payload = _build_dashboard_payload(
        None, passenger_doc, "DMM", "2026-03-19", {}, reservation_doc)
    analysis = payload["analysis"]
    _assert_analysis_matches(analysis, expected)

    phase = _compute_phase_summary(
        passengers, _build_gender_lookup(reservation_doc))
    assert phase["totalMale"] == expected["totalMale"]
    assert phase["totalFemale"] == expected["totalFemale"]
    assert phase["economy"]["passengers"]["male"] == expected["economy"]["passengers"]["male"]
    assert phase["economy"]["passengers"]["female"] == expected["economy"]["passengers"]["female"]
    assert phase["business"]["passengers"]["male"] == expected["business"]["passengers"]["male"]
    assert phase["business"]["passengers"]["female"] == expected["business"]["passengers"]["female"]

    nodes = {node["id"]: node for node in payload["tree"]["nodes"]}
    assert _badge_value(nodes["root"], "M") == expected["totalMale"]
    assert _badge_value(nodes["root"], "F") == expected["totalFemale"]
    assert _badge_value(nodes["econPassengers"], "M") == expected["economy"]["passengers"]["male"]
    assert _badge_value(nodes["econPassengers"], "F") == expected["economy"]["passengers"]["female"]
    assert _badge_value(nodes["bizPassengers"], "M") == expected["business"]["passengers"]["male"]
    assert _badge_value(nodes["bizPassengers"], "F") == expected["business"]["passengers"]["female"]

    # A borrowed gender still leaves a gender on the row, so the unknown
    # remainder the dashboard cards display does not reveal the swap.
    shown_unknown = max(
        0,
        payload["passengerSummary"]["totalPassengers"]
        - analysis["totalChildren"]
        - analysis["totalMale"]
        - analysis["totalFemale"],
    )
    non_children = payload["passengerSummary"]["totalPassengers"] - expected["totalChildren"]
    matched = expected["totalMale"] + expected["totalFemale"]
    assert shown_unknown == non_children - matched


def test_mixed_surname_family_is_not_collapsed_to_the_last_gender():
    """One booking whose later traveler is male, and one whose later traveler is female."""
    reservation_doc, passenger_doc = _gf2274_documents(LISTS_2274[0])
    lookup = _build_gender_lookup(reservation_doc)
    passengers = passenger_doc["passengers"]

    def rows(pnr, last_name):
        return [
            passenger for passenger in passengers
            if passenger.get("pnr") == pnr
            and (passenger.get("lastName") or "").upper() == last_name
            and not passenger.get("isChild")
        ]

    # IQOWOH / BISWAS: male, female, female, male. The male entry is last.
    biswas = rows("IQOWOH", "BISWAS")
    biswas_expected = _expected_counts(biswas, reservation_doc)
    assert [biswas_expected["totalMale"], biswas_expected["totalFemale"]] == [2, 2]
    biswas_actual = _analyze_passengers(biswas, lookup)
    assert biswas_actual["totalMale"] == 2
    assert biswas_actual["totalFemale"] == 2
    assert biswas_actual["economy"]["passengers"]["male"] == 2
    assert biswas_actual["economy"]["passengers"]["female"] == 2

    # LACREE / BHAGAVATULA: male then female, so a last-name map keeps female.
    bhagavatula = rows("LACREE", "BHAGAVATULA")
    bhagavatula_expected = _expected_counts(bhagavatula, reservation_doc)
    assert [bhagavatula_expected["totalMale"], bhagavatula_expected["totalFemale"]] == [1, 1]
    bhagavatula_actual = _analyze_passengers(bhagavatula, lookup)
    assert bhagavatula_actual["totalMale"] == 1
    assert bhagavatula_actual["totalFemale"] == 1


def _passenger_list_xml(rows):
    """GetPassengerListRS body in the shape convert_passenger_list reads.

    No CHD edit code, so these rows are non-child manifest rows and the
    male and female cards count them.
    """
    blocks = []
    for index, row in enumerate(rows, start=1):
        blocks.append(
            "<PassengerInfo>"
            "<Name_Details>"
            f"<LastName>{escape(row['lastName'])}</LastName>"
            f"<FirstName>{escape(row['firstName'])}</FirstName>"
            "</Name_Details>"
            f'<PNRLocator nameAssociationID="{index}">{escape(row["pnr"])}</PNRLocator>'
            f"<PassengerID>PAX{index}</PassengerID>"
            f"<LineNumber>{index}</LineNumber>"
            "<Cabin>Y</Cabin>"
            "<PassengerType>F</PassengerType>"
            "<Indicators><Indicator>Revenue</Indicator></Indicators>"
            "</PassengerInfo>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body><GetPassengerListRS><PassengerInfoList>"
        + "".join(blocks)
        + "</PassengerInfoList></GetPassengerListRS></soap-env:Body>"
        "</soap-env:Envelope>"
    )
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def test_gf2057_shared_surname_booking_keeps_both_docs_genders():
    """GF2057 Trip Search booking whose travelers share a surname.

    The captured response stores the female DOCS entry before the male entry.
    No passenger list for that date is in the repository, so the manifest rows
    are built with convert_passenger_list from those stored names. Each row is
    a separate non-child traveler.
    """
    trip_body = _load_sabre_body(TRIP_2057, "Trip_SearchRS")
    reservation_doc = convert_reservations(
        trip_body, "GF", "2057", "BOM", "2026-03-18")
    booking = next(
        reservation for reservation in reservation_doc["reservations"]
        if reservation.get("pnr") == "CQVJQD"
    )
    stored = {
        (
            (traveler.get("lastName") or "").upper(),
            (traveler.get("firstName") or "").upper(),
        ): traveler.get("gender") or ""
        for traveler in booking["passengers"]
    }
    assert stored[("PEWEKAR", "NORA MISS")] == "F"
    assert stored[("PEWEKAR", "HADI MSTR")] == "M"
    assert stored[("SHAIKH", "SANIYA ALAM MRS")] == "F"

    list_body = _passenger_list_xml([
        {"pnr": "CQVJQD", "lastName": traveler.get("lastName", ""),
         "firstName": traveler.get("firstName", "")}
        for traveler in booking["passengers"]
    ])
    passenger_doc = convert_passenger_list(
        list_body, "GF", "2057", "2026-03-18", "BOM")
    assert len(passenger_doc["passengers"]) == 3
    assert all(not passenger.get("isChild") for passenger in passenger_doc["passengers"])

    expected = _expected_counts(passenger_doc["passengers"], reservation_doc)
    assert [expected["totalMale"], expected["totalFemale"]] == [1, 2]

    payload = _build_dashboard_payload(
        None, passenger_doc, "BOM", "2026-03-18", {}, reservation_doc)
    _assert_analysis_matches(payload["analysis"], expected)
    phase = _compute_phase_summary(
        passenger_doc["passengers"], _build_gender_lookup(reservation_doc))
    assert phase["totalMale"] == 1
    assert phase["totalFemale"] == 2
    nodes = {node["id"]: node for node in payload["tree"]["nodes"]}
    assert _badge_value(nodes["root"], "M") == 1
    assert _badge_value(nodes["root"], "F") == 2
    assert _badge_value(nodes["econPassengers"], "M") == 1
    assert _badge_value(nodes["econPassengers"], "F") == 2
