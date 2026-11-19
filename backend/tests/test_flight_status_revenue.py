"""Cabin revenue read from an ACS flight-detail response.

Sabre reports local revenue passengers in Local. Non-revenue passengers are
reported separately in NonRevenueLocal and NonRevenueThru. The stored cabin
revenue count is Local. The stored non-revenue count is those two fields
added together.

These tests parse the response with the SOAP client and then read it back
through the flight-status API. They fail while revenue is computed by
subtracting NonRevenueLocal from Local, or while NonRevenueThru is dropped.
"""

import copy
from pathlib import Path

from backend.api.routes.flights import (
    get_flight_dashboard,
    get_flight_status,
    get_flight_status_history,
    list_flights,
)
from backend.feeder import storage
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient

_REPO = Path(__file__).resolve().parents[2]
_GF2057_XML = (
    _REPO
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"
)

# One cabin, so xmltodict leaves PassengerCounts as a dict rather than a list.
# Booked = Local + Thru + NonRevenueLocal + NonRevenueThru = 6 + 2 + 1 + 1.
_THRU_NONREVENUE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>9999</Flight>
          <Origin>BAH</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <FlightStatus>FINAL</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Authorized>180</Authorized>
        <Booked>10</Booked>
        <Available>170</Available>
        <Thru>2</Thru>
        <Local>6</Local>
        <NonRevenueLocal>1</NonRevenueLocal>
        <NonRevenueThru>1</NonRevenueThru>
        <TotalOnBoard>10</TotalOnBoard>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _response_body(xml_text):
    """Run the flight-status SOAP body extraction the client uses."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(
        object.__new__(SabreClient), parsed, "ns3:ACS_FlightDetailRS"
    )


def _cabin_row(body, cabin):
    raw = body.get("PassengerCounts")
    rows = raw if isinstance(raw, list) else [raw]
    for row in rows:
        if row.get("@classOfService") == cabin:
            return row
    raise AssertionError(f"cabin {cabin} missing from parsed PassengerCounts")


def _expected_counts(row):
    local = int(row["Local"])
    non_revenue_local = int(row["NonRevenueLocal"])
    non_revenue_thru = int(row.get("NonRevenueThru") or 0)
    return {
        "booked": int(row["Booked"]),
        "local": local,
        "thru": int(row["Thru"]),
        "revenue": local,
        "nonRevenue": non_revenue_local + non_revenue_thru,
    }


def _assert_cabin(actual, expected):
    assert actual["booked"] == expected["booked"]
    assert actual["local"] == expected["local"]
    assert actual["thru"] == expected["thru"]
    assert (actual["revenue"], actual["nonRevenue"]) == (
        expected["revenue"],
        expected["nonRevenue"],
    )


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        self._docs.sort(
            key=lambda doc: (doc.get(key) is None, doc.get(key)),
            reverse=direction < 0,
        )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _match(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and set(expected) == {"$lte"}:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


def _eval_expr(doc, expr):
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict) and set(expr) == {"$ifNull"}:
        path, default = expr["$ifNull"]
        value = _eval_expr(doc, path)
        return default if value is None else value
    raise AssertionError(f"unsupported aggregation expression: {expr!r}")


def _exclude_path(doc, path):
    current = doc
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        found = matches[0]
        if not projection:
            return found
        return {key: found.get(key) for key, flag in projection.items() if flag}

    def find(self, query, projection=None, sort=None):
        del projection, sort
        return _Cursor(
            [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        )

    def aggregate(self, pipeline, allowDiskUse=False):
        del allowDiskUse
        docs = [copy.deepcopy(doc) for doc in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _match(doc, stage["$match"])]
            elif "$sort" in stage:
                field, direction = next(iter(stage["$sort"].items()))
                docs = sorted(
                    docs,
                    key=lambda doc: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
            elif "$group" in stage:
                spec = stage["$group"]
                grouped = []
                seen = {}
                for doc in docs:
                    identity = {
                        field: _eval_expr(doc, expr)
                        for field, expr in spec["_id"].items()
                    }
                    key = tuple(sorted(identity.items()))
                    if key in seen:
                        continue
                    if spec.get("doc") != {"$first": "$$ROOT"}:
                        raise AssertionError(f"unsupported group spec: {spec!r}")
                    grouped.append({"_id": identity, "doc": copy.deepcopy(doc)})
                    seen[key] = True
                docs = grouped
            elif "$project" in stage:
                projected = []
                for doc in docs:
                    clone = copy.deepcopy(doc)
                    for path, flag in stage["$project"].items():
                        if flag != 0:
                            raise AssertionError(stage["$project"])
                        _exclude_path(clone, path)
                    projected.append(clone)
                docs = projected
            elif "$addFields" in stage:
                if docs:
                    raise AssertionError("addFields stand-in is only used on empty collections")
            else:
                raise AssertionError(f"unsupported aggregation stage: {stage!r}")
        return docs


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _ingest_gf2057(monkeypatch):
    db = _Database()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: db)

    body = _response_body(_GF2057_XML.read_text(encoding="utf-8"))
    converted = convert_flight_status(body, "GF", "2057", "BOM", "2026-03-19")
    storage.store_flight_status(converted)
    storage.store_snapshot(
        "req-gf2057",
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
        "flight_status",
        converted,
    )
    expected = _expected_counts(_cabin_row(body, "Y"))
    return expected


def test_gf2057_economy_keeps_local_revenue_and_sums_nonrevenue():
    body = _response_body(_GF2057_XML.read_text(encoding="utf-8"))
    economy = _cabin_row(body, "Y")
    assert isinstance(body.get("PassengerCounts"), list)
    assert int(economy["NonRevenueLocal"]) > 0
    assert int(economy["Booked"]) == (
        int(economy["Local"])
        + int(economy["NonRevenueLocal"])
        + int(economy["Thru"])
        + int(economy["NonRevenueThru"])
    )
    assert int(economy["TotalOnBoard"]) == (
        int(economy["Local"]) + int(economy["NonRevenueLocal"]) + int(economy["NonRevenueThru"])
    )

    converted = convert_flight_status(body, "GF", "2057", "BOM", "2026-03-19")
    for cabin in ("Y", "J"):
        _assert_cabin(
            converted["passengerCounts"][cabin],
            _expected_counts(_cabin_row(body, cabin)),
        )


def test_single_cabin_counts_keep_thru_nonrevenue():
    body = _response_body(_THRU_NONREVENUE_XML)
    assert isinstance(body.get("PassengerCounts"), dict)
    economy = _cabin_row(body, "Y")
    assert int(economy["NonRevenueThru"]) > 0
    assert int(economy["NonRevenueLocal"]) > 0
    assert int(economy["Booked"]) == (
        int(economy["Local"])
        + int(economy["Thru"])
        + int(economy["NonRevenueLocal"])
        + int(economy["NonRevenueThru"])
    )

    converted = convert_flight_status(body, "GF", "9999", "BAH", "2026-03-19")
    _assert_cabin(converted["passengerCounts"]["Y"], _expected_counts(economy))


def test_flight_status_api_returns_ingested_cabin_revenue(monkeypatch):
    expected = _ingest_gf2057(monkeypatch)
    result = get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    _assert_cabin(result["passengerCounts"]["Y"], expected)
    assert "_raw" not in result


def test_flight_status_history_returns_ingested_cabin_revenue(monkeypatch):
    expected = _ingest_gf2057(monkeypatch)
    rows = get_flight_status_history(
        "2057", origin="BOM", date="2026-03-19", limit=20
    )
    assert len(rows) == 1
    _assert_cabin(rows[0]["passengerCounts"]["Y"], expected)


def test_flight_status_snapshot_returns_ingested_cabin_revenue(monkeypatch):
    expected = _ingest_gf2057(monkeypatch)
    result = get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=1
    )
    _assert_cabin(result["passengerCounts"]["Y"], expected)
    assert result["snapshotSequenceNumber"] == 1


def test_flight_dashboard_returns_ingested_cabin_revenue(monkeypatch):
    expected = _ingest_gf2057(monkeypatch)
    result = get_flight_dashboard(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
    )
    _assert_cabin(result["flightStatus"]["passengerCounts"]["Y"], expected)


def test_flight_list_returns_ingested_cabin_revenue(monkeypatch):
    expected = _ingest_gf2057(monkeypatch)
    flights = list_flights(date="2026-03-19")
    assert len(flights) == 1
    assert flights[0]["flightNumber"] == "2057"
    _assert_cabin(flights[0]["passengerCounts"]["Y"], expected)
