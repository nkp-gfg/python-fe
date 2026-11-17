"""Committed Sabre captures must not contain operational passenger identity data.

A clone of this repository can open the tracked Trip Search responses, passenger
lists, and saved Sabre HTTP results and read them directly. These tests do that
read: they parse the git-tracked tree and fail while travel-document numbers,
dates of birth, passenger phone numbers, passenger email addresses, or ticket
serial numbers in those captures are still operational.

They pass once those values are removed or replaced with obvious placeholders
(for example SYNTHETIC, all-zero numbers, 1900-01-01, or example.com). Vendor
documentation under sabre_docs/ is a published Sabre sample for a different
carrier and is not part of this scan. This file is not scanned, so the
in-memory fixtures below cannot satisfy the tree checks.

The checks are intentionally about the checked-out tree. Deleting a file only
in a later commit makes that checkout pass; rewriting older git history is a
separate step these tests do not perform.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]

# Vendor WSDL package, not a Gulf Air operational capture. This test module
# holds invented fixtures and must not be treated as a capture either.
SKIP_PREFIXES = ("sabre_docs/", "backend/tests/")

DOB_SENTINELS = frozenset(
    {"1900-01-01", "1970-01-01", "1990-01-01", "2000-01-01", "0001-01-01", "9999-12-31"}
)
EMAIL_DOMAINS = frozenset(
    {"example.com", "example.org", "example.net", "localhost", "sabre.com", "clientofsabre.com"}
)
PLACEHOLDER_WORD = re.compile(
    r"(?i)^(?:synthetic|redacted|placeholder|dummy|fake|sample|test|passenger|doe|xxxxxx+|x+|0+|n/?a|none|unknown)$"
)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NS = r"(?:[\w.-]+:)?"
DOCUMENT_RE = re.compile(rf"<{NS}DocumentNumber>\s*([^<]*?)\s*</{NS}DocumentNumber>", re.I)
DOB_RE = re.compile(rf"<{NS}DateOfBirth>\s*([^<]*?)\s*</{NS}DateOfBirth>", re.I)
SERIAL_RE = re.compile(rf"<{NS}SerialNumber>\s*([^<]*?)\s*</{NS}SerialNumber>", re.I)
TICKET_RE = re.compile(rf"<{NS}TicketNumber>\s*([^<]*?)\s*</{NS}TicketNumber>", re.I)
LAST_RE = re.compile(rf"<{NS}LastName>\s*([^<]*?)\s*</{NS}LastName>", re.I)
PNR_RE = re.compile(rf"<{NS}PNRLocator\b[^>]*>\s*([^<]*?)\s*</{NS}PNRLocator>", re.I)
PHONE_BLOCK_RE = re.compile(rf"<{NS}PhoneNumber\b[^>]*>(.*?)</{NS}PhoneNumber>", re.I | re.S)
NUMBER_RE = re.compile(rf"<{NS}Number>\s*([^<]*?)\s*</{NS}Number>", re.I)
EMAIL_BLOCK_RE = re.compile(rf"<{NS}EmailAddress\b[^>]*>(.*?)</{NS}EmailAddress>", re.I | re.S)
ADDRESS_RE = re.compile(rf"<{NS}Address>\s*([^<]*?)\s*</{NS}Address>", re.I)

FIELD_NAMES = (
    "travel_document",
    "date_of_birth",
    "phone",
    "email",
    "ticket_serial",
    "passenger_list_identity",
    "passenger_name_and_locator",
)

# Evidence paths named by the finding. Other tracked captures are scanned too.
CITED_TRIP_SEARCH = "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
CITED_PASSENGER_LIST = "sample_data/sample_xmlfiles/GetPassengerListRS_GF2006_LHR_2026-03-18.xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else str(tag)


def is_placeholder(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if PLACEHOLDER_WORD.match(text):
        return True
    lowered = text.lower()
    if lowered.startswith(("synth", "redact", "dummy", "fake", "placeholder", "example", "test")):
        return True
    digits = re.sub(r"\D", "", text)
    if digits and len(set(digits)) == 1:
        return True
    # Only an all-digit sequential value is a placeholder. A letter prefix
    # keeps a document number operational.
    if text.isdigit() and digits in {"1234567", "12345678", "123456789", "1234567890", "0123456789"}:
        return True
    return False


def is_placeholder_email(value: str) -> bool:
    text = value.strip().lower()
    if "@" not in text:
        return True
    local, _, domain = text.partition("@")
    if domain in EMAIL_DOMAINS:
        return True
    if domain.endswith((".example", ".invalid", ".test", ".localhost", ".sabre.com")):
        return True
    if "example" in domain:
        return True
    return is_placeholder(local)


def _operational_documents(values: list[str]) -> int:
    count = 0
    for value in values:
        text = value.strip()
        if is_placeholder(text):
            continue
        digits = re.sub(r"\D", "", text)
        # Travel-document numbers in these responses carry digits and are
        # longer than a country or gender code.
        if len(text) >= 6 and len(digits) >= 1:
            count += 1
    return count


def _operational_dobs(values: list[str]) -> int:
    dates = []
    for value in values:
        text = value.strip()
        if ISO_DATE.match(text) and text not in DOB_SENTINELS and not is_placeholder(text):
            dates.append(text)
    return len(dates)


def _operational_phones(values: list[str]) -> int:
    count = 0
    for value in values:
        if is_placeholder(value):
            continue
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 7:
            count += 1
    return count


def _operational_emails(values: list[str]) -> int:
    return sum(1 for value in values if not is_placeholder_email(value))


def _operational_serials(values: list[str]) -> int:
    count = 0
    for value in values:
        if is_placeholder(value):
            continue
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 8:
            count += 1
    return count


def _operational_names(values: list[str]) -> int:
    count = 0
    for value in values:
        text = value.strip()
        if is_placeholder(text) or len(text) < 2:
            continue
        count += 1
    return count


def _operational_locators(values: list[str]) -> int:
    count = 0
    for value in values:
        text = value.strip()
        if is_placeholder(text):
            continue
        if re.fullmatch(r"[A-Za-z0-9]{5,8}", text):
            count += 1
    return count


def counts_from_fields(fields: dict[str, list[str]]) -> dict[str, int]:
    documents = _operational_documents(fields["document"])
    dobs = _operational_dobs(fields["dob"])
    phones = _operational_phones(fields["phone"])
    emails = _operational_emails(fields["email"])
    serials = _operational_serials(fields["serial"] + fields["ticket"])
    names = _operational_names(fields["last_name"])
    locators = _operational_locators(fields["pnr"])
    # Passenger-list identity is the combination the finding describes:
    # a name, a record locator, and a ticket serial in the same capture.
    # Some lists omit the serial and still publish the name with the locator.
    identity = min(names, locators, serials) if names and locators and serials else 0
    name_and_locator = min(names, locators) if names and locators else 0
    return {
        "travel_document": documents,
        "date_of_birth": dobs,
        "phone": phones,
        "email": emails,
        "ticket_serial": serials,
        "passenger_list_identity": identity,
        "passenger_name_and_locator": name_and_locator,
    }


def _empty_fields() -> dict[str, list[str]]:
    return {
        "document": [],
        "dob": [],
        "phone": [],
        "email": [],
        "serial": [],
        "ticket": [],
        "last_name": [],
        "pnr": [],
    }


def _append_text(bucket: list[str], value: str | None) -> None:
    if value and value.strip():
        bucket.append(value.strip())


def fields_from_xml_root(root: ET.Element) -> dict[str, list[str]]:
    fields = _empty_fields()
    for element in root.iter():
        name = _local(element.tag)
        if name == "DocumentNumber":
            _append_text(fields["document"], element.text)
        elif name == "DateOfBirth":
            _append_text(fields["dob"], element.text)
        elif name == "SerialNumber":
            _append_text(fields["serial"], element.text)
        elif name == "TicketNumber":
            _append_text(fields["ticket"], element.text)
        elif name == "LastName":
            _append_text(fields["last_name"], element.text)
        elif name == "PNRLocator":
            _append_text(fields["pnr"], element.text)
        elif name == "PhoneNumber":
            numbers = [
                (child.text or "").strip()
                for child in list(element)
                if _local(child.tag) == "Number" and (child.text or "").strip()
            ]
            if numbers:
                fields["phone"].extend(numbers)
            else:
                _append_text(fields["phone"], element.text)
        elif name == "EmailAddress":
            addresses = [
                (child.text or "").strip()
                for child in list(element)
                if _local(child.tag) == "Address" and (child.text or "").strip()
            ]
            if addresses:
                fields["email"].extend(addresses)
            else:
                _append_text(fields["email"], element.text)
    return fields


def _merge_fields(left: dict[str, list[str]], right: dict[str, list[str]]) -> dict[str, list[str]]:
    for key, values in right.items():
        left[key].extend(values)
    return left


def fields_from_text(text: str) -> dict[str, list[str]]:
    """Parse one XML document. On a parse failure, count the same tags by pattern.

    Parse errors are not re-raised: their messages can quote the source line.
    """
    start = _xml_start(text)
    if start is not None:
        try:
            root = ET.fromstring(text[start:])
        except ET.ParseError:
            root = None
        if root is not None:
            return fields_from_xml_root(root)
    return _fields_from_regex(text)


def _xml_start(text: str) -> int | None:
    positions = []
    for marker in ("<?xml", "<soap-env:", "<soapenv:", "<soap:", "<SOAP-ENV:"):
        index = text.find(marker)
        if index != -1:
            positions.append(index)
    if positions:
        return min(positions)
    if "<" in text and any(
        token in text for token in ("DocumentNumber", "DateOfBirth", "SerialNumber", "PhoneNumber")
    ):
        return text.find("<")
    return None


def _fields_from_regex(text: str) -> dict[str, list[str]]:
    fields = _empty_fields()
    fields["document"] = [m.group(1).strip() for m in DOCUMENT_RE.finditer(text) if m.group(1).strip()]
    fields["dob"] = [m.group(1).strip() for m in DOB_RE.finditer(text) if m.group(1).strip()]
    fields["serial"] = [m.group(1).strip() for m in SERIAL_RE.finditer(text) if m.group(1).strip()]
    fields["ticket"] = [m.group(1).strip() for m in TICKET_RE.finditer(text) if m.group(1).strip()]
    fields["last_name"] = [m.group(1).strip() for m in LAST_RE.finditer(text) if m.group(1).strip()]
    fields["pnr"] = [m.group(1).strip() for m in PNR_RE.finditer(text) if m.group(1).strip()]
    for block in PHONE_BLOCK_RE.finditer(text):
        numbers = [m.group(1).strip() for m in NUMBER_RE.finditer(block.group(1)) if m.group(1).strip()]
        fields["phone"].extend(numbers or ([block.group(1).strip()] if block.group(1).strip() else []))
    for block in EMAIL_BLOCK_RE.finditer(text):
        addresses = [m.group(1).strip() for m in ADDRESS_RE.finditer(block.group(1)) if m.group(1).strip()]
        fields["email"].extend(addresses)
    return fields


def fields_from_json_text(text: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return fields_from_text(text)
    fields = _empty_fields()
    for value in _walk_strings(payload):
        if "<" not in value:
            continue
        _merge_fields(fields, fields_from_text(value))
    return fields


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        if rel.startswith(SKIP_PREFIXES):
            continue
        paths.append(REPO_ROOT / rel)
    return paths


def counts_for_path(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {name: 0 for name in FIELD_NAMES}
    data = path.read_bytes()
    if b"\0" in data:
        return {name: 0 for name in FIELD_NAMES}
    text = data.decode("utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        fields = fields_from_json_text(text)
    elif "<" in text:
        fields = fields_from_text(text)
    else:
        return {name: 0 for name in FIELD_NAMES}
    return counts_from_fields(fields)


def scan_tracked_tree() -> list[tuple[str, str, int]]:
    findings = []
    for path in tracked_files():
        counts = counts_for_path(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for kind, count in counts.items():
            if count:
                findings.append((rel, kind, count))
    return sorted(findings)


def findings_of_kind(kind: str) -> list[tuple[str, int]]:
    return [(path, count) for path, found_kind, count in scan_tracked_tree() if found_kind == kind]


def _format_findings(kind: str, rows: list[tuple[str, int]]) -> str:
    body = ", ".join(f"{path} x{count}" for path, count in rows)
    return f"operational {kind} data is still in the checked-out tree: {body}"


def test_detector_allows_synthetic_placeholders():
    text = """
    <Capture>
      <DocumentNumber>SYNTHETIC</DocumentNumber>
      <DateOfBirth>1900-01-01</DateOfBirth>
      <PhoneNumber><Number>0000000000</Number></PhoneNumber>
      <EmailAddress><Address>passenger@example.com</Address></EmailAddress>
      <LastName>DOE</LastName>
      <PNRLocator>TEST01</PNRLocator>
      <SerialNumber>0000000000</SerialNumber>
      <TicketNumber>0000000000000</TicketNumber>
    </Capture>
    """
    counts = counts_from_fields(fields_from_text(text))
    assert counts == {name: 0 for name in FIELD_NAMES}


def test_detector_flags_invented_operational_payload():
    # Invented values, used only to prove the classifier. Not taken from captures.
    text = """
    <Capture>
      <DocumentNumber>AB1234567</DocumentNumber>
      <DateOfBirth>1985-04-02</DateOfBirth>
      <PhoneNumber><Number>12025550198</Number></PhoneNumber>
      <EmailAddress><Address>traveler@gmail.com</Address></EmailAddress>
      <LastName>TRAVELER</LastName>
      <PNRLocator>ABC123</PNRLocator>
      <SerialNumber>9876543210</SerialNumber>
    </Capture>
    """
    counts = counts_from_fields(fields_from_text(text))
    assert counts["travel_document"] == 1
    assert counts["date_of_birth"] == 1
    assert counts["phone"] == 1
    assert counts["email"] == 1
    assert counts["ticket_serial"] == 1
    assert counts["passenger_list_identity"] == 1
    assert counts["passenger_name_and_locator"] == 1


def test_checked_out_tree_has_no_travel_document_numbers():
    rows = findings_of_kind("travel_document")
    assert rows == [], _format_findings("travel_document", rows)


def test_checked_out_tree_has_no_dates_of_birth():
    rows = findings_of_kind("date_of_birth")
    assert rows == [], _format_findings("date_of_birth", rows)


def test_checked_out_tree_has_no_passenger_phone_numbers():
    rows = findings_of_kind("phone")
    assert rows == [], _format_findings("phone", rows)


def test_checked_out_tree_has_no_passenger_email_addresses():
    rows = findings_of_kind("email")
    assert rows == [], _format_findings("email", rows)


def test_checked_out_tree_has_no_ticket_serial_numbers():
    rows = findings_of_kind("ticket_serial")
    assert rows == [], _format_findings("ticket_serial", rows)


def test_checked_out_passenger_lists_have_no_name_locator_and_serial():
    rows = findings_of_kind("passenger_list_identity")
    assert rows == [], _format_findings("passenger_list_identity", rows)


def test_checked_out_passenger_lists_have_no_name_and_locator():
    rows = findings_of_kind("passenger_name_and_locator")
    assert rows == [], _format_findings("passenger_name_and_locator", rows)


def test_cited_trip_search_capture_has_no_apis_contact_data():
    path = REPO_ROOT / CITED_TRIP_SEARCH
    if not path.is_file():
        return
    counts = counts_for_path(path)
    leaked = {kind: counts[kind] for kind in ("travel_document", "date_of_birth", "phone", "email") if counts[kind]}
    assert leaked == {}, f"{CITED_TRIP_SEARCH} still has operational fields {leaked}"


def test_cited_passenger_list_capture_has_no_identity_bundle():
    path = REPO_ROOT / CITED_PASSENGER_LIST
    if not path.is_file():
        return
    counts = counts_for_path(path)
    assert counts["passenger_list_identity"] == 0, (
        f"{CITED_PASSENGER_LIST} still pairs passenger names, record locators, "
        f"and ticket serials x{counts['passenger_list_identity']}"
    )
    assert counts["ticket_serial"] == 0, (
        f"{CITED_PASSENGER_LIST} still has ticket serials x{counts['ticket_serial']}"
    )
