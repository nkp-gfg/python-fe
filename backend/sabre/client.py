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
from xml.sax.saxutils import escape

import requests
import structlog
import xmltodict
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.api.runtime_config import get_multiflight_settings
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
        self._multiflight_endpoint = os.environ.get(
            "SABRE_MULTIFLIGHT_URL", base_url)
        self._multiflight_action = os.environ.get(
            "SABRE_MULTIFLIGHT_SOAP_ACTION", "ASAAOperation")
        self._multiflight_action_fallbacks = [
            a.strip() for a in os.environ.get(
                "SABRE_MULTIFLIGHT_SOAP_ACTION_FALLBACKS",
                "ASAAOperation,,MultiFlightRQ",
            ).split(",")
        ]
        runtime_multiflight = get_multiflight_settings()
        self._multiflight_timeout_seconds = int(
            runtime_multiflight["timeoutSeconds"])
        self._multiflight_max_attempts = int(
            runtime_multiflight["maxAttempts"])
        self._multiflight_include_cpaid_endpoint = bool(
            runtime_multiflight["includeCpaidEndpoint"])
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

    def get_passenger_list(self, airline, flight_number, departure_date, origin):
        """
        Call GetPassengerListRQ.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        body = templates.PASSENGER_LIST.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
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
        Call Trip_SearchRQ.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
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
        meta = {
            "requestXml": request_xml,
            "httpStatus": http_status,
            "durationMs": duration_ms,
            "sessionToken": self._token,
            "conversationId": self._conversation_id,
        }
        return data, xml_text, meta

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

    # ── MultiFlight Availability ──────────────────────────────────────────

    @staticmethod
    def _mf_attr(name, value):
        if value is None:
            return ""
        value_s = str(value).strip()
        if value_s == "":
            return ""
        return f' {name}="{escape(value_s)}"'

    def _build_multiflight_origin_destinations_xml(self, origin_destinations):
        parts = []
        for od in origin_destinations:
            parts.append(
                f'<mf:OriginDestination{self._mf_attr("origin", od.get("origin"))}{self._mf_attr("destination", od.get("destination"))}>'
            )
            for itin in od.get("itineraries", []):
                parts.append("<mf:Itinerary>")
                for seg in itin.get("segments", []):
                    parts.append(
                        "<mf:Segment"
                        f'{self._mf_attr("origin", seg.get("origin"))}'
                        f'{self._mf_attr("destination", seg.get("destination"))}'
                        f'{self._mf_attr("carrierCode", seg.get("carrierCode"))}'
                        f'{self._mf_attr("marketingCarrier", seg.get("marketingCarrier"))}'
                        f'{self._mf_attr("departureTime", seg.get("departureTime"))}'
                        f'{self._mf_attr("arrivalTime", seg.get("arrivalTime"))}'
                        f'{self._mf_attr("flightNumber", seg.get("flightNumber"))}'
                        f'{self._mf_attr("marketingFlightNumber", seg.get("marketingFlightNumber"))}'
                        f'{self._mf_attr("departureDate", seg.get("departureDate"))}'
                        f'{self._mf_attr("arrivalDate", seg.get("arrivalDate"))}'
                        f'{self._mf_attr("segmentId", seg.get("segmentId"))}'
                        f'{self._mf_attr("classCodes", seg.get("classCodes"))}'
                        f'{self._mf_attr("resolveIndicator", seg.get("resolveIndicator"))}'
                        "/>"
                    )
                parts.append("</mf:Itinerary>")
            parts.append("</mf:OriginDestination>")
        return "".join(parts)

    def _build_multiflight_agent_info_xml(self, agent_info):
        return (
            "<mf:AgentInfo"
            f'{self._mf_attr("agentCityCode", agent_info.get("agentCityCode"))}'
            f'{self._mf_attr("agencyPcc", agent_info.get("agencyPcc"))}'
            f'{self._mf_attr("mainAgencyPcc", agent_info.get("mainAgencyPcc"))}'
            f'{self._mf_attr("agencyIata", agent_info.get("agencyIata"))}'
            f'{self._mf_attr("homeAgencyIata", agent_info.get("homeAgencyIata"))}'
            f'{self._mf_attr("crsPartitionCode", agent_info.get("crsPartitionCode"))}'
            f'{self._mf_attr("agentDepartmentCode", agent_info.get("agentDepartmentCode"))}'
            f'{self._mf_attr("agentDutyCode", agent_info.get("agentDutyCode"))}'
            f'{self._mf_attr("currencyCode", agent_info.get("currencyCode"))}'
            f'{self._mf_attr("agentCountry", agent_info.get("agentCountry"))}'
            f'{self._mf_attr("accountingCity", agent_info.get("accountingCity"))}'
            f'{self._mf_attr("accountingCode", agent_info.get("accountingCode"))}'
            f'{self._mf_attr("accountingOfficeCode", agent_info.get("accountingOfficeCode"))}'
            "/>"
        )

    def _build_multiflight_optional_xml(self, point_of_commencement=None, associate_item=None):
        poc_xml = ""
        if point_of_commencement:
            poc_xml = (
                "<mf:PointOfCommencement"
                f'{self._mf_attr("cityCode", point_of_commencement.get("cityCode"))}'
                f'{self._mf_attr("departureDate", point_of_commencement.get("departureDate"))}'
                f'{self._mf_attr("departureTime", point_of_commencement.get("departureTime"))}'
                "/>"
            )
        associate_xml = ""
        if associate_item and associate_item.get("carrierCode"):
            associate_xml = (
                "<mf:AssociateItem"
                f'{self._mf_attr("carrierCode", associate_item.get("carrierCode"))}'
                "/>"
            )
        return poc_xml, associate_xml

    def get_multi_flight_availability(self, request_payload):
        """
        Call MultiFlightRQ for itinerary-level class availability.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        origin_destinations = request_payload.get("originDestinations", [])
        if not origin_destinations:
            raise SabreError(
                "MultiFlight request requires at least one originDestination")

        agent_info = request_payload.get("agentInfo", {})
        required_agent_fields = ["agentCityCode",
                                 "agencyPcc", "crsPartitionCode", "agentCountry"]
        missing_agent = [
            f for f in required_agent_fields if not agent_info.get(f)]
        if missing_agent:
            raise SabreError(
                f"MultiFlight request missing required agentInfo fields: {', '.join(missing_agent)}")

        version = int(request_payload.get("version", 1))
        if version not in (0, 1):
            raise SabreError("MultiFlight version must be 0 or 1")

        od_xml = self._build_multiflight_origin_destinations_xml(
            origin_destinations)
        agent_xml = self._build_multiflight_agent_info_xml(agent_info)
        include_optional = os.environ.get(
            "SABRE_MULTIFLIGHT_INCLUDE_OPTIONAL_ITEMS", "false").lower() == "true"
        poc_xml, associate_xml = self._build_multiflight_optional_xml(
            request_payload.get(
                "pointOfCommencement") if include_optional else None,
            request_payload.get("associateItem") if include_optional else None,
        )

        ebxml_versions = [
            v.strip() for v in os.environ.get(
                "SABRE_MULTIFLIGHT_EBXML_VERSIONS", "1.0,2.0.0").split(",") if v.strip()
        ]
        must_understand_values = [
            v.strip() for v in os.environ.get(
                "SABRE_MULTIFLIGHT_MUST_UNDERSTAND_VALUES", "1,0").split(",") if v.strip()
        ]

        # Try combinations to handle provider-specific ASAA expectations.
        last_error = None
        attempted = []
        endpoint_candidates = []
        candidate_sources = [self._multiflight_endpoint, self._base_url]
        if self._multiflight_include_cpaid_endpoint:
            candidate_sources.append(self._endpoint)

        for endpoint in candidate_sources:
            if endpoint and endpoint not in endpoint_candidates:
                endpoint_candidates.append(endpoint)

        actions = []
        for action in [self._multiflight_action] + self._multiflight_action_fallbacks:
            if action not in actions:
                actions.append(action)

        attempt_number = 0
        for ebxml_version in ebxml_versions:
            for must_understand in must_understand_values:
                body = templates.MULTI_FLIGHT.format(
                    **self._common_vars(),
                    version=version,
                    ebxml_version=ebxml_version,
                    must_understand=must_understand,
                    origin_destinations_xml=od_xml,
                    agent_info_xml=agent_xml,
                    point_of_commencement_xml=poc_xml,
                    associate_item_xml=associate_xml,
                )
                for action in actions:
                    for endpoint in endpoint_candidates:
                        if attempt_number >= self._multiflight_max_attempts:
                            break
                        attempt_number += 1
                        attempted.append(
                            f"endpoint='{endpoint}' action='{action}' ebxml={ebxml_version} mustUnderstand={must_understand}")
                        try:
                            xml_text, http_status, duration_ms, request_xml = self._post(
                                action,
                                body,
                                timeout=self._multiflight_timeout_seconds,
                                endpoint=endpoint,
                            )
                            parsed = self._parse_xml(xml_text)
                            data = self._extract_body(parsed, "MultiFlightRS")
                            meta = {
                                "requestXml": request_xml,
                                "httpStatus": http_status,
                                "durationMs": duration_ms,
                                "sessionToken": self._token,
                                "conversationId": self._conversation_id,
                                "multiFlightAttempt": {
                                    "attempt": attempt_number,
                                    "action": action,
                                    "ebxmlVersion": ebxml_version,
                                    "mustUnderstand": must_understand,
                                    "endpoint": endpoint,
                                },
                            }
                            logger.info(
                                "multiflight_attempt_succeeded",
                                attempt=attempt_number,
                                action=action,
                                ebxml_version=ebxml_version,
                                must_understand=must_understand,
                                endpoint=endpoint,
                            )
                            return data, xml_text, meta
                        except SabreError as exc:
                            last_error = exc
                            logger.debug(
                                "multiflight_attempt_failed",
                                action=action,
                                ebxml_version=ebxml_version,
                                must_understand=must_understand,
                                endpoint=endpoint,
                                error=str(exc),
                            )
                            if "Availability not found" in str(exc):
                                logger.info(
                                    "multiflight_endpoint_misconfiguration_hint",
                                    endpoint=endpoint,
                                    hint="Endpoint appears to point to FalconEye API route instead of Sabre SOAP URL",
                                )
                    if attempt_number >= self._multiflight_max_attempts:
                        break
                if attempt_number >= self._multiflight_max_attempts:
                    break
            if attempt_number >= self._multiflight_max_attempts:
                break

        logger.warning(
            "multiflight_unavailable",
            attempts=len(attempted),
            timeout_seconds=self._multiflight_timeout_seconds,
            last_error=str(last_error),
        )
        raise SabreError(
            f"MultiFlight failed after {len(attempted)} attempts ({'; '.join(attempted)}). Last error: {last_error}")
