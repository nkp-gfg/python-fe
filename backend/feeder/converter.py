"""
Convert Sabre XML/dict responses into clean, normalized documents for MongoDB.

Handles:
- Stripping namespace prefixes (stl19:, ns2:, ns3:, etc.)
- Normalizing single items to lists (passengers, reservations, segments)
- Extracting key fields into a flat metadata header
- Preserving original data under _raw
"""

import re
from datetime import datetime, timezone


def _strip_ns(obj):
    """Recursively strip namespace prefixes from dict keys."""
    if isinstance(obj, dict):
        return {re.sub(r"^[a-zA-Z0-9]+:", "", k): _strip_ns(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_ns(item) for item in obj]
    return obj


def _ensure_list(val):
    """Wrap a single dict in a list; return lists as-is; return [] for None."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Flight Status ──────────────────────────────────────────────────────────

def convert_flight_status(raw_data, airline, flight_number, origin):
    """Convert ACS_FlightDetailRS dict into a clean MongoDB document."""
    data = _strip_ns(raw_data)
    itin = data.get("ItineraryResponseList", {}).get(
        "ItineraryInfoResponse", {})
    legs = _ensure_list(
        data.get("LegInfoList", {}).get("LegInfo", [])
    )
    counts_raw = _ensure_list(data.get("PassengerCounts", []))
    jump = data.get("JumpSeat", {})

    # Build passenger counts per class
    pax_counts = {}
    for c in counts_raw:
        cls = c.get("@classOfService", "?")
        pax_counts[cls] = {
            "authorized": _safe_int(c.get("Authorized")),
            "booked": _safe_int(c.get("Booked")),
            "available": _safe_int(c.get("Available")),
            "thru": _safe_int(c.get("Thru")),
            "local": _safe_int(c.get("Local")),
            "onBoard": _safe_int(c.get("TotalOnBoard")),
            "boardingPasses": _safe_int(c.get("TotalBoardingPassIssued")),
            "meals": _safe_int(c.get("Meals")),
            "revenue": _safe_int(c.get("Local")) - _safe_int(c.get("NonRevenueLocal")),
            "nonRevenue": _safe_int(c.get("NonRevenueLocal")),
        }

    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": itin.get("ScheduledDepartureDate", ""),
        "fetchedAt": _now_iso(),
        "status": itin.get("FlightStatus", ""),
        "aircraft": {
            "type": itin.get("AircraftType", ""),
            "registration": itin.get("AircraftRegistration", ""),
            "configNumber": itin.get("AircraftConfigNumber", ""),
            "seatConfig": itin.get("SeatConfig", ""),
        },
        "schedule": {
            "scheduledDeparture": f"{itin.get('ScheduledDepartureDate', '')}T{itin.get('ScheduledDepartureTime', '')}",
            "estimatedDeparture": f"{itin.get('EstimatedDepartureDate', '')}T{itin.get('EstimatedDepartureTime', '')}",
            "scheduledArrival": f"{itin.get('ScheduledArrivalDate', '')}T{itin.get('ScheduledArrivalTime', '')}",
            "estimatedArrival": f"{itin.get('EstimatedArrivalDate', '')}T{itin.get('EstimatedArrivalTime', '')}",
            "durationMinutes": _safe_int(itin.get("FlightDurationInMinutes")),
        },
        "gate": itin.get("DepartureGate", ""),
        "terminal": itin.get("DepartureTerminal", ""),
        "boarding": {
            "time": itin.get("BoardingTime", ""),
            "indicator": (itin.get("BoardingTime") or {}).get("@boardingIndicator", "") if isinstance(itin.get("BoardingTime"), dict) else "",
        },
        "legs": [
            {
                "city": leg.get("City", ""),
                "controllingCity": leg.get("@controllingCity", "") == "true",
                "status": leg.get("LegStatus", ""),
            }
            for leg in legs
        ],
        "passengerCounts": pax_counts,
        "jumpSeat": {
            "cockpit": _safe_int(jump.get("Cockpit")),
            "cabin": _safe_int(jump.get("Cabin")),
            "cockpitInUse": jump.get("@cockpitInUse", "false") == "true",
            "cabinInUse": jump.get("@cabinInUse", "false") == "true",
        },
        "_raw": raw_data,
    }
    return doc


# ── Passenger List ─────────────────────────────────────────────────────────

def convert_passenger_list(raw_data, airline, flight_number, departure_date, origin):
    """Convert GetPassengerListRS dict into a clean MongoDB document."""
    data = _strip_ns(raw_data)
    itin_info = data.get("ItineraryInfo", {})
    itin = itin_info.get("Itinerary", {})
    pax_list = _ensure_list(
        data.get("PassengerInfoList", {}).get("PassengerInfo", [])
    )

    # Cabin summary
    cabin_info_list = _ensure_list(
        itin_info.get("CabinInfoList", {}).get("CabinInfo", [])
    )
    cabin_summary = [
        {
            "cabin": ci.get("Cabin", ""),
            "count": _safe_int(ci.get("Count")),
            "authorized": _safe_int(ci.get("Authorized")),
            "destination": ci.get("Destination", ""),
        }
        for ci in cabin_info_list
    ]

    # Passengers
    passengers = []
    infant_count = 0
    child_count = 0
    adult_count = 0

    for p in pax_list:
        name = p.get("Name_Details", {})
        indicators = _ensure_list(
            (p.get("Indicators") or {}).get("Indicator", [])
        )
        checkin_info = p.get("CheckIn_Info", {})
        boarding_info = p.get("Boarding_Info", {})
        vcr = p.get("VCR_Info", {}).get("VCR_Data", {})
        edit_codes = _ensure_list(
            (p.get("EditCodeList") or {}).get("EditCode", [])
        )

        # Classify passenger: CHD edit code = child, INF = parent with infant
        is_child = "CHD" in edit_codes
        has_infant = "INF" in edit_codes

        if is_child:
            child_count += 1
        else:
            adult_count += 1
        if has_infant:
            infant_count += 1

        passengers.append({
            "lastName": name.get("LastName", ""),
            "firstName": name.get("FirstName", ""),
            "pnr": (p.get("PNRLocator") or {}).get("#text", "") if isinstance(p.get("PNRLocator"), dict) else str(p.get("PNRLocator", "")),
            "passengerId": p.get("PassengerID", ""),
            "bookingClass": p.get("BookingClass", ""),
            "cabin": p.get("Cabin", ""),
            "seat": p.get("Seat", ""),
            "destination": p.get("Destination", ""),
            "passengerType": p.get("PassengerType", ""),
            "bagCount": _safe_int(p.get("BagCount")),
            "isCheckedIn": str(checkin_info.get("CheckInStatus", "false")).lower() == "true",
            "isBoarded": str(boarding_info.get("BoardStatus", "false")).lower() == "true",
            "isRevenue": "Revenue" in indicators,
            "isThru": str(p.get("ThruIndicator", "false")).lower() == "true",
            "isChild": is_child,
            "hasInfant": has_infant,
            "vcrType": vcr.get("@type", "") if isinstance(vcr, dict) else "",
            "ticketNumber": vcr.get("SerialNumber", "") if isinstance(vcr, dict) else "",
            "editCodes": edit_codes,
        })

    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "destination": itin.get("Destination", ""),
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "aircraftType": itin.get("AircraftType", ""),
        "cabinSummary": cabin_summary,
        "totalPassengers": len(passengers),
        "adultCount": adult_count,
        "childCount": child_count,
        "infantCount": infant_count,
        "totalSouls": len(passengers) + infant_count,
        "passengers": passengers,
        "_raw": raw_data,
    }
    return doc


# ── Reservations ───────────────────────────────────────────────────────────

def convert_reservations(raw_data, airline, flight_number, departure_airport, departure_date):
    """Convert Trip_SearchRS dict into a clean MongoDB document."""
    data = _strip_ns(raw_data)
    res_list_wrapper = data.get("ReservationsList", {})
    total = _safe_int(res_list_wrapper.get("@NumberResults",
                      res_list_wrapper.get("@TotalResults", 0)))
    raw_reservations = _ensure_list(
        (res_list_wrapper.get("Reservations") or {}).get("Reservation", [])
    )

    reservations = []
    for r in raw_reservations:
        pnr = r.get("@Locator", "")
        # The inner record may be under GetReservationRS
        inner = r.get("GetReservationRS", {}).get("Reservation", {})

        booking = inner.get("BookingDetails", {})
        pax_res = inner.get("PassengerReservation", {})

        # Passengers
        raw_passengers = _ensure_list(
            (pax_res.get("Passengers") or {}).get("Passenger", [])
        )
        passengers = []
        for px in raw_passengers:
            passengers.append({
                "lastName": px.get("LastName", ""),
                "firstName": px.get("FirstName", ""),
                "nameId": px.get("@nameId", ""),
                "nameType": px.get("@nameType", ""),
            })

        # Segments
        raw_segments = _ensure_list(
            (pax_res.get("Segments") or {}).get("Segment", [])
        )
        segments = []
        for seg in raw_segments:
            air = seg.get("Air", {})
            if air:
                segments.append({
                    "departureAirport": air.get("DepartureAirport", ""),
                    "arrivalAirport": air.get("ArrivalAirport", ""),
                    "departureDate": air.get("DepartureDateTime", ""),
                    "arrivalDate": air.get("ArrivalDateTime", ""),
                    "marketingAirline": air.get("MarketingAirlineCode", ""),
                    "flightNumber": air.get("FlightNumber", ""),
                    "bookingClass": air.get("ClassOfService", ""),
                    "status": air.get("ActionCode", ""),
                })

        # Tickets
        raw_tickets = _ensure_list(
            (pax_res.get("TicketingInfo") or {}).get("Ticketing", [])
        )
        tickets = []
        for tkt in raw_tickets:
            if isinstance(tkt, dict):
                tickets.append({
                    "ticketNumber": tkt.get("@RPH", ""),
                    "eTicket": tkt.get("@eTicketNumber", ""),
                })

        reservations.append({
            "pnr": pnr,
            "numberInParty": _safe_int(inner.get("@numberInParty")),
            "numberOfInfants": _safe_int(inner.get("@numberOfInfants")),
            "createdAt": booking.get("CreationTimestamp", ""),
            "updatedAt": booking.get("ModificationTimestamp", ""),
            "passengers": passengers,
            "segments": segments,
            "tickets": tickets,
        })

    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "departureAirport": departure_airport,
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "totalResults": total,
        "reservations": reservations,
        "_raw": raw_data,
    }
    return doc
