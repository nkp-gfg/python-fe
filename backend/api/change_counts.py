"""Server-side aggregations over the `changes` collection."""


def count_change_types(db, match: dict) -> dict[str, int]:
    """Return {changeType: count} for changes matching `match`, grouped in MongoDB."""
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
    ]
    return {
        row["_id"]: row["count"]
        for row in db["changes"].aggregate(pipeline)
        if row.get("_id")
    }
