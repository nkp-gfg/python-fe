"""The live passenger detail sheet must show Sabre's check-in sequence.

These tests fail while the itinerary row omits the check-in number. Run:

    python3 -m pytest backend/tests/test_live_itinerary_checkin_sequence.py -q
"""

import subprocess
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_live_itinerary_prints_check_in_sequence():
    completed = subprocess.run(
        [
            "npx",
            "vitest",
            "run",
            "components/dashboard/passenger-detail-checkin-sequence.test.tsx",
        ],
        cwd=FRONTEND,
        check=False,
        text=True,
        capture_output=True,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
