"""Repository readers must not be able to correlate passengers with bookings.

The checks below open the same committed extracts a clone of this tree exposes
and fail while passenger names are still stored beside booking locators,
ticket numbers, or passenger ids. They pass once those fields are gone from
the tree (files removed, or the fields cleared). They do not print field values.

Not covered here:
- sabre_docs/ vendor sample payloads, and the byte-identical
  GetPassengerListRQ copies under sample_data/sample_xmlfiles/
- blobs still reachable from older commits after this tree is cleaned
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# JSON keys the boarded extracts use for a person plus a booking identifier.
_JSON_IDENTIFIER_KEYS = (
    "passengerName",
    "pnr",
    "ticketNumber",
    "passengerId",
    "documentNumber",
    "passengerDocument",
)

_JSON_FIELD = re.compile(
    r'"(' + "|".join(_JSON_IDENTIFIER_KEYS) + r')"\s*:\s*'
    r'(null|"(?:\\.|[^"\\]*)*"|-?\d+(?:\.\d+)?)'
)

# Element local-names on captured Sabre responses that carry the same data.
_XML_IDENTIFIER_TAGS = (
    "LastName",
    "FirstName",
    "PassengerName",
    "PNRLocator",
    "TicketNumber",
    "ETicketNumber",
    "PassengerID",
)

_XML_FIELD = re.compile(
    r"<(?:[\w.-]+:)?(" + "|".join(_XML_IDENTIFIER_TAGS) + r")\b[^>]*>"
    r"([^<]*)"
)


def _json_value_present(raw: str) -> bool:
    if raw == "null":
        return False
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        return str(value).strip() != ""
    return True


def _json_identifier_fields(text: str) -> list[str]:
    """Return key names whose values are present. Values are discarded."""
    return [
        match.group(1)
        for match in _JSON_FIELD.finditer(text)
        if _json_value_present(match.group(2))
    ]


def _xml_identifier_fields(text: str) -> list[str]:
    found: list[str] = []
    for match in _XML_FIELD.finditer(text):
        if match.group(2).strip() == "":
            continue
        found.append(match.group(1))
    return found


def _field_counts(fields: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in fields:
        counts[field] = counts.get(field, 0) + 1
    return counts


def _exposure_report(paths_and_fields: list[tuple[Path, list[str]]]) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for path, fields in paths_and_fields:
        counts = _field_counts(fields)
        if counts:
            report[str(path.relative_to(REPO_ROOT))] = counts
    return report


class PassengerFixtureExposureTests(unittest.TestCase):
    def test_testing_tree_has_no_passenger_booking_identifiers(self) -> None:
        testing = REPO_ROOT / "testing"
        if not testing.exists():
            return
        found: list[tuple[Path, list[str]]] = []
        for path in sorted(p for p in testing.rglob("*") if p.is_file()):
            found.append(
                (path, _json_identifier_fields(path.read_text(encoding="utf-8", errors="replace")))
            )
        report = _exposure_report(found)
        self.assertFalse(
            report,
            f"testing/ still exposes passenger identifier fields: {report}",
        )

    def test_cited_boarded_extracts_identify_a_flight_when_present(self) -> None:
        """Names next to locators on the GF0205 BAH-NJF extract are the leak."""
        tied: dict[str, list[str]] = {}
        for rel in (
            "testing/boarded_passengers_summary.json",
            "testing/boarded_diff.json",
        ):
            path = REPO_ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            fields = sorted(set(_json_identifier_fields(text)))
            if fields and "GF0205" in text and "BAH-NJF" in text:
                tied[rel] = fields
        self.assertFalse(
            tied,
            f"boarded extracts still tie identifier fields to flight GF0205 BAH-NJF: {tied}",
        )

    def test_empty_identifier_fields_are_not_exposure(self) -> None:
        text = '{"passengerName":"","pnr":null,"ticketNumber":"","passengerId":null}'
        self.assertEqual(_json_identifier_fields(text), [])
        self.assertEqual(
            _xml_identifier_fields("<LastName></LastName><PNRLocator>   </PNRLocator>"),
            [],
        )

    def test_captured_sabre_responses_have_no_passenger_booking_identifiers(self) -> None:
        sample_dir = REPO_ROOT / "sample_data" / "sample_xmlfiles"
        if not sample_dir.exists():
            return
        found = [
            (path, _xml_identifier_fields(path.read_text(encoding="utf-8", errors="replace")))
            for path in sorted(sample_dir.glob("*RS*.xml"))
        ]
        report = _exposure_report(found)
        self.assertFalse(
            report,
            "captured Sabre responses still expose passenger identifier fields: "
            f"{report}",
        )


if __name__ == "__main__":
    unittest.main()
