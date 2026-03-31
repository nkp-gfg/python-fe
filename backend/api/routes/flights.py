"""Flight status API endpoints."""

import time
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError
from backend.api.database import get_db
from backend.api.snapshot_versioning import get_snapshot_data_as_of
from backend.api.validators import validate_date, validate_origin

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["flights"])

# Simple TTL cache for dashboard data (30 second expiration)
_dashboard_cache = {}
_cache_ttl = 30  # seconds


def _get_cache_key(flight_number, origin, date, snapshot_sequence):
    return f"{flight_number}:{origin}:{date}:{snapshot_sequence}"


def _get_cached(key):
    if key in _dashboard_cache:
        data, ts = _dashboard_cache[key]
        if time.time() - ts < _cache_ttl:
            return data
        del _dashboard_cache[key]
    return None


def _set_cache(key, data):
    # Limit cache size
    if len(_dashboard_cache) > 100:
        oldest_key = min(_dashboard_cache,
                         key=lambda k: _dashboard_cache[k][1])
        del _dashboard_cache[oldest_key]
    _dashboard_cache[key] = (data, time.time())


def _strip_id(doc):
    """Remove MongoDB _id and _raw from response."""
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _empty_cabin_detail():
    return {"adults": 0, "children": 0, "infants": 0, "staff": 0}


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
        "economyDetail": _empty_cabin_detail(),
        "businessDetail": _empty_cabin_detail(),
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


def _tree_badges_all(*items):
    """Like _tree_badges but always includes all items (even zero values)."""
    return [{"type": key, "value": value} for key, value in items]


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
        "loyaltyCounts": {"FF": 0, "BLU": 0, "SLV": 0, "GLD": 0, "BLK": 0},
        "nationalityCounts": {},
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
        # Per-cabin detail: adults, children, infants, staff
        detail = bucket[cabin_key + "Detail"]
        if is_staff:
            detail["staff"] += 1
        elif is_child:
            detail["children"] += 1
        else:
            detail["adults"] += 1
        if has_infant:
            detail["infants"] += 1

        # Loyalty tier counts
        for tier in ("FF", "BLU", "SLV", "GLD", "BLK"):
            if tier in edit_codes:
                result["loyaltyCounts"][tier] += 1

        # Nationality counts
        nat = p.get("nationality") or p.get("countryCode", "")
        if nat:
            result["nationalityCounts"][nat] = result["nationalityCounts"].get(
                nat, 0) + 1

    return result


def _resolve_offloaded(offloaded, offloaded_available, flight_closed, ci_not_boarded):
    """Resolve offloaded value, availability text, and colors."""
    if offloaded_available:
        return offloaded, "MLX report", offloaded > 0
    if flight_closed:
        return ci_not_boarded, "Inferred from manifest", ci_not_boarded > 0
    return "—", "Needs Trip_ReportsRQ", False


def _resolve_no_show(no_show, no_show_available, flight_closed, not_checked_in):
    """Resolve no-show value, availability text, and colors."""
    if no_show_available:
        return no_show, "MLC vs manifest", no_show > 0
    if flight_closed:
        return not_checked_in, "Inferred from manifest", not_checked_in > 0
    return "—", "Needs FINAL/PDC", False


def _offloaded_card(offloaded, offloaded_available, flight_closed, ci_not_boarded, na_border, na_text):
    val, sub, is_alert = _resolve_offloaded(
        offloaded, offloaded_available, flight_closed, ci_not_boarded)
    return {
        "id": "offloaded", "label": "Offloaded", "value": val, "subLabel": sub,
        "borderColor": "#e84545" if is_alert else (na_border if isinstance(val, str) else "#2ec27e"),
        "textColor": "#e84545" if is_alert else (na_text if isinstance(val, str) else "#2ec27e"),
    }


def _no_show_card(no_show, no_show_available, flight_closed, not_checked_in, na_border, na_text):
    val, sub, is_alert = _resolve_no_show(
        no_show, no_show_available, flight_closed, not_checked_in)
    return {
        "id": "noShow", "label": "No-Show", "value": val, "subLabel": sub,
        "borderColor": "#e84545" if is_alert else (na_border if isinstance(val, str) else "#2ec27e"),
        "textColor": "#e84545" if is_alert else (na_text if isinstance(val, str) else "#2ec27e"),
    }


