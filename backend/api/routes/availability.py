"""MultiFlight availability API endpoints.

Provides:
  GET  /flights/{flight_number}/availability            — latest stored availability
  POST /flights/{flight_number}/availability/lookup     — live Sabre lookup from stored schedule
  POST /availability/multi-flight                       — live custom MultiFlight request
"""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownReturnType=false

from __future__ import annotations

import os
from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.api.database import get_db
from backend.api.validators import validate_airline, validate_date, validate_flight_number, validate_origin
from backend.feeder import storage
from backend.feeder.converter import convert_multi_flight_availability
from backend.sabre.client import SabreClient, SabreError

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["availability"])


def _strip_id(doc: dict[str, Any] | None):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _to_mf_date_time(dt_iso: str, default_date: str):
    if dt_iso and len(dt_iso) >= 16:
        return dt_iso[:10].replace("-", ""), dt_iso[11:16].replace(":", "")
    date_part = default_date.replace("-", "") if default_date else ""
    return date_part, "0000"


class MultiFlightSegment(BaseModel):
    segmentId: int = Field(..., ge=1, le=65535)
    origin: str = Field(..., min_length=3, max_length=5)
    destination: str = Field(..., min_length=3, max_length=5)
    carrierCode: str = Field(..., min_length=1, max_length=3)
    departureTime: str = Field(..., pattern=r"^\d{4}$")
    arrivalTime: str = Field(..., pattern=r"^\d{4}$")
    flightNumber: int = Field(..., ge=1)
    departureDate: str = Field(..., pattern=r"^\d{8}$")
    arrivalDate: str = Field(..., pattern=r"^\d{8}$")
    classCodes: str | None = Field(default=None, pattern=r"^[A-Z]{0,26}$")
    resolveIndicator: str | None = Field(default=None, pattern=r"^[YN]$")
    marketingCarrier: str | None = None
    marketingFlightNumber: int | None = None


class MultiFlightItinerary(BaseModel):
    segments: list[MultiFlightSegment] = Field(..., min_length=1)


class MultiFlightOriginDestination(BaseModel):
    origin: str | None = None
    destination: str | None = None
    itineraries: list[MultiFlightItinerary] = Field(..., min_length=1)


class MultiFlightAgentInfo(BaseModel):
    agentCityCode: str = Field(..., min_length=3, max_length=5)
    agencyPcc: str = Field(..., min_length=3, max_length=4)
    crsPartitionCode: str = Field(..., min_length=1, max_length=3)
    agentCountry: str = Field(..., min_length=2, max_length=2)
    mainAgencyPcc: str | None = None
    agencyIata: str | None = None
    homeAgencyIata: str | None = None
    agentDepartmentCode: str | None = None
    agentDutyCode: str | None = None
    currencyCode: str | None = None
    accountingCity: str | None = None
    accountingCode: str | None = None
    accountingOfficeCode: str | None = None


class MultiFlightPointOfCommencement(BaseModel):
    cityCode: str
    departureDate: str | None = Field(default=None, pattern=r"^\d{8}$")
    departureTime: str | None = Field(default=None, pattern=r"^\d{4}$")


class MultiFlightAssociateItem(BaseModel):
    carrierCode: str


class MultiFlightRequest(BaseModel):
    airline: str = Field(default="GF", min_length=2, max_length=2)
    flightNumber: str = Field(..., min_length=1,
                              max_length=5, pattern=r"^\d{1,5}$")
    origin: str = Field(default="", max_length=5)
    departureDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    version: int = Field(default=1, ge=0, le=1)
    originDestinations: list[MultiFlightOriginDestination] = Field(
        ..., min_length=1)
    agentInfo: MultiFlightAgentInfo
    pointOfCommencement: MultiFlightPointOfCommencement | None = None
    associateItem: MultiFlightAssociateItem | None = None


