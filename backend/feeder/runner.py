"""
FalconEye Data Feeder — Orchestrates Sabre API calls with full data preservation.

Architecture:
  Layer 1: Raw Archive    — stores complete XML request/response (sabre_requests)
  Layer 2: Snapshots      — stores normalized JSON with checksums (snapshots)
  Layer 3: Change Detect  — computes diffs between consecutive snapshots (changes)
  Layer 4: Current State  — updates materialized view (flights)

Usage (CLI):
    python -m backend.feeder.runner input.json

Input JSON format:
    {
        "flights": [
            {
                "airline": "GF",
                "flightNumber": "2006",
                "origin": "LHR",
                "departureDate": "2026-03-19",
                "departureDateTime": "2026-03-19T08:00:00"
            }
        ]
    }
"""

from backend.feeder import storage
from backend.feeder.converter import convert_flight_status, convert_passenger_list, convert_reservations
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient, SabreError
import json
import sys
import os
import logging

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _process_api_call(api_type, snapshot_type, flight_info,
                      raw_data, raw_xml, meta, converter_fn, converter_args):
    """
    Execute the 3-layer storage pipeline for one API call:
    1. Store raw request/response
    2. Convert + store snapshot
    3. Diff against previous snapshot + store changes
    4. Update flight current state
    """
    airline = flight_info["airline"]
    fn = flight_info["flightNumber"]
    origin = flight_info["origin"]
    dep_date = flight_info["departureDate"]

    # Layer 1: Raw archive
    request_id = storage.store_raw_request(
        api_type=api_type,
        flight_info=flight_info,
        request_xml=meta["requestXml"],
        response_xml=raw_xml,
        response_json=raw_data,
        http_status=meta["httpStatus"],
        duration_ms=meta["durationMs"],
        session_token=meta.get("sessionToken"),
        conversation_id=meta.get("conversationId"),
    )

    # Layer 2: Convert and store snapshot
    converted = converter_fn(*converter_args)
    snapshot_id, checksum, is_dup = storage.store_snapshot(
        request_id=request_id,
        airline=airline,
        flight_number=fn,
        origin=origin,
        departure_date=dep_date,
        snapshot_type=snapshot_type,
        data=converted,
    )

    # Also store in legacy collections for backward compatibility
    if snapshot_type == "flight_status":
        storage.store_flight_status(converted)
    elif snapshot_type == "passenger_list":
        storage.store_passenger_list(converted)
    elif snapshot_type == "reservations":
        storage.store_reservations(converted)

    # Layer 3: Change detection
    num_changes = 0
    if not is_dup:
        prev_snap = storage.get_previous_snapshot(
            fn, origin, dep_date, snapshot_type)
        if prev_snap:
            after_snap = {"snapshotId": snapshot_id, "data": converted}
            changes = detect_changes(
                prev_snap, after_snap, snapshot_type, flight_info)
            num_changes = storage.store_changes(changes)

    # Layer 4: Update current state
    summary = None
    if snapshot_type == "passenger_list":
        passengers = converted.get("passengers", [])
        summary = {
            "totalPax": len(passengers),
            "adultCount": converted.get("adultCount", 0),
            "childCount": converted.get("childCount", 0),
            "infantCount": converted.get("infantCount", 0),
            "totalSouls": converted.get("totalSouls", len(passengers)),
            "checkedIn": sum(1 for p in passengers if p.get("isCheckedIn")),
            "boarded": sum(1 for p in passengers if p.get("isBoarded")),
            "revenue": sum(1 for p in passengers if p.get("isRevenue")),
        }
    elif snapshot_type == "flight_status":
        summary = {
            "status": converted.get("status", ""),
            "gate": converted.get("gate", ""),
        }

    storage.update_flight_state(airline, fn, origin, dep_date,
                                snapshot_id, snapshot_type, summary)

    logger.info("Pipeline complete: %s %s%s — snapshot=%s changes=%d%s",
                api_type, airline, fn, snapshot_id[:8], num_changes,
                " [no-change]" if is_dup else "")

    return {
        "apiType": api_type,
        "snapshotType": snapshot_type,
        "requestId": request_id,
        "snapshotId": snapshot_id,
        "checksum": checksum,
        "isDuplicate": is_dup,
        "changesStored": num_changes,
        "httpStatus": meta["httpStatus"],
        "durationMs": meta["durationMs"],
    }


