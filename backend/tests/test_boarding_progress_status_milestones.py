"""Status milestones on the boarding-progress card must name the Sabre statuses.

Ingestion stores a STATUS_CHANGE with oldValue and newValue. The boarding-progress
route copies those onto each milestone as previousStatus and status, and does not
send type or label. The Activity and overview cards both render BoardingProgress,
which paints milestone.type and milestone.label. Those fields are absent, so the
row is only a clock.

This test fails while that mismatch is present. It passes once each status
milestone row shows previousStatus and status, and the row is colored from the
new status using the same tokens as getStatusColor in
frontend/components/dashboard/workbench-utils.tsx (OPENCI blue, FINAL red,
PDC emerald).

Not covered here, because get_boarding_progress never builds a milestone for them:
GATE_CHANGE, TERMINAL_CHANGE, BOARDING_TIME_CHANGE, JUMPSEAT_CHANGE, and
COUNT_CHANGE. A gate change stored beside a status change does not become a
blank milestone row. The check-in and boarding series are also left untested:
the route returns them as checkinProgress.series / boardingProgress.series with
count, while the card reads .data and cumulativeCount.

Run from the repository root:

    python3 -m pip install 'fastapi>=0.110,<1.0' 'pydantic>=2.0,<3.0' 'structlog>=24.1,<26.0' 'pymongo>=4.6,<5.0' 'requests>=2.31,<3.0' 'python-dotenv>=1.0,<2.0' 'xmltodict>=0.13,<1.0' 'tenacity>=8.2,<10.0'
    cd frontend && npm ci && cd ..
    python3 backend/tests/test_boarding_progress_status_milestones.py

The Python test installs jsdom@26.1.0 and tsx@4.20.6 into a temporary
directory and renders the card with those. They are not application dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.encoders import jsonable_encoder  # noqa: E402

from backend.api.routes.changes import get_boarding_progress  # noqa: E402
from backend.feeder.differ import diff_flight_status, diff_passenger_list  # noqa: E402
import backend.api.routes.changes as changes_route  # noqa: E402

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}

# Color tokens inside getStatusColor (workbench-utils.tsx). The milestone row
# for a status must carry the token for that new status.
STATUS_COLOR = {
    "OPENCI": "bg-blue-500",
    "FINAL": "bg-red-500",
    "PDC": "bg-emerald-500",
}


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction == -1)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query, projection=None):
        return _Cursor(doc for doc in self._docs if _matches(doc, query))

    def find_one(self, query, sort=None, projection=None):
        matched = [doc for doc in self._docs if _matches(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                matched.sort(
                    key=lambda doc: doc.get(key) if doc.get(key) is not None else 0,
                    reverse=direction == -1,
                )
        return matched[0] if matched else None


class _Db:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _stamp(changes, detected_at):
    stamped = []
    for change in changes:
        cloned = dict(change)
        cloned["detectedAt"] = detected_at
        stamped.append(cloned)
    return stamped


def _status_transition(old, new, detected_at, gate_before, gate_after):
    before = {
        "snapshotId": f"status-before-{detected_at}",
        "data": {"status": old, "gate": gate_before, "passengerCounts": {}},
    }
    after = {
        "snapshotId": f"status-after-{detected_at}",
        "data": {"status": new, "gate": gate_after, "passengerCounts": {}},
    }
    return _stamp(
        diff_flight_status(before, after, FLIGHT_INFO),
        detected_at,
    )


def _passenger(checked_in, boarded):
    return {
        "pnr": "ABC123",
        "lastName": "SMITH",
        "firstName": "JOHN",
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": "12A",
        "bagCount": 1,
        "passengerType": "F",
    }


def _passenger_transitions():
    booked = {
        "snapshotId": "pax-booked",
        "data": {"passengers": [_passenger(False, False)]},
    }
    checked_in = {
        "snapshotId": "pax-checked-in",
        "data": {"passengers": [_passenger(True, False)]},
    }
    boarded = {
        "snapshotId": "pax-boarded",
        "data": {"passengers": [_passenger(True, True)]},
    }
    return (
        _stamp(diff_passenger_list(booked, checked_in, FLIGHT_INFO), "2026-03-19T04:00:00Z")
        + _stamp(diff_passenger_list(checked_in, boarded, FLIGHT_INFO), "2026-03-19T05:00:00Z")
    )


def boarding_progress_body():
    """JSON body the boarding-progress route returns for a real status change."""
    changes = []
    changes.extend(
        _status_transition("OPENCI", "FINAL", "2026-03-19T06:00:00Z", "B12", "B14")
    )
    changes.extend(
        _status_transition("FINAL", "PDC", "2026-03-19T07:30:00Z", "B14", "B14")
    )
    changes.extend(_passenger_transitions())
    snapshots = [
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "snapshotType": "passenger_list",
            "sequenceNumber": 4,
            "data": {"totalPassengers": 1, "passengers": [_passenger(True, True)]},
        }
    ]
    db = _Db({"changes": _Collection(changes), "snapshots": _Collection(snapshots)})
    previous = changes_route.get_db
    changes_route.get_db = lambda: db
    try:
        payload = get_boarding_progress(FLIGHT, origin=ORIGIN, date=DATE)
    finally:
        changes_route.get_db = previous
    encoded = jsonable_encoder(payload)
    body = json.dumps(
        encoded,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded, body


class _Handler(BaseHTTPRequestHandler):
    body = b""
    seen = []

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        _Handler.seen.append((parsed.path, query))
        expected = f"/flights/{FLIGHT}/boarding-progress"
        origin = query.get("origin", [None])[0]
        date = query.get("date", [None])[0]
        if parsed.path == expected and origin == ORIGIN and date == DATE:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        message = json.dumps({"detail": "not found"}).encode()
        self.send_header("Content-Length", str(len(message)))
        self.end_headers()
        self.wfile.write(message)

    def log_message(self, fmt, *args):
        return


def _serve(body):
    _Handler.body = body
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


class _El:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts)

    def classes(self):
        own = self.attrs.get("class", "")
        nested = [child.classes() for child in self.children if not isinstance(child, str)]
        return " ".join([own, *nested])


def _parse_html(html):
    from html.parser import HTMLParser

    root = _El("root", {})
    stack = [root]

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            node = _El(tag, attrs)
            stack[-1].children.append(node)
            stack.append(node)

        def handle_endtag(self, tag):
            for index in range(len(stack) - 1, 0, -1):
                if stack[index].tag == tag:
                    del stack[index:]
                    break

        def handle_data(self, data):
            stack[-1].children.append(data)

    parser = Parser()
    parser.feed(html)
    return root


def _walk(node):
    yield node
    for child in node.children:
        if not isinstance(child, str):
            yield from _walk(child)


def _rows(root, required, forbidden):
    found = []
    for node in _walk(root):
        text = node.text()
        if all(part in text for part in required) and all(part not in text for part in forbidden):
            found.append(node)
    return found


def _assert_row(html, previous, status):
    root = _parse_html(html)
    rows = _rows(root, [previous, status], [other for other in ("OPENCI", "FINAL", "PDC") if other not in (previous, status)])
    assert rows, (
        f"No milestone row shows previous status {previous} and new status {status}. "
        f"Rendered text: {_visible_text(html)!r}"
    )
    color = STATUS_COLOR[status]
    assert any(color in row.classes() for row in rows), (
        f"Milestone {previous} -> {status} is not colored with {color} from the new status. "
        f"Rendered text: {_visible_text(html)!r}"
    )


def _visible_text(html):
    return " ".join(_parse_html(html).text().split())


def _render_runner():
    """tsx and jsdom are test-only. Node resolves jsdom through NODE_PATH."""
    runner = Path(os.environ.get("TMPDIR", "/tmp")) / "boarding-progress-milestone-runner"
    jsdom_pkg = runner / "node_modules" / "jsdom" / "package.json"
    tsx_bin = runner / "node_modules" / ".bin" / "tsx"
    if not jsdom_pkg.is_file() or not tsx_bin.is_file():
        runner.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["npm", "install", "--silent", "jsdom@26.1.0", "tsx@4.20.6"],
            cwd=runner,
            check=True,
            timeout=180,
        )
    return tsx_bin, runner / "node_modules"


def _render(api_base, html_path):
    frontend = ROOT / "frontend"
    script = frontend / "tests" / "render-boarding-progress-milestones.tsx"
    tsx_bin, node_modules = _render_runner()
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = api_base
    env["MILESTONE_HTML_OUT"] = str(html_path)
    env["NODE_PATH"] = str(node_modules)
    command = [str(tsx_bin), str(script)]
    completed = subprocess.run(
        command,
        cwd=frontend,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not html_path.is_file():
        raise AssertionError(
            "Boarding progress render failed.\n"
            f"exit={completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return html_path.read_text(encoding="utf-8"), completed


def test_status_change_milestones_show_old_and_new_status(tmp_path=None):
    payload, body = boarding_progress_body()
    pairs = [
        (milestone.get("previousStatus"), milestone.get("status"))
        for milestone in payload["milestones"]
    ]
    assert ("OPENCI", "FINAL") in pairs, payload["milestones"]
    assert ("FINAL", "PDC") in pairs, payload["milestones"]
    assert payload["checkinProgress"]["series"], payload["checkinProgress"]
    assert payload["boardingProgress"]["series"], payload["boardingProgress"]

    server, base = _serve(body)
    out_dir = Path(tmp_path) if tmp_path is not None else Path(os.environ.get("TMPDIR", "/tmp"))
    html_path = out_dir / "boarding-progress-milestones.html"
    try:
        html, completed = _render(base, html_path)
    finally:
        server.shutdown()

    assert _Handler.seen, completed.stderr
    _assert_row(html, "OPENCI", "FINAL")
    _assert_row(html, "FINAL", "PDC")


if __name__ == "__main__":
    test_status_change_milestones_show_old_and_new_status()
    print("PASS: test_status_change_milestones_show_old_and_new_status")
