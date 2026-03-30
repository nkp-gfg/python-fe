"""
Process Audit Rules Engine.

Evaluates passenger data against operational rules to detect
process violations and discrepancies. Rules run against a single
point-in-time snapshot plus the change history for that flight.
"""

import logging
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)


# ── Severity Levels ───────────────────────────────────────────────────────

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


def _alert(rule_id, severity, message, pnr=None, passenger_name=None, details=None):
    """Create an audit alert dict."""
    alert = {
        "ruleId": rule_id,
        "severity": severity,
        "message": message,
    }
    if pnr:
        alert["pnr"] = pnr
    if passenger_name:
        alert["passengerName"] = passenger_name
    if details:
        alert["details"] = details
    return alert


def _pax_name(p):
    return f"{p.get('lastName', '')}/{p.get('firstName', '')}".strip("/")


# ── Individual Rule Functions ─────────────────────────────────────────────

def rule_desired_upgrade_not_processed(passengers, changes):
    """
    Passengers who have desiredBookingClass set, are boarded,
    but cabin still matches their original bookingClass (not upgraded).
    """
    alerts = []
    # Build set of PNRs that received UPGRADE_CONFIRMED or CABIN_CHANGE (upgrade direction)
    upgraded_pnrs = set()
    for c in changes:
        ct = c.get("changeType", "")
        if ct == "UPGRADE_CONFIRMED":
            pax = c.get("passenger", {})
            upgraded_pnrs.add(pax.get("pnr", ""))
        elif ct == "CABIN_CHANGE":
            meta = c.get("metadata", {})
            if meta.get("direction") == "UPGRADE":
                pax = c.get("passenger", {})
                upgraded_pnrs.add(pax.get("pnr", ""))

    for p in passengers:
        desired = p.get("desiredBookingClass", "")
        cabin = p.get("cabin", "")
        booking = p.get("bookingClass", "")
        pnr = p.get("pnr", "")

        if desired and cabin != desired and p.get("isBoarded"):
            if pnr not in upgraded_pnrs:
                alerts.append(_alert(
                    "UPGRADE_DESIRED_NOT_PROCESSED",
                    WARNING,
                    f"Desired upgrade to {desired} but still in {cabin} cabin (boarded)",
                    pnr=pnr,
                    passenger_name=_pax_name(p),
                    details={
                        "currentCabin": cabin,
                        "bookingClass": booking,
                        "desiredBookingClass": desired,
                        "priorityCode": p.get("priorityCode", ""),
                        "seniorityDate": p.get("seniorityDate", ""),
                        "lineNumber": p.get("lineNumber"),
                    },
                ))
    return alerts


def rule_boarded_without_checkin(passengers):
    """Passengers marked as boarded but not checked in."""
    alerts = []
    for p in passengers:
        if p.get("isBoarded") and not p.get("isCheckedIn"):
            alerts.append(_alert(
                "BOARDED_WITHOUT_CHECKIN",
                CRITICAL,
                "Passenger boarded without being checked in",
                pnr=p.get("pnr"),
                passenger_name=_pax_name(p),
            ))
    return alerts


def rule_boarded_without_boarding_pass(passengers):
    """Passengers boarded but no boarding pass issued."""
    alerts = []
    for p in passengers:
        if p.get("isBoarded") and not p.get("boardingPassIssued"):
            alerts.append(_alert(
                "BOARDED_WITHOUT_BP",
                CRITICAL,
                "Passenger boarded without a boarding pass",
                pnr=p.get("pnr"),
                passenger_name=_pax_name(p),
            ))
    return alerts


def rule_cabin_overcapacity(passengers, cabin_summary):
    """More passengers in a cabin than the authorized capacity."""
    alerts = []
    cabin_counts = Counter()
    for p in passengers:
        cab = p.get("cabin", "")
        if cab:
            cabin_counts[cab] += 1

    auth_map = {}
    for cs in cabin_summary:
        auth_map[cs.get("cabin", "")] = cs.get("authorized", 0)

    for cab, count in cabin_counts.items():
        auth = auth_map.get(cab, 0)
        if auth > 0 and count > auth:
            alerts.append(_alert(
                "CABIN_OVERCAPACITY",
                CRITICAL,
                f"Cabin {cab}: {count} passengers exceed authorized capacity of {auth}",
                details={"cabin": cab, "count": count, "authorized": auth},
            ))
    return alerts


