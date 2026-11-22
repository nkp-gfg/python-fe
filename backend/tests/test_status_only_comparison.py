"""Status-only flights must not crash the comparison view.

A flight-status document can be stored while every passenger-list call fails.
The flight list is built from flight status, and the dashboard currently
returns HTTP 200 with an empty analysis object. These tests are the spec for
the fix: that response must carry an explicit empty-manifest flag and no
analysis object. They fail until that response shape is returned.

The in-memory collection below executes the aggregation stages list_flights
sends ($match, $sort, $group, $project, and $addFields on an empty passenger
collection). It is not a substitute for the dashboard or feeder code.
"""

from copy import deepcopy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.api.main import app as product_app
from backend.api.routes import flights as flights_route
from backend.feeder import runner, storage
from backend.sabre.client import SabreError


STATUS_ONLY = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-03-20",
    "departureDateTime": "2026-03-20T09:00:00",
}


def _get_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _delete_path(doc, path):
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _eval_expr(expr, doc):
    if isinstance(expr, str) and expr.startswith("$"):
        if expr == "$$ROOT":
            return doc
        if expr.startswith("$$"):
            raise NotImplementedError(expr)
        return _get_path(doc, expr[1:])
    if isinstance(expr, dict) and len(expr) == 1:
        op, arg = next(iter(expr.items()))
        if op == "$ifNull":
            value = _eval_expr(arg[0], doc)
            return arg[1] if value is None else value
        if op == "$first":
            return _eval_expr(arg, doc)
        raise NotImplementedError(op)
    if isinstance(expr, dict):
        return {key: _eval_expr(value, doc) for key, value in expr.items()}
    return expr


def _sort_docs(docs, spec):
    items = list(spec.items()) if isinstance(spec, dict) else list(spec)
    ordered = list(docs)
    for field, direction in reversed(items):
        ordered.sort(
            key=lambda doc, field=field: (
                doc.get(field) is None,
                "" if doc.get(field) is None else doc.get(field),
            ),
            reverse=direction < 0,
        )
    return ordered


def _match(doc, query):
    for key, expected in (query or {}).items():
        if isinstance(expected, dict):
            raise NotImplementedError(expected)
        if doc.get(key) != expected:
            return False
    return True


def _project(doc, spec):
    if spec is None:
        return deepcopy(doc)
    exclusion = all(value == 0 for value in spec.values())
    if exclusion:
        projected = deepcopy(doc)
        for path, flag in spec.items():
            if flag == 0:
                _delete_path(projected, path)
        return projected
    projected = {}
    if spec.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, flag in spec.items():
        if key == "_id" or not flag:
            continue
        if key in doc:
            projected[key] = deepcopy(doc[key])
    return projected


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class MemoryCollection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = deepcopy(doc)
        stored["_id"] = f"mem-{self._next_id}"
        self._next_id += 1
        self.docs.append(stored)
        return type("InsertResult", (), {"inserted_id": stored["_id"]})()

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        docs = [doc for doc in self.docs if _match(doc, query)]
        if sort:
            docs = _sort_docs(docs, sort)
        if not docs:
            return None
        return _project(docs[0], projection)

    def find(self, query=None, projection=None, sort=None, **kwargs):
        docs = [_project(doc, projection) for doc in self.docs if _match(doc, query)]
        if sort:
            docs = _sort_docs(docs, sort)
        return _Cursor(docs)

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self.docs if _match(doc, query)]
        if matches:
            target = matches[0]
        elif upsert:
            target = {}
            for key, value in (query or {}).items():
                if not isinstance(value, dict):
                    target[key] = deepcopy(value)
            for key, value in update.get("$setOnInsert", {}).items():
                target[key] = deepcopy(value)
            target["_id"] = f"mem-{self._next_id}"
            self._next_id += 1
            self.docs.append(target)
        else:
            return None
        for key, value in update.get("$set", {}).items():
            target[key] = deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            target[key] = target.get(key, 0) + value
        return None

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = [deepcopy(doc) for doc in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _match(doc, stage["$match"])]
            elif "$sort" in stage:
                docs = _sort_docs(docs, stage["$sort"])
            elif "$group" in stage:
                docs = self._group(docs, stage["$group"])
            elif "$project" in stage:
                docs = [_project(doc, stage["$project"]) for doc in docs]
            elif "$addFields" in stage:
                if docs:
                    raise AssertionError(
                        "passenger aggregation expressions were not needed for an "
                        "empty manifest; refusing to invent their results"
                    )
            else:
                raise NotImplementedError(stage)
        return docs

    @staticmethod
    def _group(docs, spec):
        grouped = {}
        order = []
        for doc in docs:
            group_id = _eval_expr(spec["_id"], doc)
            key = repr(group_id)
            if key not in grouped:
                grouped[key] = doc
                order.append(key)
        rows = []
        for key in order:
            first = grouped[key]
            row = {"_id": _eval_expr(spec["_id"], first)}
            for field, accumulator in spec.items():
                if field == "_id":
                    continue
                if not (isinstance(accumulator, dict) and "$first" in accumulator):
                    raise NotImplementedError(accumulator)
                row[field] = _eval_expr(accumulator, first)
            rows.append(row)
        return rows


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]

    def command(self, *args, **kwargs):
        return {"ok": 1}


class _StatusOnlySabre:
    """Sabre stand-in: flight status succeeds and every later call fails.

    get_passenger_list checks that flight status was already stored, which is
    the product order in runner.run_feeder. The four display-code calls are
    the ones run_feeder actually issues.
    """

    def __init__(self, db):
        self.db = db
        self.passenger_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        raw = {
            "ItineraryResponseList": {
                "ItineraryInfoResponse": {
                    "FlightStatus": "OPEN",
                    "DepartureGate": "A1",
                    "AircraftType": "32N",
                    "ScheduledDepartureDate": departure_date,
                }
            },
            "LegInfoList": {"LegInfo": [{"City": "DXB", "LegStatus": "OPEN"}]},
        }
        meta = {
            "requestXml": "<GetFlightStatus/>",
            "httpStatus": 200,
            "durationMs": 5,
        }
        return raw, "<GetFlightStatusRS/>", meta

    def get_passenger_list(self, *args, **kwargs):
        stored = self.db["flight_status"].docs
        if len(stored) != 1:
            raise AssertionError(
                "passenger-list calls ran before flight status was stored"
            )
        self.passenger_calls += 1
        raise SabreError("passenger list unavailable")

    def get_reservations(self, *args, **kwargs):
        raise SabreError("reservations unavailable")

    def get_trip_report(self, *args, **kwargs):
        raise SabreError("trip report unavailable")

    def verify_flight_details(self, *args, **kwargs):
        raise SabreError("schedule unavailable")


class StatusOnlyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        storage.init_db(self.db)
        flights_route._dashboard_cache.clear()
        self._original_get_db = flights_route.get_db
        flights_route.get_db = lambda: self.db
        self.client = TestClient(FastAPI())
        self.client.app.include_router(flights_route.router)

    def tearDown(self):
        flights_route.get_db = self._original_get_db
        flights_route._dashboard_cache.clear()
        storage.init_db(None)

    def test_product_dashboard_route_has_no_auth_dependency(self):
        included = [
            route
            for route in product_app.routes
            if getattr(route, "original_router", None) is flights_route.router
        ]
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0].include_context.dependencies, [])
        dashboard = next(
            route
            for route in flights_route.router.routes
            if isinstance(route, APIRoute)
            and route.path == "/flights/{flight_number}/dashboard"
        )
        self.assertEqual(dashboard.dependencies, [])
        self.assertEqual(dashboard.dependant.dependencies, [])

    def test_status_only_ingestion_is_listed_and_dashboard_flags_empty_manifest(self):
        sabre = _StatusOnlySabre(self.db)

        class _Client:
            def __enter__(self_inner):
                return sabre

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        original_client = runner.SabreClient
        runner.SabreClient = lambda: _Client()
        try:
            payload = runner.run_feeder([dict(STATUS_ONLY)])
        finally:
            runner.SabreClient = original_client

        self.assertEqual(payload["processedFlights"], 1)
        results = payload["results"]
        self.assertEqual(len(results), 1)
        apis = results[0]["apis"]
        self.assertEqual(apis["flightStatus"]["status"], "success")
        self.assertEqual(apis["passengerList"]["status"], "error")
        self.assertEqual(sabre.passenger_calls, 4)
        self.assertEqual(len(self.db["flight_status"].docs), 1)
        self.assertEqual(len(self.db["passenger_list"].docs), 0)
        stored = self.db["flight_status"].docs[0]
        self.assertEqual(stored["flightNumber"], "2057")
        self.assertEqual(stored["origin"], "BAH")
        self.assertNotIn("_id", stored.get("_raw", {}))

        listed = self.client.get("/flights")
        self.assertEqual(listed.status_code, 200)
        numbers = [item["flightNumber"] for item in listed.json()]
        self.assertIn("2057", numbers)

        response = self.client.get(
            "/flights/2057/dashboard",
            params={"origin": "BAH", "date": "2026-03-20"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("authorization", {key.lower() for key in response.request.headers})
        body = response.json()
        self.assertIsNotNone(body.get("flightStatus"))
        self.assertTrue(body.get("emptyManifest"), body)
        self.assertIsNone(body.get("analysis"), body)

    def test_manifest_flight_keeps_cabin_breakdown(self):
        self.db["flight_status"].insert_one({
            "airline": "GF",
            "flightNumber": "2006",
            "origin": "LHR",
            "departureDate": "2026-03-19",
            "fetchedAt": "2026-03-19T08:00:00Z",
            "status": "OPEN",
            "gate": "B4",
            "aircraft": {"type": "789"},
            "legs": [{"city": "BAH"}],
            "boarding": {"indicator": ""},
            "jumpSeat": {"cockpit": 0, "cabin": 0},
        })
        self.db["passenger_list"].insert_one({
            "airline": "GF",
            "flightNumber": "2006",
            "origin": "LHR",
            "departureDate": "2026-03-19",
            "fetchedAt": "2026-03-19T08:05:00Z",
            "destination": "BAH",
            "totalPassengers": 1,
            "adultCount": 1,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 1,
            "cabinSummary": [],
            "passengers": [{
                "cabin": "Y",
                "isRevenue": True,
                "passengerType": "F",
                "isChild": False,
                "hasInfant": False,
                "isCheckedIn": True,
                "isBoarded": False,
                "pnr": "ABC123",
                "lastName": "SMITH",
            }],
        })

        response = self.client.get(
            "/flights/2006/dashboard",
            params={"origin": "LHR", "date": "2026-03-19"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNot(body.get("emptyManifest"), True)
        economy = body["analysis"]["economy"]
        business = body["analysis"]["business"]
        self.assertIn("passengers", economy)
        self.assertIn("staff", economy)
        self.assertIn("passengers", business)


if __name__ == "__main__":
    unittest.main()
