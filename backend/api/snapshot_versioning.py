"""Helpers for loading immutable snapshot data for historical views."""


def get_snapshot_data_as_of(
    db,
    *,
    flight_number: str,
    snapshot_type: str,
    snapshot_sequence: int,
    origin: str | None = None,
    departure_date: str | None = None,
):
    """Return snapshot.data for the latest sequence <= snapshot_sequence."""
    query = {
        "flightNumber": flight_number,
        "snapshotType": snapshot_type,
        "sequenceNumber": {"$lte": snapshot_sequence},
    }
    if origin:
        query["origin"] = origin
    if departure_date:
        query["departureDate"] = departure_date

    snap = db["snapshots"].find_one(query, sort=[("sequenceNumber", -1)])
    if not snap:
        return None

    data = snap.get("data")
    if isinstance(data, dict):
        data = dict(data)
        data["snapshotSequenceNumber"] = snap.get("sequenceNumber")
        data["snapshotCapturedAt"] = snap.get("capturedAt")
    return data
