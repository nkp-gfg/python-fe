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

import re as _re
from backend.feeder import storage
from backend.feeder.converter import _strip_ns, convert_flight_status, convert_passenger_list, convert_reservations, convert_trip_report, merge_trip_reports, convert_schedule
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient, SabreError
from backend.sabre import templates
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


class IngestionPayloadRejectedError(Exception):
    """Raised when a supplied ingestion payload conflicts with live Sabre data."""


def _flatten_sabre_messages(value):
    """Return a flat list of message strings from a Sabre Result.Message payload."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        messages = []
        for inner in value.values():
            messages.extend(_flatten_sabre_messages(inner))
        return messages
    if isinstance(value, list):
        messages = []
        for item in value:
            messages.extend(_flatten_sabre_messages(item))
        return messages
    text = str(value).strip()
    return [text] if text else []


def _get_sabre_business_error(raw_data):
    """Extract a Sabre business error from a parsed SOAP body, if present."""
    data = _strip_ns(raw_data or {})
    result = data.get("Result")
    if not isinstance(result, dict):
        return None

    status = str(result.get("Status", "")).strip()
    if not status or status.lower() in {"complete", "completed", "success"}:
        return None

    system_results = result.get("SystemSpecificResults") or {}
    error_message = system_results.get(
        "ErrorMessage") if isinstance(system_results, dict) else None
    messages = _flatten_sabre_messages(result.get("Message"))
    messages.extend(_flatten_sabre_messages(error_message))
    code = str(result.get("ErrorCode", "")).strip()
    if not code and isinstance(error_message, dict):
        code = str(error_message.get("@code", "")).strip()

    return {
        "status": status,
        "code": code,
        "messages": messages,
    }


def _validate_live_flight_payload(flight_info, flight_sequence_number, raw_data):
    """Reject payloads where live Sabre says the submitted flight is invalid."""
    if flight_sequence_number is None:
        return

    business_error = _get_sabre_business_error(raw_data)
    if not business_error:
        return

    combined_message = " | ".join(business_error["messages"]).upper()
    invalid_markers = (
        "FLIGHT NOT INITIALIZED",
        "INVALID DATE OR CITY",
    )
    if business_error["code"] != "2566" and not any(marker in combined_message for marker in invalid_markers):
        return

    airline = flight_info["airline"]
    fn = flight_info["flightNumber"]
    origin = flight_info["origin"]
    dep_date = flight_info["departureDate"]
    message = " | ".join(
        business_error["messages"]) or "unknown Sabre business error"
    raise IngestionPayloadRejectedError(
        "Rejected ingestion payload: "
        f"live Sabre rejected {airline}{fn} {origin} {dep_date} while the request supplied "
        f"flightSequenceNumber={flight_sequence_number}. "
        f"Sabre status={business_error['status']}, code={business_error['code'] or 'n/a'}, "
        f"message={message}. "
        f"HINT: If the flight departs around midnight, the departureDate may not match "
        f"the operational flightDate — verify the date sent matches the actual local departure date."
    )


def _mark_skipped_api_results(flight_result, error_message):
    """Fill remaining API slots when a flight is rejected before persistence."""
    flight_result["apis"]["flightStatus"] = {
        "status": "error",
        "error": error_message,
    }
    for api_name in ("passengerList", "reservations", "tripReports", "schedule"):
        flight_result["apis"][api_name] = {
            "status": "error",
            "error": "Skipped because the ingestion payload was rejected during live validation.",
        }


# ── Passenger list merge helpers ──────────────────────────────────────────


def _strip_ns_key(key):
    """Remove XML namespace prefix from a key."""
    return _re.sub(r'^[a-zA-Z0-9]+:', '', key)


def _extract_passenger_info_list(raw):
    """Extract the PassengerInfo list from raw GetPassengerListRS dict."""
    for k, v in (raw or {}).items():
        if _strip_ns_key(k) == "PassengerInfoList":
            inner = v or {}
            for k2, v2 in inner.items():
                if _strip_ns_key(k2) == "PassengerInfo":
                    if isinstance(v2, list):
                        return v2
                    elif v2:
                        return [v2]
            return []
    return []


def _set_passenger_info_list(raw, pax_list):
    """Set the PassengerInfo list back into the raw dict."""
    for k, v in (raw or {}).items():
        if _strip_ns_key(k) == "PassengerInfoList":
            for k2 in list((v or {}).keys()):
                if _strip_ns_key(k2) == "PassengerInfo":
                    v[k2] = pax_list
                    return


def _pax_dedup_key(p):
    """Build a dedup key from a raw passenger dict."""
    if isinstance(p, dict):
        pnr_raw = p.get("PNRLocator") or p.get("v4:PNRLocator", "")
        if isinstance(pnr_raw, dict):
            pnr = pnr_raw.get("#text", "")
        else:
            pnr = str(pnr_raw)
        name = p.get("Name_Details") or p.get("v4:Name_Details", {})
        if not isinstance(name, dict):
            name = {}
        last = name.get("LastName", name.get("v4:LastName", ""))
        pid = p.get("PassengerID", p.get("v4:PassengerID", ""))
        return f"{pnr}|{last}|{pid}"
    return str(id(p))


# ── Pipeline helpers ──────────────────────────────────────────────────────


def _lookup_flight_sequence_number(flight_number, origin, departure_date):
    """Fallback: look up flight_sequence_number from PostgreSQL.

    Returns the int sequence number or None if not found / PG unavailable.
    """
    try:
        from backend.api.postgres import query_all
        rows = query_all(
            "SELECT flight_sequence_number FROM otp.flight_xml_current "
            "WHERE flight_number = %s AND scheduled_origin = %s "
            "AND flight_date = %s LIMIT 1",
            (flight_number, origin, departure_date),
        )
        if rows:
            return rows[0]["flight_sequence_number"]
    except Exception as exc:
        logger.warning("pg_sequence_lookup_failed",
                       flight_number=flight_number, origin=origin,
                       departure_date=departure_date, error=str(exc))
    return None


def _process_api_call(api_type, snapshot_type, flight_info,
                      raw_data, raw_xml, meta, converter_fn, converter_args,
                      flight_sequence_number=None):
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
                                snapshot_id, snapshot_type, summary,
                                flight_sequence_number=flight_sequence_number)

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


def run_feeder(flights, progress_callback=None):
    """
    Fetch data from Sabre for a list of flights and store with full preservation.

    Args:
        flights: List of flight dicts with airline, flightNumber, origin, etc.
        progress_callback: Optional callable(index, flight_key) called after each flight.
    """
    results = []

    with SabreClient() as client:
        for i, flight in enumerate(flights, 1):
            airline = flight["airline"]
            fn = flight["flightNumber"]
            origin = flight["origin"]
            dep_date = flight["departureDate"]
            dep_dt = flight["departureDateTime"]
            flight_seq = flight.get("flightSequenceNumber")

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
                    "flightSequenceNumber": flight_seq,
                },
                "success": True,
                "apis": {},
            }

            service_type = flight.get("serviceTypeCode")

            logger.info("processing_flight",
                        index=i, total=len(flights),
                        flight=f"{airline}{fn}", origin=origin,
                        departure_date=dep_date,
                        service_type=service_type)

            # Log non-scheduled flights but still attempt ingestion
            if service_type and service_type != "J":
                svc_label = {"P": "positioning/ferry", "C": "charter"}.get(
                    service_type, f"non-scheduled ({service_type})")
                logger.info("non_scheduled_flight",
                            flight=f"{airline}{fn}", service_type=service_type,
                            label=svc_label)

            # Resolve flight_sequence_number: use provided value or fallback to PG lookup
            if flight_seq is None:
                flight_seq = _lookup_flight_sequence_number(
                    fn, origin, dep_date)
                if flight_seq is not None:
                    logger.info("resolved_flight_seq_from_pg",
                                flight=f"{airline}{fn}", seq=flight_seq)
                    flight_result["flight"]["flightSequenceNumber"] = flight_seq

            # 1. Flight Status
            try:
                raw, xml, meta = client.get_flight_status(
                    airline, fn, origin, dep_date)
                _validate_live_flight_payload(flight_info, flight_seq, raw)
                details = _process_api_call(
                    "FlightStatus", "flight_status", flight_info,
                    raw, xml, meta,
                    convert_flight_status, (raw, airline,
                                            fn, origin, dep_date),
                    flight_sequence_number=flight_seq,
                )
                flight_result["apis"]["flightStatus"] = {
                    "status": "success",
                    **details,
                }
            except IngestionPayloadRejectedError as e:
                logger.error("flight_payload_rejected",
                             flight=f"{airline}{fn}", origin=origin,
                             departure_date=dep_date, seq=flight_seq,
                             error=str(e))
                flight_result["success"] = False
                _mark_skipped_api_results(flight_result, str(e))
                results.append(flight_result)

                if progress_callback is not None:
                    try:
                        progress_callback(i, f"{airline}{fn}")
                    except Exception:
                        pass
                continue
            except SabreError as e:
                logger.error("flight_status_failed",
                             flight=f"{airline}{fn}", error=str(e))
                flight_result["success"] = False
                flight_result["apis"]["flightStatus"] = {
                    "status": "error",
                    "error": str(e),
                }

            # 2. Passenger List — 4 sequential calls with different display codes
            #    SEQ 3: RV,XRV (Booked)  SEQ 4: BP,BT (Checked-in/Boarded)
            #    SEQ 5: NS,OFL (No-show/Offloaded)  SEQ 6: AE (catch-all)
            pax_calls = [
                ("Booked",      templates.DISPLAY_CODES_BOOKED),
                ("CheckedIn",   templates.DISPLAY_CODES_CHECKEDIN),
                ("NoShowOFL",   templates.DISPLAY_CODES_NOSHOW_OFL),
                ("AllEdit",     templates.DISPLAY_CODES_ALL),
            ]
            merged_raw = None
            merged_xml_parts = []
            merged_meta = None
            pax_call_statuses = []

            for label, codes in pax_calls:
                try:
                    raw, xml, meta = client.get_passenger_list(
                        airline, fn, dep_date, origin,
                        display_codes=codes)
                    pax_call_statuses.append(
                        {"call": label, "status": "success"})

                    if merged_raw is None:
                        # First successful call: use as base
                        merged_raw = raw
                        merged_xml_parts.append(xml)
                        merged_meta = meta
                    else:
                        # Merge: append passengers from subsequent calls
                        merged_xml_parts.append(xml)
                        subsequent_pax = _extract_passenger_info_list(raw)
                        if subsequent_pax:
                            base_pax_list = _extract_passenger_info_list(
                                merged_raw)
                            # De-duplicate by PNR + LastName + PassengerID
                            existing_keys = set()
                            for p in base_pax_list:
                                existing_keys.add(_pax_dedup_key(p))
                            for p in subsequent_pax:
                                if _pax_dedup_key(p) not in existing_keys:
                                    base_pax_list.append(p)
                                    existing_keys.add(_pax_dedup_key(p))
                            # Update the merged raw with combined list
                            _set_passenger_info_list(merged_raw, base_pax_list)

                except SabreError as e:
                    logger.warning("passenger_list_partial_fail",
                                   flight=f"{airline}{fn}",
                                   call=label, error=str(e))
                    pax_call_statuses.append({"call": label, "status": "error",
                                              "error": str(e)})

            if merged_raw is not None:
                try:
                    details = _process_api_call(
                        "PassengerList", "passenger_list", flight_info,
                        merged_raw, merged_xml_parts[0], merged_meta,
                        convert_passenger_list, (merged_raw, airline,
                                                 fn, dep_date, origin),
                        flight_sequence_number=flight_seq,
                    )
                    flight_result["apis"]["passengerList"] = {
                        "status": "success",
                        "calls": pax_call_statuses,
                        **details,
                    }
                except Exception as e:
                    logger.error("passenger_list_convert_failed",
                                 flight=f"{airline}{fn}", error=str(e))
                    flight_result["success"] = False
                    flight_result["apis"]["passengerList"] = {
                        "status": "error",
                        "error": str(e),
                        "calls": pax_call_statuses,
                    }
            else:
                logger.error("passenger_list_all_calls_failed",
                             flight=f"{airline}{fn}")
                flight_result["success"] = False
                flight_result["apis"]["passengerList"] = {
                    "status": "error",
                    "error": "All 4 passenger list calls failed",
                    "calls": pax_call_statuses,
                }

            # 3. Reservations
            try:
                raw, xml, meta = client.get_reservations(
                    airline, fn, origin, dep_dt)
                details = _process_api_call(
                    "Reservations", "reservations", flight_info,
                    raw, xml, meta,
                    convert_reservations, (raw, airline, fn, origin, dep_date),
                    flight_sequence_number=flight_seq,
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

            results.append(flight_result)

            # Report progress to caller (Celery task / SSE)
            if progress_callback is not None:
                try:
                    progress_callback(i, f"{airline}{fn}")
                except Exception:
                    pass  # Never let progress reporting break the pipeline

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
