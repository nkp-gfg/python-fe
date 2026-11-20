"""Concurrent dashboard-cache tests.

These tests fail while successful flight-board responses share one unlocked
process-wide dictionary. Overlapping lookups and eviction raise KeyError or
RuntimeError, and the dashboard and tree routes turn that into HTTP 500.
They pass once lookup, insert, and eviction of that cache are serialized and
the size cap still applies.

Run:
    PYTHONPATH=. python -m pytest backend/tests/test_dashboard_cache_concurrency.py -q
"""

import asyncio
import os
import sys
import threading

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.main import app
from backend.api.routes import flights

ORIGIN = "BAH"
DATE = "2026-03-19"
WAIT_S = 2.0


class _Collection:
    """Minimal stand-in for the collection methods the dashboard calls."""

    def __init__(self, document):
        self._document = document

    def find_one(self, query, projection=None, sort=None):
        if self._document is None:
            return None
        return dict(self._document)

    def find(self, query, projection=None):
        return iter(())


class _ExistingFlightDatabase:
    """Every dashboard lookup finds a flight, as it would for stored flights."""

    def __getitem__(self, name):
        if name == "flight_status":
            return _Collection(
                {
                    "flightNumber": "2057",
                    "origin": ORIGIN,
                    "departureDate": DATE,
                    "status": "SCHEDULED",
                    "legs": [{"destination": "LHR"}],
                    "aircraft": {"type": "A320"},
                }
            )
        return _Collection(None)


def _install_database(monkeypatch):
    monkeypatch.setattr(flights, "get_db", lambda: _ExistingFlightDatabase())


def _cache_key(flight_number, origin=ORIGIN, date=DATE):
    return flights._get_cache_key(flight_number, origin, date, None)


def _load_dashboard(flight_number):
    # Pass snapshot_sequence explicitly. Calling the endpoint as a function
    # otherwise keeps the Query() default, which is truthy and skips the cache.
    return flights.get_flight_dashboard(
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None
    )


def _load_tree(flight_number):
    return flights.get_flight_tree(
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None
    )


def _seed_cached_flights(count):
    """Fill the cache through the real dashboard handler."""
    flights._dashboard_cache.clear()
    for offset in range(count):
        _load_dashboard(str(1000 + offset))
    return str(1000)


