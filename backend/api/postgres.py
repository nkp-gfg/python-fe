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
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        pool.putconn(conn)


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
