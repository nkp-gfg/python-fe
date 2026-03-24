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
from backend.feeder.converter import convert_flight_status, convert_passenger_list, convert_reservations, convert_trip_report, merge_trip_reports, convert_schedule, convert_multi_flight_availability
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient, SabreError
import json
import sys
import os

import structlog
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger("INFO"),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)


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

    logger.info("pipeline_complete",
                api_type=api_type, flight=f"{airline}{fn}",
                snapshot_id=snapshot_id[:8], changes=num_changes,
                is_duplicate=is_dup)

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

            logger.info("processing_flight",
                        index=i, total=len(flights),
                        flight=f"{airline}{fn}", origin=origin,
                        departure_date=dep_date)

            # 1. Flight Status
            try:
                raw, xml, meta = client.get_flight_status(
                    airline, fn, origin, dep_date)
                details = _process_api_call(
                    "FlightStatus", "flight_status", flight_info,
                    raw, xml, meta,
                    convert_flight_status, (raw, airline,
                                            fn, origin, dep_date),
                )
                flight_result["apis"]["flightStatus"] = {
                    "status": "success",
                    **details,
                }
            except SabreError as e:
                logger.error("flight_status_failed",
                             flight=f"{airline}{fn}", error=str(e))
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
                logger.error("passenger_list_failed",
                             flight=f"{airline}{fn}", error=str(e))
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
                logger.error("reservations_failed",
                             flight=f"{airline}{fn}", error=str(e))
                flight_result["success"] = False
                flight_result["apis"]["reservations"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 4. Trip Reports (MLX + MLC for offloaded / no-show detection)
            try:
                # MLX = cancelled passengers
                mlx_raw, mlx_xml, mlx_meta = client.get_trip_report(
                    airline, fn, dep_date, origin, "MLX")
                mlx_doc = convert_trip_report(
                    mlx_raw, airline, fn, dep_date, origin, "MLX")

                # Store raw MLX request
                storage.store_raw_request(
                    api_type="TripReport_MLX",
                    flight_info=flight_info,
                    request_xml=mlx_meta["requestXml"],
                    response_xml=mlx_xml,
                    response_json=mlx_raw,
                    http_status=mlx_meta["httpStatus"],
                    duration_ms=mlx_meta["durationMs"],
                    session_token=mlx_meta.get("sessionToken"),
                    conversation_id=mlx_meta.get("conversationId"),
                )

                # MLC = ever-booked passengers
                mlc_raw, mlc_xml, mlc_meta = client.get_trip_report(
                    airline, fn, dep_date, origin, "MLC")
                mlc_doc = convert_trip_report(
                    mlc_raw, airline, fn, dep_date, origin, "MLC")

                # Store raw MLC request
                storage.store_raw_request(
                    api_type="TripReport_MLC",
                    flight_info=flight_info,
                    request_xml=mlc_meta["requestXml"],
                    response_xml=mlc_xml,
                    response_json=mlc_raw,
                    http_status=mlc_meta["httpStatus"],
                    duration_ms=mlc_meta["durationMs"],
                    session_token=mlc_meta.get("sessionToken"),
                    conversation_id=mlc_meta.get("conversationId"),
                )

                # Merge both reports into one document for storage
                merged = merge_trip_reports(
                    mlx_doc, mlc_doc, airline, fn, dep_date, origin)
                storage.store_trip_reports(merged)

                flight_result["apis"]["tripReports"] = {
                    "status": "success",
                    "cancelledCount": merged.get("cancelledCount", 0),
                    "everBookedCount": merged.get("everBookedCount", 0),
                    "mlxDurationMs": mlx_meta["durationMs"],
                    "mlcDurationMs": mlc_meta["durationMs"],
                }
                logger.info("trip_reports_complete",
                            flight=f"{airline}{fn}",
                            cancelled_count=merged.get("cancelledCount", 0),
                            ever_booked_count=merged.get("everBookedCount", 0))
            except SabreError as e:
                logger.warning("trip_reports_failed",
                               flight=f"{airline}{fn}", error=str(e))
                flight_result["apis"]["tripReports"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 5. Flight Schedule (VerifyFlightDetailsLLSRQ)
            schedule_doc = None
            try:
                raw, xml, meta = client.verify_flight_details(
                    airline, fn, dep_date, origin="", destination="")
                schedule_doc = convert_schedule(raw, airline, fn, dep_date)

                # Store raw request
                storage.store_raw_request(
                    api_type="FlightSchedule",
                    flight_info=flight_info,
                    request_xml=meta["requestXml"],
                    response_xml=xml,
                    response_json=raw,
                    http_status=meta["httpStatus"],
                    duration_ms=meta["durationMs"],
                    session_token=meta.get("sessionToken"),
                    conversation_id=meta.get("conversationId"),
                )

                # Store in dedicated collection
                if schedule_doc.get("success"):
                    storage.store_flight_schedule(schedule_doc)

                flight_result["apis"]["schedule"] = {
                    "status": "success" if schedule_doc.get("success") else "error",
                    "durationMs": meta["durationMs"],
                    "origin": schedule_doc.get("origin", ""),
                    "destination": schedule_doc.get("destination", ""),
                    "scheduledDeparture": schedule_doc.get("scheduledDeparture", ""),
                    "scheduledArrival": schedule_doc.get("scheduledArrival", ""),
                    "aircraftType": schedule_doc.get("aircraftType", ""),
                    "error": schedule_doc.get("error"),
                }
                logger.info("schedule_complete",
                            flight=f"{airline}{fn}",
                            origin=schedule_doc.get("origin", ""),
                            destination=schedule_doc.get("destination", ""),
                            departure=schedule_doc.get("scheduledDeparture", ""))
            except SabreError as e:
                logger.warning("schedule_failed",
                               flight=f"{airline}{fn}", error=str(e))
                flight_result["apis"]["schedule"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 6. MultiFlight Availability (class inventory by itinerary)
            try:
                if not schedule_doc or not schedule_doc.get("success"):
                    raise SabreError(
                        "Skipping MultiFlightAvailability: no successful schedule available")

                segments = schedule_doc.get("segments", [])
                if not segments:
                    raise SabreError(
                        "Skipping MultiFlightAvailability: schedule has no segments")

                first = segments[0]
                # MultiFlight requires YYYYMMDD + HHMM values.
                dep_dt = first.get("departureDateTime", "")
                arr_dt = first.get("arrivalDateTime", "")
                dep_date_mf = dep_dt[:10].replace(
                    "-", "") if dep_dt else dep_date.replace("-", "")
                arr_date_mf = arr_dt[:10].replace(
                    "-", "") if arr_dt else dep_date.replace("-", "")
                dep_time_mf = dep_dt[11:16].replace(
                    ":", "") if len(dep_dt) >= 16 else "0000"
                arr_time_mf = arr_dt[11:16].replace(
                    ":", "") if len(arr_dt) >= 16 else dep_time_mf

                request_payload = {
                    "version": int(os.environ.get("SABRE_MULTIFLIGHT_VERSION", "1")),
                    "originDestinations": [
                        {
                            "origin": first.get("origin", ""),
                            "destination": first.get("destination", ""),
                            "itineraries": [
                                {
                                    "segments": [
                                        {
                                            "segmentId": 1,
                                            "origin": first.get("origin", ""),
                                            "destination": first.get("destination", ""),
                                            "carrierCode": airline,
                                            "departureTime": dep_time_mf,
                                            "arrivalTime": arr_time_mf,
                                            "flightNumber": int(fn),
                                            "departureDate": dep_date_mf,
                                            "arrivalDate": arr_date_mf,
                                            "classCodes": os.environ.get("SABRE_MULTIFLIGHT_CLASS_CODES", "YJ"),
                                            "resolveIndicator": "Y",
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                    "agentInfo": {
                        "agentCityCode": os.environ.get("SABRE_MULTIFLIGHT_AGENT_CITY", first.get("origin", "")),
                        "agencyPcc": os.environ["SABRE_PSEUDO_CITY_CODE"],
                        "crsPartitionCode": os.environ.get("SABRE_MULTIFLIGHT_PARTITION", os.environ.get("SABRE_CPAID", airline)),
                        "agentCountry": os.environ.get("SABRE_MULTIFLIGHT_AGENT_COUNTRY", "BH"),
                    },
                }

                if os.environ.get("SABRE_MULTIFLIGHT_INCLUDE_OPTIONAL_ITEMS", "false").lower() == "true":
                    request_payload["pointOfCommencement"] = {
                        "cityCode": first.get("origin", ""),
                        "departureDate": dep_date_mf,
                        "departureTime": dep_time_mf,
                    }
                    request_payload["associateItem"] = {
                        "carrierCode": airline,
                    }

                raw, xml, meta = client.get_multi_flight_availability(
                    request_payload)
                details = _process_api_call(
                    "MultiFlightAvailability", "multi_flight_availability", flight_info,
                    raw, xml, meta,
                    convert_multi_flight_availability, (
                        raw, airline, fn, dep_date, origin),
                )

                availability_doc = convert_multi_flight_availability(
                    raw, airline, fn, dep_date, origin)
                availability_doc["requestProfile"] = meta.get(
                    "multiFlightAttempt")
                storage.store_multi_flight_availability(availability_doc)

                flight_result["apis"]["multiFlightAvailability"] = {
                    "status": "success",
                    **details,
                    "returnCode": availability_doc.get("returnCode"),
                    "segments": availability_doc.get("summary", {}).get("segments", 0),
                    "requestProfile": meta.get("multiFlightAttempt"),
                }
                logger.info("multi_flight_availability_complete",
                            flight=f"{airline}{fn}",
                            return_code=availability_doc.get("returnCode"),
                            segments=availability_doc.get(
                                "summary", {}).get("segments", 0),
                            request_profile=meta.get("multiFlightAttempt"))
            except SabreError as e:
                logger.warning("multi_flight_availability_failed",
                               flight=f"{airline}{fn}", error=str(e))
                flight_result["apis"]["multiFlightAvailability"] = {
                    "status": "error",
                    "error": str(e),
                }

            results.append(flight_result)

    logger.info("feeder_run_complete", processed_flights=len(flights))
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

    storage.ensure_indexes()
    run_feeder(flights)
    storage.close()


if __name__ == "__main__":
    main()
