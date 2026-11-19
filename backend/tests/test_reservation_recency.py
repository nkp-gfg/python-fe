"""Reservation recency must report the Sabre update clock.

The dashboard insight reads ``modifiedAt`` and, when that is empty, ``createdAt``.
Trip Search conversion stores ``UpdateTimestamp`` (or ``ModificationTimestamp``)
on ``updatedAt`` and ``CreationTimestamp`` on ``createdAt``. It does not write
``modifiedAt``, so the card labeled "Latest Modification" shows a creation time.

These tests drive the product path:

    SOAP XML
      -> SabreClient._parse_xml / _extract_body
      -> convert_reservations
      -> _strip_id (the reservation document the dashboard reads back)
      -> _build_dashboard_payload / _build_insights

They fail while that insight ignores ``updatedAt``. A fix passes them by
choosing, for each reservation, ``updatedAt`` when it is non-empty and
``createdAt`` otherwise, then reporting the newest of those instants as
``reservationRecency.latestModification``.

Not covered here, because they do not choose the recency instant:

- ``frontend/components/dashboard/flight-insights.tsx`` renders
  ``latestModification`` under a fixed "Latest Modification" label. It does
  not pick the timestamp. Once the API value is the update time, that label
  matches the WUFOLD case. A creation-only fallback is still shown with that
  same label; this module does not render React.
- ``frontend/components/dashboard/insight-info-panel.tsx`` documents
  ``modifiedAt``. It does not compute a time.
- ``frontend/components/dashboard/reservation-view.tsx`` already displays
  ``updatedAt`` on its own.
- ``get_flight_tree`` builds the same dashboard payload and returns only the
  tree, so the recency value is not on that response.
- Historical dashboard loads the same converted document from ``snapshots``
  via ``get_snapshot_data_as_of`` and then calls ``_build_insights``. Storage
  inserts that document unchanged (``store_reservations`` / ``store_snapshot``).
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    _build_dashboard_payload,
    _build_insights,
    _strip_id,
)
from backend.feeder.converter import convert_reservations  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

_WUFOLD_CREATED = "2026-03-18T02:27:00"
_WUFOLD_UPDATED = "2026-03-18T02:33:46"


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ingest_trip_search(xml_text: str, departure_date: str = "2026-03-18") -> dict:
    """Parse a Trip_Search SOAP response the way get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    # __new__ skips SabreClient.__init__, which reads session credentials.
    client = SabreClient.__new__(SabreClient)
    body = client._extract_body(parsed, "Trip_SearchRS")
    doc = convert_reservations(body, "GF", "2057", "BOM", departure_date)
    return _strip_id(doc)


def _passenger_list(departure_date: str = "2026-03-18") -> dict:
    return {
        "origin": "BOM",
        "departureDate": departure_date,
        "passengers": [
            {"isThru": False, "editCodes": [], "bagCount": 0, "seat": ""},
        ],
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "cabinSummary": [],
    }


def _recency_from_dashboard(reservation_doc: dict, departure_date: str = "2026-03-18"):
    """Return the recency object both dashboard callers build."""
    pl = _passenger_list(departure_date)
    fs = {"origin": "BOM", "departureDate": departure_date}
    payload = _build_dashboard_payload(
        fs, pl, "BOM", departure_date, {}, reservation_doc, None, None,
    )
    via_payload = (payload.get("insights") or {}).get("reservationRecency")
    via_helper = _build_insights(
        pl["passengers"], reservation_doc, None, pl, {},
    ).get("reservationRecency")
    return via_payload, via_helper


def _chosen_clock(reservation: dict) -> str:
    """Update time when ingestion stored one, otherwise the creation time."""
    updated = reservation.get("updatedAt") or ""
    if isinstance(updated, str) and updated.strip():
        return updated
    created = reservation.get("createdAt") or ""
    return created if isinstance(created, str) else ""


def _expected_latest(reservations: list) -> datetime | None:
    instants = []
    for reservation in reservations:
        clock = _chosen_clock(reservation)
        if not clock:
            continue
        instants.append(_instant(clock))
    if not instants:
        return None
    return max(instants)


