"""The passengers-on-board count must stay readable on the passenger tree.

The flight board (PassengerTree mode="tree") and the comparison page
(PassengerTree mode="combined") render the tree returned by
_build_tree_payload. These tests fail while that count is painted in the
payload color on the theme card. They pass once the count contrasts with
the card in both the light theme and the dark theme.

Run from the repository root, after installing backend and frontend dependencies:

    pip install -r requirements.txt
    cd frontend && npm ci && npx --yes tsx@4.20.6 --version
    python -m pytest backend/tests/test_pax_tree_contrast.py -q

Google Chrome must be on PATH (or set CHROME_PATH). The frontend measurement
compiles app/globals.css with the installed Tailwind and reads the painted
colors from Chrome.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_tree_payload

# WCAG 2.x AA for large bold text. The count is font-size 20 and font-weight 600.
MIN_CONTRAST = 3.0

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MEASURE_SCRIPT = os.path.join(
    REPO_ROOT, "frontend", "tests", "measure-pax-tree-contrast.mjs"
)


def _sample_inputs():
    """Counts chosen so only the root value equals souls plus jump seats."""
    analysis = {
        "economy": {
            "total": 100,
            "passengers": {
                "total": 90,
                "male": 40,
                "female": 45,
                "children": 5,
                "infants": 2,
            },
            "staff": {"total": 10, "male": 6, "female": 4},
        },
        "business": {
            "total": 80,
            "passengers": {
                "total": 70,
                "male": 30,
                "female": 35,
                "children": 5,
                "infants": 1,
            },
            "staff": {"total": 10, "male": 4, "female": 6},
        },
        "totalMale": 80,
        "totalFemale": 90,
        "totalChildren": 10,
        "stateBreakdown": {"checkedIn": {"totalPassengers": 3}},
        "notCheckedIn": 4,
        "boarded": 170,
        "revenue": 160,
        "nonRevenue": 20,
    }
    passenger_summary = {
        "totalSouls": 187,
        "infantCount": 3,
        "totalPassengers": 180,
    }
    flight_status = {"status": "OPEN", "jumpSeat": {"cockpit": 2, "cabin": 1}}
    return analysis, passenger_summary, flight_status


def _measure(tree, label, value):
    tree_path = os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "pax-tree-contrast-payload.json"
    )
    with open(tree_path, "w", encoding="utf-8") as handle:
        json.dump(tree, handle)
    completed = subprocess.run(
        ["node", MEASURE_SCRIPT, tree_path, label, str(value)],
        cwd=os.path.join(REPO_ROOT, "frontend"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "contrast measurement failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def test_passengers_on_board_count_is_readable_in_light_and_dark_themes():
    analysis, passenger_summary, flight_status = _sample_inputs()
    tree = _build_tree_payload(
        analysis, passenger_summary, flight_status=flight_status
    )
    root = next(node for node in tree["nodes"] if node["id"] == "root")
    jump_seat = flight_status["jumpSeat"]["cockpit"] + flight_status["jumpSeat"]["cabin"]
    souls_on_board = passenger_summary["totalSouls"] + jump_seat

    assert root["display"] is True
    assert root["label"] == "Passengers on Board"
    assert root["value"] == souls_on_board

    # Cabin counts are different numbers, so the contrast check is the
    # passengers-on-board figure and not a cabin count.
    visible = {
        node["id"]: node["value"]
        for node in tree["nodes"]
        if node.get("display") is True and node["id"] != "root"
    }
    assert visible["economy"] != souls_on_board
    assert visible["business"] != souls_on_board

    measured = _measure(tree, root["label"], root["value"])
    failures = []

    for theme_result in measured["themes"]:
        theme = theme_result["theme"]
        luminance = theme_result["sentinel"]["luminance"]
        if theme == "light" and luminance < 0.95:
            failures.append(
                f"light theme card did not resolve to white (luminance {luminance})"
            )
        if theme == "dark" and luminance > 0.05:
            failures.append(
                f"dark theme card did not resolve to the dark card (luminance {luminance})"
            )

        surfaces = {item["surface"]: item for item in theme_result["surfaces"]}
        for surface in ("flight-board", "compare"):
            item = surfaces.get(surface)
            if item is None:
                failures.append(f"{theme} {surface}: tree was not rendered")
                continue
            if item["svgCount"] != 2:
                failures.append(
                    f"{theme} {surface}: expected the inline tree and the magnified "
                    f"tree, found {item['svgCount']} diagram(s)"
                )
            for copy in item["copies"]:
                if not copy.get("found") or copy.get("contrast") is None:
                    failures.append(f"{theme} {surface} svg {copy.get('svgIndex')}: count not painted")
                    continue
                if copy["contrast"] < MIN_CONTRAST:
                    failures.append(
                        f"{theme} {surface} svg {copy['svgIndex']}: "
                        f"Passengers on Board {root['value']} contrast "
                        f"{copy['contrast']:.2f} between fill {copy['text']} and "
                        f"card {copy['card']} (minimum {MIN_CONTRAST})"
                    )

        compare = surfaces.get("compare")
        matrix = compare.get("matrix") if compare else None
        if matrix is None or matrix.get("contrast") is None:
            failures.append(f"{theme} compare: matrix total for the same count was not rendered")
        elif matrix["contrast"] < MIN_CONTRAST:
            failures.append(
                f"{theme} compare matrix total contrast {matrix['contrast']:.2f} "
                f"between {matrix['text']} and {matrix['card']}"
            )

    assert not failures, "passengers-on-board count is not readable:\n" + "\n".join(failures)