def rule_duplicate_seats(passengers):
    """Two or more passengers assigned to the same seat."""
    alerts = []
    seat_pax = {}
    for p in passengers:
        seat = p.get("seat", "")
        if seat:
            seat_pax.setdefault(seat, []).append(p)

    for seat, pax_list in seat_pax.items():
        if len(pax_list) > 1:
            names = [f"{_pax_name(p)} ({p.get('pnr', '')})" for p in pax_list]
            alerts.append(_alert(
                "DUPLICATE_SEAT",
                CRITICAL,
                f"Seat {seat} assigned to {len(pax_list)} passengers: {', '.join(names)}",
                details={
                    "seat": seat,
                    "passengers": [
                        {"pnr": p.get("pnr", ""), "name": _pax_name(p)}
                        for p in pax_list
                    ],
                },
            ))
    return alerts


def rule_cabin_available_mismatch(passengers, cabin_summary):
    """
    cabinSummary.available doesn't match authorized - actual count.
    Indicates a Sabre vs manifest discrepancy.
    """
    alerts = []
    cabin_counts = Counter()
    for p in passengers:
        cab = p.get("cabin", "")
        if cab:
            cabin_counts[cab] += 1

    for cs in cabin_summary:
        cab = cs.get("cabin", "")
        auth = cs.get("authorized", 0)
        reported_avail = cs.get("available", 0)
        actual_count = cabin_counts.get(cab, 0)
        expected_avail = auth - actual_count

        if auth > 0 and reported_avail != expected_avail:
            alerts.append(_alert(
                "CABIN_AVAILABLE_MISMATCH",
                WARNING,
                f"Cabin {cab}: Sabre reports {reported_avail} available but "
                f"authorized({auth}) - manifest({actual_count}) = {expected_avail}",
                details={
                    "cabin": cab,
                    "authorized": auth,
                    "manifestCount": actual_count,
                    "reportedAvailable": reported_avail,
                    "expectedAvailable": expected_avail,
                },
            ))
    return alerts


def rule_boarded_without_docs(passengers):
    """Boarded passengers missing DOCS edit code (API compliance concern)."""
    alerts = []
    for p in passengers:
        if p.get("isBoarded"):
            codes = set(p.get("editCodes", []))
            if "DOCS" not in codes:
                alerts.append(_alert(
                    "BOARDED_WITHOUT_DOCS",
                    WARNING,
                    "Boarded passenger missing DOCS (travel document data)",
                    pnr=p.get("pnr"),
                    passenger_name=_pax_name(p),
                    details={"editCodes": list(codes)},
                ))
    return alerts


def rule_checkedin_without_boarding_pass(passengers):
    """Checked-in passengers without a boarding pass (may miss boarding)."""
    alerts = []
    for p in passengers:
        if p.get("isCheckedIn") and not p.get("isBoarded") and not p.get("boardingPassIssued"):
            alerts.append(_alert(
                "CHECKEDIN_NO_BP",
                WARNING,
                "Checked-in but no boarding pass issued",
                pnr=p.get("pnr"),
                passenger_name=_pax_name(p),
            ))
    return alerts


def rule_party_size_mismatch(passengers, reservations):
    """
    Reservation numberInParty doesn't match actual passenger count on manifest.
    """
    alerts = []
    pnr_counts = Counter()
    for p in passengers:
        pnr = p.get("pnr", "")
        if pnr:
            pnr_counts[pnr] += 1

    for r in reservations:
        pnr = r.get("pnr", "")
        declared = r.get("numberInParty", 0)
        actual = pnr_counts.get(pnr, 0)
        # Only flag if PNR is on the manifest and counts differ
        if actual > 0 and declared != actual:
            alerts.append(_alert(
                "PARTY_SIZE_MISMATCH",
                WARNING,
                f"PNR {pnr}: reservation says {declared} in party but {actual} on manifest",
                pnr=pnr,
                details={
                    "declaredPartySize": declared,
                    "manifestCount": actual,
                },
            ))
    return alerts