class MultiFlightLookupRequest(BaseModel):
    airline: str = Field(default="GF", min_length=2, max_length=2)
    origin: str | None = Field(default=None, min_length=3, max_length=3)
    departureDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    classCodes: str = Field(default_factory=lambda: os.environ.get(
        "SABRE_MULTIFLIGHT_CLASS_CODES", "YJ"), pattern=r"^[A-Z]{1,26}$")
    resolveIndicator: str = Field(default="Y", pattern=r"^[YN]$")


@router.get("/flights/{flight_number}/availability")
def get_flight_availability(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Get the latest stored MultiFlight availability for a flight."""
    validate_flight_number(flight_number)
    validate_origin(origin)
    validate_date(date)

    db = cast(Any, get_db())
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = cast(dict[str, Any] | None,
               db["multi_flight_availability"].find_one(query, sort=[("fetchedAt", -1)]))
    if not doc:
        raise HTTPException(
            status_code=404, detail="Availability not found — try lookup endpoint")
    return _strip_id(doc)


@router.post("/flights/{flight_number}/availability/lookup")
async def lookup_flight_availability(
    flight_number: str,
    payload: MultiFlightLookupRequest,
):
    """Run MultiFlight from the latest stored published schedule and persist the result."""
    validate_flight_number(flight_number)
    validate_airline(payload.airline)
    validate_origin(payload.origin)
    validate_date(payload.departureDate)

    def _do_lookup() -> dict[str, Any]:
        db = cast(Any, get_db())
        sched_query = {
            "airline": payload.airline,
            "flightNumber": flight_number,
            "departureDate": payload.departureDate,
        }
        schedule_doc = cast(dict[str, Any] | None,
                            db["flight_schedules"].find_one(sched_query, sort=[("fetchedAt", -1)]))
        if not schedule_doc or not schedule_doc.get("success"):
            raise HTTPException(
                status_code=404,
                detail="No successful schedule found. Run /flights/schedule/lookup first.",
            )

        segments = cast(list[dict[str, Any]], schedule_doc.get("segments", []))
        if not segments:
            raise HTTPException(
                status_code=400, detail="Schedule has no segments for MultiFlight request")

        req_segments = []
        for idx, seg in enumerate(segments, start=1):
            dep_date, dep_time = _to_mf_date_time(
                seg.get("departureDateTime", ""), payload.departureDate)
            arr_date, arr_time = _to_mf_date_time(
                seg.get("arrivalDateTime", ""), payload.departureDate)
            req_segments.append({
                "segmentId": idx,
                "origin": seg.get("origin", ""),
                "destination": seg.get("destination", ""),
                "carrierCode": payload.airline,
                "departureTime": dep_time,
                "arrivalTime": arr_time,
                "flightNumber": int(seg.get("flightNumber") or flight_number),
                "departureDate": dep_date,
                "arrivalDate": arr_date,
                "classCodes": payload.classCodes,
                "resolveIndicator": payload.resolveIndicator,
            })

        origin_code = str(payload.origin or req_segments[0].get("origin", ""))
        request_payload = {
            "version": int(os.environ.get("SABRE_MULTIFLIGHT_VERSION", "1")),
            "originDestinations": [
                {
                    "origin": req_segments[0].get("origin", ""),
                    "destination": req_segments[-1].get("destination", ""),
                    "itineraries": [{"segments": req_segments}],
                }
            ],
            "agentInfo": {
                "agentCityCode": os.environ.get("SABRE_MULTIFLIGHT_AGENT_CITY", origin_code),
                "agencyPcc": os.environ["SABRE_PSEUDO_CITY_CODE"],
                "crsPartitionCode": os.environ.get("SABRE_MULTIFLIGHT_PARTITION", os.environ.get("SABRE_CPAID", payload.airline)),
                "agentCountry": os.environ.get("SABRE_MULTIFLIGHT_AGENT_COUNTRY", "BH"),
            },
        }

        if os.environ.get("SABRE_MULTIFLIGHT_INCLUDE_OPTIONAL_ITEMS", "false").lower() == "true":
            request_payload["pointOfCommencement"] = {
                "cityCode": origin_code,
                "departureDate": req_segments[0].get("departureDate", ""),
                "departureTime": req_segments[0].get("departureTime", ""),
            }
            request_payload["associateItem"] = {"carrierCode": payload.airline}

        with SabreClient() as client:
            raw, xml, meta = client.get_multi_flight_availability(
                request_payload)
        meta = cast(dict[str, Any], meta)

        availability_doc = cast(dict[str, Any], convert_multi_flight_availability(
            raw,
            payload.airline,
            flight_number,
            payload.departureDate,
            origin_code,
        ))
        availability_doc["requestProfile"] = meta.get("multiFlightAttempt")
        storage.store_raw_request(
            api_type="MultiFlightAvailability",
            flight_info={
                "airline": payload.airline,
                "flightNumber": flight_number,
                "origin": origin_code,
                "departureDate": payload.departureDate,
            },
            request_xml=meta["requestXml"],
            response_xml=xml,
            response_json=raw,
            http_status=meta["httpStatus"],
            duration_ms=meta["durationMs"],
            session_token=meta.get("sessionToken"),
            conversation_id=meta.get("conversationId"),
        )
        storage.store_multi_flight_availability(availability_doc)
        availability_doc.pop("_raw", None)
        availability_doc["durationMs"] = meta["durationMs"]
        availability_doc["requestProfile"] = meta.get("multiFlightAttempt")
        return availability_doc

    try:
        return await run_in_threadpool(_do_lookup)
    except HTTPException:
        raise
    except SabreError as exc:
        logger.exception("MultiFlight lookup failed for %s%s",
                         payload.airline, flight_number)
        raise HTTPException(status_code=502, detail=f"Sabre API error: {exc}")


@router.post("/availability/multi-flight")
async def custom_multi_flight_lookup(payload: MultiFlightRequest):
    """Run a fully custom MultiFlight request and persist the normalized result."""
    validate_airline(payload.airline)
    validate_flight_number(payload.flightNumber)
    validate_date(payload.departureDate, param_name="departureDate")
    validate_origin(payload.origin if payload.origin else None)

    def _do_custom_lookup() -> dict[str, Any]:
        request_payload = {
            "version": payload.version,
            "originDestinations": [od.model_dump(mode="python") for od in payload.originDestinations],
            "agentInfo": payload.agentInfo.model_dump(mode="python"),
            "pointOfCommencement": payload.pointOfCommencement.model_dump(mode="python") if payload.pointOfCommencement else None,
            "associateItem": payload.associateItem.model_dump(mode="python") if payload.associateItem else None,
        }

        with SabreClient() as client:
            raw, xml, meta = client.get_multi_flight_availability(
                request_payload)
        meta = cast(dict[str, Any], meta)

        availability_doc = cast(dict[str, Any], convert_multi_flight_availability(
            raw,
            payload.airline,
            payload.flightNumber,
            payload.departureDate,
            payload.origin,
        ))
        availability_doc["requestProfile"] = meta.get("multiFlightAttempt")
        storage.store_raw_request(
            api_type="MultiFlightAvailability",
            flight_info={
                "airline": payload.airline,
                "flightNumber": payload.flightNumber,
                "origin": payload.origin,
                "departureDate": payload.departureDate,
            },
            request_xml=meta["requestXml"],
            response_xml=xml,
            response_json=raw,
            http_status=meta["httpStatus"],
            duration_ms=meta["durationMs"],
            session_token=meta.get("sessionToken"),
            conversation_id=meta.get("conversationId"),
        )
        storage.store_multi_flight_availability(availability_doc)
        availability_doc.pop("_raw", None)
        availability_doc["durationMs"] = meta["durationMs"]
        availability_doc["requestProfile"] = meta.get("multiFlightAttempt")
        return availability_doc

    try:
        return await run_in_threadpool(_do_custom_lookup)
    except SabreError as exc:
        logger.exception("Custom MultiFlight lookup failed for %s%s",
                         payload.airline, payload.flightNumber)
        raise HTTPException(status_code=502, detail=f"Sabre API error: {exc}")