def _build_tree_payload(analysis, passenger_summary, flight_status=None,
                        offloaded=None, no_show=None,
                        offloaded_available=False, no_show_available=False):
    """Build a consolidated passenger tree with 7 visible SVG nodes.

    Demographics are shown as badges inside parent nodes instead of separate
    leaf boxes.  Crew and status cards are kept as hidden data nodes so that
    the PaxMatrix table can still look them up by ID.
    """
    ep = analysis.get("economy", {}).get("passengers", {})
    es = analysis.get("economy", {}).get("staff", {})
    bp = analysis.get("business", {}).get("passengers", {})
    bs = analysis.get("business", {}).get("staff", {})
    total_souls = passenger_summary.get("totalSouls", 0)

    js = (flight_status or {}).get("jumpSeat") or {}
    jump_seat = js.get("cockpit", 0) + js.get("cabin", 0)
    persons_on_board = total_souls + jump_seat

    na_border = "#555555"
    na_text = "#777777"

    current_status = ((flight_status or {}).get("status", "")).upper()
    flight_closed = current_status in ("FINAL", "PDC")
    ci_not_boarded = (analysis.get("stateBreakdown", {})
                      .get("checkedIn", {}).get("totalPassengers", 0))

    # ── Consolidated 3-row layout ───────────────────
    # Row 2 (leaf) x-positions
    ep_x, es_x = 140, 330
    bp_x, bs_x = 530, 720
    # Row 1 — centered on children
    econ_x = (ep_x + es_x) // 2   # 235
    biz_x = (bp_x + bs_x) // 2    # 625
    # Root — centered
    root_x = (econ_x + biz_x) // 2  # 430

    RW, RH = 180, 80    # root
    PW, PH = 150, 72    # cabin parent
    LW, LH = 160, 90    # leaf — taller to hold badge row

    def _hidden(nid, label, value, sub="", border=na_border, text=na_text):
        """Data-only node (not rendered in SVG, available for PaxMatrix lookups)."""
        return {"id": nid, "x": 0, "y": 0, "w": 0, "h": 0,
                "display": False,
                "borderColor": border, "textColor": text,
                "label": label, "value": value, "subLabel": sub, "badges": []}

    nodes = [
        # ── Visible tree nodes (7) ─────────────────
        {
            "id": "root", "x": root_x, "y": 55, "w": RW, "h": RH,
            "display": True,
            "borderColor": "hsl(var(--muted-foreground))",
            "textColor": "#ffffff",
            "label": "Passengers on Board",
            "value": persons_on_board,
            "subLabel": "Manifest + JumpSeat",
            "badges": _tree_badges(
                ("M", analysis.get("totalMale", 0)),
                ("F", analysis.get("totalFemale", 0)),
                ("C", analysis.get("totalChildren", 0)),
                ("I", passenger_summary.get("infantCount", 0)),
            ),
        },
        {
            "id": "economy", "x": econ_x, "y": 175, "w": PW, "h": PH,
            "display": True,
            "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Economy",
            "value": analysis.get("economy", {}).get("total", 0),
            "subLabel": f"{analysis.get('economy', {}).get('total', 0)} pax",
            "badges": [],
        },
        {
            "id": "business", "x": biz_x, "y": 175, "w": PW, "h": PH,
            "display": True,
            "borderColor": "#c9a43a", "textColor": "#c9a43a",
            "label": "Business",
            "value": analysis.get("business", {}).get("total", 0),
            "subLabel": f"{analysis.get('business', {}).get('total', 0)} pax",
            "badges": [],
        },
        {
            "id": "econPassengers", "x": ep_x, "y": 295, "w": LW, "h": LH,
            "display": True,
            "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Passengers",
            "value": ep.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges_all(
                ("M", ep.get("male", 0)), ("F", ep.get("female", 0)),
                ("C", ep.get("children", 0)), ("I", ep.get("infants", 0)),
            ),
        },
        {
            "id": "econStaff", "x": es_x, "y": 295, "w": LW, "h": LH,
            "display": True,
            "borderColor": "#9b6dff", "textColor": "#9b6dff",
            "label": "Staff",
            "value": es.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges_all(
                ("M", es.get("male", 0)), ("F", es.get("female", 0)),
            ),
        },
        {
            "id": "bizPassengers", "x": bp_x, "y": 295, "w": LW, "h": LH,
            "display": True,
            "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Passengers",
            "value": bp.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges_all(
                ("M", bp.get("male", 0)), ("F", bp.get("female", 0)),
                ("C", bp.get("children", 0)), ("I", bp.get("infants", 0)),
            ),
        },
        {
            "id": "bizStaff", "x": bs_x, "y": 295, "w": LW, "h": LH,
            "display": True,
            "borderColor": "#9b6dff", "textColor": "#9b6dff",
            "label": "Staff",
            "value": bs.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges_all(
                ("M", bs.get("male", 0)), ("F", bs.get("female", 0)),
            ),
        },
        # ── Hidden data nodes (PaxMatrix lookups) ───
        _hidden("cabinCrew", "Cabin Crew", "—", "No Sabre source"),
        _hidden("flightCrew", "Flight Crew", "—", "No Sabre source"),
        _hidden("cabinCrewMale", "Male", "—", "No Sabre source"),
        _hidden("cabinCrewFemale", "Female", "—", "No Sabre source"),
        _hidden("flightCrewMale", "Male", "—", "No Sabre source"),
        _hidden("flightCrewFemale", "Female", "—", "No Sabre source"),
        _hidden("econPaxMale", "Male", ep.get("male", 0),
                border="#3b82f6", text="#3b82f6"),
        _hidden("econPaxFemale", "Female", ep.get("female", 0),
                border="#ec4899", text="#ec4899"),
        _hidden("econPaxChildren", "Children", ep.get("children", 0),
                border="#2ec27e", text="#2ec27e"),
        _hidden("econPaxInfants", "Infants", ep.get("infants", 0),
                border="#e89a3c", text="#e89a3c"),
        _hidden("econStaffMale", "Male", es.get("male", 0),
                border="#3b82f6", text="#3b82f6"),
        _hidden("econStaffFemale", "Female", es.get("female", 0),
                border="#ec4899", text="#ec4899"),
        _hidden("bizPaxMale", "Male", bp.get("male", 0),
                border="#3b82f6", text="#3b82f6"),
        _hidden("bizPaxFemale", "Female", bp.get("female", 0),
                border="#ec4899", text="#ec4899"),
        _hidden("bizPaxChildren", "Children", bp.get("children", 0),
                border="#2ec27e", text="#2ec27e"),
        _hidden("bizPaxInfants", "Infants", bp.get("infants", 0),
                border="#e89a3c", text="#e89a3c"),
        _hidden("bizStaffMale", "Male", bs.get("male", 0),
                border="#3b82f6", text="#3b82f6"),
        _hidden("bizStaffFemale", "Female", bs.get("female", 0),
                border="#ec4899", text="#ec4899"),
    ]

    edges = [
        {"from": "root", "to": "economy"},
        {"from": "root", "to": "business"},
        {"from": "economy", "to": "econPassengers"},
        {"from": "economy", "to": "econStaff"},
        {"from": "business", "to": "bizPassengers"},
        {"from": "business", "to": "bizStaff"},
    ]

    return {
        "title": "Passengers on Board Breakdown",
        "badge": "Sabre Live",
        "width": 860,
        "height": 360,
        "nodes": nodes,
        "edges": edges,
        "statusCards": [
            {"id": "boarded", "label": "Boarded",
             "value": analysis.get("boarded", 0),
             "subLabel": f"of {passenger_summary.get('totalPassengers', 0)}",
             "borderColor": "#2ec27e", "textColor": "#2ec27e"},
            {"id": "notCheckedIn", "label": "Not Checked-In",
             "value": analysis.get("notCheckedIn", 0),
             "subLabel": "Not in SOB" if analysis.get("notCheckedIn", 0) > 0 else "",
             "borderColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
             "textColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e"},
            {"id": "revenue", "label": "Revenue",
             "value": analysis.get("revenue", 0),
             "subLabel": f"{analysis.get('nonRevenue', 0)} non-rev",
             "borderColor": "#35c0c0", "textColor": "#35c0c0"},
            _offloaded_card(offloaded, offloaded_available,
                            flight_closed, ci_not_boarded, na_border, na_text),
            _no_show_card(no_show, no_show_available, flight_closed,
                          analysis.get("notCheckedIn", 0), na_border, na_text),
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
    phase = _derive_flight_phase(flight_status_doc, analysis)
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
        "flightPhase": phase,
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


def _derive_flight_phase(fs, analysis):
    """Derive the operational flight phase from Sabre status + passenger data.

    Returns a dict with:
      - phase: SCHEDULED | CHECK_IN | BOARDING | CLOSED | DEPARTED
      - label: Human-readable label
      - focusCard: Which StatePanels card is primary for this phase
      - alertColor: Tailwind color token for the phase
      - alertIcon: Icon name hint for frontend
      - description: One-line operational summary
    """
    status = (fs or {}).get("status", "")
    boarding_indicator = (fs or {}).get("boarding", {}).get("indicator", "")
    boarded = analysis.get("boarded", 0) if analysis else 0
    checked_in = analysis.get("checkedIn", 0) if analysis else 0
    not_checked_in = analysis.get("notCheckedIn", 0) if analysis else 0
    total = (analysis.get("stateBreakdown", {}).get("booked", {}).get("totalPassengers", 0)
             + analysis.get("stateBreakdown", {}).get("checkedIn",
                                                      {}).get("totalPassengers", 0)
             + analysis.get("stateBreakdown", {}).get("boarded", {}).get("totalPassengers", 0)) if analysis else 0
    gate = (fs or {}).get("gate", "")
    ci_not_boarded = analysis.get("stateBreakdown", {}).get(
        "checkedIn", {}).get("totalPassengers", 0) if analysis else 0

    if status == "PDC":
        return {
            "phase": "DEPARTED",
            "label": "Departed",
            "focusCard": "others",
            "alertColor": "gray",
            "alertIcon": "plane-departure",
            "description": "Flight has departed — PDC reconciliation complete.",
        }
    if status == "FINAL":
        exceptions = []
        if not_checked_in > 0:
            exceptions.append(
                f"{not_checked_in} no-show{'s' if not_checked_in != 1 else ''}")
        if ci_not_boarded > 0:
            exceptions.append(
                f"{ci_not_boarded} offload{'s' if ci_not_boarded != 1 else ''}")
        desc = "Flight closed"
        if exceptions:
            desc += f" — {', '.join(exceptions)}"
        else:
            desc += " — clean departure"
        return {
            "phase": "CLOSED",
            "label": "Closed",
            "focusCard": "others",
            "alertColor": "red" if exceptions else "green",
            "alertIcon": "door-closed",
            "description": desc,
        }
    if status == "OPENCI":
        # Distinguish CHECK_IN vs BOARDING
        if boarding_indicator == "BDG" or boarded > 0:
            pending = ci_not_boarded + not_checked_in
            desc = f"Boarding in progress — {boarded}/{total} boarded"
            if pending > 0:
                desc += f" | {pending} pending"
            if gate:
                desc += f" | Gate {gate}"
            return {
                "phase": "BOARDING",
                "label": "Boarding",
                "focusCard": "boarded",
                "alertColor": "amber",
                "alertIcon": "scan-line",
                "description": desc,
            }
        # Still in check-in phase
        desc = f"Check-in open — {checked_in + boarded}/{total} checked in"
        if not_checked_in > 0:
            desc += f" | {not_checked_in} pending"
        if gate:
            desc += f" | Gate {gate}"
        return {
            "phase": "CHECK_IN",
            "label": "Check-In Open",
            "focusCard": "checkedIn",
            "alertColor": "blue",
            "alertIcon": "user-check",
            "description": desc,
        }
    # No status or unknown — infer from passenger data
    if total > 0 and boarded > 0:
        # Has passengers and boardings but no status — treat as post-departure if mostly boarded
        boarded_pct = boarded / total if total else 0
        if boarded_pct > 0.8:
            return {
                "phase": "DEPARTED",
                "label": "Departed (inferred)",
                "focusCard": "others",
                "alertColor": "gray",
                "alertIcon": "plane-departure",
                "description": f"Flight likely departed — {boarded}/{total} boarded, status unavailable.",
            }
    if total > 0 and checked_in > 0:
        # Has checked-in passengers but no Sabre status — infer CHECK_IN
        if boarded > 0:
            desc = f"Boarding in progress — {boarded}/{total} boarded"
            if gate:
                desc += f" | Gate {gate}"
            return {
                "phase": "BOARDING",
                "label": "Boarding (inferred)",
                "focusCard": "boarded",
                "alertColor": "amber",
                "alertIcon": "scan-line",
                "description": desc,
            }
        desc = f"Check-in open — {checked_in}/{total} checked in"
        if not_checked_in > 0:
            desc += f" | {not_checked_in} pending"
        if gate:
            desc += f" | Gate {gate}"
        return {
            "phase": "CHECK_IN",
            "label": "Check-In Open (inferred)",
            "focusCard": "checkedIn",
            "alertColor": "blue",
            "alertIcon": "user-check",
            "description": desc,
        }
    return {
        "phase": "SCHEDULED",
        "label": "Scheduled",
        "focusCard": "booked",
        "alertColor": "slate",
        "alertIcon": "calendar",
        "description": "Flight scheduled — awaiting check-in open.",
    }


def _build_insights(passengers, reservation_doc, schedule_doc, pl, change_summary):
    """Compute 28 analytics insights from available data sources."""
    insights = {}
    total_pax = len(passengers)

    # ── 1. Connecting vs Local passengers ───────────────────
    connecting = 0
    local = 0
    for px in passengers:
        if px.get("isThru"):
            connecting += 1
        else:
            local += 1
    insights["connectingPassengers"] = {
        "connecting": connecting,
        "local": local,
        "connectingPct": round(connecting / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 2. Booking channel analytics ────────────────────────
    channel_counts = {}
    channel_categories = {"online": 0, "agent": 0, "corporate": 0, "other": 0}
    online_sines = {"WEB", "MOB", "APP", "ND1", "ND2", "NDC"}
    if reservation_doc:
        for rv in reservation_doc.get("reservations", []):
            pos = rv.get("pointOfSale", {})
            if isinstance(pos, dict):
                src = pos.get("agentSine", "") or ""
                if src:
                    channel_counts[src] = channel_counts.get(
                        src, 0) + rv.get("numberInParty", 1)
                    if src.upper() in online_sines:
                        channel_categories["online"] += rv.get(
                            "numberInParty", 1)
                    elif src.upper().startswith("STX") or len(src) <= 4:
                        channel_categories["agent"] += rv.get(
                            "numberInParty", 1)
                    else:
                        channel_categories["other"] += rv.get(
                            "numberInParty", 1)
    insights["bookingChannels"] = {
        "channels": dict(sorted(channel_counts.items(), key=lambda x: -x[1])),
        "categories": channel_categories,
    }

    # ── 3. Payment method distribution ──────────────────────
    payment_counts = {}
    if reservation_doc:
        for rv in reservation_doc.get("reservations", []):
            fop = rv.get("formOfPayment", "")
            if fop:
                payment_counts[fop] = payment_counts.get(fop, 0) + 1
    insights["paymentMethods"] = dict(
        sorted(payment_counts.items(), key=lambda x: -x[1]))

    # ── 4. Document compliance (DOCS/DOCV/DOCA) ────────────
    doc_codes = {"DOCS": 0, "DOCV": 0, "DOCA": 0}
    for px in passengers:
        codes = px.get("editCodes", [])
        for dc in ("DOCS", "DOCV", "DOCA"):
            if dc in codes:
                doc_codes[dc] += 1
    insights["documentCompliance"] = {
        k: {"count": v, "pct": round(
            v / total_pax * 100, 1) if total_pax else 0}
        for k, v in doc_codes.items()
    }

    # ── 5. Check-in sequence analysis ───────────────────────
    sequences = []
    for px in passengers:
        seq = px.get("checkInSequence", 0)
        if seq and seq > 0:
            sequences.append(seq)
    if sequences:
        sequences.sort()
        insights["checkInSequence"] = {
            "total": len(sequences),
            "earliest": sequences[0],
            "latest": sequences[-1],
            "median": sequences[len(sequences) // 2],
        }
    else:
        insights["checkInSequence"] = {
            "total": 0, "earliest": 0, "latest": 0, "median": 0}

    # ── 6. Booking lead time ────────────────────────────────
    dep_date_str = (pl or {}).get("departureDate", "")
    lead_times = []
    if reservation_doc and dep_date_str:
        from datetime import datetime
        try:
            dep_dt = datetime.strptime(dep_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            dep_dt = None
        if dep_dt:
            for rv in reservation_doc.get("reservations", []):
                created = rv.get("createdAt", "")
                if created:
                    try:
                        cr_dt = datetime.fromisoformat(
                            created.replace("Z", "+00:00")).replace(tzinfo=None)
                        days = (dep_dt - cr_dt).days
                        if 0 <= days <= 365:
                            lead_times.append(days)
                    except (ValueError, TypeError):
                        pass
    if lead_times:
        lead_times.sort()
        insights["bookingLeadTime"] = {
            "avgDays": round(sum(lead_times) / len(lead_times), 1),
            "minDays": lead_times[0],
            "maxDays": lead_times[-1],
            "medianDays": lead_times[len(lead_times) // 2],
            "distribution": {
                "sameDay": sum(1 for d in lead_times if d == 0),
                "within7d": sum(1 for d in lead_times if 1 <= d <= 7),
                "within30d": sum(1 for d in lead_times if 8 <= d <= 30),
                "within90d": sum(1 for d in lead_times if 31 <= d <= 90),
                "over90d": sum(1 for d in lead_times if d > 90),
            },
        }
    else:
        insights["bookingLeadTime"] = None

    # ── 7. Seat occupancy ───────────────────────────────────
    seated = 0
    unseated = 0
    seat_map = {}
    for px in passengers:
        seat = px.get("seat", "")
        if seat:
            seated += 1
            row = "".join(c for c in seat if c.isdigit()) or "?"
            seat_map[row] = seat_map.get(row, 0) + 1
        else:
            unseated += 1
    insights["seatOccupancy"] = {
        "seated": seated,
        "unseated": unseated,
        "seatPct": round(seated / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 8. Baggage analytics ────────────────────────────────
    bag_counts = []
    has_bag_routes = 0
    for px in passengers:
        bc = px.get("bagCount")
        if bc is not None and bc >= 0:
            bag_counts.append(bc)
        if px.get("baggageRoutes"):
            has_bag_routes += 1
    insights["baggage"] = {
        "withBags": sum(1 for b in bag_counts if b > 0),
        "withoutBags": sum(1 for b in bag_counts if b == 0),
        "totalBags": sum(bag_counts),
        "avgBags": round(sum(bag_counts) / len(bag_counts), 1) if bag_counts else 0,
        "dataAvailablePct": round(len(bag_counts) / total_pax * 100, 1) if total_pax else 0,
        "withBagRoutes": has_bag_routes,
    }

    # ── 9. Edit code intelligence ───────────────────────────
    code_freq = {}
    for px in passengers:
        for code in px.get("editCodes", []):
            code_freq[code] = code_freq.get(code, 0) + 1
    top_codes = sorted(code_freq.items(), key=lambda x: -x[1])[:20]
    insights["editCodes"] = {
        "uniqueCodes": len(code_freq),
        "topCodes": [{"code": c, "count": n} for c, n in top_codes],
    }

    # ── 10. Multi-segment itinerary ─────────────────────────
    seg_counts = {}
    if reservation_doc:
        for rv in reservation_doc.get("reservations", []):
            segs = len(rv.get("segments", []))
            seg_counts[segs] = seg_counts.get(segs, 0) + 1
    insights["multiSegment"] = {
        "distribution": dict(sorted(seg_counts.items())),
        "multiSegmentPct": round(
            sum(v for k, v in seg_counts.items() if k > 1) /
            sum(seg_counts.values()) * 100, 1
        ) if seg_counts else 0,
    }

    # ── 11. PNR party size distribution ─────────────────────
    party_sizes = {}
    if reservation_doc:
        for rv in reservation_doc.get("reservations", []):
            ps = rv.get("numberInParty", 1)
            party_sizes[ps] = party_sizes.get(ps, 0) + 1
    insights["pnrPartySize"] = {
        "distribution": dict(sorted(party_sizes.items())),
        "avgSize": round(
            sum(k * v for k, v in party_sizes.items()) /
            sum(party_sizes.values()), 1
        ) if party_sizes else 0,
    }

    # ── 12. Infant tracking ─────────────────────────────────
    infants_with_parent = 0
    infant_names = []
    for px in passengers:
        if px.get("hasInfant"):
            infants_with_parent += 1
            infant_names.append(
                f"{px.get('lastName', '')} (via {px.get('pnr', '')})")
    insights["infantTracking"] = {
        "total": infants_with_parent,
        "details": infant_names[:20],
    }

    # ── 13. Wheelchair type breakdown ───────────────────────
    wc_types = {"WCHR": 0, "WCHS": 0, "WCHC": 0}
    for px in passengers:
        for code in px.get("editCodes", []):
            if code in wc_types:
                wc_types[code] += 1
    insights["wheelchairTypes"] = {k: v for k, v in wc_types.items() if v > 0}

    # ── 14. Meal code detail ────────────────────────────────
    meal_codes = {}
    if reservation_doc:
        for rv in reservation_doc.get("reservations", []):
            for pax in rv.get("passengers", []):
                meal = pax.get("specialMeal", "")
                if meal:
                    meal_codes[meal] = meal_codes.get(meal, 0) + 1
    insights["mealCodes"] = dict(
        sorted(meal_codes.items(), key=lambda x: -x[1]))

    # ── 15. Boarding rate vs schedule ───────────────────────
    boarded_total = sum(1 for px in passengers if px.get("isBoarded"))
    checked_in_total = sum(1 for px in passengers if px.get("isCheckedIn"))
    insights["boardingRate"] = {
        "boarded": boarded_total,
        "checkedIn": checked_in_total,
        "notCheckedIn": total_pax - checked_in_total - boarded_total,
        "boardedPct": round(boarded_total / total_pax * 100, 1) if total_pax else 0,
        "checkedInPct": round((checked_in_total + boarded_total) / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 16. Change velocity ─────────────────────────────────
    change_types = dict(sorted(
        ((k, v) for k, v in (change_summary or {}).items()),
        key=lambda x: -x[1]
    ))
    total_changes = sum(change_types.values())
    insights["changeVelocity"] = {
        "totalChanges": total_changes,
        "changeTypes": change_types,
    }

    # ── 17. Revenue class mix (booking class distribution) ──
    class_dist = {}
    for px in passengers:
        bc = px.get("bookingClass", "")
        if bc:
            class_dist[bc] = class_dist.get(bc, 0) + 1
    insights["revenueClassMix"] = dict(
        sorted(class_dist.items(), key=lambda x: -x[1]))

    # ── 18. VCR / ticket status ─────────────────────────────
    vcr_types = {}
    has_ticket = 0
    for px in passengers:
        vt = px.get("vcrType", "")
        if vt:
            vcr_types[vt] = vcr_types.get(vt, 0) + 1
        if px.get("ticketNumber"):
            has_ticket += 1
    insights["ticketStatus"] = {
        "vcrTypes": vcr_types,
        "withTicket": has_ticket,
        "withoutTicket": total_pax - has_ticket,
        "ticketPct": round(has_ticket / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 19. Flight duration & distance ──────────────────────
    if schedule_doc:
        insights["flightInfo"] = {
            "elapsedTime": schedule_doc.get("elapsedTime", ""),
            "airMilesFlown": schedule_doc.get("airMilesFlown", 0),
            "aircraftType": schedule_doc.get("aircraftType", ""),
            "mealCode": schedule_doc.get("mealCode", ""),
        }
    else:
        insights["flightInfo"] = None

    # ── 20. Corporate travel IDs ────────────────────────────
    corp_ids = {}
    for px in passengers:
        cid = px.get("corpId", "")
        if cid:
            corp_ids[cid] = corp_ids.get(cid, 0) + 1
    insights["corporateTravel"] = {
        "totalCorporate": sum(corp_ids.values()),
        "corporatePct": round(sum(corp_ids.values()) / total_pax * 100, 1) if total_pax else 0,
        "companies": dict(sorted(corp_ids.items(), key=lambda x: -x[1])),
    }

    # ── 21. Priority passengers ─────────────────────────────
    priority_codes = {}
    for px in passengers:
        pc = px.get("priorityCode", "")
        if pc:
            priority_codes[pc] = priority_codes.get(pc, 0) + 1
    insights["priorityPassengers"] = {
        "total": sum(priority_codes.values()),
        "codes": dict(sorted(priority_codes.items(), key=lambda x: -x[1])),
    }

    # ── 22. Seniority analytics ─────────────────────────────
    seniority_count = 0
    for px in passengers:
        if px.get("seniorityDate"):
            seniority_count += 1
    insights["seniority"] = {
        "withSeniority": seniority_count,
        "pct": round(seniority_count / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 23. Connection risk scoring ─────────────────────────
    # Passengers with 1-segment are direct; multi-segment with short layovers
    # could be at-risk. Simple heuristic: connecting pax without check-in.
    at_risk = 0
    for px in passengers:
        if px.get("isThru") and not px.get("isCheckedIn") and not px.get("isBoarded"):
            at_risk += 1
    insights["connectionRisk"] = {
        "atRiskCount": at_risk,
        "totalConnecting": connecting,
        "riskPct": round(at_risk / connecting * 100, 1) if connecting else 0,
    }

    # ── 24. Desired vs actual booking class (upgrade/downgrade) ──
    upgrade_count = 0
    downgrade_count = 0
    class_mismatch = 0
    for px in passengers:
        desired = px.get("desiredBookingClass", "")
        actual = px.get("bookingClass", "")
        if desired and actual and desired != actual:
            class_mismatch += 1
            # Simple heuristic: J > Y means upgrade to business
            biz_classes = {"J", "C", "D", "I", "R"}
            if actual in biz_classes and desired not in biz_classes:
                upgrade_count += 1
            elif desired in biz_classes and actual not in biz_classes:
                downgrade_count += 1
    insights["classMismatch"] = {
        "total": class_mismatch,
        "upgrades": upgrade_count,
        "downgrades": downgrade_count,
    }

    # ── 25. Passenger type distribution ─────────────────────
    pax_types = {}
    for px in passengers:
        pt = px.get("passengerType", "")
        if pt:
            pax_types[pt] = pax_types.get(pt, 0) + 1
    insights["passengerTypes"] = dict(
        sorted(pax_types.items(), key=lambda x: -x[1]))

    # ── 26. Boarding pass issuance ──────────────────────────
    bp_issued = sum(1 for px in passengers if px.get("boardingPassIssued"))
    insights["boardingPasses"] = {
        "issued": bp_issued,
        "notIssued": total_pax - bp_issued,
        "issuedPct": round(bp_issued / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 27. Reservation recency ─────────────────────────────
    if reservation_doc:
        from datetime import datetime
        timestamps = []
        for rv in reservation_doc.get("reservations", []):
            mod = rv.get("modifiedAt", "") or rv.get("createdAt", "")
            if mod:
                try:
                    ts = datetime.fromisoformat(mod.replace("Z", "+00:00"))
                    timestamps.append(ts.isoformat())
                except (ValueError, TypeError):
                    pass
        timestamps.sort(reverse=True)
        insights["reservationRecency"] = {
            "latestModification": timestamps[0] if timestamps else None,
            "totalReservations": len(reservation_doc.get("reservations", [])),
        }
    else:
        insights["reservationRecency"] = None

    # ── 28. Equipment & configuration ───────────────────────
    insights["equipment"] = {
        "aircraftType": (schedule_doc or {}).get("aircraftType", "") or (pl or {}).get("aircraftType", ""),
        "seatConfig": ((pl or {}).get("seatConfig", "")
                       or (schedule_doc or {}).get("seatConfig", "")),
    }

    # ── 29. Check-in timeline (time-of-day distribution) ────
    ci_hours = {}
    ci_count = 0
    for px in passengers:
        ci_time = px.get("checkInTime", "")
        if ci_time and ":" in ci_time:
            try:
                hh = int(ci_time.split(":")[0])
                bucket = f"{hh:02d}:00"
                ci_hours[bucket] = ci_hours.get(bucket, 0) + 1
                ci_count += 1
            except (ValueError, IndexError):
                pass
    insights["checkInTimeline"] = {
        "totalWithTime": ci_count,
        "hourDistribution": dict(sorted(ci_hours.items())),
        "peakHour": max(ci_hours, key=ci_hours.get) if ci_hours else None,
        "coveragePct": round(ci_count / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 30. Emergency contact coverage ──────────────────────
    ec_count = sum(1 for px in passengers if px.get("hasEmergencyContact"))
    insights["emergencyContacts"] = {
        "withContact": ec_count,
        "withoutContact": total_pax - ec_count,
        "coveragePct": round(ec_count / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 31. Nationality distribution ────────────────────────
    nat_counts = {}
    for px in passengers:
        nat = px.get("nationality", "")
        if nat:
            nat_counts[nat] = nat_counts.get(nat, 0) + 1
    unknown_nat = total_pax - sum(nat_counts.values())
    insights["nationalityBreakdown"] = {
        "countries": dict(sorted(nat_counts.items(), key=lambda x: -x[1])),
        "uniqueCountries": len(nat_counts),
        "unknown": unknown_nat,
        "coveragePct": round((total_pax - unknown_nat) / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 32. Baggage routing destinations ────────────────────
    route_dests = {}
    pax_with_routes = 0
    for px in passengers:
        routes = px.get("baggageRoutes") or []
        if routes:
            pax_with_routes += 1
            for rt in routes:
                dest = rt.get("destination", "")
                if dest:
                    route_dests[dest] = route_dests.get(dest, 0) + 1
    insights["baggageRouting"] = {
        "destinations": dict(sorted(route_dests.items(), key=lambda x: -x[1])),
        "paxWithRoutes": pax_with_routes,
        "coveragePct": round(pax_with_routes / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 33. Standby & upgrade queue summary ─────────────────
    standby_count = 0
    upgrade_count_q = 0
    standby_cabins = {}
    for px in passengers:
        if px.get("isStandby"):
            standby_count += 1
            cab = px.get("cabin", "?")
            standby_cabins[cab] = standby_cabins.get(cab, 0) + 1
        if px.get("priorityCode") == "UPG":
            upgrade_count_q += 1
    insights["standbyUpgrade"] = {
        "standbyTotal": standby_count,
        "upgradeTotal": upgrade_count_q,
        "standbyCabins": standby_cabins,
        "standbyPct": round(standby_count / total_pax * 100, 1) if total_pax else 0,
    }

    # ── 34. Operational readiness risk ──────────────────────
    no_seat = 0
    no_bp = 0
    no_checkin = 0
    thru_no_seat = 0
    for px in passengers:
        has_seat = bool(px.get("seat", ""))
        has_bp = bool(px.get("boardingPassIssued"))
        is_ci = bool(px.get("isCheckedIn") or px.get("isBoarded"))
        if not has_seat:
            no_seat += 1
        if not has_bp and is_ci:
            no_bp += 1
        if not is_ci:
            no_checkin += 1
        if px.get("isThru") and not has_seat:
            thru_no_seat += 1
    insights["operationalReadiness"] = {
        "noSeat": no_seat,
        "checkedInNoBP": no_bp,
        "notCheckedIn": no_checkin,
        "thruNoSeat": thru_no_seat,
        "readinessPct": round((total_pax - no_checkin) / total_pax * 100, 1) if total_pax else 0,
    }

    return insights


def _build_dashboard_payload(fs, pl, origin, date, change_summary, reservation_doc=None, trip_report_doc=None, schedule_doc=None):
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
        # Enrich nationality counts from reservations (nationality isn't in passenger_list)
        if reservation_doc and "nationalityCounts" in analysis:
            nat_counts = {}
            for rv in reservation_doc.get("reservations", []):
                for pax in rv.get("passengers", []):
                    nat = pax.get("nationality", "")
                    if nat:
                        nat_counts[nat] = nat_counts.get(nat, 0) + 1
            analysis["nationalityCounts"] = nat_counts
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
    current_flight_status = (fs or {}).get("status", "")

    # --- Manifest-derived counts (always available from Sabre data) ---
    # "Not checked in" = passengers on manifest who never checked in.
    # On a PDC/FINAL flight these are effective no-shows.
    not_checked_in = booked_breakdown.get("totalPassengers", 0)
    # "Checked in, not boarded" = checked in but didn't board.
    # On a PDC/FINAL flight these could be offloaded or gate-returned.
    checked_in_not_boarded = checked_in_breakdown.get("totalPassengers", 0)

    # --- Trip-report enrichment (optional, from MLC/MLX Sabre calls) ---
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

    # Enrich aircraft from schedule/passenger list when FlightStatus is empty
    aircraft = (fs or {}).get("aircraft", {})
    if not aircraft.get("type"):
        sched_type = (schedule_doc or {}).get("aircraftType",
                                              "") or (pl or {}).get("aircraftType", "")
        if sched_type:
            aircraft = {**aircraft, "type": sched_type}
            if fs:
                fs["aircraft"] = aircraft

    # --- Group booking summary ---
    group_booking_summary = None
    if pl:
        group_bookings = pl.get("groupBookings", [])
        if not group_bookings:
            # Fallback: compute from passengers (covers pre-backfill docs)
            gmap = {}
            for px in passengers:
                gc = px.get("groupCode", "")
                if not gc:
                    continue
                if gc not in gmap:
                    gmap[gc] = {
                        "groupCode": gc, "pnr": px.get("pnr", ""),
                        "cabin": px.get("cabin", ""),
                        "bookingClass": px.get("bookingClass", ""),
                        "totalMembers": 0, "namedMembers": 0,
                        "unnamedMembers": 0, "checkedIn": 0, "boarded": 0,
                        "members": [],
                    }
                g = gmap[gc]
                g["totalMembers"] += 1
                is_unnamed = px.get("isUnnamedGroup", False) or (
                    px.get("lastName") == "PAX" and not px.get("firstName"))
                if is_unnamed:
                    g["unnamedMembers"] += 1
                else:
                    g["namedMembers"] += 1
                if px.get("isCheckedIn"):
                    g["checkedIn"] += 1
                if px.get("isBoarded"):
                    g["boarded"] += 1
                g["members"].append({
                    "lastName": px.get("lastName", ""),
                    "firstName": px.get("firstName", ""),
                    "pnr": px.get("pnr", ""),
                    "passengerId": px.get("passengerId", ""),
                    "lineNumber": px.get("lineNumber", 0),
                    "isCheckedIn": px.get("isCheckedIn", False),
                    "isBoarded": px.get("isBoarded", False),
                    "isUnnamed": is_unnamed,
                    "seat": px.get("seat", ""),
                })
            group_bookings = sorted(
                gmap.values(), key=lambda g: g["groupCode"])
        else:
            # Enrich stored groups with member details from passengers
            pax_by_group = {}
            for px in passengers:
                gc = px.get("groupCode", "")
                if gc:
                    pax_by_group.setdefault(gc, []).append(px)
            for gb in group_bookings:
                if not gb.get("members"):
                    gc = gb["groupCode"]
                    gb["members"] = []
                    for px in pax_by_group.get(gc, []):
                        is_unnamed = px.get("isUnnamedGroup", False) or (
                            px.get("lastName") == "PAX" and not px.get("firstName"))
                        gb["members"].append({
                            "lastName": px.get("lastName", ""),
                            "firstName": px.get("firstName", ""),
                            "pnr": px.get("pnr", ""),
                            "passengerId": px.get("passengerId", ""),
                            "lineNumber": px.get("lineNumber", 0),
                            "isCheckedIn": px.get("isCheckedIn", False),
                            "isBoarded": px.get("isBoarded", False),
                            "isUnnamed": is_unnamed,
                            "seat": px.get("seat", ""),
                        })
        if group_bookings:
            group_booking_summary = {
                "totalGroups": len(group_bookings),
                "totalGroupPassengers": sum(g["totalMembers"] for g in group_bookings),
                "totalUnnamed": sum(g["unnamedMembers"] for g in group_bookings),
                "totalNamed": sum(g["namedMembers"] for g in group_bookings),
                "groups": group_bookings,
            }

    # --- Special requests & services summary from reservations ---
    special_requests_summary = None
    if reservation_doc:
        meal_counts = {}
        wheelchair_counts = {}
        total_emergency = 0
        total_ff = 0
        ff_tiers = {}
        booking_sources = {}
        for rv in reservation_doc.get("reservations", []):
            # Booking channel
            pos = rv.get("pointOfSale", {})
            if isinstance(pos, dict) and pos:
                src = pos.get("agentSine", "")
                if src:
                    booking_sources[src] = booking_sources.get(src, 0) + 1
            for pax in rv.get("passengers", []):
                meal = pax.get("specialMeal", "")
                if meal:
                    meal_counts[meal] = meal_counts.get(meal, 0) + 1
                wc = pax.get("wheelchairCode", "")
                if wc:
                    wheelchair_counts[wc] = wheelchair_counts.get(wc, 0) + 1
                if pax.get("hasEmergencyContact"):
                    total_emergency += 1
                if pax.get("frequentFlyerNumber"):
                    total_ff += 1
                    tier = pax.get("ffTierName", "")
                    if tier:
                        ff_tiers[tier] = ff_tiers.get(tier, 0) + 1
        special_requests_summary = {
            "specialMeals": meal_counts,
            "totalSpecialMeals": sum(meal_counts.values()),
            "wheelchairs": wheelchair_counts,
            "totalWheelchairs": sum(wheelchair_counts.values()),
            "emergencyContacts": total_emergency,
            "frequentFlyers": total_ff,
            "ffTiers": ff_tiers,
            "bookingSources": booking_sources,
        }

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
        "flightPhase": _derive_flight_phase(fs, analysis),
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
                # Manifest-derived (always available from Sabre GetPassengerListRS)
                "notCheckedIn": not_checked_in,
                "checkedInNotBoarded": checked_in_not_boarded,
                "flightClosed": current_flight_status in ("FINAL", "PDC"),
            },
        },
        "tree": tree,
        "schedule": _strip_id(schedule_doc) if schedule_doc else None,
        "groupBookingSummary": group_booking_summary,
        "specialRequestsSummary": special_requests_summary,
        "codeshareInfo": fs.get("codeshareInfo", []) if fs else [],
        "departureGate": pl.get("departureGate", "") if pl else "",
        "insights": _build_insights(passengers, reservation_doc, schedule_doc, pl, change_summary) if pl and passengers else None,
    }


def _latest_by_key(cursor, key_fn):
    """Given a cursor sorted by fetchedAt descending, keep only the first doc per key."""
    seen = {}
    for doc in cursor:
        k = key_fn(doc)
        if k not in seen:
            seen[k] = doc
    return seen


def _flight_key(doc):
    return (doc.get("airline", "GF"), doc.get("flightNumber"), doc.get("origin", ""), doc.get("departureDate", ""))


# ── Aggregation pipelines for fast list_flights ─────────────────────────

def _latest_per_flight_agg(match: dict, extra_project: dict | None = None):
    """Build a MongoDB aggregation pipeline that returns the latest doc per
    (airline, flightNumber, origin, departureDate), excluding _raw."""
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$sort": {"fetchedAt": -1}})
    pipeline.append({
        "$group": {
            "_id": {
                "airline": {"$ifNull": ["$airline", "GF"]},
                "flightNumber": "$flightNumber",
                "origin": {"$ifNull": ["$origin", ""]},
                "departureDate": {"$ifNull": ["$departureDate", ""]},
            },
            "doc": {"$first": "$$ROOT"},
        }
    })
    project = {"doc._raw": 0,
               "doc.passengers": 0} if extra_project is None else extra_project
    pipeline.append({"$project": project})
    return pipeline


def _latest_schedule_agg(match: dict):
    """Aggregation for schedules keyed by (airline, flightNumber, departureDate)."""
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$sort": {"fetchedAt": -1}})
    pipeline.append({
        "$group": {
            "_id": {
                "airline": {"$ifNull": ["$airline", "GF"]},
                "flightNumber": "$flightNumber",
                "departureDate": {"$ifNull": ["$departureDate", ""]},
            },
            "doc": {"$first": "$$ROOT"},
        }
    })
    pipeline.append({"$project": {"doc._raw": 0}})
    return pipeline


def _quick_operational_counts(passenger_doc):
    """Extract checkedIn/boarded/notCheckedIn from pre-computed aggregation fields
    (set by _agg_passenger_list), falling back to a single-pass count if missing."""
    if not passenger_doc:
        return {"checkedIn": 0, "boarded": 0, "notCheckedIn": 0,
                "soulsOnBoard": 0, "economySouls": 0, "businessSouls": 0}
    # Prefer pre-computed fields from the aggregation pipeline
    if "_checkedIn" in passenger_doc:
        return {
            "checkedIn": passenger_doc.get("_checkedIn", 0),
            "boarded": passenger_doc.get("_boarded", 0),
            "notCheckedIn": passenger_doc.get("_notCheckedIn", 0),
            "soulsOnBoard": passenger_doc.get("_boardedSouls", 0),
            "economySouls": passenger_doc.get("_econSouls", 0),
            "businessSouls": passenger_doc.get("_bizSouls", 0),
        }
    # Fallback: compute from passengers array (e.g. when called outside list)
    # Note: checkedIn includes boarded (same as _analyze_passengers)
    checked_in = 0
    boarded = 0
    not_checked_in = 0
    econ_souls = 0
    biz_souls = 0
    boarded_souls = 0
    for p in passenger_doc.get("passengers", []):
        has_infant = 1 if p.get("hasInfant") else 0
        cabin_key = "J" if p.get("cabin") == "J" else "Y"
        if p.get("isCheckedIn"):
            checked_in += 1
        if p.get("isBoarded"):
            boarded += 1
            boarded_souls += 1 + has_infant
        if not p.get("isCheckedIn"):
            not_checked_in += 1
        if cabin_key == "J":
            biz_souls += 1 + has_infant
        else:
            econ_souls += 1 + has_infant
    return {
        "checkedIn": checked_in,
        "boarded": boarded,
        "notCheckedIn": not_checked_in,
        "soulsOnBoard": boarded_souls,
        "economySouls": econ_souls,
        "businessSouls": biz_souls,
    }


def _summarize_flight_for_list_fast(flight_status_doc, passenger_doc):
    """Lightweight summary for the flight list — avoids full _analyze_passengers."""
    destination = _extract_destination(flight_status_doc, passenger_doc)
    passenger_summary = {
        "totalPassengers": passenger_doc.get("totalPassengers", 0) if passenger_doc else 0,
        "adultCount": passenger_doc.get("adultCount", 0) if passenger_doc else 0,
        "childCount": passenger_doc.get("childCount", 0) if passenger_doc else 0,
        "infantCount": passenger_doc.get("infantCount", 0) if passenger_doc else 0,
        "totalSouls": passenger_doc.get("totalSouls", 0) if passenger_doc else 0,
    }
    ops = _quick_operational_counts(passenger_doc)
    # Build a minimal analysis-like dict for _derive_flight_phase.
    # Top-level checkedIn/boarded match _analyze_passengers (checkedIn INCLUDES boarded).
    # stateBreakdown is mutually exclusive: booked → checkedIn-only → boarded.
    ci_only = ops["checkedIn"] - ops["boarded"]  # checked-in but NOT boarded
    mini_analysis = {
        "checkedIn": ops["checkedIn"],
        "boarded": ops["boarded"],
        "notCheckedIn": ops["notCheckedIn"],
        "stateBreakdown": {
            "booked": {"totalPassengers": ops["notCheckedIn"]},
            "checkedIn": {"totalPassengers": ci_only},
            "boarded": {"totalPassengers": ops["boarded"], "totalSouls": ops["soulsOnBoard"]},
        },
        "cabinTotals": {
            "economy": {"souls": ops["economySouls"]},
            "business": {"souls": ops["businessSouls"]},
        },
    }
    phase = _derive_flight_phase(flight_status_doc, mini_analysis)
    return {
        "destination": destination,
        "passengerSummary": passenger_summary,
        "operationalSummary": {
            "checkedIn": ops["checkedIn"],
            "boarded": ops["boarded"],
            "notCheckedIn": ops["notCheckedIn"],
            "soulsOnBoard": ops["soulsOnBoard"],
            "economySouls": ops["economySouls"],
            "businessSouls": ops["businessSouls"],
        },
        "flightPhase": phase,
    }


@router.get("")
def list_flights(
    date: str = Query(None, description="Filter by departure date YYYY-MM-DD"),
):
    """List all distinct flights in the database with their latest status."""
    validate_date(date)
    db = get_db()
    match = {"departureDate": date} if date else {}

    # ── Parallel aggregation: latest doc per flight key from each collection ──
    def _agg_flight_status():
        pipeline = _latest_per_flight_agg(match, extra_project={"doc._raw": 0})
        return {
            (d["_id"]["airline"], d["_id"]["flightNumber"],
             d["_id"]["origin"], d["_id"]["departureDate"]): d["doc"]
            for d in db["flight_status"].aggregate(pipeline, allowDiskUse=True)
        }

    def _agg_passenger_list():
        """Return latest passenger_list per flight key with pre-computed status
        counts.  The aggregation computes checkedIn/boarded/notCheckedIn and
        soul counts server-side so we don't transfer the full passengers array."""
        pipeline = []
        if match:
            pipeline.append({"$match": match})
        pipeline.append({"$sort": {"fetchedAt": -1}})
        pipeline.append({
            "$group": {
                "_id": {
                    "airline": {"$ifNull": ["$airline", "GF"]},
                    "flightNumber": "$flightNumber",
                    "origin": {"$ifNull": ["$origin", ""]},
                    "departureDate": {"$ifNull": ["$departureDate", ""]},
                },
                "doc": {"$first": "$$ROOT"},
            }
        })
        # Compute operational counts from the passengers array inside MongoDB
        # Note: checkedIn includes boarded passengers (same as _analyze_passengers)
        pipeline.append({"$addFields": {
            "doc._checkedIn": {
                "$size": {"$filter": {
                    "input": {"$ifNull": ["$doc.passengers", []]},
                    "as": "p",
                    "cond": {"$eq": [{"$ifNull": ["$$p.isCheckedIn", False]}, True]},
                }}
            },
            "doc._boarded": {
                "$size": {"$filter": {
                    "input": {"$ifNull": ["$doc.passengers", []]},
                    "as": "p",
                    "cond": {"$eq": [{"$ifNull": ["$$p.isBoarded", False]}, True]},
                }}
            },
            "doc._notCheckedIn": {
                "$size": {"$filter": {
                    "input": {"$ifNull": ["$doc.passengers", []]},
                    "as": "p",
                    "cond": {"$ne": [{"$ifNull": ["$$p.isCheckedIn", False]}, True]},
                }}
            },
            "doc._boardedSouls": {
                "$reduce": {
                    "input": {"$filter": {
                        "input": {"$ifNull": ["$doc.passengers", []]},
                        "as": "p",
                        "cond": {"$eq": [{"$ifNull": ["$$p.isBoarded", False]}, True]},
                    }},
                    "initialValue": 0,
                    "in": {"$add": [
                        "$$value", 1,
                        {"$cond": [
                            {"$ifNull": ["$$this.hasInfant", False]}, 1, 0]},
                    ]},
                }
            },
            "doc._econSouls": {
                "$reduce": {
                    "input": {"$ifNull": ["$doc.passengers", []]},
                    "initialValue": 0,
                    "in": {"$add": [
                        "$$value",
                        {"$cond": [{"$ne": ["$$this.cabin", "J"]}, {
                            "$add": [1, {"$cond": [{"$ifNull": ["$$this.hasInfant", False]}, 1, 0]}]}, 0]},
                    ]},
                }
            },
            "doc._bizSouls": {
                "$reduce": {
                    "input": {"$ifNull": ["$doc.passengers", []]},
                    "initialValue": 0,
                    "in": {"$add": [
                        "$$value",
                        {"$cond": [{"$eq": ["$$this.cabin", "J"]}, {
                            "$add": [1, {"$cond": [{"$ifNull": ["$$this.hasInfant", False]}, 1, 0]}]}, 0]},
                    ]},
                }
            },
        }})
        # Strip the heavy arrays — we only need top-level counts + computed fields
        pipeline.append({"$project": {"doc.passengers": 0, "doc._raw": 0}})
        result = {}
        for d in db["passenger_list"].aggregate(pipeline, allowDiskUse=True):
            key = (d["_id"]["airline"], d["_id"]["flightNumber"],
                   d["_id"]["origin"], d["_id"]["departureDate"])
            result[key] = d["doc"]
        return result

    def _agg_schedules():
        pipeline = _latest_schedule_agg(match)
        return {
            (d["_id"]["airline"], d["_id"]["flightNumber"],
             d["_id"]["departureDate"]): d["doc"]
            for d in db["flight_schedules"].aggregate(pipeline, allowDiskUse=True)
        }

    # Run all 3 aggregations in parallel threads
    with ThreadPoolExecutor(max_workers=3) as executor:
        fs_future = executor.submit(_agg_flight_status)
        pl_future = executor.submit(_agg_passenger_list)
        sc_future = executor.submit(_agg_schedules)
        fs_by_key = fs_future.result()
        passenger_by_key = pl_future.result()
        schedule_by_key = sc_future.result()

    # Sort by (departureDate, flightNumber)
    sorted_keys = sorted(fs_by_key.keys(), key=lambda k: (k[3], k[1]))

    flights = []
    for key in sorted_keys:
        r = fs_by_key[key]
        airline, fn, origin_val, dep_date = key
        passenger_doc = passenger_by_key.get(key)
        summary = _summarize_flight_for_list_fast(r, passenger_doc)

        sched_key = (airline, fn, dep_date)
        schedule_data = schedule_by_key.get(sched_key)
        sched_info = None
        if schedule_data:
            sched_info = {
                "origin": schedule_data.get("origin", ""),
                "destination": schedule_data.get("destination", ""),
                "scheduledDeparture": schedule_data.get("scheduledDeparture", ""),
                "scheduledArrival": schedule_data.get("scheduledArrival", ""),
                "aircraftType": schedule_data.get("aircraftType", ""),
                "elapsedTime": schedule_data.get("elapsedTime", ""),
                "airMilesFlown": schedule_data.get("airMilesFlown", 0),
            }

        flights.append({
            "airline": airline,
            "flightNumber": fn,
            "origin": origin_val,
            "destination": summary["destination"],
            "departureDate": dep_date,
            "status": r.get("status"),
            "gate": r.get("gate"),
            "aircraft": r.get("aircraft"),
            "schedule": r.get("schedule"),
            "passengerCounts": r.get("passengerCounts"),
            "jumpSeat": r.get("jumpSeat"),
            "passengerSummary": summary["passengerSummary"],
            "operationalSummary": summary["operationalSummary"],
            "flightPhase": summary["flightPhase"],
            "publishedSchedule": sched_info,
            "fetchedAt": str(r.get("fetchedAt", "")),
        })
    return flights


def _fetch_dashboard_data_parallel(db, flight_number, origin, date, snapshot_sequence=None):
    """Fetch all dashboard data in parallel using ThreadPoolExecutor."""
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    res_query = {"flightNumber": flight_number}
    if origin:
        res_query["departureAirport"] = origin
    if date:
        res_query["departureDate"] = date

    report_query = {"flightNumber": flight_number}
    if origin:
        report_query["origin"] = origin
    if date:
        report_query["departureDate"] = date

    sched_query = {"flightNumber": flight_number}
    if date:
        sched_query["departureDate"] = date

    change_match = {"flightNumber": flight_number}
    if origin:
        change_match["origin"] = origin
    if date:
        change_match["departureDate"] = date

    results = {}

    def fetch_flight_status():
        if snapshot_sequence:
            return get_snapshot_data_as_of(db, flight_number, "flight_status", snapshot_sequence, origin, date)
        doc = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
        return _strip_id(doc)

    def fetch_passenger_list():
        if snapshot_sequence:
            return get_snapshot_data_as_of(db, flight_number, "passenger_list", snapshot_sequence, origin, date)
        doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
        return _strip_id(doc)

    def fetch_reservations():
        if snapshot_sequence:
            return get_snapshot_data_as_of(db, flight_number, "reservations", snapshot_sequence, origin, date)
        doc = db["reservations"].find_one(res_query, sort=[("fetchedAt", -1)])
        return _strip_id(doc)

    def fetch_trip_reports():
        doc = db["trip_reports"].find_one(
            report_query, sort=[("fetchedAt", -1)])
        return _strip_id(doc)

    def fetch_schedule():
        doc = db["flight_schedules"].find_one(
            sched_query, sort=[("fetchedAt", -1)])
        return _strip_id(doc)

    def fetch_change_summary():
        counts = {}
        for doc in db["changes"].find(change_match, {"changeType": 1}):
            ct = doc.get("changeType")
            if ct:
                counts[ct] = counts.get(ct, 0) + 1
        return counts

    def fetch_flight_meta():
        """Fetch flight-level metadata (e.g. flightSequenceNumber) from the flights collection."""
        fq = {"flightNumber": flight_number}
        if origin:
            fq["origin"] = origin
        if date:
            fq["departureDate"] = date
        doc = db["flights"].find_one(fq, {"flightSequenceNumber": 1})
        return doc

    # Run all queries in parallel
    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = {
            executor.submit(fetch_flight_status): "flight_status",
            executor.submit(fetch_passenger_list): "passenger_list",
            executor.submit(fetch_reservations): "reservations",
            executor.submit(fetch_trip_reports): "trip_reports",
            executor.submit(fetch_schedule): "schedule",
            executor.submit(fetch_change_summary): "change_summary",
            executor.submit(fetch_flight_meta): "flight_meta",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error(f"Error fetching {key}: {e}")
                results[key] = None

    return results


@router.get("/{flight_number}/dashboard")
def get_flight_dashboard(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
):
    """
    Combined dashboard endpoint — returns flight status + deep passenger
    analysis in a single response for the frontend dashboard view.
    """
    validate_date(date)
    validate_origin(origin)

    # Check cache first
    cache_key = _get_cache_key(flight_number, origin, date, snapshot_sequence)
    cached = _get_cached(cache_key)
    if cached:
        return cached

    db = get_db()

    # Fetch all data in parallel
    data = _fetch_dashboard_data_parallel(
        db, flight_number, origin, date, snapshot_sequence)

    fs = data.get("flight_status")
    pl = data.get("passenger_list")
    reservation_doc = data.get("reservations")
    trip_report_doc = data.get("trip_reports")
    schedule_doc = data.get("schedule")
    change_summary = data.get("change_summary") or {}
    flight_meta = data.get("flight_meta")

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    result = _build_dashboard_payload(fs, pl, origin, date, change_summary,
                                      reservation_doc, trip_report_doc, schedule_doc)

    # Attach flight sequence number from flights collection
    if flight_meta and flight_meta.get("flightSequenceNumber"):
        result["flightSequenceNumber"] = flight_meta["flightSequenceNumber"]

    # Cache the result (don't cache snapshot views)
    if not snapshot_sequence:
        _set_cache(cache_key, result)

    return result


@router.get("/{flight_number}/tree")
def get_flight_tree(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
):
    """Return a dedicated tree payload for the selected flight."""
    validate_date(date)
    validate_origin(origin)

    # Tree uses same cache as dashboard
    cache_key = _get_cache_key(flight_number, origin, date, snapshot_sequence)
    cached = _get_cached(cache_key)
    if cached:
        return cached.get("tree") or {
            "title": "Aircraft Humans Breakdown Tree",
            "badge": "Sabre Live",
            "width": 940,
            "height": 600,
            "nodes": [],
            "edges": [],
            "statusCards": [],
        }

    db = get_db()

    # Reuse parallel data fetching (only need fs, pl, reservations, trip_reports for tree)
    data = _fetch_dashboard_data_parallel(
        db, flight_number, origin, date, snapshot_sequence)

    fs = data.get("flight_status")
    pl = data.get("passenger_list")
    reservation_doc = data.get("reservations")
    trip_report_doc = data.get("trip_reports")

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    payload = _build_dashboard_payload(fs, pl, origin, date, {},
                                       reservation_doc, trip_report_doc, None)
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
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
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

    if snapshot_sequence:
        doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="flight_status",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
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