def rule_upgrade_queue_skipped(passengers, changes):
    """
    A lower-priority (higher lineNumber or later seniority) passenger was upgraded,
    while a higher-priority passenger with the same desired cabin was not.
    """
    alerts = []

    # Build set of PNRs that received an upgrade
    upgraded_pnrs = set()
    for c in changes:
        ct = c.get("changeType", "")
        if ct in ("UPGRADE_CONFIRMED", "CABIN_CHANGE"):
            meta = c.get("metadata", {})
            if ct == "UPGRADE_CONFIRMED" or meta.get("direction") == "UPGRADE":
                pax = c.get("passenger", {})
                upgraded_pnrs.add(pax.get("pnr", ""))

    if not upgraded_pnrs:
        return alerts

    # Group upgrade candidates by desired cabin
    candidates_by_desired = {}
    for p in passengers:
        desired = p.get("desiredBookingClass", "")
        if desired:
            candidates_by_desired.setdefault(desired, []).append(p)

    for desired, candidates in candidates_by_desired.items():
        upgraded = [p for p in candidates if p.get("pnr") in upgraded_pnrs]
        not_upgraded = [p for p in candidates if p.get("pnr") not in upgraded_pnrs
                        and p.get("cabin") != desired]  # Still waiting

        for skipped in not_upgraded:
            skip_seniority = skipped.get("seniorityDate", "9999-12-31")
            skip_line = skipped.get("lineNumber", 9999)

            for upg in upgraded:
                upg_seniority = upg.get("seniorityDate", "9999-12-31")
                upg_line = upg.get("lineNumber", 9999)

                # Higher priority = earlier seniority or lower line number
                if skip_seniority < upg_seniority or (skip_seniority == upg_seniority and skip_line < upg_line):
                    alerts.append(_alert(
                        "UPGRADE_QUEUE_SKIPPED",
                        WARNING,
                        f"Higher-priority passenger {_pax_name(skipped)} (line {skip_line}, "
                        f"seniority {skip_seniority}) was skipped while "
                        f"{_pax_name(upg)} (line {upg_line}, seniority {upg_seniority}) was upgraded",
                        pnr=skipped.get("pnr"),
                        passenger_name=_pax_name(skipped),
                        details={
                            "skippedLine": skip_line,
                            "skippedSeniority": skip_seniority,
                            "upgradedPnr": upg.get("pnr"),
                            "upgradedName": _pax_name(upg),
                            "upgradedLine": upg_line,
                            "upgradedSeniority": upg_seniority,
                            "desiredCabin": desired,
                        },
                    ))
                    break  # One alert per skipped passenger is enough

    return alerts


def rule_informal_cabin_move(passengers, changes):
    """
    Passenger's cabin changed (via CABIN_CHANGE event) but their bookingClass
    was never updated — indicates an informal/unprocessed upgrade.
    """
    alerts = []
    # PNRs that had cabin change
    cabin_changed_pnrs = set()
    # PNRs that had class change
    class_changed_pnrs = set()

    for c in changes:
        ct = c.get("changeType", "")
        pax = c.get("passenger", {})
        pnr = pax.get("pnr", "")
        if ct == "CABIN_CHANGE":
            cabin_changed_pnrs.add(pnr)
        elif ct == "CLASS_CHANGE":
            class_changed_pnrs.add(pnr)

    informal = cabin_changed_pnrs - class_changed_pnrs
    if not informal:
        return alerts

    pax_by_pnr = {p.get("pnr"): p for p in passengers}
    for pnr in informal:
        p = pax_by_pnr.get(pnr)
        if p:
            alerts.append(_alert(
                "INFORMAL_CABIN_MOVE",
                CRITICAL,
                f"Cabin changed but booking class not updated (cabin={p.get('cabin')}, "
                f"bookingClass={p.get('bookingClass')})",
                pnr=pnr,
                passenger_name=_pax_name(p),
                details={
                    "currentCabin": p.get("cabin"),
                    "bookingClass": p.get("bookingClass"),
                },
            ))
    return alerts


def rule_staff_in_premium_no_upgrade(passengers, changes):
    """
    Staff passengers (corpId='T') seated in J/F cabin without an
    UPGRADE_CONFIRMED or CABIN_CHANGE upgrade event.
    """
    alerts = []
    upgraded_pnrs = set()
    for c in changes:
        ct = c.get("changeType", "")
        if ct == "UPGRADE_CONFIRMED":
            pax = c.get("passenger", {})
            upgraded_pnrs.add(pax.get("pnr", ""))
        elif ct == "CABIN_CHANGE":
            meta = c.get("metadata", {})
            if meta.get("direction") == "UPGRADE":
                pax = c.get("passenger", {})
                upgraded_pnrs.add(pax.get("pnr", ""))

    for p in passengers:
        pnr = p.get("pnr", "")
        if (p.get("corpId") == "T"
                and p.get("cabin") in ("J", "F")
                and pnr not in upgraded_pnrs):
            alerts.append(_alert(
                "STAFF_PREMIUM_NO_UPGRADE",
                CRITICAL,
                f"Staff passenger in {p.get('cabin')} cabin with no upgrade event on record",
                pnr=pnr,
                passenger_name=_pax_name(p),
                details={
                    "cabin": p.get("cabin"),
                    "bookingClass": p.get("bookingClass"),
                    "corpId": p.get("corpId"),
                },
            ))
    return alerts


# ── Main Audit Function ──────────────────────────────────────────────────

def run_audit(passenger_doc, reservations_doc, changes_list):
    """
    Run all audit rules against a flight's data.

    Parameters
    ----------
    passenger_doc : dict
        Latest passenger_list document (with passengers, cabinSummary, etc.)
    reservations_doc : dict
        Latest reservations document (with reservations array)
    changes_list : list
        All change records for this flight

    Returns
    -------
    dict with keys: alerts (list), summary (counts by severity), totalAlerts (int)
    """
    passengers = passenger_doc.get("passengers", []) if passenger_doc else []
    cabin_summary = passenger_doc.get(
        "cabinSummary", []) if passenger_doc else []
    reservations = reservations_doc.get(
        "reservations", []) if reservations_doc else []
    changes = changes_list or []

    all_alerts = []

    # Single-snapshot rules
    all_alerts.extend(rule_boarded_without_checkin(passengers))
    all_alerts.extend(rule_boarded_without_boarding_pass(passengers))
    all_alerts.extend(rule_cabin_overcapacity(passengers, cabin_summary))
    all_alerts.extend(rule_duplicate_seats(passengers))
    all_alerts.extend(rule_cabin_available_mismatch(passengers, cabin_summary))
    all_alerts.extend(rule_boarded_without_docs(passengers))
    all_alerts.extend(rule_checkedin_without_boarding_pass(passengers))
    all_alerts.extend(rule_party_size_mismatch(passengers, reservations))

    # Multi-snapshot rules (use change history)
    all_alerts.extend(rule_desired_upgrade_not_processed(passengers, changes))
    all_alerts.extend(rule_upgrade_queue_skipped(passengers, changes))
    all_alerts.extend(rule_informal_cabin_move(passengers, changes))
    all_alerts.extend(rule_staff_in_premium_no_upgrade(passengers, changes))

    # Compute summary
    severity_counts = Counter(a["severity"] for a in all_alerts)
    summary = {
        "critical": severity_counts.get(CRITICAL, 0),
        "warning": severity_counts.get(WARNING, 0),
        "info": severity_counts.get(INFO, 0),
    }

    # Build per-passenger alert index (for inline badges)
    pax_alerts = {}
    for a in all_alerts:
        pnr = a.get("pnr")
        if pnr:
            pax_alerts.setdefault(pnr, []).append(a["ruleId"])

    return {
        "alerts": all_alerts,
        "summary": summary,
        "totalAlerts": len(all_alerts),
        "passengerAlerts": pax_alerts,
    }
