"""MongoDB connection for the FastAPI layer (read-only queries)."""

import os
from pymongo import MongoClient

DB_NAME = "falconeye"

_client = None
_db = None


def get_db():
    """Return the falconeye database, creating the connection on first call."""
    global _client, _db
    if _db is None:
        uri = os.environ["MONGODB_URI"]
        _client = MongoClient(
            uri,
            serverSelectionTimeoutMS=10_000,
            connectTimeoutMS=10_000,
        )
        _db = _client[DB_NAME]
    return _db


def close_db():
    """Close the MongoDB connection."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
