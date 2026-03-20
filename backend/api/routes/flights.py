"""Flight status API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError
from backend.api.database import get_db
from backend.api.validators import validate_date, validate_origin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flights", tags=["flights"])


def _strip_id(doc):
    """Remove MongoDB _id and _raw from response."""
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _empty_state_bucket():
    return {
        "totalPassengers": 0,
        "totalSouls": 0,
        "economy": 0,
        "business": 0,
        "economySouls": 0,
        "businessSouls": 0,
        "adults": 0,
        "children": 0,
        "infants": 0,
    }


def _extract_destination(flight_status_doc, passenger_doc):
    if passenger_doc:
        if passenger_doc.get("destination"):
            return passenger_doc["destination"]
        for cabin in passenger_doc.get("cabinSummary", []):
            if cabin.get("destination"):
                return cabin["destination"]

    if flight_status_doc:
        for leg in flight_status_doc.get("legs", []):
            city = leg.get("city")
            if city and city != flight_status_doc.get("origin"):
                return city

    return ""


def _tree_badges(*items):
    badges = []
    for key, value in items:
        if value:
            badges.append({"type": key, "value": value})
    return badges


def _build_gender_lookup(reservation_doc):
    """Build a PNR+lastName → gender map from reservation DOCSEntry data."""
    lookup = {}
    if not reservation_doc:
        return lookup
    for res in reservation_doc.get("reservations", []):
        pnr = res.get("pnr", "")
        for pax in res.get("passengers", []):
            gender = pax.get("gender", "")
            if gender and pnr:
                key = (pnr, pax.get("lastName", "").upper())
                lookup[key] = gender
    return lookup


def _analyze_passengers(passengers, gender_lookup=None):
    """Deep passenger analysis: cabin × gender/age/staff breakdown."""
    result = {
        "economy": {
            "total": 0,
            "passengers": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
            "staff": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
        },
        "business": {
            "total": 0,
            "passengers": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
            "staff": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
        },
        "checkedIn": 0, "boarded": 0, "notCheckedIn": 0,
        "revenue": 0, "nonRevenue": 0,
        "totalMale": 0, "totalFemale": 0, "totalChildren": 0, "totalInfants": 0,
        "cabinTotals": {
            "economy": {"passengers": 0, "souls": 0},
            "business": {"passengers": 0, "souls": 0},
        },
        "stateBreakdown": {
            "booked": _empty_state_bucket(),
            "checkedIn": _empty_state_bucket(),
            "boarded": _empty_state_bucket(),
        },
    }
    for p in passengers:
        cabin = p.get("cabin", "Y")
        is_revenue = p.get("isRevenue", True)
        # Staff = employee type OR non-revenue indicator.
        # Note: "S" = Standby (can be revenue), not staff.
        is_staff = p.get("passengerType") == "E" or not is_revenue
        edit_codes = p.get("editCodes", [])
        is_child = p.get("isChild", False)
        has_infant = p.get("hasInfant", False)
        # Note: Sabre edit codes "M" and "F" are meal/fare codes,
        # NOT gender indicators. Gender is resolved from Trip_SearchRS
        # DOCSEntry (passport data) via cross-reference lookup.
        is_male = False
        is_female = False
        if gender_lookup:
            pnr = p.get("pnr", "")
            last_name = p.get("lastName", "").upper()
            gender = gender_lookup.get((pnr, last_name), "")
            is_male = gender == "M"
            is_female = gender in ("F", "FI")

        if p.get("isCheckedIn"):
            result["checkedIn"] += 1
        if p.get("isBoarded"):
            result["boarded"] += 1
        if not p.get("isCheckedIn"):
            result["notCheckedIn"] += 1
        if is_revenue:
            result["revenue"] += 1
        else:
            result["nonRevenue"] += 1

        if is_child:
            result["totalChildren"] += 1
        elif is_male:
            result["totalMale"] += 1
        elif is_female:
            result["totalFemale"] += 1

        if has_infant:
            result["totalInfants"] += 1

        cabin_key = "business" if cabin == "J" else "economy"
        result[cabin_key]["total"] += 1
        result["cabinTotals"][cabin_key]["passengers"] += 1
        result["cabinTotals"][cabin_key]["souls"] += 1 + \
            (1 if has_infant else 0)
        cat = "staff" if is_staff else "passengers"
        result[cabin_key][cat]["total"] += 1

        if is_child:
            result[cabin_key][cat]["children"] += 1
        elif is_male:
            result[cabin_key][cat]["male"] += 1
        elif is_female:
            result[cabin_key][cat]["female"] += 1
        if has_infant:
            if cat == "passengers":
                result[cabin_key][cat]["infants"] += 1

        state_key = "boarded" if p.get("isBoarded") else "checkedIn" if p.get(
            "isCheckedIn") else "booked"
        bucket = result["stateBreakdown"][state_key]
        bucket["totalPassengers"] += 1
        bucket["totalSouls"] += 1 + (1 if has_infant else 0)
        bucket[cabin_key] += 1
        bucket[cabin_key + "Souls"] += 1 + (1 if has_infant else 0)
        if is_child:
            bucket["children"] += 1
        else:
            bucket["adults"] += 1
        if has_infant:
            bucket["infants"] += 1

    return result


def _build_tree_payload(analysis, passenger_summary, flight_status=None,
                        offloaded=None, no_show=None,
                        offloaded_available=False, no_show_available=False):
    ep = analysis.get("economy", {}).get("passengers", {})
    es = analysis.get("economy", {}).get("staff", {})
    bp = analysis.get("business", {}).get("passengers", {})
    bs = analysis.get("business", {}).get("staff", {})
    total_souls = passenger_summary.get("totalSouls", 0)

    # Jump seat from flight_status (ACS_FlightDetailRS)
    js = (flight_status or {}).get("jumpSeat") or {}
    jump_seat = js.get("cockpit", 0) + js.get("cabin", 0)

    # Persons on board = all souls from manifest + jump seat occupants
    persons_on_board = total_souls + jump_seat

    # Gender availability: Trip_SearchRS DOCSEntry cross-ref via PNR+lastName
    has_gender = (analysis.get("totalMale", 0) +
                  analysis.get("totalFemale", 0)) > 0
    gender_sub = "DOCS gender" if has_gender else "Needs reservations"

    # Adults = total passengers minus children (derived count)
    ep_adults = ep.get("total", 0) - ep.get("children", 0)
    bp_adults = bp.get("total", 0) - bp.get("children", 0)
    es_adults = es.get("total", 0)  # staff has no children
    bs_adults = bs.get("total", 0)

    # Ungendered = passengers with no gender from Trip_SearchRS lookup
    ep_ungendered = ep_adults - ep.get("male", 0) - ep.get("female", 0)
    bp_ungendered = bp_adults - bp.get("male", 0) - bp.get("female", 0)
    es_ungendered = es_adults - es.get("male", 0) - es.get("female", 0)
    bs_ungendered = bs_adults - bs.get("male", 0) - bs.get("female", 0)

    # ── Unavailable colour ──────────────────────────
    na_border = "#555555"
    na_text = "#777777"

    # ── Layout coordinates ──────────────────────────
    # Row 3 leaf nodes (y=420) — placed first, parents centered above
    # Economy passengers: Male, Female, Children, Infants
    ep_m_x, ep_f_x, ep_c_x, ep_i_x = 58, 136, 214, 292
    # Economy staff: Male, Female
    es_m_x, es_f_x = 396, 474
    # Business passengers: Male, Female, Children, Infants
    bp_m_x, bp_f_x, bp_c_x, bp_i_x = 578, 656, 734, 812
    # Business staff: Male, Female
    bs_m_x, bs_f_x = 916, 994

    # Row 2 crew leaves (y=300) — under CabinCrew / FlightCrew
    cc_m_x, cc_f_x = 1062, 1140
    fc_m_x, fc_f_x = 1218, 1296

    # Row 2 parents (y=300) — centered on their row-3 children
    econ_pax_x = (ep_m_x + ep_i_x) // 2      # 175
    econ_staff_x = (es_m_x + es_f_x) // 2     # 435
    biz_pax_x = (bp_m_x + bp_i_x) // 2        # 695
    biz_staff_x = (bs_m_x + bs_f_x) // 2      # 955

    # Row 1 parents (y=175) — centered on row-2 children
    econ_x = (econ_pax_x + econ_staff_x) // 2    # 305
    biz_x = (biz_pax_x + biz_staff_x) // 2       # 825
    cabin_crew_x = (cc_m_x + cc_f_x) // 2        # 1101
    flight_crew_x = (fc_m_x + fc_f_x) // 2       # 1257

    # Root (y=55) — centered on canvas
    root_x = 690

    LW, LH = 74, 70       # leaf node size
    MW, MH = 116, 66      # mid-level node size
    PW, PH = 130, 72      # parent node size (cabins)
    CW, CH = 124, 72      # crew parent node size
    RW, RH = 175, 80      # root node size
    SW, SH = 108, 58      # status card size

    positions = {
        # Row 0 — Root
        "root":             {"x": root_x, "y": 55, "w": RW, "h": RH},
        # Row 1 — Cabin + Crew parents
        "economy":          {"x": econ_x, "y": 175, "w": PW, "h": PH},
        "business":         {"x": biz_x, "y": 175, "w": PW, "h": PH},
        "cabinCrew":        {"x": cabin_crew_x, "y": 175, "w": CW, "h": CH},
        "flightCrew":       {"x": flight_crew_x, "y": 175, "w": CW, "h": CH},
        # Row 2 — Pax/Staff categories + crew leaves
        "econPassengers":   {"x": econ_pax_x, "y": 300, "w": MW, "h": MH},
        "econStaff":        {"x": econ_staff_x, "y": 300, "w": MW, "h": MH},
        "bizPassengers":    {"x": biz_pax_x, "y": 300, "w": MW, "h": MH},
        "bizStaff":         {"x": biz_staff_x, "y": 300, "w": MW, "h": MH},
        "cabinCrewMale":    {"x": cc_m_x, "y": 300, "w": LW, "h": LH},
        "cabinCrewFemale":  {"x": cc_f_x, "y": 300, "w": LW, "h": LH},
        "flightCrewMale":   {"x": fc_m_x, "y": 300, "w": LW, "h": LH},
        "flightCrewFemale": {"x": fc_f_x, "y": 300, "w": LW, "h": LH},
        # Row 3 — Demographics (leaves)
        "econPaxMale":      {"x": ep_m_x, "y": 420, "w": LW, "h": LH},
        "econPaxFemale":    {"x": ep_f_x, "y": 420, "w": LW, "h": LH},
        "econPaxChildren":  {"x": ep_c_x, "y": 420, "w": LW, "h": LH},
        "econPaxInfants":   {"x": ep_i_x, "y": 420, "w": LW, "h": LH},
        "econStaffMale":    {"x": es_m_x, "y": 420, "w": LW, "h": LH},
        "econStaffFemale":  {"x": es_f_x, "y": 420, "w": LW, "h": LH},
        "bizPaxMale":       {"x": bp_m_x, "y": 420, "w": LW, "h": LH},
        "bizPaxFemale":     {"x": bp_f_x, "y": 420, "w": LW, "h": LH},
        "bizPaxChildren":   {"x": bp_c_x, "y": 420, "w": LW, "h": LH},
        "bizPaxInfants":    {"x": bp_i_x, "y": 420, "w": LW, "h": LH},
        "bizStaffMale":     {"x": bs_m_x, "y": 420, "w": LW, "h": LH},
        "bizStaffFemale":   {"x": bs_f_x, "y": 420, "w": LW, "h": LH},
        # Row 4 — Status cards
        "boarded":          {"x": 470, "y": 555, "w": SW, "h": SH},
        "notCheckedIn":     {"x": 580, "y": 555, "w": SW, "h": SH},
        "revenue":          {"x": 690, "y": 555, "w": SW, "h": SH},
        "offloaded":        {"x": 800, "y": 555, "w": SW, "h": SH},
        "noShow":           {"x": 910, "y": 555, "w": SW, "h": SH},
    }

    nodes = [
        # ── Root ────────────────────────────────────
        {
            "id": "root", **positions["root"],
            "borderColor": "hsl(var(--muted-foreground))",
            "textColor": "hsl(var(--foreground))",
            "label": "Persons on Board",
            "value": persons_on_board,
            "subLabel": "Manifest + JumpSeat",
            "badges": _tree_badges(
                ("M", analysis.get("totalMale", 0)),
                ("F", analysis.get("totalFemale", 0)),
                ("C", analysis.get("totalChildren", 0)),
                ("I", passenger_summary.get("infantCount", 0)),
            ),
        },
        # ── Row 1 — Cabins ─────────────────────────
        {
            "id": "economy", **positions["economy"],
            "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Economy",
            "value": analysis.get("economy", {}).get("total", 0),
            "subLabel": f"{analysis.get('economy', {}).get('total', 0)} pax",
            "badges": [],
        },
        {
            "id": "business", **positions["business"],
            "borderColor": "#c9a43a", "textColor": "#c9a43a",
            "label": "Business",
            "value": analysis.get("business", {}).get("total", 0),
            "subLabel": f"{analysis.get('business', {}).get('total', 0)} pax",
            "badges": [],
        },
        # ── Row 1 — Crew (NOT in Sabre) ────────────
        {
            "id": "cabinCrew", **positions["cabinCrew"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Cabin Crew",
            "value": "—",
            "subLabel": "No Sabre source",
            "badges": [],
        },
        {
            "id": "flightCrew", **positions["flightCrew"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Flight Crew",
            "value": "—",
            "subLabel": "No Sabre source",
            "badges": [],
        },
        # ── Row 2 — Passengers / Staff ─────────────
        {
            "id": "econPassengers", **positions["econPassengers"],
            "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Passengers",
            "value": ep.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges(
                ("M", ep.get("male", 0)), ("F", ep.get("female", 0)),
                ("C", ep.get("children", 0)), ("I", ep.get("infants", 0)),
            ),
        },
        {
            "id": "econStaff", **positions["econStaff"],
            "borderColor": "#9b6dff", "textColor": "#9b6dff",
            "label": "Staff",
            "value": es.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges(("M", es.get("male", 0)), ("F", es.get("female", 0))),
        },
        {
            "id": "bizPassengers", **positions["bizPassengers"],
            "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Passengers",
            "value": bp.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges(
                ("M", bp.get("male", 0)), ("F", bp.get("female", 0)),
                ("C", bp.get("children", 0)), ("I", bp.get("infants", 0)),
            ),
        },
        {
            "id": "bizStaff", **positions["bizStaff"],
            "borderColor": "#9b6dff", "textColor": "#9b6dff",
            "label": "Staff",
            "value": bs.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges(("M", bs.get("male", 0)), ("F", bs.get("female", 0))),
        },
        # ── Row 2 — Crew leaves (NOT in Sabre) ─────
        {"id": "cabinCrewMale", **positions["cabinCrewMale"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Male", "value": "—", "subLabel": "No Sabre source", "badges": []},
        {"id": "cabinCrewFemale", **positions["cabinCrewFemale"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Female", "value": "—", "subLabel": "No Sabre source", "badges": []},
        {"id": "flightCrewMale", **positions["flightCrewMale"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Male", "value": "—", "subLabel": "No Sabre source", "badges": []},
        {"id": "flightCrewFemale", **positions["flightCrewFemale"],
            "borderColor": na_border, "textColor": na_text,
            "label": "Female", "value": "—", "subLabel": "No Sabre source", "badges": []},
        # ── Row 3 — Economy Passenger demographics ──
        {"id": "econPaxMale", **positions["econPaxMale"],
            "borderColor": "#3b82f6", "textColor": "#3b82f6",
            "label": "Male", "value": ep.get("male", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("M", ep.get("male", 0)))},
        {"id": "econPaxFemale", **positions["econPaxFemale"],
            "borderColor": "#ec4899", "textColor": "#ec4899",
            "label": "Female", "value": ep.get("female", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("F", ep.get("female", 0)))},
        {"id": "econPaxChildren", **positions["econPaxChildren"],
            "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Children", "value": ep.get("children", 0),
            "subLabel": "CHD edit code",
            "badges": _tree_badges(("C", ep.get("children", 0)))},
        {"id": "econPaxInfants", **positions["econPaxInfants"],
            "borderColor": "#e89a3c", "textColor": "#e89a3c",
            "label": "Infants", "value": ep.get("infants", 0),
            "subLabel": "INF (lap baby)",
            "badges": _tree_badges(("I", ep.get("infants", 0)))},
        # ── Row 3 — Economy Staff demographics ──────
        {"id": "econStaffMale", **positions["econStaffMale"],
            "borderColor": "#3b82f6", "textColor": "#3b82f6",
            "label": "Male", "value": es.get("male", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("M", es.get("male", 0)))},
        {"id": "econStaffFemale", **positions["econStaffFemale"],
            "borderColor": "#ec4899", "textColor": "#ec4899",
            "label": "Female", "value": es.get("female", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("F", es.get("female", 0)))},
        # ── Row 3 — Business Passenger demographics ─
        {"id": "bizPaxMale", **positions["bizPaxMale"],
            "borderColor": "#3b82f6", "textColor": "#3b82f6",
            "label": "Male", "value": bp.get("male", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("M", bp.get("male", 0)))},
        {"id": "bizPaxFemale", **positions["bizPaxFemale"],
            "borderColor": "#ec4899", "textColor": "#ec4899",
            "label": "Female", "value": bp.get("female", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("F", bp.get("female", 0)))},
        {"id": "bizPaxChildren", **positions["bizPaxChildren"],
            "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Children", "value": bp.get("children", 0),
            "subLabel": "CHD edit code",
            "badges": _tree_badges(("C", bp.get("children", 0)))},
        {"id": "bizPaxInfants", **positions["bizPaxInfants"],
            "borderColor": "#e89a3c", "textColor": "#e89a3c",
            "label": "Infants", "value": bp.get("infants", 0),
            "subLabel": "INF (lap baby)",
            "badges": _tree_badges(("I", bp.get("infants", 0)))},
        # ── Row 3 — Business Staff demographics ─────
        {"id": "bizStaffMale", **positions["bizStaffMale"],
            "borderColor": "#3b82f6", "textColor": "#3b82f6",
            "label": "Male", "value": bs.get("male", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("M", bs.get("male", 0)))},
        {"id": "bizStaffFemale", **positions["bizStaffFemale"],
            "borderColor": "#ec4899", "textColor": "#ec4899",
            "label": "Female", "value": bs.get("female", 0),
            "subLabel": gender_sub,
            "badges": _tree_badges(("F", bs.get("female", 0)))},
        # ── Row 4 — Status cards ────────────────────
        {
            "id": "boarded", **positions["boarded"],
            "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Boarded",
            "value": analysis.get("boarded", 0),
            "subLabel": f"of {passenger_summary.get('totalPassengers', 0)}",
            "badges": [],
        },
        {
            "id": "notCheckedIn", **positions["notCheckedIn"],
            "borderColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
            "textColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
            "label": "Not Checked-In",
            "value": analysis.get("notCheckedIn", 0),
            "subLabel": "Not in SOB" if analysis.get("notCheckedIn", 0) > 0 else "",
            "badges": [],
        },
        {
            "id": "revenue", **positions["revenue"],
            "borderColor": "#35c0c0", "textColor": "#35c0c0",
            "label": "Revenue",
            "value": analysis.get("revenue", 0),
            "subLabel": f"{analysis.get('nonRevenue', 0)} non-rev",
            "badges": [],
        },
        {
            "id": "offloaded", **positions["offloaded"],
            "borderColor": "#e84545" if offloaded_available and offloaded else na_border,
            "textColor": "#e84545" if offloaded_available and offloaded else (na_text if not offloaded_available else "#2ec27e"),
            "label": "Offloaded",
            "value": offloaded if offloaded_available else "—",
            "subLabel": "MLX report" if offloaded_available else "Needs Trip_ReportsRQ",
            "badges": [],
        },
        {
            "id": "noShow", **positions["noShow"],
            "borderColor": "#e84545" if no_show_available and no_show else na_border,
            "textColor": "#e84545" if no_show_available and no_show else (na_text if not no_show_available else "#2ec27e"),
            "label": "No-Show",
            "value": no_show if no_show_available else "—",
            "subLabel": "MLC vs manifest" if no_show_available else "Needs FINAL/PDC",
            "badges": [],
        },
    ]

    edges = [
        # Root → Cabins + Crew
        ["root", "economy"], ["root", "business"],
        ["root", "cabinCrew"], ["root", "flightCrew"],
        # Cabins → Pax/Staff
        ["economy", "econPassengers"], ["economy", "econStaff"],
        ["business", "bizPassengers"], ["business", "bizStaff"],
        # Crew → M/F
        ["cabinCrew", "cabinCrewMale"], ["cabinCrew", "cabinCrewFemale"],
        ["flightCrew", "flightCrewMale"], ["flightCrew", "flightCrewFemale"],
        # Economy Passengers → demographics
        ["econPassengers", "econPaxMale"], ["econPassengers", "econPaxFemale"],
        ["econPassengers", "econPaxChildren"], [
            "econPassengers", "econPaxInfants"],
        # Economy Staff → demographics
        ["econStaff", "econStaffMale"], ["econStaff", "econStaffFemale"],
        # Business Passengers → demographics
        ["bizPassengers", "bizPaxMale"], ["bizPassengers", "bizPaxFemale"],
        ["bizPassengers", "bizPaxChildren"], [
            "bizPassengers", "bizPaxInfants"],
        # Business Staff → demographics
        ["bizStaff", "bizStaffMale"], ["bizStaff", "bizStaffFemale"],
    ]

    return {
        "title": "Persons on Board Breakdown",
        "badge": "Sabre Live",
        "width": 1380,
        "height": 650,
        "nodes": nodes,
        "edges": [{"from": s, "to": e} for s, e in edges],
        "statusCards": [
            {"id": "boarded", "label": "Boarded", "value": analysis.get("boarded", 0),
             "subLabel": f"of {passenger_summary.get('totalPassengers', 0)}",
             "borderColor": "#2ec27e", "textColor": "#2ec27e"},
            {"id": "notCheckedIn", "label": "Not Checked-In",
             "value": analysis.get("notCheckedIn", 0),
             "subLabel": "Not in SOB" if analysis.get("notCheckedIn", 0) > 0 else "",
             "borderColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
             "textColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e"},
            {"id": "revenue", "label": "Revenue", "value": analysis.get("revenue", 0),
             "subLabel": f"{analysis.get('nonRevenue', 0)} non-rev",
             "borderColor": "#35c0c0", "textColor": "#35c0c0"},
            {"id": "offloaded", "label": "Offloaded",
             "value": offloaded if offloaded_available else "—",
             "subLabel": "MLX report" if offloaded_available else "Needs Trip_ReportsRQ",
             "borderColor": "#e84545" if offloaded_available and offloaded else na_border,
             "textColor": "#e84545" if offloaded_available and offloaded else na_text},
            {"id": "noShow", "label": "No-Show",
             "value": no_show if no_show_available else "—",
             "subLabel": "MLC vs manifest" if no_show_available else "Needs FINAL/PDC",
             "borderColor": "#e84545" if no_show_available and no_show else na_border,
             "textColor": "#e84545" if no_show_available and no_show else na_text},
        ],
    }


def _summarize_flight_for_list(flight_status_doc, passenger_doc):
    analysis = _analyze_passengers(passenger_doc.get(
        "passengers", [])) if passenger_doc else None
    destination = _extract_destination(flight_status_doc, passenger_doc)
    passenger_summary = {
        "totalPassengers": passenger_doc.get("totalPassengers", 0) if passenger_doc else 0,
        "adultCount": passenger_doc.get("adultCount", 0) if passenger_doc else 0,
        "childCount": passenger_doc.get("childCount", 0) if passenger_doc else 0,
        "infantCount": passenger_doc.get("infantCount", 0) if passenger_doc else 0,
        "totalSouls": passenger_doc.get("totalSouls", 0) if passenger_doc else 0,
    }
    return {
        "destination": destination,
        "passengerSummary": passenger_summary,
        "operationalSummary": {
            "checkedIn": analysis.get("checkedIn", 0) if analysis else 0,
            "boarded": analysis.get("boarded", 0) if analysis else 0,
            "notCheckedIn": analysis.get("notCheckedIn", 0) if analysis else 0,
            "soulsOnBoard": analysis.get("stateBreakdown", {}).get("boarded", {}).get("totalSouls", 0) if analysis else 0,
            "economySouls": analysis.get("cabinTotals", {}).get("economy", {}).get("souls", 0) if analysis else 0,
            "businessSouls": analysis.get("cabinTotals", {}).get("business", {}).get("souls", 0) if analysis else 0,
        },
    }


def _validate_counts(passenger_summary, analysis, state_breakdown):
    """Cross-check passenger math and return integrity report."""
    warnings = []
    total_pax = passenger_summary.get("totalPassengers", 0)
    total_souls = passenger_summary.get("totalSouls", 0)
    infant_count = passenger_summary.get("infantCount", 0)
    adult_count = passenger_summary.get("adultCount", 0)
    child_count = passenger_summary.get("childCount", 0)

    # Check: adults + children == totalPassengers
    if adult_count + child_count != total_pax:
        warnings.append(
            f"adults({adult_count})+children({child_count}) != totalPassengers({total_pax})")

    # Check: totalSouls == totalPassengers + infantCount
    if total_souls != total_pax + infant_count:
        warnings.append(
            f"totalSouls({total_souls}) != totalPassengers({total_pax})+infants({infant_count})")

    # Check: economy + business == totalPassengers in analysis
    econ = analysis.get("economy", {}).get("total", 0)
    biz = analysis.get("business", {}).get("total", 0)
    if econ + biz != total_pax:
        warnings.append(
            f"economy({econ})+business({biz}) != totalPassengers({total_pax})")

    # Check: booked + checkedIn + boarded == totalPassengers
    booked_t = state_breakdown.get("booked", {}).get("totalPassengers", 0)
    ci_t = state_breakdown.get("checkedIn", {}).get("totalPassengers", 0)
    bd_t = state_breakdown.get("boarded", {}).get("totalPassengers", 0)
    if booked_t + ci_t + bd_t != total_pax:
        warnings.append(
            f"booked({booked_t})+checkedIn({ci_t})+boarded({bd_t}) != totalPassengers({total_pax})")

    return {
        "valid": len(warnings) == 0,
        "checks": 4,
        "warnings": warnings,
    }


def _build_dashboard_payload(fs, pl, origin, date, change_summary, reservation_doc=None, trip_report_doc=None):
    if pl:
        passengers = pl.get("passengers", [])
        passenger_summary = {
            "totalPassengers": pl.get("totalPassengers", 0),
            "adultCount": pl.get("adultCount", 0),
            "childCount": pl.get("childCount", 0),
            "infantCount": pl.get("infantCount", 0),
            "totalSouls": pl.get("totalSouls", 0),
            "cabinSummary": pl.get("cabinSummary", []),
        }
        gender_lookup = _build_gender_lookup(reservation_doc)
        analysis = _analyze_passengers(passengers, gender_lookup)
    else:
        passenger_summary = {}
        analysis = {}

    destination = _extract_destination(fs, pl)
    boarded_breakdown = analysis.get("stateBreakdown", {}).get(
        "boarded", _empty_state_bucket())
    checked_in_breakdown = analysis.get("stateBreakdown", {}).get(
        "checkedIn", _empty_state_bucket())
    booked_breakdown = analysis.get("stateBreakdown", {}).get(
        "booked", _empty_state_bucket())
    tracked_changes = sum(change_summary.values())
    # Compute offloaded / no-show from trip reports
    offloaded = None
    no_show = None
    offloaded_available = False
    no_show_available = False
    if trip_report_doc:
        # MLX report → cancelled passengers = offloaded
        mlx = trip_report_doc.get("cancelledPassengers", [])
        if mlx is not None:
            offloaded = len(mlx)
            offloaded_available = True

        # MLC report → ever-booked passengers for no-show detection
        mlc = trip_report_doc.get("everBookedPassengers", [])
        current_flight_status = (fs or {}).get("status", "")
        if mlc is not None and current_flight_status in ("FINAL", "PDC") and pl:
            # No-show = passengers in MLC (ever-booked) who are NOT in current manifest
            current_pnr_names = set()
            for pax in pl.get("passengers", []):
                key = (pax.get("pnr", ""), pax.get("lastName", "").upper())
                current_pnr_names.add(key)
            no_show_count = 0
            for eb in mlc:
                key = (eb.get("pnr", ""), eb.get("lastName", "").upper())
                if key not in current_pnr_names:
                    no_show_count += 1
            no_show = no_show_count
            no_show_available = True

    tree = _build_tree_payload(
        analysis, passenger_summary,
        flight_status=fs,
        offloaded=offloaded, no_show=no_show,
        offloaded_available=offloaded_available,
        no_show_available=no_show_available,
    ) if analysis and passenger_summary else None

    # Self-validation: cross-check all passenger math
    data_integrity = _validate_counts(
        passenger_summary, analysis,
        analysis.get("stateBreakdown", {}),
    ) if passenger_summary and analysis else {"valid": True, "checks": 0, "warnings": []}

    # Last-fetched timestamp from passenger list document
    fetched_at = (pl or {}).get("fetchedAt", "")

    return {
        "flightStatus": fs,
        "route": {
            "origin": (fs or {}).get("origin") or (pl or {}).get("origin") or origin or "",
            "destination": destination,
            "departureDate": date or (fs or {}).get("departureDate") or (pl or {}).get("departureDate") or "",
        },
        "passengerSummary": passenger_summary,
        "analysis": analysis,
        "changeSummary": change_summary,
        "overview": {
            "soulsOnBoard": boarded_breakdown.get("totalSouls", 0),
            "manifestRecords": passenger_summary.get("totalPassengers", 0),
            "totalSouls": passenger_summary.get("totalSouls", 0),
            "economySouls": analysis.get("cabinTotals", {}).get("economy", {}).get("souls", 0),
            "businessSouls": analysis.get("cabinTotals", {}).get("business", {}).get("souls", 0),
            "trackedChanges": tracked_changes,
        },
        "dataIntegrity": data_integrity,
        "fetchedAt": fetched_at,
        "stateSummary": {
            "booked": booked_breakdown,
            "checkedIn": checked_in_breakdown,
            "boarded": boarded_breakdown,
            "others": {
                "jumpSeat": ((fs or {}).get("jumpSeat") or {}).get("cockpit", 0) + ((fs or {}).get("jumpSeat") or {}).get("cabin", 0),
                "nonRevenue": analysis.get("nonRevenue", 0),
                "offloaded": offloaded,
                "noShow": no_show,
                "offloadedAvailable": offloaded_available,
                "noShowAvailable": no_show_available,
            },
        },
        "tree": tree,
    }


@router.get("")
def list_flights(
    date: str = Query(None, description="Filter by departure date YYYY-MM-DD"),
):
    """List all distinct flights in the database with their latest status."""
    validate_date(date)
    db = get_db()
    match = {}
    if date:
        match["departureDate"] = date

    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"fetchedAt": -1}},
        {
            "$group": {
                "_id": {
                    "airline": "$airline",
                    "flightNumber": "$flightNumber",
                    "origin": "$origin",
                    "departureDate": "$departureDate",
                },
                "status": {"$first": "$status"},
                "gate": {"$first": "$gate"},
                "aircraft": {"$first": "$aircraft"},
                "schedule": {"$first": "$schedule"},
                "passengerCounts": {"$first": "$passengerCounts"},
                "jumpSeat": {"$first": "$jumpSeat"},
                "fetchedAt": {"$first": "$fetchedAt"},
            }
        },
        {"$sort": {"_id.departureDate": 1, "_id.flightNumber": 1}},
    ]
    results = list(db["flight_status"].aggregate(pipeline))

    passenger_pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"fetchedAt": -1}},
        {
            "$group": {
                "_id": {
                    "airline": "$airline",
                    "flightNumber": "$flightNumber",
                    "origin": "$origin",
                    "departureDate": "$departureDate",
                },
                "destination": {"$first": "$destination"},
                "totalPassengers": {"$first": "$totalPassengers"},
                "adultCount": {"$first": "$adultCount"},
                "childCount": {"$first": "$childCount"},
                "infantCount": {"$first": "$infantCount"},
                "totalSouls": {"$first": "$totalSouls"},
                "passengers": {"$first": "$passengers"},
                "cabinSummary": {"$first": "$cabinSummary"},
            }
        },
    ]
    passenger_results = list(
        db["passenger_list"].aggregate(passenger_pipeline))
    passenger_by_key = {
        (
            r["_id"].get("airline"),
            r["_id"].get("flightNumber"),
            r["_id"].get("origin"),
            r["_id"].get("departureDate"),
        ): r
        for r in passenger_results
    }

    flights = []
    for r in results:
        fid = r["_id"]
        passenger_doc = passenger_by_key.get(
            (
                fid.get("airline", "GF"),
                fid.get("flightNumber"),
                fid.get("origin", ""),
                fid.get("departureDate", ""),
            )
        )
        summary = _summarize_flight_for_list(r, passenger_doc)
        flights.append({
            "airline": fid.get("airline", "GF"),
            "flightNumber": fid.get("flightNumber", ""),
            "origin": fid.get("origin", ""),
            "destination": summary["destination"],
            "departureDate": fid.get("departureDate", ""),
            "status": r.get("status"),
            "gate": r.get("gate"),
            "aircraft": r.get("aircraft"),
            "schedule": r.get("schedule"),
            "passengerCounts": r.get("passengerCounts"),
            "jumpSeat": r.get("jumpSeat"),
            "passengerSummary": summary["passengerSummary"],
            "operationalSummary": summary["operationalSummary"],
            "fetchedAt": str(r.get("fetchedAt", "")),
        })
    return flights


@router.get("/{flight_number}/dashboard")
def get_flight_dashboard(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Combined dashboard endpoint — returns flight status + deep passenger
    analysis in a single response for the frontend dashboard view.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    # Flight status
    fs = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    if fs:
        fs.pop("_id", None)
        fs.pop("_raw", None)

    # Passenger list
    pl = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if pl:
        pl.pop("_id", None)
        pl.pop("_raw", None)

    # Reservations (for gender cross-reference and enrichment)
    res_query = {"flightNumber": flight_number}
    if origin:
        res_query["departureAirport"] = origin
    if date:
        res_query["departureDate"] = date
    reservation_doc = db["reservations"].find_one(
        res_query, sort=[("fetchedAt", -1)])
    if reservation_doc:
        reservation_doc.pop("_id", None)
        reservation_doc.pop("_raw", None)

    # Trip reports (for offloaded / no-show)
    report_query = {"flightNumber": flight_number}
    if origin:
        report_query["origin"] = origin
    if date:
        report_query["departureDate"] = date
    trip_report_doc = db["trip_reports"].find_one(
        report_query, sort=[("fetchedAt", -1)])
    if trip_report_doc:
        trip_report_doc.pop("_id", None)
        trip_report_doc.pop("_raw", None)

    # Change summary
    change_match = {"flightNumber": flight_number}
    if origin:
        change_match["origin"] = origin
    if date:
        change_match["departureDate"] = date
    change_pipeline = [
        {"$match": change_match},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
    ]
    change_results = list(db["changes"].aggregate(change_pipeline))
    change_summary = {r["_id"]: r["count"] for r in change_results}

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    return _build_dashboard_payload(fs, pl, origin, date, change_summary,
                                    reservation_doc, trip_report_doc)


@router.get("/{flight_number}/tree")
def get_flight_tree(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Return a dedicated tree payload for the selected flight."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    fs = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    pl = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if fs:
        fs.pop("_id", None)
        fs.pop("_raw", None)
    if pl:
        pl.pop("_id", None)
        pl.pop("_raw", None)

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    payload = _build_dashboard_payload(fs, pl, origin, date, {})
    return payload.get("tree") or {
        "title": "Aircraft Humans Breakdown Tree",
        "badge": "Sabre Live",
        "width": 940,
        "height": 600,
        "nodes": [],
        "edges": [],
        "statusCards": [],
    }


@router.get("/{flight_number}/status")
def get_flight_status(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code (e.g. LHR)"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Get the latest flight status for a flight.
    Optionally filter by origin and/or date.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Flight status not found")
    return _strip_id(doc)


@router.get("/{flight_number}/status/history")
def get_flight_status_history(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get historical flight status snapshots (newest first)."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    cursor = db["flight_status"].find(query).sort("fetchedAt", -1).limit(limit)
    return [_strip_id(doc) for doc in cursor]
