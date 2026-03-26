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

def _extract_flight_remarks(data):
    """Extract remarks / display data from ACS_FlightDetailRS."""
    remarks = []
    # DisplayData may contain Remarks when <Display><Type>R</Type></Display> is requested
    display_list = _ensure_list(data.get("DisplayData", []))
    for display in display_list:
        if isinstance(display, dict):
            text = display.get("Text", display.get("#text", ""))
            dtype = display.get("@type", display.get("Type", ""))
            if text:
                remarks.append({"type": dtype, "text": text})

    # Also check for top-level Remarks element (may be a wrapper or a list)
    remarks_raw = data.get("Remarks", data.get("RemarkList", {}))
    if isinstance(remarks_raw, dict):
        remark_list = _ensure_list(remarks_raw.get("Remark", []))
    else:
        remark_list = _ensure_list(remarks_raw)
    for rm in remark_list:
        if isinstance(rm, dict):
            remarks.append({
                "type": rm.get("@type", rm.get("Type", "")),
                "text": rm.get("Text", rm.get("#text", "")),
            })
        elif isinstance(rm, str):
            remarks.append({"type": "", "text": rm})

    return remarks


def convert_flight_status(raw_data, airline, flight_number, origin, departure_date=""):
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
        "departureDate": itin.get("ScheduledDepartureDate", "") or itin.get("DepartureDate", "") or departure_date,
        "fetchedAt": _now_iso(),
        "status": itin.get("FlightStatus", ""),
        "timeToDeparture": _safe_int(itin.get("TimeToDepartureInMinutes")),
        "aircraft": {
            "type": itin.get("AircraftType", ""),
            "registration": itin.get("AircraftRegistration", ""),
            "configNumber": itin.get("AircraftConfigNumber", ""),
            "seatConfig": itin.get("SeatConfig", ""),
        },
        "schedule": {
            "scheduledDeparture": f"{itin.get('ScheduledDepartureDate', '') or itin.get('DepartureDate', '')}T{itin.get('ScheduledDepartureTime', '') or itin.get('DepartureTime', '')}",
            "estimatedDeparture": f"{itin.get('EstimatedDepartureDate', '') or itin.get('DepartureDate', '')}T{itin.get('EstimatedDepartureTime', '') or itin.get('DepartureTime', '')}",
            "scheduledArrival": f"{itin.get('ScheduledArrivalDate', '') or itin.get('ArrivalDate', '')}T{itin.get('ScheduledArrivalTime', '') or itin.get('ArrivalTime', '')}",
            "estimatedArrival": f"{itin.get('EstimatedArrivalDate', '') or itin.get('ArrivalDate', '')}T{itin.get('EstimatedArrivalTime', '') or itin.get('ArrivalTime', '')}",
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
        "remarks": _extract_flight_remarks(data),
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
            "available": _safe_int(ci.get("Available")),
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
            "lineNumber": _safe_int(p.get("LineNumber")),
            "priorityCode": p.get("PriorityCode", ""),
            "bookingClass": p.get("BookingClass", ""),
            "desiredBookingClass": p.get("DesiredBookingClass", ""),
            "cabin": p.get("Cabin", ""),
            "seat": p.get("Seat", ""),
            "destination": p.get("Destination", ""),
            "passengerType": p.get("PassengerType", ""),
            "isStandby": p.get("PassengerType", "") == "S",
            "corpId": p.get("CorpID", ""),
            "seniorityDate": p.get("SeniorityDate", ""),
            "bagCount": _safe_int(p.get("BagCount")),
            "isCheckedIn": str(checkin_info.get("CheckInStatus", "false")).lower() == "true",
            "isBoarded": str(boarding_info.get("BoardStatus", "false")).lower() == "true",
            "boardingPassIssued": str(p.get("BoardingPassFlag", "false")).lower() == "true",
            "checkInSequence": _safe_int(checkin_info.get("CheckInNumber")),
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
            # Extract APIS DOCSEntry for gender/DOB/nationality
            special_requests = px.get("SpecialRequests", {})
            apis_request = special_requests.get("APISRequest", {})
            if isinstance(apis_request, list):
                apis_request = apis_request[0] if apis_request else {}
            docs_entries = _ensure_list(apis_request.get("DOCSEntry", []))
            gender = ""
            date_of_birth = ""
            nationality = ""
            for doc_entry in docs_entries:
                if isinstance(doc_entry, dict):
                    g = doc_entry.get("Gender", "")
                    if g:
                        gender = g
                        date_of_birth = doc_entry.get("DateOfBirth", "")
                        nationality = doc_entry.get(
                            "DocumentNationalityCountry", "")
                        break

            # Extract pre-reserved seat
            seats = px.get("Seats") or {}
            pre_reserved = _ensure_list(
                (seats.get("PreReservedSeats") or {}).get("PreReservedSeat", [])
            )
            seat_number = ""
            if pre_reserved and isinstance(pre_reserved[0], dict):
                seat_number = pre_reserved[0].get("SeatNumber", "")

            # Extract frequent flyer info
            loyalty_list = _ensure_list(px.get("FrequentFlyer", []))
            frequent_flyer = ""
            ff_airline = ""
            for ff in loyalty_list:
                if isinstance(ff, dict):
                    frequent_flyer = ff.get("FrequentFlyerNumber", "")
                    ff_airline = ff.get("AirlineCode", "")
                    break

            passengers.append({
                "lastName": px.get("LastName", ""),
                "firstName": px.get("FirstName", ""),
                "nameId": px.get("@nameId", ""),
                "nameType": px.get("@nameType", ""),
                "gender": gender,
                "dateOfBirth": date_of_birth,
                "nationality": nationality,
                "seatNumber": seat_number,
                "frequentFlyerNumber": frequent_flyer,
                "frequentFlyerAirline": ff_airline,
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

        # SSR — Special Service Requests (wheelchair, meal, medical, etc.)
        raw_ssrs = _ensure_list(
            (pax_res.get("SpecialServices") or {}).get("SpecialService", [])
        )
        ssr_requests = []
        for ssr in raw_ssrs:
            if isinstance(ssr, dict):
                ssr_requests.append({
                    "code": ssr.get("Code", ssr.get("@code", "")),
                    "text": ssr.get("Text", ssr.get("FreeText", "")),
                    "status": ssr.get("ActionCode", ssr.get("Status", "")),
                    "airline": ssr.get("AirlineCode", ""),
                    "type": ssr.get("@type", ""),
                })

        # Email addresses
        raw_emails = _ensure_list(
            (pax_res.get("EmailAddresses") or {}).get("EmailAddress", [])
        )
        emails = []
        for em in raw_emails:
            if isinstance(em, dict):
                emails.append(em.get("Address", em.get("#text", "")))
            elif isinstance(em, str):
                emails.append(em)

        # Phone numbers
        raw_phones = _ensure_list(
            (pax_res.get("PhoneNumbers") or {}).get("PhoneNumber", [])
        )
        phones = []
        for ph in raw_phones:
            if isinstance(ph, dict):
                phones.append({
                    "number": ph.get("Number", ph.get("#text", "")),
                    "type": ph.get("@type", ph.get("Type", "")),
                })
            elif isinstance(ph, str):
                phones.append({"number": ph, "type": ""})

        # Remarks
        raw_remarks = _ensure_list(
            (pax_res.get("Remarks") or {}).get("Remark", [])
        )
        remarks = []
        for rm in raw_remarks:
            if isinstance(rm, dict):
                remarks.append({
                    "type": rm.get("@type", rm.get("Type", "")),
                    "text": rm.get("Text", rm.get("RemarkText", rm.get("#text", ""))),
                })
            elif isinstance(rm, str):
                remarks.append({"type": "", "text": rm})

        # Ancillary services
        raw_ancillaries = _ensure_list(
            (pax_res.get("AncillaryServices") or {}).get("AncillaryService", [])
        )
        ancillary_services = []
        for anc in raw_ancillaries:
            if isinstance(anc, dict):
                ancillary_services.append({
                    "code": anc.get("CommercialName", anc.get("Code", "")),
                    "status": anc.get("StatusCode", anc.get("ActionCode", "")),
                    "quantity": _safe_int(anc.get("Quantity", 1)),
                    "emdNumber": anc.get("EMDNumber", ""),
                    "groupCode": anc.get("GroupCode", ""),
                    "subGroupCode": anc.get("SubGroupCode", ""),
                })

        # Received from (last agent who modified PNR)
        received_from = ""
        rf = inner.get("ReceivedFrom", pax_res.get("ReceivedFrom", {}))
        if isinstance(rf, dict):
            received_from = rf.get("Name", rf.get(
                "AgentName", rf.get("#text", "")))
        elif isinstance(rf, str):
            received_from = rf

        reservations.append({
            "pnr": pnr,
            "numberInParty": _safe_int(inner.get("@numberInParty")),
            "numberOfInfants": _safe_int(inner.get("@numberOfInfants")),
            "createdAt": booking.get("CreationTimestamp", ""),
            "updatedAt": booking.get("ModificationTimestamp", ""),
            "passengers": passengers,
            "segments": segments,
            "tickets": tickets,
            "ssrRequests": ssr_requests,
            "emails": emails,
            "phones": phones,
            "remarks": remarks,
            "ancillaryServices": ancillary_services,
            "receivedFrom": received_from,
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


# ── Trip Reports ───────────────────────────────────────────────────────

def convert_trip_report(raw_data, airline, flight_number, departure_date, origin, report_type):
    """Convert Trip_ReportsRS dict into a clean MongoDB document.

    Handles both MLX (cancelled passengers) and MLC (ever-booked passengers)
    report types. Both share a similar passenger list structure.
    """
    data = _strip_ns(raw_data)
    passenger_list = _ensure_list(
        data.get("PassengerList", {}).get("Passenger", [])
    )

    passengers = []
    for p in passenger_list:
        name = p.get("Name", {}) if isinstance(p.get("Name"), dict) else {}
        passengers.append({
            "lastName": name.get("LastName", p.get("LastName", "")),
            "firstName": name.get("FirstName", p.get("FirstName", "")),
            "pnr": p.get("PNRLocator", p.get("Locator", "")),
            "passengerType": p.get("PassengerType", ""),
            "cabin": p.get("Cabin", ""),
            "seat": p.get("Seat", ""),
            "bookingClass": p.get("BookingClass", ""),
        })

    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "reportType": report_type,
        "totalPassengers": len(passengers),
        "passengers": passengers,
        "_raw": raw_data,
    }
    return doc


# ── Passenger Data (per-passenger detail) ──────────────────────────────

def _parse_docs_string(docs_text):
    """Parse a DOCS free-text string into structured fields.

    Format: P/{docCountry}/{docNumber}/{nationality}/{DOB_ddMMMYY}/{gender}/{expiry_ddMMMYY}/{lastName}/{firstName}
    Example: P/US/123456789/US/01JAN70/F/01JAN20/ALPHA/PAX
    """
    if not docs_text or not docs_text.startswith("P/"):
        return {}
    parts = docs_text.split("/")
    if len(parts) < 9:
        return {}
    return {
        "documentType": parts[0],
        "documentCountry": parts[1],
        "documentNumber": parts[2],
        "nationality": parts[3],
        "dateOfBirth": parts[4],
        "gender": parts[5],
        "expiryDate": parts[6],
        "lastName": parts[7],
        "firstName": parts[8] if len(parts) > 8 else "",
    }


def convert_passenger_data(raw_data, airline, flight_number, departure_date, origin):
    """Convert GetPassengerDataRS dict into a clean document.

    This is a per-passenger detail response (not a flight-level list).
    Returns a document with one or more detailed passenger records.
    """
    data = _strip_ns(raw_data)

    status = data.get("@Status", "")
    completion = data.get("@CompletionStatus", "")

    # Error info
    errors = []
    error_info = data.get("ErrorInfo", {})
    if error_info:
        errors.append({
            "code": error_info.get("ErrorCode", ""),
            "message": error_info.get("ErrorMessage", ""),
        })

    pax_response_list = _ensure_list(
        (data.get("PassengerDataResponseList") or {}).get(
            "PassengerDataResponse", [])
    )

    passengers = []
    for pax in pax_response_list:
        line_number = _safe_int(pax.get("LineNumber"))
        last_name = pax.get("LastName", "")
        first_name = pax.get("FirstName", "")
        passenger_id = pax.get("PassengerID", "")

        # PNR
        pnr_raw = pax.get("PNRLocator", "")
        if isinstance(pnr_raw, dict):
            pnr = pnr_raw.get("#text", "")
            name_assoc_id = pnr_raw.get("@nameAssociationID", "")
        else:
            pnr = str(pnr_raw)
            name_assoc_id = ""

        group_code = pax.get("GroupCode", "")
        vcr_number = pax.get("VCRNumber", {})
        if isinstance(vcr_number, dict):
            vcr_text = vcr_number.get("#text", "")
            coupon_number = vcr_number.get("@couponNumber", "")
        else:
            vcr_text = str(vcr_number) if vcr_number else ""
            coupon_number = ""

        # Baggage routes
        baggage_routes = []
        for br in _ensure_list(
            (pax.get("BaggageRouteList") or {}).get("BaggageRoute", [])
        ):
            baggage_routes.append({
                "segmentId": br.get("SegmentID", ""),
                "airline": br.get("Airline", ""),
                "flight": br.get("Flight", ""),
                "operatingAirline": br.get("OperatingAirline", ""),
                "operatingFlight": br.get("OperatingFlight", ""),
                "origin": br.get("Origin", ""),
                "destination": br.get("Destination", ""),
                "departureDate": br.get("DepartureDate", ""),
                "departureTime": br.get("DepartureTime", ""),
                "arrivalDate": br.get("ArrivalDate", ""),
                "arrivalTime": br.get("ArrivalTime", ""),
                "bookingClass": br.get("BookingClass", ""),
                "segmentStatus": br.get("SegmentStatus", ""),
                "lateCheckin": br.get("LateCheckin", "false") == "true",
                "homePrintedBagTag": br.get("HomePrintedBagTag", ""),
                "bagEmbargo": br.get("BagEmbargo", "false") == "true",
            })

        # Passenger itinerary segments
        itinerary = []
        for seg in _ensure_list(
            (pax.get("PassengerItineraryList") or {}).get(
                "PassengerItinerary", [])
        ):
            # Edit codes for this segment
            seg_edit_codes = _ensure_list(
                (seg.get("EditCodeList") or {}).get("EditCode", [])
            )

            # Bag tag info
            bag_tags = []
            for bt in _ensure_list(
                (seg.get("BagTagInfoList") or {}).get("BagTagInfo", [])
            ):
                bag_tags.append({
                    "bagTagNumber": bt.get("BagTagNumber", ""),
                    "weight": bt.get("Weight", ""),
                    "unit": bt.get("Unit", ""),
                    "origin": bt.get("Origin", ""),
                    "destination": bt.get("Destination", ""),
                })

            # VCR info (fare basis, bag allowance)
            vcr_info_list = []
            for vi in _ensure_list(
                (seg.get("VCRInfoList") or {}).get("VCRInfo", [])
            ):
                vcr_info_list.append({
                    "fareBasisCode": vi.get("FareBasisCode", ""),
                    "bagAllowance": vi.get("BagAllowance", ""),
                })

            # Ancillary/EMD details
            ae_details = []
            for ae in _ensure_list(
                (seg.get("AEDetailsList") or {}).get("AEDetails", [])
            ):
                price = ae.get("PriceDetails", {})
                ae_details.append({
                    "itemId": ae.get("ItemID", ""),
                    "groupCode": ae.get("ATPCOGroupCode", ""),
                    "statusCode": ae.get("StatusCode", ""),
                    "usedEMD": ae.get("UsedEMD", ""),
                    "quantity": _safe_int(ae.get("Quantity")),
                    "price": price.get("Amount", "") if isinstance(price, dict) else "",
                    "currency": price.get("Currency", "") if isinstance(price, dict) else "",
                })

            # Required info for this segment
            required_info = []
            for ri in _ensure_list(
                (seg.get("RequiredInfoList") or {}).get("RequiredInfo", [])
            ):
                required_info.append({
                    "code": ri.get("Code", ""),
                    "detailStatus": ri.get("DetailStatus", ""),
                    "freeText": ri.get("FreeText", ""),
                })

            itinerary.append({
                "segmentId": seg.get("SegmentID", ""),
                "airline": seg.get("Airline", ""),
                "flight": seg.get("Flight", ""),
                "operatingAirline": seg.get("OperatingAirline", ""),
                "operatingFlight": seg.get("OperatingFlight", ""),
                "marketingAirline": seg.get("MarketingAirline", ""),
                "marketingFlight": seg.get("MarketingFlight", ""),
                "bookingClass": seg.get("BookingClass", ""),
                "marketingBookingClass": seg.get("MarketingBookingClass", ""),
                "operatingBookingClass": seg.get("OperatingBookingClass", ""),
                "origin": seg.get("Origin", ""),
                "destination": seg.get("Destination", ""),
                "departureDate": seg.get("DepartureDate", ""),
                "aircraftType": seg.get("AircraftType", ""),
                "cabin": seg.get("Cabin", ""),
                "seat": seg.get("Seat", ""),
                "passengerType": seg.get("PassengerType", ""),
                "priority": seg.get("Priority", ""),
                "bagCount": _safe_int(seg.get("BagCount")),
                "totalBagWeight": seg.get("TotalBagWeightAndUnit", ""),
                "checkInNumber": seg.get("CheckInNumber", ""),
                "editCodes": seg_edit_codes,
                "bagTags": bag_tags,
                "vcrInfo": vcr_info_list,
                "aeDetails": ae_details,
                "requiredInfo": required_info,
            })

        # Required info summary (check-in requirements)
        required_info_sum = []
        for ri in _ensure_list(
            (pax.get("RequiredInfoSumList") or {}).get("RequiredInfo", [])
        ):
            required_info_sum.append({
                "code": ri.get("Code", ""),
                "detailStatus": ri.get("DetailStatus", ""),
                "freeText": ri.get("FreeText", ""),
            })

        # Free-text info (DOCS, DOCO, PCTC, INF, BT, TIM, APP, AE, UK)
        free_text_entries = []
        gender_from_docs = ""
        docs_parsed = {}
        for ft in _ensure_list(
            (pax.get("FreeTextInfoList") or {}).get("FreeTextInfo", [])
        ):
            edit_code = ft.get("EditCode", "")
            text = ft.get("Text", "")
            free_text_entries.append({
                "editCode": edit_code,
                "text": text,
            })
            # Parse DOCS string for gender
            if edit_code == "DOCS" and text and not gender_from_docs:
                parsed = _parse_docs_string(text)
                if parsed.get("gender"):
                    gender_from_docs = parsed["gender"]
                    docs_parsed = parsed

        # Passenger edit details (TIM attributes etc.)
        passenger_edits = []
        for edit in _ensure_list(
            (pax.get("PassengerEditList") or {}).get("Edit", [])
        ):
            if isinstance(edit, dict):
                passenger_edits.append({
                    "name": edit.get("@name", ""),
                    "attributes": {
                        k.lstrip("@"): v for k, v in edit.items()
                        if k.startswith("@") and k != "@name"
                    },
                })

        # Timatic info
        timatic_info = []
        for ti in _ensure_list(
            (pax.get("TimaticInfoList") or {}).get("TimaticInfo", [])
        ):
            timatic_info.append({
                "country": ti.get("Country", ""),
                "text": ti.get("Text", "") if isinstance(ti.get("Text"), str) else str(ti.get("Text", "")),
            })

        passengers.append({
            "lineNumber": line_number,
            "lastName": last_name,
            "firstName": first_name,
            "passengerId": passenger_id,
            "pnr": pnr,
            "nameAssociationId": name_assoc_id,
            "groupCode": group_code,
            "vcrNumber": vcr_text,
            "couponNumber": coupon_number,
            "gender": gender_from_docs,
            "docsData": docs_parsed,
            "baggageRoutes": baggage_routes,
            "itinerary": itinerary,
            "requiredInfo": required_info_sum,
            "freeText": free_text_entries,
            "passengerEdits": passenger_edits,
            "timaticInfo": timatic_info,
        })

    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "status": status,
        "completionStatus": completion,
        "errors": errors,
        "totalPassengers": len(passengers),
        "passengers": passengers,
    }
    return doc


def merge_trip_reports(mlx_doc, mlc_doc, airline, flight_number, departure_date, origin):
    """Merge MLX and MLC reports into a single trip_reports document for storage.

    This combined document is what the dashboard queries:
    - cancelledPassengers (from MLX)
    - everBookedPassengers (from MLC)
    """
    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "cancelledPassengers": mlx_doc.get("passengers", []) if mlx_doc else [],
        "cancelledCount": mlx_doc.get("totalPassengers", 0) if mlx_doc else 0,
        "everBookedPassengers": mlc_doc.get("passengers", []) if mlc_doc else [],
        "everBookedCount": mlc_doc.get("totalPassengers", 0) if mlc_doc else 0,
    }
    return doc


# ── Schedule (VerifyFlightDetailsRS) ───────────────────────────────────────

def convert_schedule(raw_data, airline, flight_number, departure_date):
    """
    Convert VerifyFlightDetailsRS into a normalized schedule document
    for the flight_schedules collection.

    Returns a flat document with published timetable data:
    departure/arrival times, aircraft, duration, route, terminals, time zones.
    """
    data = _strip_ns(raw_data)

    # Check for errors
    app_results = data.get("ApplicationResults", {})
    status = app_results.get("@status", "")
    if status != "Complete":
        error_msg = ""
        err = app_results.get("Error", {})
        if isinstance(err, dict):
            sys_results = err.get("SystemSpecificResults", {})
            msg = sys_results.get("Message", {})
            if isinstance(msg, dict):
                error_msg = msg.get("#text", "")
            elif isinstance(msg, str):
                error_msg = msg
        return {
            "airline": airline,
            "flightNumber": flight_number,
            "departureDate": departure_date,
            "fetchedAt": _now_iso(),
            "success": False,
            "error": error_msg or f"Status: {status}",
            "segments": [],
            "_raw": raw_data,
        }

    # Extract segments from OriginDestinationOptions
    options = data.get("OriginDestinationOptions", {})
    od_option = options.get("OriginDestinationOption", {})
    if isinstance(od_option, list):
        od_option = od_option[0] if od_option else {}

    origin_tz = od_option.get("@OriginTimeZone", "")
    dest_tz = od_option.get("@DestinationTimeZone", "")

    raw_segments = _ensure_list(od_option.get("FlightSegment", []))
    segments = []
    for seg in raw_segments:
        dep_dt = seg.get("@DepartureDateTime", "")
        arr_dt = seg.get("@ArrivalDateTime", "")
        origin_loc = seg.get("OriginLocation", {})
        dest_loc = seg.get("DestinationLocation", {})
        equip = seg.get("Equipment", {})
        marketing = seg.get("MarketingAirline", {})
        meal = seg.get("Meal", {})

        segments.append({
            "departureDateTime": dep_dt,
            "arrivalDateTime": arr_dt,
            "origin": origin_loc.get("@LocationCode", ""),
            "originTerminal": origin_loc.get("@Terminal", ""),
            "destination": dest_loc.get("@LocationCode", ""),
            "destinationTerminal": dest_loc.get("@Terminal", ""),
            "aircraftType": equip.get("@AirEquipType", ""),
            "marketingAirline": marketing.get("@Code", ""),
            "flightNumber": marketing.get("@FlightNumber", ""),
            "airMilesFlown": _safe_int(seg.get("@AirMilesFlown")),
            "elapsedTime": seg.get("@ElapsedTime", ""),
            "accumulatedElapsedTime": seg.get("@AccumulatedElapsedTime", ""),
            "mealCode": meal.get("@Code", "") if isinstance(meal, dict) else "",
        })

    # Build the top-level summary from the first segment
    first = segments[0] if segments else {}
    doc = {
        "airline": airline,
        "flightNumber": flight_number,
        "departureDate": departure_date,
        "fetchedAt": _now_iso(),
        "success": True,
        "error": None,
        # Top-level summary (from first/only segment)
        "origin": first.get("origin", ""),
        "destination": first.get("destination", ""),
        "scheduledDeparture": first.get("departureDateTime", ""),
        "scheduledArrival": first.get("arrivalDateTime", ""),
        "aircraftType": first.get("aircraftType", ""),
        "elapsedTime": first.get("elapsedTime", ""),
        "airMilesFlown": first.get("airMilesFlown", 0),
        "originTerminal": first.get("originTerminal", ""),
        "destinationTerminal": first.get("destinationTerminal", ""),
        "originTimeZone": origin_tz,
        "destinationTimeZone": dest_tz,
        "mealCode": first.get("mealCode", ""),
        # Full segment list (for multi-leg flights)
        "segments": segments,
        "_raw": raw_data,
    }
    return doc