def _soap(reservations_xml: str, count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        "<Trip_SearchRS>"
        f'<ReservationsList NumberResults="{count}">'
        "<Reservations>"
        f"{reservations_xml}"
        "</Reservations>"
        "</ReservationsList>"
        "</Trip_SearchRS>"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _reservation(pnr: str, created: str, updated: str = "", modified: str = "") -> str:
    """One Trip_Search reservation. updated/modified are omitted when empty."""
    stamps = f"<stl19:CreationTimestamp>{created}</stl19:CreationTimestamp>"
    if updated:
        stamps += f"<stl19:UpdateTimestamp>{updated}</stl19:UpdateTimestamp>"
    if modified:
        stamps += f"<stl19:ModificationTimestamp>{modified}</stl19:ModificationTimestamp>"
    return (
        f'<Reservation Locator="{pnr}">'
        "<stl19:GetReservationRS>"
        '<stl19:Reservation numberInParty="1" numberOfInfants="0">'
        "<stl19:BookingDetails>"
        f"{stamps}"
        "</stl19:BookingDetails>"
        "</stl19:Reservation>"
        "</stl19:GetReservationRS>"
        "</Reservation>"
    )


class ReservationRecencyTests(unittest.TestCase):
    def assert_latest_is(self, reservation_doc: dict, expected: datetime | None):
        via_payload, via_helper = _recency_from_dashboard(
            reservation_doc, reservation_doc.get("departureDate") or "2026-03-18",
        )
        self.assertIsNotNone(via_payload)
        self.assertIsNotNone(via_helper)
        self.assertEqual(
            via_payload.get("totalReservations"),
            len(reservation_doc.get("reservations") or []),
        )
        for label, recency in (("dashboard", via_payload), ("insights", via_helper)):
            raw = recency.get("latestModification")
            got = _instant(raw) if raw else None
            shown = expected.isoformat() if expected else None
            self.assertEqual(
                got,
                expected,
                f"{label} latestModification {raw!r} is not the per-reservation "
                f"update clock (updatedAt, else createdAt) {shown}",
            )
        # Both entry points must agree with each other, not only with the spec.
        self.assertEqual(
            via_payload.get("latestModification"),
            via_helper.get("latestModification"),
        )

    def test_gf2057_sample_reports_later_update_not_wufold_creation(self):
        """Committed Trip_Search for GF2057 on 2026-03-18.

        WUFOLD was created at 02:27 and updated at 02:33. Another booking on
        the same response was updated later still. The insight currently
        returns WUFOLD's creation time, the newest CreationTimestamp.
        """
        xml_text = _SAMPLE.read_text(encoding="utf-8")
        doc = _ingest_trip_search(xml_text)
        by_pnr = {rv.get("pnr"): rv for rv in doc["reservations"]}
        wufold = by_pnr["WUFOLD"]
        self.assertEqual(wufold.get("createdAt"), _WUFOLD_CREATED)
        self.assertEqual(wufold.get("updatedAt"), _WUFOLD_UPDATED)
        self.assertGreater(_instant(wufold["updatedAt"]), _instant(wufold["createdAt"]))

        expected = _expected_latest(doc["reservations"])
        self.assertIsNotNone(expected)
        self.assertGreater(expected, _instant(_WUFOLD_UPDATED))
        self.assertNotEqual(expected, _instant(_WUFOLD_CREATED))
        self.assert_latest_is(doc, expected)

    def test_later_update_beats_a_newer_creation(self):
        """A booking created later must not hide an older PNR's later update."""
        xml_text = _soap(
            _reservation("NEWBK", "2026-03-18T10:00:00")
            + _reservation("CHNGD", "2026-03-01T08:00:00", updated="2026-03-18T16:00:00"),
            2,
        )
        doc = _ingest_trip_search(xml_text)
        by_pnr = {rv["pnr"]: rv for rv in doc["reservations"]}
        self.assertEqual(by_pnr["NEWBK"].get("createdAt"), "2026-03-18T10:00:00")
        self.assertFalse(by_pnr["NEWBK"].get("updatedAt"))
        self.assertEqual(by_pnr["CHNGD"].get("updatedAt"), "2026-03-18T16:00:00")
        self.assert_latest_is(doc, _instant("2026-03-18T16:00:00"))

    def test_update_time_wins_even_when_earlier_than_creation(self):
        """createdAt is only the fallback. An earlier update is still the change clock."""
        xml_text = _soap(
            _reservation("EARLY", "2026-03-18T12:00:00", updated="2026-03-18T11:00:00")
            + _reservation("OTHER", "2026-03-18T10:00:00", updated="2026-03-18T10:30:00"),
            2,
        )
        doc = _ingest_trip_search(xml_text)
        self.assert_latest_is(doc, _instant("2026-03-18T11:00:00"))

    def test_modification_timestamp_is_the_update_when_update_timestamp_absent(self):
        """Converter maps ModificationTimestamp onto updatedAt. Recency must use it."""
        xml_text = _soap(
            _reservation("MODFY", "2026-03-18T08:00:00", modified="2026-03-18T09:30:00"),
            1,
        )
        doc = _ingest_trip_search(xml_text)
        only = doc["reservations"][0]
        self.assertEqual(only.get("createdAt"), "2026-03-18T08:00:00")
        self.assertEqual(only.get("updatedAt"), "2026-03-18T09:30:00")
        self.assert_latest_is(doc, _instant("2026-03-18T09:30:00"))

    def test_missing_update_falls_back_to_creation(self):
        xml_text = _soap(
            _reservation("ONLYC", "2026-03-18T04:15:00"),
            1,
        )
        doc = _ingest_trip_search(xml_text)
        only = doc["reservations"][0]
        self.assertEqual(only.get("createdAt"), "2026-03-18T04:15:00")
        self.assertFalse(only.get("updatedAt"))
        self.assert_latest_is(doc, _instant("2026-03-18T04:15:00"))

    def test_blank_update_timestamp_falls_back_to_creation(self):
        xml_text = _soap(
            _reservation("BLANK", "2026-03-18T05:45:00", updated="   "),
            1,
        )
        doc = _ingest_trip_search(xml_text)
        only = doc["reservations"][0]
        self.assertEqual(only.get("createdAt"), "2026-03-18T05:45:00")
        self.assertFalse((only.get("updatedAt") or "").strip())
        self.assert_latest_is(doc, _instant("2026-03-18T05:45:00"))

    def test_no_reservations_has_no_modification_time(self):
        xml_text = _soap("", 0)
        doc = _ingest_trip_search(xml_text)
        self.assertEqual(doc.get("reservations"), [])
        self.assert_latest_is(doc, None)


if __name__ == "__main__":
    unittest.main()
