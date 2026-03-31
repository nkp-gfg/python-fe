"""PostgreSQL connection pool for the falcon_eye OTP database."""

import os
import logging

import psycopg2
import psycopg2.pool
import psycopg2.extras

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        uri = os.environ.get("POSTGRES_URI")
        if not uri:
            raise RuntimeError("POSTGRES_URI environment variable is not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=uri,
        )
        logger.info("PostgreSQL connection pool created")
    return _pool


def query_all(sql: str, params: tuple | None = None) -> list[dict]:
    """Execute a read-only query and return rows as list of dicts."""
    global _pool
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            pool = _get_pool()
            conn = pool.getconn()
        except psycopg2.OperationalError as exc:
            logger.warning(
                "Failed to get connection from pool: %s – recreating pool", exc)
            last_err = exc
            _close_pool_quiet()
            continue
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        except psycopg2.OperationalError as exc:
            logger.warning("Connection lost during query: %s – retrying", exc)
            last_err = exc
            pool.putconn(conn, close=True)
            _close_pool_quiet()
            continue
        finally:
            if last_err is None:
                pool.putconn(conn)
    raise last_err  # type: ignore[misc]


def _close_pool_quiet():
    """Silently close and discard the current pool."""
    global _pool
    try:
        if _pool and not _pool.closed:
            _pool.closeall()
    except Exception:
        pass
    _pool = None


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
