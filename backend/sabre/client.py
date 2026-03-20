"""
Sabre SOAP API client with session management.

Usage:
    with SabreClient() as client:
        status = client.get_flight_status("GF", "2006", "LHR")
        passengers = client.get_passenger_list("GF", "2006", "2026-03-19", "LHR")
        reservations = client.get_reservations("GF", "2006", "LHR", "2026-03-19T08:00:00")
"""

import os
import re
import uuid
import logging
from datetime import datetime, timezone

import requests
import xmltodict

from . import templates

logger = logging.getLogger(__name__)


class SabreError(Exception):
    """Raised when a Sabre API call fails."""
    pass


class SabreClient:
    """Manages a Sabre SOAP session and provides methods for each API."""

    def __init__(self):
        self._token = None
        self._conversation_id = None
        self._endpoint = f"{os.environ['SABRE_BASE_URL']}/{os.environ['SABRE_CPAID']}"
        self._cpaid = os.environ["SABRE_CPAID"]
        self._username = os.environ["SABRE_USERNAME"]
        self._password = os.environ["SABRE_PASSWORD"]
        self._pseudo_city_code = os.environ["SABRE_PSEUDO_CITY_CODE"]
        self._organization = os.environ["SABRE_ORGANIZATION"]
        self._domain = os.environ["SABRE_DOMAIN"]

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

    def _post(self, action, body, timeout=30):
        """Send a SOAP POST and return (raw_response_text, http_status, duration_ms, request_body)."""
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": action,
        }
        import time
        start = time.monotonic()
        resp = requests.post(self._endpoint, data=body,
                             headers=headers, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
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
        logger.info("Sabre session created (token: %s...)", self._token[:20])
        return self._token

    def close_session(self):
        """Close the Sabre session. Safe to call even if no session is active."""
        if not self._token:
            return
        try:
            body = templates.SESSION_CLOSE.format(**self._common_vars())
            self._post("SessionCloseRQ", body)
            logger.info("Sabre session closed.")
        except Exception as e:
            logger.warning("Error closing session: %s", e)
        finally:
            self._token = None

    # ── Flight Status ──────────────────────────────────────────────────────

    def get_flight_status(self, airline, flight_number, origin):
        """
        Call ACS_FlightDetailRQ.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        request_meta = {requestXml, httpStatus, durationMs, sessionToken, conversationId}
        """
        body = templates.FLIGHT_STATUS.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
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

    def get_passenger_data(self, airline, flight_number, departure_date, origin, last_name, pnr=None):
        """
        Call GetPassengerDataRQ for detailed per-passenger data.
        Looks up by LastName + optional PNR within a flight itinerary.
        Returns (parsed_body_dict, raw_xml_string, request_meta).
        """
        pnr_element = ""
        if pnr:
            pnr_element = f'<v4:PNRLocator>{pnr}</v4:PNRLocator>'
        body = templates.PASSENGER_DATA.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            last_name=last_name,
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
