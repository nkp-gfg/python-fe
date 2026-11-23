"""Transition Details must show the status a passenger left or entered.

The phase-journey API tags a disappearance with the status from the earlier
snapshot and an arrival with the status in the later snapshot. The Transition
Details card drops those flows and prints one added total and one removed
total, so a booked cancellation and a boarded offload look the same.

These tests fail while that card still hides the rows. They pass when every
addition and removal is visible as its own row and the totals are the sums of
those rows.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes import flights as flights_route

STATE_LABELS = {
    "booked": "Booked",
    "checkedIn": "Checked-In",
    "boarded": "Boarded",
    "new": "Added",
    "removed": "Removed",
}

FLIGHT_NUMBER = "2057"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-04-01"


def _passenger(number: int, state: str) -> dict:
    return {
        "pnr": f"P{number:05d}",
        "lastName": "PAX",
        "firstName": f"N{number}",
        "isCheckedIn": state in ("checkedIn", "boarded"),
        "isBoarded": state == "boarded",
    }


class _IdSource:
    def __init__(self) -> None:
        self._next = 1

    def take(self, state: str, count: int) -> list[tuple[int, str]]:
        people = []
        for _ in range(count):
            people.append((self._next, state))
            self._next += 1
        return people


def _snapshot(sequence: int, snapshot_type: str, data: dict) -> dict:
    return {
        "flightNumber": FLIGHT_NUMBER,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": f"2026-04-01T0{sequence}:00:00Z",
        "data": data,
    }


def _flight_snapshots() -> list[dict]:
    """Two phase changes built so removals are not the largest flows.

    Check-In to Boarding has six larger status changes plus a booked
    cancellation, a checked-in removal, and arrivals into each status.
    Boarding to Departed removes one still-booked passenger and one
    already-boarded passenger, and adds a booked passenger and a boarded one.
    Those terminal flows are smaller than the passengers who stay, so they
    sit past the first six flows.
    """
    ids = _IdSource()
    groups = {
        "booked_stays": ids.take("booked", 3),
        "booked_to_checked_in": ids.take("booked", 3),
        "booked_to_boarded": ids.take("booked", 3),
        "checked_in_to_booked": ids.take("checkedIn", 3),
        "checked_in_stays": ids.take("checkedIn", 3),
        "checked_in_to_boarded": ids.take("checkedIn", 3),
        "booked_removed": ids.take("booked", 1),
        "checked_in_removed": ids.take("checkedIn", 1),
    }
    check_in = [_passenger(number, state) for people in groups.values() for number, state in people]

    boarding_state = {
        "booked_stays": "booked",
        "booked_to_checked_in": "checkedIn",
        "booked_to_boarded": "boarded",
        "checked_in_to_booked": "booked",
        "checked_in_stays": "checkedIn",
        "checked_in_to_boarded": "boarded",
    }
    boarding = []
    for name, people in groups.items():
        if name.endswith("_removed"):
            continue
        for number, _state in people:
            boarding.append(_passenger(number, boarding_state[name]))
    arrivals = (
        ids.take("booked", 1)
        + ids.take("checkedIn", 1)
        + ids.take("boarded", 1)
    )
    boarding.extend(_passenger(number, state) for number, state in arrivals)

    offloaded_or_cancelled = {
        groups["booked_stays"][0][0],
        groups["booked_to_boarded"][0][0],
        groups["checked_in_stays"][0][0],
    }
    departed = [
        passenger
        for passenger in boarding
        if int(passenger["pnr"][1:]) not in offloaded_or_cancelled
    ]
    departed.extend(
        _passenger(number, state)
        for number, state in ids.take("booked", 1) + ids.take("boarded", 1)
    )

    return [
        _snapshot(1, "passenger_list", {"passengers": check_in}),
        _snapshot(1, "flight_status", {
            "status": "OPENCI",
            "boarding": {"indicator": ""},
            "gate": "A1",
        }),
        _snapshot(2, "passenger_list", {"passengers": boarding}),
        _snapshot(2, "flight_status", {
            "status": "OPENCI",
            "boarding": {"indicator": "BDG"},
            "gate": "A1",
        }),
        _snapshot(3, "passenger_list", {"passengers": departed}),
        _snapshot(3, "flight_status", {
            "status": "PDC",
            "boarding": {"indicator": ""},
            "gate": "A1",
        }),
    ]


def _matches(document: dict, query: dict) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict):
            raise AssertionError(f"snapshot query uses an operator the stand-in does not apply: {key}")
        if document.get(key) != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents: list[dict]):
        self._documents = list(documents)

    def sort(self, key: str, direction: int = 1):
        self._documents.sort(key=lambda document: document.get(key, 0), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._documents)


class _Collection:
    def __init__(self, documents: list[dict]):
        self._documents = documents

    def find(self, query, projection=None):
        return _Cursor([document for document in self._documents if _matches(document, query)])

    def find_one(self, query, projection=None, sort=None):
        matched = [document for document in self._documents if _matches(document, query)]
        if sort:
            for key, direction in reversed(sort):
                matched.sort(key=lambda document: document.get(key, 0), reverse=direction < 0)
        return matched[0] if matched else None


class _Database:
    def __init__(self, snapshots: list[dict]):
        self._snapshots = snapshots

    def __getitem__(self, name: str):
        if name == "snapshots":
            return _Collection(self._snapshots)
        return _Collection([])


def _load_phase_journey() -> dict:
    flights_route.get_db = lambda: _Database(_flight_snapshots())
    return flights_route.get_phase_journey(FLIGHT_NUMBER, origin=ORIGIN, date=DEPARTURE_DATE)


def _terminal_flows(transition: dict) -> list[dict]:
    return [
        flow for flow in transition["flows"]
        if flow["fromState"] == "new" or flow["toState"] == "removed"
    ]


def _assert_api_reports_distinct_terminal_flows(journey: dict) -> None:
    by_phase = {(item["fromPhase"], item["toPhase"]): item for item in journey["transitions"]}
    check_in = by_phase[("CHECK_IN", "BOARDING")]
    departed = by_phase[("BOARDING", "DEPARTED")]

    check_in_pairs = {(flow["fromState"], flow["toState"]) for flow in check_in["flows"]}
    assert ("booked", "removed") in check_in_pairs
    assert ("checkedIn", "removed") in check_in_pairs
    assert ("new", "booked") in check_in_pairs
    assert ("new", "checkedIn") in check_in_pairs
    assert ("new", "boarded") in check_in_pairs

    departed_pairs = {(flow["fromState"], flow["toState"]) for flow in departed["flows"]}
    assert ("booked", "removed") in departed_pairs
    assert ("checkedIn", "removed") in departed_pairs
    assert ("boarded", "removed") in departed_pairs
    assert ("new", "booked") in departed_pairs
    assert ("new", "boarded") in departed_pairs

    check_in_terminal = _terminal_flows(check_in)
    check_in_staying = [flow for flow in check_in["flows"] if flow not in check_in_terminal]
    assert len(check_in_staying) >= 6, check_in["flows"]
    assert max(flow["count"] for flow in check_in_terminal) < min(
        flow["count"] for flow in check_in_staying
    )
    assert all(
        flow["fromState"] != "new" and flow["toState"] != "removed"
        for flow in check_in["flows"][:6]
    )

    # The boarded offload is past the six flows the card keeps, so deleting
    # only the new/removed filter still hides it behind the existing slice.
    departed_top = {
        (flow["fromState"], flow["toState"]) for flow in departed["flows"][:6]
    }
    assert ("boarded", "removed") not in departed_top
    assert ("checkedIn", "removed") not in departed_top


class _CardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[dict] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "")
        self._stack.append({"class": classes, "parts": [], "rows": [], "labels": []})

    def handle_endtag(self, tag):
        node = self._stack.pop()
        classes = node["class"]
        text = _clean_text("".join(node["parts"]))
        if "justify-between" in classes and "text-[10px]" in classes:
            for parent in reversed(self._stack):
                if _is_transition_card(parent["class"]):
                    parent["rows"].append(text)
                    break
        elif "text-xs" in classes and "font-bold" in classes and text:
            for parent in reversed(self._stack):
                if _is_transition_card(parent["class"]):
                    parent["labels"].append(text)
                    break
        if _is_transition_card(classes):
            self.cards.append({"labels": node["labels"], "rows": node["rows"]})

    def handle_data(self, data):
        for node in self._stack:
            node["parts"].append(data)


def _is_transition_card(classes: str) -> bool:
    return "bg-card" in classes and "p-3" in classes and "rounded-lg" in classes


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _render_transition_details(journey: dict) -> str:
    frontend = REPO_ROOT / "frontend"
    renderer = frontend / "tests" / "render-phase-transition-details.tsx"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(
            {"phases": journey["phases"], "transitions": journey["transitions"]},
            handle,
        )
        payload_path = handle.name
    env = os.environ.copy()
    env["NODE_PATH"] = str(frontend / "node_modules")
    completed = subprocess.run(
        ["npx", "--yes", "tsx", str(renderer), payload_path],
        cwd=frontend,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    os.unlink(payload_path)
    if completed.returncode != 0:
        raise AssertionError(
            "PhaseTransitionChart failed to render\n"
            f"{completed.stderr}"
        )
    return completed.stdout


def _cards_by_heading(html: str) -> dict[tuple[str, str], list[str]]:
    parser = _CardParser()
    parser.feed(html)
    cards = {}
    for card in parser.cards:
        labels = card["labels"]
        assert len(labels) >= 2, card
        cards[(labels[0], labels[1])] = card["rows"]
    return cards


_FLOW_ROW = re.compile(
    r"^(Booked|Checked-In|Boarded|Added)\s*→\s*"
    r"(Booked|Checked-In|Boarded|Removed)\s*(\d+)$"
)


def _flow_rows(rows: list[str]) -> list[tuple[str, str, int]]:
    parsed = []
    for row in rows:
        if row.startswith("+ Added") or row.startswith("- Removed"):
            continue
        match = _FLOW_ROW.fullmatch(row)
        assert match, f"transition row is not a from/to count: {row!r}"
        parsed.append((match.group(1), match.group(2), int(match.group(3))))
    return parsed


def _total(rows: list[str], prefix: str) -> int:
    matches = [row for row in rows if row.startswith(prefix)]
    assert len(matches) == 1, rows
    match = re.search(r"(\d+)$", matches[0])
    assert match, matches[0]
    return int(match.group(1))


def _assert_terminal_rows_visible(journey: dict, html: str) -> None:
    cards = _cards_by_heading(html)
    problems = []
    for transition in journey["transitions"]:
        key = (transition["fromLabel"], transition["toLabel"])
        if key not in cards:
            problems.append(f"missing Transition Details card for {key}; cards={list(cards)}")
            continue
        rows = cards[key]
        visible = _flow_rows(rows)
        expected = [
            (STATE_LABELS[flow["fromState"]], STATE_LABELS[flow["toState"]], flow["count"])
            for flow in _terminal_flows(transition)
        ]
        missing = [row for row in expected if row not in visible]
        if missing:
            problems.append(
                f"{key[0]} → {key[1]} hides terminal status rows {missing}. "
                f"Visible rows: {rows}"
            )
            continue
        added_from_rows = sum(count for source, _dest, count in visible if source == "Added")
        removed_from_rows = sum(count for _source, dest, count in visible if dest == "Removed")
        if _total(rows, "+ Added") != added_from_rows or added_from_rows != transition["addedCount"]:
            problems.append(
                f"{key[0]} → {key[1]} added total is not the sum of Added rows "
                f"({rows}, api={transition['addedCount']})"
            )
        if _total(rows, "- Removed") != removed_from_rows or removed_from_rows != transition["removedCount"]:
            problems.append(
                f"{key[0]} → {key[1]} removed total is not the sum of Removed rows "
                f"({rows}, api={transition['removedCount']})"
            )
    assert not problems, "\n".join(problems)


def test_transition_details_show_addition_and_removal_status():
    journey = _load_phase_journey()
    _assert_api_reports_distinct_terminal_flows(journey)
    html = _render_transition_details(journey)
    _assert_terminal_rows_visible(journey, html)


if __name__ == "__main__":
    test_transition_details_show_addition_and_removal_status()
    print("PASS: test_transition_details_show_addition_and_removal_status")
