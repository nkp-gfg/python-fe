"""
Sabre SOAP API client with session management.

Usage:
    with SabreClient() as client:
        status = client.get_flight_status("GF", "2006", "LHR", "2026-03-19")
        passengers = client.get_passenger_list("GF", "2006", "2026-03-19", "LHR")
        reservations = client.get_reservations("GF", "2006", "LHR", "2026-03-19T08:00:00")
"""

import os
import re
import time
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

import requests
import structlog
import xmltodict
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import templates

logger = structlog.get_logger(__name__)


def _log_retry(retry_state):
    """Log retry attempts using structlog."""
    logger.warning("sabre_api_retry",
                   attempt=retry_state.attempt_number,
                   wait=f"{retry_state.next_action.sleep:.1f}s",
                   error=str(retry_state.outcome.exception()))


class SabreError(Exception):
    """Raised when a Sabre API call fails."""
    pass


class SabreIncompleteResponseError(SabreError):
    """Raised when a response reports more records than could be retrieved."""
    pass


# Trip_SearchRS warning: results were truncated at the view / MaxItemsReturned limit.
TRIP_SEARCH_TRUNCATED_WARNING = "200006"


def _local_child(node, name):
    """Return the child of an xmltodict node whose local name is `name`."""
    if not isinstance(node, dict):
        return None
    for key, value in node.items():
        if key.split(":")[-1] == name:
            return value
    return None


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _int_attr(node, name):
    try:
        return int(str(node.get(f"@{name}")).strip())
    except (TypeError, ValueError):
        return None