def run_feeder(flights):
    """
    Fetch data from Sabre for a list of flights and store with full preservation.
    """
    storage.ensure_indexes()
    results = []

    with SabreClient() as client:
        for i, flight in enumerate(flights, 1):
            airline = flight["airline"]
            fn = flight["flightNumber"]
            origin = flight["origin"]
            dep_date = flight["departureDate"]
            dep_dt = flight["departureDateTime"]

            flight_info = {
                "airline": airline,
                "flightNumber": fn,
                "origin": origin,
                "departureDate": dep_date,
            }
            flight_result = {
                "flight": {
                    "airline": airline,
                    "flightNumber": fn,
                    "origin": origin,
                    "departureDate": dep_date,
                    "departureDateTime": dep_dt,
                },
                "success": True,
                "apis": {},
            }

            logger.info("── Flight %d/%d: %s%s %s %s ──",
                        i, len(flights), airline, fn, origin, dep_date)

            # 1. Flight Status
            try:
                raw, xml, meta = client.get_flight_status(airline, fn, origin)
                details = _process_api_call(
                    "FlightStatus", "flight_status", flight_info,
                    raw, xml, meta,
                    convert_flight_status, (raw, airline, fn, origin),
                )
                flight_result["apis"]["flightStatus"] = {
                    "status": "success",
                    **details,
                }
            except SabreError as e:
                logger.error("Flight status failed for %s%s: %s",
                             airline, fn, e)
                flight_result["success"] = False
                flight_result["apis"]["flightStatus"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 2. Passenger List
            try:
                raw, xml, meta = client.get_passenger_list(
                    airline, fn, dep_date, origin)
                details = _process_api_call(
                    "PassengerList", "passenger_list", flight_info,
                    raw, xml, meta,
                    convert_passenger_list, (raw, airline,
                                             fn, dep_date, origin),
                )
                flight_result["apis"]["passengerList"] = {
                    "status": "success",
                    **details,
                }
            except SabreError as e:
                logger.error("Passenger list failed for %s%s: %s",
                             airline, fn, e)
                flight_result["success"] = False
                flight_result["apis"]["passengerList"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 3. Reservations
            try:
                raw, xml, meta = client.get_reservations(
                    airline, fn, origin, dep_dt)
                details = _process_api_call(
                    "Reservations", "reservations", flight_info,
                    raw, xml, meta,
                    convert_reservations, (raw, airline, fn, origin, dep_date),
                )
                flight_result["apis"]["reservations"] = {
                    "status": "success",
                    **details,
                }
            except SabreError as e:
                logger.error("Reservations failed for %s%s: %s",
                             airline, fn, e)
                flight_result["success"] = False
                flight_result["apis"]["reservations"] = {
                    "status": "error",
                    "error": str(e),
                }

            results.append(flight_result)

    storage.close()
    logger.info("Feeder run complete. Processed %d flight(s).", len(flights))
    return {
        "processedFlights": len(flights),
        "results": results,
    }


def main():
    """CLI entry point — reads input JSON from a file path argument."""
    if len(sys.argv) < 2:
        print("Usage: python -m backend.feeder.runner <input.json>")
        print()
        print("Input JSON example:")
        print(json.dumps({
            "flights": [{
                "airline": "GF",
                "flightNumber": "2006",
                "origin": "LHR",
                "departureDate": "2026-03-19",
                "departureDateTime": "2026-03-19T08:00:00",
            }]
        }, indent=2))
        sys.exit(1)

    input_path = sys.argv[1]
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    flights = data.get("flights", [])
    if not flights:
        logger.error("No flights found in input JSON.")
        sys.exit(1)

    run_feeder(flights)


if __name__ == "__main__":
    main()
