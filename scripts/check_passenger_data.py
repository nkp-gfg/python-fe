#!/usr/bin/env python3
"""Fail if tracked files contain passenger names or booking identifiers.

Captured Sabre responses, Bruno run reports, and MongoDB extracts carry real
passenger names, PNR locators, ticket numbers, passenger ids, and travel
document numbers. None of that may be committed. Fixtures must use synthetic
values (see SYNTHETIC_VALUES) or template placeholders.

Usage:
    python scripts/check_passenger_data.py            # all tracked files
    python scripts/check_passenger_data.py FILE ...   # only the given files

Findings are reported as path:line:field. Values are never printed.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Vendor documentation shipped by Sabre; its sample payloads are not Gulf Air data.
EXCLUDED_PATTERNS = (
    "sabre_docs/*",
    "sample_data/sample_xmlfiles/GetPassengerListRQ_v4_SamplePayload_*.xml",
)

_IDENTIFIER_KEYS = {
    "passengername",
    "lastname",
    "firstname",
    "surname",
    "givenname",
    "pnr",
    "pnrlocator",
    "recordlocator",
    "ticketnumber",
    "eticketnumber",
    "passengerid",
    "documentnumber",
    "passengerdocument",
    "passportnumber",
}

_XML_IDENTIFIER_TAGS = (
    "PassengerName",
    "LastName",
    "FirstName",
    "Surname",
    "GivenName",
    "PNRLocator",
    "TicketNumber",
    "ETicketNumber",
    "PassengerID",
    "DocumentNumber",
)

_XML_IDENTIFIER_ATTRS = ("Locator", "PNRLocator", "TicketNumber", "PassengerID")

# Values used by the synthetic fixtures in backend/tests and the Bruno requests.
SYNTHETIC_VALUES = {
    "SMITH",
    "JOHN",
    "JONES",
    "MARY",
    "DOE",
    "JANE",
    "TEST",
    "LEE",
    "STAFF",
    "ABC123",
    "DEF456",
    "XYZ789",
}
_SYNTHETIC_PATTERN = re.compile(r"^(PNR|TKT|PAX)\d{1,4}$")

_JSON_FIELD = re.compile(
    r'"(?P<key>[A-Za-z_]+)"\s*:\s*(?P<val>"(?:\\.|[^"\\])*"|-?\d+)'
)
_XML_ELEMENT = re.compile(
    r"<(?:[\w.-]+:)?(?P<key>" + "|".join(_XML_IDENTIFIER_TAGS) + r")\b[^>]*>(?P<val>[^<]*)<"
)
_XML_ATTR = re.compile(
    r"<[^>]*?\b(?P<key>" + "|".join(_XML_IDENTIFIER_ATTRS) + r')="(?P<val>[^"]*)"'
)
_KV_LINE = re.compile(r"^\s*(?P<key>[A-Za-z_]+)\s*[:=]\s*(?P<val>\S.*?)\s*$", re.MULTILINE)
_KV_SUFFIXES = {".bru", ".env", ".yml", ".yaml", ".ini", ".cfg", ".toml", ".properties"}
_TABLE_SUFFIXES = {".csv", ".tsv"}

_PLACEHOLDER = re.compile(
    r"^(\{\{?\s*[\w.]+\s*\}?\}|\$\{?[\w.]+\}?|<[\w.-]+>|(.)\2*)$"
)


def _is_real_value(raw: str) -> bool:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value or value.lower() in {"null", "none"}:
        return False
    if _PLACEHOLDER.match(value):
        return False
    upper = value.upper()
    return upper not in SYNTHETIC_VALUES and not _SYNTHETIC_PATTERN.match(upper)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(text: str, suffix: str = "") -> list[tuple[int, str]]:
    """Return (line, field) pairs for identifier fields holding real values."""
    findings: list[tuple[int, str]] = []
    for match in _JSON_FIELD.finditer(text):
        if match.group("key").lower() in _IDENTIFIER_KEYS and _is_real_value(match.group("val")):
            findings.append((_line_of(text, match.start()), match.group("key")))
    for pattern in (_XML_ELEMENT, _XML_ATTR):
        for match in pattern.finditer(text):
            if _is_real_value(match.group("val")):
                findings.append((_line_of(text, match.start()), match.group("key")))
    if suffix in _KV_SUFFIXES:
        for match in _KV_LINE.finditer(text):
            if match.group("key").lower() in _IDENTIFIER_KEYS and _is_real_value(match.group("val")):
                findings.append((_line_of(text, match.start()), match.group("key")))
    if suffix in _TABLE_SUFFIXES:
        findings.extend(_scan_table(text, "\t" if suffix == ".tsv" else ","))
    return findings


def _scan_table(text: str, delimiter: str) -> list[tuple[int, str]]:
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(rows, None)
    if not header:
        return []
    columns = {
        i: name.strip()
        for i, name in enumerate(header)
        if re.sub(r"[^a-z]", "", name.lower()) in _IDENTIFIER_KEYS
    }
    findings: list[tuple[int, str]] = []
    for row in rows:
        for i, name in columns.items():
            if i < len(row) and _is_real_value(row[i]):
                findings.append((rows.line_num, name))
    return findings


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _excluded(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in EXCLUDED_PATTERNS)


def main(argv: list[str]) -> int:
    paths = argv or _tracked_files()
    failures = 0
    for name in paths:
        path = Path(name)
        if not path.is_absolute():
            path = REPO_ROOT / path
        try:
            rel = path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = name
        if _excluded(rel) or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="replace")
        for line, field in scan_text(text, path.suffix.lower()):
            print(f"{rel}:{line}: passenger identifier field '{field}' has a non-synthetic value")
            failures += 1
    if failures:
        print(
            f"\n{failures} passenger identifier value(s) found. Remove captured "
            "passenger data or replace it with synthetic values.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
