"""
Change detection between consecutive Sabre snapshots.

Compares two parsed snapshots and produces a list of change records
for storage in the `changes` collection.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pax_key(passenger):
    """Create a unique key for a passenger.

    Uses PNR + lastName + firstName when PNR is available.
    Falls back to lineNumber for staff/non-revenue passengers without a PNR.
    """
    pnr = passenger.get("pnr", "")
    last = passenger.get("lastName", "")
    first = passenger.get("firstName", "")
    if pnr:
        return f"{pnr}|{last}|{first}"
    # Staff may have no PNR — use lineNumber as fallback key
    line = passenger.get("lineNumber", "")
    return f"LINE:{line}|{last}|{first}"


def _make_change(flight_info, change_type, before_snap_id, after_snap_id,
                 passenger=None, field=None, old_value=None, new_value=None):
    """Create a change document."""
    doc = {
        "flightNumber": flight_info["flightNumber"],
        "origin": flight_info["origin"],
        "departureDate": flight_info["departureDate"],
        "changeType": change_type,
        "beforeSnapshotId": before_snap_id,
        "afterSnapshotId": after_snap_id,
        "detectedAt": _now_iso(),
    }
    if passenger:
        doc["passenger"] = {
            "pnr": passenger.get("pnr", ""),
            "lastName": passenger.get("lastName", ""),
            "firstName": passenger.get("firstName", ""),
        }
    if field:
        doc["field"] = field
    if old_value is not None:
        doc["oldValue"] = str(old_value)
    if new_value is not None:
        doc["newValue"] = str(new_value)
    return doc


# ── Flight Status Diff ────────────────────────────────────────────────────

def diff_flight_status(before_snap, after_snap, flight_info):
    """
    Compare two flight_status snapshots.
    Returns a list of change dicts.
    """
    before = before_snap["data"]
    after = after_snap["data"]
    before_id = before_snap["snapshotId"]
    after_id = after_snap["snapshotId"]
    changes = []

    # Flight status change
    if before.get("status") != after.get("status"):
        changes.append(_make_change(
            flight_info, "STATUS_CHANGE", before_id, after_id,
            field="status",
            old_value=before.get("status"),
            new_value=after.get("status"),
        ))

    # Gate change
    if before.get("gate") != after.get("gate"):
        changes.append(_make_change(
            flight_info, "GATE_CHANGE", before_id, after_id,
            field="gate",
            old_value=before.get("gate"),
            new_value=after.get("gate"),
        ))

    # Passenger count changes
    before_counts = before.get("passengerCounts", {})
    after_counts = after.get("passengerCounts", {})
    for cls in set(list(before_counts.keys()) + list(after_counts.keys())):
        bc = before_counts.get(cls, {})
        ac = after_counts.get(cls, {})
        for metric in ["booked", "onBoard", "boardingPasses"]:
            bv = bc.get(metric, 0)
            av = ac.get(metric, 0)
            if bv != av:
                changes.append(_make_change(
                    flight_info, "COUNT_CHANGE", before_id, after_id,
                    field=f"passengerCounts.{cls}.{metric}",
                    old_value=bv,
                    new_value=av,
                ))

    return changes


# ── Passenger List Diff ───────────────────────────────────────────────────

def diff_passenger_list(before_snap, after_snap, flight_info):
    """
    Compare two passenger_list snapshots.
    Returns a list of change dicts.
    """
    before = before_snap["data"]
    after = after_snap["data"]
    before_id = before_snap["snapshotId"]
    after_id = after_snap["snapshotId"]
    changes = []

    before_pax = {_pax_key(p): p for p in before.get("passengers", [])}
    after_pax = {_pax_key(p): p for p in after.get("passengers", [])}

    before_keys = set(before_pax.keys())
    after_keys = set(after_pax.keys())

    # New passengers
    for key in after_keys - before_keys:
        p = after_pax[key]
        changes.append(_make_change(
            flight_info, "PASSENGER_ADDED", before_id, after_id,
            passenger=p,
            field="passenger",
            new_value=f"cabin={p.get('cabin')} class={p.get('bookingClass')}",
        ))

    # Removed passengers
    for key in before_keys - after_keys:
        p = before_pax[key]
        changes.append(_make_change(
            flight_info, "PASSENGER_REMOVED", before_id, after_id,
            passenger=p,
            field="passenger",
            old_value=f"cabin={p.get('cabin')} class={p.get('bookingClass')}",
        ))

    # State changes on existing passengers
    for key in before_keys & after_keys:
        b = before_pax[key]
        a = after_pax[key]

        # Check-in
        if not b.get("isCheckedIn") and a.get("isCheckedIn"):
            changes.append(_make_change(
                flight_info, "CHECKED_IN", before_id, after_id,
                passenger=a,
                field="isCheckedIn",
                old_value=False, new_value=True,
            ))

        # Boarding
        if not b.get("isBoarded") and a.get("isBoarded"):
            changes.append(_make_change(
                flight_info, "BOARDED", before_id, after_id,
                passenger=a,
                field="isBoarded",
                old_value=False, new_value=True,
            ))

        # Cabin change (upgrade/downgrade)
        if b.get("cabin") != a.get("cabin"):
            changes.append(_make_change(
                flight_info, "CABIN_CHANGE", before_id, after_id,
                passenger=a,
                field="cabin",
                old_value=b.get("cabin"),
                new_value=a.get("cabin"),
            ))

        # Booking class change
        if b.get("bookingClass") != a.get("bookingClass"):
            changes.append(_make_change(
                flight_info, "CLASS_CHANGE", before_id, after_id,
                passenger=a,
                field="bookingClass",
                old_value=b.get("bookingClass"),
                new_value=a.get("bookingClass"),
            ))

        # Seat change
        if b.get("seat") != a.get("seat"):
            changes.append(_make_change(
                flight_info, "SEAT_CHANGE", before_id, after_id,
                passenger=a,
                field="seat",
                old_value=b.get("seat"),
                new_value=a.get("seat"),
            ))

        # Bag count change
        if b.get("bagCount") != a.get("bagCount"):
            changes.append(_make_change(
                flight_info, "BAG_COUNT_CHANGE", before_id, after_id,
                passenger=a,
                field="bagCount",
                old_value=b.get("bagCount"),
                new_value=a.get("bagCount"),
            ))

        # Passenger type change
        if b.get("passengerType") != a.get("passengerType"):
            changes.append(_make_change(
                flight_info, "PAX_TYPE_CHANGE", before_id, after_id,
                passenger=a,
                field="passengerType",
                old_value=b.get("passengerType"),
                new_value=a.get("passengerType"),
            ))

    logger.info("Passenger list diff: %d changes detected", len(changes))

    # Summary-level count changes (infant/child/adult/totalSouls)
    for count_field in ["adultCount", "childCount", "infantCount", "totalSouls"]:
        bv = before.get(count_field, 0)
        av = after.get(count_field, 0)
        if bv != av:
            changes.append(_make_change(
                flight_info, "COUNT_CHANGE", before_id, after_id,
                field=count_field,
                old_value=bv,
                new_value=av,
            ))

    return changes


# ── Reservations Diff ─────────────────────────────────────────────────────

def diff_reservations(before_snap, after_snap, flight_info):
    """
    Compare two reservations snapshots.
    Returns a list of change dicts.
    """
    before = before_snap["data"]
    after = after_snap["data"]
    before_id = before_snap["snapshotId"]
    after_id = after_snap["snapshotId"]
    changes = []

    before_res = {r.get("pnr"): r for r in before.get("reservations", [])}
    after_res = {r.get("pnr"): r for r in after.get("reservations", [])}

    before_pnrs = set(before_res.keys())
    after_pnrs = set(after_res.keys())

    # New reservations
    for pnr in after_pnrs - before_pnrs:
        r = after_res[pnr]
        changes.append(_make_change(
            flight_info, "RESERVATION_ADDED", before_id, after_id,
            field="reservation",
            new_value=f"pnr={pnr} party={r.get('numberInParty', 0)}",
        ))

    # Removed reservations
    for pnr in before_pnrs - after_pnrs:
        r = before_res[pnr]
        changes.append(_make_change(
            flight_info, "RESERVATION_REMOVED", before_id, after_id,
            field="reservation",
            old_value=f"pnr={pnr} party={r.get('numberInParty', 0)}",
        ))

    # Changed reservations (party size, timestamps)
    for pnr in before_pnrs & after_pnrs:
        b = before_res[pnr]
        a = after_res[pnr]
        if b.get("numberInParty") != a.get("numberInParty"):
            changes.append(_make_change(
                flight_info, "RESERVATION_PARTY_CHANGE", before_id, after_id,
                field=f"reservation.{pnr}.numberInParty",
                old_value=b.get("numberInParty"),
                new_value=a.get("numberInParty"),
            ))

    logger.info("Reservations diff: %d changes detected", len(changes))
    return changes


# ── Dispatcher ────────────────────────────────────────────────────────────

DIFF_FUNCTIONS = {
    "flight_status": diff_flight_status,
    "passenger_list": diff_passenger_list,
    "reservations": diff_reservations,
}


def detect_changes(before_snapshot, after_snapshot, snapshot_type, flight_info):
    """
    Detect changes between two snapshots of the given type.
    Returns a list of change dicts ready for storage.
    """
    diff_fn = DIFF_FUNCTIONS.get(snapshot_type)
    if not diff_fn:
        logger.warning("No diff function for snapshot type: %s", snapshot_type)
        return []
    return diff_fn(before_snapshot, after_snapshot, flight_info)