class _RacingCache(dict):
    """Dict that opens the real check-then-use windows, then lets the other thread in.

    Hooks sit on the operation that uses the key (iteration, delete, or read),
    after any earlier membership test. A second lookup before that operation
    still reaches the hook. A lock held across the operation stops the other
    thread from arriving, the wait times out, and the operation runs alone.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = None
        self.wait_s = WAIT_S
        self._gate = threading.Lock()
        self._scan_paused = False
        self.scan_entered = threading.Event()
        self.scan_release = threading.Event()
        self._delete_arrivals = 0
        self.delete_release = threading.Event()
        self._read_paused = False
        self.read_target = None
        self.read_entered = threading.Event()
        self.read_release = threading.Event()

    def arm_scan(self):
        self.mode = "scan"
        self._scan_paused = False
        self.scan_entered = threading.Event()
        self.scan_release = threading.Event()

    def arm_double_delete(self):
        self.mode = "double_delete"
        self._delete_arrivals = 0
        self.delete_release = threading.Event()

    def arm_read(self, target):
        self.mode = "read"
        self._read_paused = False
        self.read_target = target
        self.read_entered = threading.Event()
        self.read_release = threading.Event()

    def disarm(self):
        self.mode = None
        self.scan_entered.set()
        self.scan_release.set()
        self.delete_release.set()
        self.read_entered.set()
        self.read_release.set()

    def _called_from_get_cached(self):
        code = flights._get_cached.__code__
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code is code:
                return True
            frame = frame.f_back
        return False

    def __iter__(self):
        pause = False
        follow = False
        if self.mode == "scan":
            with self._gate:
                if not self._scan_paused:
                    self._scan_paused = True
                    pause = True
                else:
                    follow = True
        if follow:
            self.scan_entered.wait(self.wait_s)
        iterator = super().__iter__()
        try:
            first = next(iterator)
        except StopIteration:
            return
        yield first
        if pause:
            self.scan_entered.set()
            self.scan_release.wait(self.wait_s)
        yield from iterator

    def __delitem__(self, key):
        if self.mode == "double_delete":
            with self._gate:
                self._delete_arrivals += 1
                if self._delete_arrivals >= 2:
                    self.delete_release.set()
            self.delete_release.wait(self.wait_s)
        super().__delitem__(key)
        if self.mode == "scan":
            self.scan_release.set()
        if self.mode == "read" and key == self.read_target:
            self.read_release.set()

    def __getitem__(self, key):
        if (
            self.mode == "read"
            and key == self.read_target
            and self._called_from_get_cached()
        ):
            with self._gate:
                pause = not self._read_paused
                self._read_paused = True
            if pause:
                self.read_entered.set()
                self.read_release.wait(self.wait_s)
        return super().__getitem__(key)


def _arm(cache):
    flights._dashboard_cache = cache
    return cache


def _run_together(calls):
    errors = []
    barrier = threading.Barrier(len(calls))

    def invoke(fn):
        barrier.wait()
        try:
            fn()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=invoke, args=(fn,)) for fn in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def _stamp_monotonic_ages(cache):
    for index, key in enumerate(list(cache)):
        data, _timestamp = cache[key]
        cache[key] = (data, float(index))
    return min(cache, key=lambda key: cache[key][1])


async def _get(client, flight_number, route):
    response = await client.get(
        f"/flights/{flight_number}/{route}",
        params={"origin": ORIGIN, "date": DATE},
        headers={},
    )
    return response


async def _racing_http(
    first_route, first_flight, second_route, second_flight, entered_event
):
    # ServerErrorMiddleware sends the 500 response and then re-raises so servers
    # can log it. Keep the response instead of surfacing that re-raise.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=15.0
    ) as client:
        first_task = asyncio.create_task(_get(client, first_flight, first_route))
        entered = await asyncio.to_thread(entered_event.wait, 5)
        second = await _get(client, second_flight, second_route)
        first = await first_task
        health = await client.get("/health/app")
    return entered, first, second, health


def _assert_board_stays_up(entered, responses, health):
    assert entered, "request never reached the shared dashboard cache"
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "healthy"
    details = [(response.status_code, response.text) for response in responses]
    assert all(response.status_code == 200 for response in responses), details


def test_unauthenticated_dashboard_caches_an_existing_flight(monkeypatch):
    """A caller with no credentials can cache a flight the database already has."""
    _install_database(monkeypatch)
    flights._dashboard_cache.clear()
    # ServerErrorMiddleware sends the 500 response and then re-raises so servers
    # can log it. Keep the response instead of surfacing that re-raise.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def request_twice():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=15.0
        ) as client:
            first = await _get(client, "2057", "dashboard")
            second = await _get(client, "2057", "dashboard")
            return first, second

    first, second = asyncio.run(request_twice())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert "passengerSummary" in first.json()
    assert _cache_key("2057") in flights._dashboard_cache


def test_eviction_scan_survives_a_concurrent_delete(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    cache.arm_scan()
    errors = []

    def peer():
        assert cache.scan_entered.wait(5), "eviction scan never started"
        try:
            flights._set_cache(_cache_key("3001"), {"peer": True})
        except Exception as exc:
            errors.append(exc)

    peer_thread = threading.Thread(target=peer)
    peer_thread.start()
    try:
        flights._set_cache(_cache_key("3000"), {"main": True})
    except Exception as exc:
        errors.append(exc)
    finally:
        cache.disarm()
        peer_thread.join()
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)


def test_eviction_of_the_same_oldest_key_survives_overlap(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    _stamp_monotonic_ages(cache)
    cache.arm_double_delete()
    try:
        errors = _run_together(
            [
                lambda: flights._set_cache(_cache_key("3000"), {"n": 0}),
                lambda: flights._set_cache(_cache_key("3001"), {"n": 1}),
            ]
        )
    finally:
        cache.disarm()
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)


def test_expired_entry_read_survives_overlap(monkeypatch):
    _install_database(monkeypatch)
    flights._dashboard_cache.clear()
    _load_dashboard("2057")
    key = _cache_key("2057")
    data, _timestamp = flights._dashboard_cache[key]
    flights._dashboard_cache[key] = (data, 0.0)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    cache.arm_double_delete()
    try:
        errors = _run_together(
            [
                lambda: flights._get_cached(key),
                lambda: flights._get_cached(key),
            ]
        )
    finally:
        cache.disarm()
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)


def test_cache_hit_survives_eviction_of_that_entry(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    target = _stamp_monotonic_ages(cache)
    cache.arm_read(target)
    errors = []

    def peer():
        assert cache.read_entered.wait(5), "cache hit never read the entry"
        try:
            flights._set_cache(_cache_key("3001"), {"peer": True})
        except Exception as exc:
            errors.append(exc)

    peer_thread = threading.Thread(target=peer)
    peer_thread.start()
    try:
        flights._get_cached(target)
    except Exception as exc:
        errors.append(exc)
    finally:
        cache.disarm()
        peer_thread.join()
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)


def test_tree_cache_hit_survives_eviction_of_that_entry(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    target = _stamp_monotonic_ages(cache)
    cache.arm_read(target)
    flight_number = target.split(":", 1)[0]
    errors = []

    def peer():
        assert cache.read_entered.wait(5), "tree read never hit the cache"
        try:
            flights._set_cache(_cache_key("3001"), {"peer": True})
        except Exception as exc:
            errors.append(exc)

    peer_thread = threading.Thread(target=peer)
    peer_thread.start()
    try:
        _load_tree(flight_number)
    except Exception as exc:
        errors.append(exc)
    finally:
        cache.disarm()
        peer_thread.join()
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)


def test_overlapping_dashboard_eviction_returns_500_and_process_stays_up(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    cache.arm_scan()
    try:
        entered, first, second, health = asyncio.run(
            _racing_http(
                "dashboard", "3000", "dashboard", "3001", cache.scan_entered
            )
        )
    finally:
        cache.disarm()
    _assert_board_stays_up(entered, [first, second], health)


def test_overlapping_dashboard_hit_during_eviction_returns_500(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    target = _stamp_monotonic_ages(cache)
    cache.arm_read(target)
    flight_number = target.split(":", 1)[0]
    try:
        entered, first, second, health = asyncio.run(
            _racing_http(
                "dashboard",
                flight_number,
                "dashboard",
                "3001",
                cache.read_entered,
            )
        )
    finally:
        cache.disarm()
    _assert_board_stays_up(entered, [first, second], health)


def test_overlapping_tree_hit_during_eviction_returns_500(monkeypatch):
    _install_database(monkeypatch)
    _seed_cached_flights(101)
    cache = _arm(_RacingCache(flights._dashboard_cache))
    target = _stamp_monotonic_ages(cache)
    cache.arm_read(target)
    flight_number = target.split(":", 1)[0]
    try:
        entered, first, second, health = asyncio.run(
            _racing_http(
                "tree",
                flight_number,
                "dashboard",
                "3001",
                cache.read_entered,
            )
        )
    finally:
        cache.disarm()
    _assert_board_stays_up(entered, [first, second], health)


def test_overlapping_expired_dashboard_and_tree_reads_return_500(monkeypatch):
    _install_database(monkeypatch)
    flights._dashboard_cache.clear()
    _load_dashboard("2057")
    key = _cache_key("2057")
    data, _timestamp = flights._dashboard_cache[key]
    cache = _arm(_RacingCache({key: (data, 0.0)}))
    cache.arm_double_delete()
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async def both():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=15.0
            ) as client:
                dashboard, tree = await asyncio.gather(
                    _get(client, "2057", "dashboard"),
                    _get(client, "2057", "tree"),
                )
                health = await client.get("/health/app")
                return dashboard, tree, health

        dashboard, tree, health = asyncio.run(both())
    finally:
        cache.disarm()
    _assert_board_stays_up(True, [dashboard, tree], health)


def test_plain_dict_overlapping_eviction_does_not_raise(monkeypatch):
    """Same eviction on the real dict, with the interpreter free to switch threads."""
    _install_database(monkeypatch)
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    errors = []
    try:
        for round_index in range(3):
            flights._dashboard_cache.clear()
            for index in range(101):
                flights._dashboard_cache[f"seed-{index}"] = ({"i": index}, float(index))
            errors.extend(
                _run_together(
                    [
                        (lambda n=n, round_index=round_index: flights._set_cache(
                            f"new-{round_index}-{n}", {"n": n}
                        ))
                        for n in range(48)
                    ]
                )
            )
            if errors:
                break
    finally:
        sys.setswitchinterval(previous)
    assert not errors, "\n".join(f"{type(exc).__name__}: {exc}" for exc in errors)
