"""Run the data-audit airline-prefix tests.

The frontend suite is expected to fail until the flight form strips a leading
airline code before calling the comparison routes. This module only launches
that suite with the interpreter that is running pytest.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def test_data_audit_airline_prefix_form():
    env = os.environ.copy()
    env["DATA_AUDIT_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [
            "node",
            "--import",
            "tsx",
            "--test",
            "tests/data-audit-airline-prefix.test.mjs",
        ],
        cwd=FRONTEND,
        env=env,
    )
    assert completed.returncode == 0