class SabreClient:
    """Manages a Sabre SOAP session and provides methods for each API."""

    # Minimum delay in seconds between consecutive Sabre API calls
    API_CALL_DELAY = float(os.environ.get("SABRE_API_DELAY_SECONDS", "0.5"))

    def __init__(self):
        self._token = None
        self._conversation_id = None
        base_url = os.environ["SABRE_BASE_URL"]
        self._base_url = base_url
        self._endpoint = f"{os.environ['SABRE_BASE_URL']}/{os.environ['SABRE_CPAID']}"
        self._cpaid = os.environ["SABRE_CPAID"]
        self._username = os.environ["SABRE_USERNAME"]
        self._password = os.environ["SABRE_PASSWORD"]
        self._pseudo_city_code = os.environ["SABRE_PSEUDO_CITY_CODE"]
        self._organization = os.environ["SABRE_ORGANIZATION"]
        self._domain = os.environ["SABRE_DOMAIN"]
        self._last_call_time = 0.0

    def __enter__(self):
        self.create_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_session()
        return False

    @property
    def is_active(self):
        return self._token is not None

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _message_id():
        return f"mid:{uuid.uuid4()}@clientofsabre.com"

    @staticmethod
    def _conversation_id_new():
        return f"uuid-{uuid.uuid4()}"

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def _common_vars(self):
        """Variables shared across all authenticated templates."""
        return {
            "cpaid": self._cpaid,
            "conversation_id": self._conversation_id,
            "message_id": self._message_id(),
            "timestamp": self._timestamp(),
            "token": self._token,
        }

    def _rate_limit(self):
        """Enforce minimum delay between consecutive Sabre API calls."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self.API_CALL_DELAY:
            time.sleep(self.API_CALL_DELAY - elapsed)
        self._last_call_time = time.monotonic()

    @retry(
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _post(self, action, body, timeout=30, endpoint=None):
        """Send a SOAP POST and return (raw_response_text, http_status, duration_ms, request_body)."""
        self._rate_limit()
        target_endpoint = endpoint or self._endpoint
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": action,
        }
        start = time.monotonic()
        resp = requests.post(target_endpoint, data=body,
                             headers=headers, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("sabre_api_call", action=action,
                    http_status=resp.status_code, duration_ms=duration_ms,
                    endpoint=target_endpoint)
        if resp.status_code != 200:
            raise SabreError(
                f"{action} returned HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.text, resp.status_code, duration_ms, body

    @staticmethod
    def _parse_xml(xml_text):
        """Parse SOAP XML to an ordered dict."""
        return xmltodict.parse(xml_text, process_namespaces=False)

    def _extract_body(self, parsed, response_key):
        """Extract the response body from the SOAP envelope."""
        body = parsed.get("soap-env:Envelope", {}).get("soap-env:Body", {})
        if response_key in body:
            return body[response_key]
        # Try without namespace prefix
        for key in body:
            if key.endswith(response_key) or response_key in key:
                return body[key]
        raise SabreError(
            f"Response key '{response_key}' not found in SOAP body. Keys: {list(body.keys())}")

    # ── Session ────────────────────────────────────────────────────────────

    def create_session(self):
        """Authenticate and obtain a BinarySecurityToken."""
        self._conversation_id = self._conversation_id_new()
        body = templates.SESSION_CREATE.format(
            cpaid=self._cpaid,
            conversation_id=self._conversation_id,
            message_id=self._message_id(),
            timestamp=self._timestamp(),
            username=self._username,
            password=self._password,
            organization=self._organization,
            domain=self._domain,
            pseudo_city_code=self._pseudo_city_code,
        )
        xml_text, _, _, _ = self._post("SessionCreateRQ", body)
        match = re.search(
            r"<wsse:BinarySecurityToken[^>]*>([^<]+)</wsse:BinarySecurityToken>",
            xml_text, re.IGNORECASE,
        )
        if not match:
            raise SabreError(
                f"Session creation failed. Response: {xml_text[:500]}")
        self._token = match.group(1).strip()
        logger.info("sabre_session_created",
                    token_prefix=self._token[:20],
                    conversation_id=self._conversation_id)
        return self._token

    def close_session(self):
        """Close the Sabre session. Safe to call even if no session is active."""
        if not self._token:
            return
        try:
            body = templates.SESSION_CLOSE.format(**self._common_vars())
            self._post("SessionCloseRQ", body)
            logger.info("sabre_session_closed")
        except Exception as e:
            logger.warning("sabre_session_close_error", error=str(e))
        finally:
            self._token = None

    # ── Flight Status ──────────────────────────────────────────────────────

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        """
        Call ACS_FlightDetailRQ.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        request_meta = {requestXml, httpStatus, durationMs, sessionToken, conversationId}
        """
        if departure_date is None:
            departure_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        body = templates.FLIGHT_STATUS.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "ACS_FlightDetailRQ", body)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "ns3:ACS_FlightDetailRS")
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta

    # ── Passenger List ─────────────────────────────────────────────────────

    def get_passenger_list(self, airline, flight_number, departure_date, origin,
                           display_codes=None):
        """
        Call GetPassengerListRQ.
        display_codes: pre-built XML string of DisplayCode elements
                       (use templates.DISPLAY_CODES_* constants).
                       Defaults to DISPLAY_CODES_BOOKED if not supplied.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        if display_codes is None:
            display_codes = templates.DISPLAY_CODES_BOOKED
        body = templates.PASSENGER_LIST.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            display_codes=display_codes,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "GetPassengerListRQ", body)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "GetPassengerListRS")
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta

    # ── Reservations ───────────────────────────────────────────────────────

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        """
        Call Trip_SearchRQ, following TokenCriteria pages until every record
        counted by ReservationsList@TotalResults has been retrieved. Later pages
        are merged into the returned body.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        Raises SabreIncompleteResponseError when the full result set cannot be
        retrieved, so callers never treat a partial list as complete.
        """
        body = templates.RESERVATION.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_airport=departure_airport,
            departure_datetime=departure_datetime,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "Trip_SearchRQ", body, timeout=60)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "Trip_SearchRS")
        flight = f"{airline}{flight_number}"
        self._raise_on_trip_search_errors(data, flight)
        pages_fetched, pages_ms = self._merge_remaining_reservation_pages(data, flight)
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms + pages_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
            "pagesFetched": 1 + pages_fetched,
        }
        return data, xml_text, meta

    def _get_reservation_page(self, page_token, page):
        """Fetch one follow-up page of a Trip_SearchRQ result by its Token."""
        body = templates.RESERVATION_PAGE.format(
            **self._common_vars(),
            page_token=xml_escape(page_token),
            page=int(page),
        )
        xml_text, _, duration_ms, _ = self._post("Trip_SearchRQ", body, timeout=60)
        data = self._extract_body(self._parse_xml(xml_text), "Trip_SearchRS")
        return data, duration_ms

    @staticmethod
    def _raise_on_trip_search_errors(data, flight):
        errors = _as_list(_local_child(_local_child(data, "Errors"), "Error"))
        if errors:
            raise SabreError(
                f"Trip_SearchRS for {flight} returned errors: {str(errors)[:500]}")

    @staticmethod
    def _trip_search_truncated(data):
        """True when Sabre warns that results were cut at the response limit."""
        warnings = _as_list(_local_child(_local_child(data, "Warnings"), "Warning"))
        for warning in warnings:
            if isinstance(warning, dict):
                code = str(warning.get("@ShortText") or "").strip()
                text = str(warning.get("#text") or "")
            else:
                code, text = "", str(warning or "")
            if code == TRIP_SEARCH_TRUNCATED_WARNING or "Number of results exceeded" in text:
                return True
        return False

    @staticmethod
    def _reservation_records(data):
        res_list = _local_child(data, "ReservationsList")
        container = _local_child(res_list, "Reservations")
        return res_list, container, _as_list(_local_child(container, "Reservation"))

    def _merge_remaining_reservation_pages(self, data, flight):
        """
        Merge every remaining Trip_SearchRS page into `data` in place.
        Returns (pages_fetched, total_duration_ms).
        """
        res_list, container, records = self._reservation_records(data)
        if not isinstance(res_list, dict):
            res_list = {}
        total = _int_attr(res_list, "TotalResults")
        page_count = _int_attr(res_list, "NumberPages") or 1
        page_token = str(res_list.get("@Token") or "").strip()
        if page_token == "0":
            page_token = ""
        truncated = self._trip_search_truncated(data)

        merged = list(records)
        seen = {r.get("@Locator") if isinstance(r, dict) else None for r in records}
        pages_fetched = 0
        pages_ms = 0
        page = 2

        def needs_more():
            if total is not None:
                return len(seen) < total
            return truncated

        while needs_more() and page_token and page <= page_count:
            page_data, page_ms = self._get_reservation_page(page_token, page)
            pages_fetched += 1
            pages_ms += page_ms
            self._raise_on_trip_search_errors(page_data, flight)
            _, _, page_records = self._reservation_records(page_data)
            added = 0
            for record in page_records:
                locator = record.get("@Locator") if isinstance(record, dict) else None
                if locator in seen:
                    continue
                seen.add(locator)
                merged.append(record)
                added += 1
            page += 1
            if added == 0:
                break

        incomplete_reason = None
        if total is not None and len(seen) < total:
            incomplete_reason = f"retrieved {len(seen)} of {total} reservations"
        elif truncated and pages_fetched == 0:
            incomplete_reason = "response was truncated at the result limit"
        elif truncated and total is None and page <= page_count:
            incomplete_reason = f"stopped at page {page - 1} of {page_count}"
        if incomplete_reason:
            logger.warning("trip_search_incomplete", flight=flight,
                           total_results=total, retrieved=len(seen),
                           number_pages=page_count, pages_fetched=1 + pages_fetched,
                           has_page_token=bool(page_token), truncated=truncated)
            raise SabreIncompleteResponseError(
                f"Trip_SearchRS for {flight} is incomplete: {incomplete_reason}")

        if pages_fetched:
            if not isinstance(container, dict):
                container = {}
                res_list["Reservations"] = container
            key = next((k for k in container if k.split(":")[-1] == "Reservation"),
                       "Reservation")
            container[key] = merged
            res_list["@NumberResults"] = str(len(merged))
            logger.info("trip_search_pages_merged", flight=flight,
                        pages=1 + pages_fetched, reservations=len(merged),
                        total_results=total)
        return pages_fetched, pages_ms

    # ── Passenger Data (per-passenger detail) ─────────────────────────────

    def get_passenger_data(self, airline, flight_number, departure_date, origin,
                           last_name, first_name=None, pnr=None):
        """
        Call GetPassengerDataRQ for detailed per-passenger data.
        Looks up by LastName + optional FirstName + optional PNR within a flight itinerary.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        first_name_element = ""
        if first_name:
            first_name_element = f'<FirstName>{first_name}</FirstName>'
        pnr_element = ""
        if pnr:
            pnr_element = f'<PNRLocator>{pnr}</PNRLocator>'
        body = templates.PASSENGER_DATA.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            last_name=last_name,
            first_name_element=first_name_element,
            pnr_element=pnr_element,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "GetPassengerDataRQ", body)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "GetPassengerDataRS")
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta

    # ── Trip Reports ───────────────────────────────────────────────────────

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        """
        Call Trip_ReportsRQ with a specific report type.
        report_type: "MLX" (cancelled passengers), "MLC" (ever-booked passengers)
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        body = templates.TRIP_REPORT.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            report_type=report_type,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "Trip_ReportsRQ", body, timeout=60)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "Trip_ReportsRS")
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta

    # ── Verify Flight Details ──────────────────────────────────────────────

    def verify_flight_details(self, airline, flight_number, departure_date,
                              origin, destination=""):
        """
        Call VerifyFlightDetailsLLSRQ to get published schedule data.
        departure_date: YYYY-MM-DD (converted to MM-DDTHH:MM for Sabre).
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        # Sabre expects DepartureDateTime as MM-DDTHH:MM (no year)
        mm_dd = departure_date[5:]  # "2026-03-25" → "03-25"
        departure_datetime = f"{mm_dd}T00:00"
        body = templates.VERIFY_FLIGHT_DETAILS.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_datetime=departure_datetime,
            origin=origin,
            destination=destination,
        )
        xml_text, http_status, duration_ms, request_xml = self._post(
            "VerifyFlightDetailsLLSRQ", body, timeout=30)
        parsed = self._parse_xml(xml_text)
        data = self._extract_body(parsed, "VerifyFlightDetailsRS")
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta
