"""The stored-departure route refuses a date that is not a calendar day.

The analytics page builds this date by stringifying the data client's query
context. These checks hit the real list_flights route, including the query
string shape the browser produces for that value.
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import router  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(router)
    return app


def test_query_context_date_is_rejected_before_the_database():
    client = TestClient(_app())
    paths = [
        "/flights?date=[object%20Object]",
        "/flights?date=%5Bobject%20Object%5D",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 400, (path, response.status_code, response.text)
        assert response.json()["detail"] == "Invalid date: expected YYYY-MM-DD format"
    print("  PASS: test_query_context_date_is_rejected_before_the_database")


if __name__ == "__main__":
    test_query_context_date_is_rejected_before_the_database()
