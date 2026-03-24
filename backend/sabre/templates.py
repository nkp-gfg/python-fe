"""SOAP XML templates for Sabre APIs with {placeholder} variables."""

SESSION_CREATE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:xlink="http://www.w3.org/1999/xlink"
                  xmlns:xsd="http://www.w3.org/1999/XMLSchema">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="1" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>SessionCreateRQ</eb:Service>
            <eb:Action>SessionCreateRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                       xmlns:wsu="http://schemas.xmlsoap.org/ws/2002/12/utility">
            <wsse:UsernameToken>
                <wsse:Username>{username}</wsse:Username>
                <wsse:Password>{password}</wsse:Password>
                <Organization>{organization}</Organization>
                <Domain>{domain}</Domain>
            </wsse:UsernameToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <SessionCreateRQ xmlns="http://webservices.sabre.com" Version="1.0.0">
            <POS>
                <Source PseudoCityCode="{pseudo_city_code}"/>
            </POS>
        </SessionCreateRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

SESSION_CLOSE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="1" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>SessionCloseRQ</eb:Service>
            <eb:Action>SessionCloseRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="1">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <SessionCloseRQ xmlns="http://webservices.sabre.com" Version="1.0.0"/>
    </soapenv:Body>
</soapenv:Envelope>"""

FLIGHT_STATUS = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                  xmlns:v3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>ACS_FlightDetailRQ</eb:Service>
            <eb:Action>ACS_FlightDetailRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <v3:ACS_FlightDetailRQ Version="3.2.0">
            <FlightInfo>
                <Airline>{airline}</Airline>
                <Flight>{flight_number}</Flight>
                <DepartureDate>{departure_date}</DepartureDate>
                <Origin>{origin}</Origin>
            </FlightInfo>
            <Display><Type>R</Type></Display>
            <Client>WEB</Client>
        </v3:ACS_FlightDetailRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

PASSENGER_LIST = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                  xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>GetPassengerListRQ</eb:Service>
            <eb:Action>GetPassengerListRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <v4:GetPassengerListRQ version="4.0.0"
                               timeStamp="{timestamp}"
                               messageID="{message_id}"
                               serviceOption="Stateless">
            <v4:Itinerary>
                <v4:Airline>{airline}</v4:Airline>
                <v4:Flight>{flight_number}</v4:Flight>
                <v4:DepartureDate>{departure_date}</v4:DepartureDate>
                <v4:Origin>{origin}</v4:Origin>
            </v4:Itinerary>
            <v4:DisplayCodeRequest>
                <v4:DisplayCodes condition="OR">
                    <v4:DisplayCode>RV</v4:DisplayCode>
                    <v4:DisplayCode>XRV</v4:DisplayCode>
                    <v4:DisplayCode>BP</v4:DisplayCode>
                </v4:DisplayCodes>
                <v4:SortSequence>Name</v4:SortSequence>
            </v4:DisplayCodeRequest>
        </v4:GetPassengerListRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

RESERVATION = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>Trip_SearchRQ</eb:Service>
            <eb:Action>Trip_SearchRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <Trip_SearchRQ Version="4.5.0"
                       xmlns:ns2="http://webservices.sabre.com/sabreXML/2003/07"
                       xmlns="http://webservices.sabre.com/triprecord">
            <ReadRequests>
                <ReservationReadRequest>
                    <FlightCriteria>
                        <Flight>
                            <FlightNumber>{flight_number}</FlightNumber>
                            <DepartureAirport>{departure_airport}</DepartureAirport>
                            <DepartureDateTime Is="{departure_datetime}"/>
                            <AirlineCode>{airline}</AirlineCode>
                        </Flight>
                    </FlightCriteria>
                    <PosCriteria AirlineCode="{airline}"/>
                    <ReturnOptions ViewName="TripSearchBlob"
                                  ResponseFormat="STL"
                                  SearchType="ACTIVE"
                                  MaxItemsReturned="800">
                        <SubjectAreas>
                            <SubjectArea>HEADER</SubjectArea>
                            <SubjectArea>NAME</SubjectArea>
                            <SubjectArea>PASSENGERDETAILS</SubjectArea>
                            <SubjectArea>PRERESERVEDSEAT</SubjectArea>
                            <SubjectArea>TICKETS</SubjectArea>
                            <SubjectArea>ITINERARY</SubjectArea>
                            <SubjectArea>LOYALTY</SubjectArea>
                            <SubjectArea>FQTV</SubjectArea>
                            <SubjectArea>EXT_FQTV</SubjectArea>
                            <SubjectArea>SSR</SubjectArea>
                            <SubjectArea>OSI</SubjectArea>
                            <SubjectArea>REMARKS</SubjectArea>
                            <SubjectArea>ADDRESS</SubjectArea>
                            <SubjectArea>EMAIL</SubjectArea>
                            <SubjectArea>PHONE</SubjectArea>
                            <SubjectArea>GROUP</SubjectArea>
                            <SubjectArea>RECEIVED_FROM</SubjectArea>
                            <SubjectArea>ACCOUNTING</SubjectArea>
                            <SubjectArea>GENERAL_FACTS</SubjectArea>
                            <SubjectArea>HISTORY</SubjectArea>
                            <SubjectArea>PROFILE_INDEX</SubjectArea>
                            <SubjectArea>OPTION_QUEUE</SubjectArea>
                            <SubjectArea>QUEUE_PLACE</SubjectArea>
                            <SubjectArea>SERVICE_INFORMATION</SubjectArea>
                            <SubjectArea>DIVIDE</SubjectArea>
                            <SubjectArea>ASSOCIATED_CONTENT</SubjectArea>
                            <SubjectArea>ANCILLARY_SERVICES</SubjectArea>
                            <SubjectArea>DELIVERY_ADDRESS</SubjectArea>
                        </SubjectAreas>
                    </ReturnOptions>
                </ReservationReadRequest>
            </ReadRequests>
        </Trip_SearchRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

PASSENGER_DATA = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                  xmlns:v4="http://services.sabre.com/checkin/getPassengerData/v4">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>GetPassengerDataRQ</eb:Service>
            <eb:Action>GetPassengerDataRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <v4:GetPassengerDataRQ version="4.0.4"
                               validateCheckInRequirements="true"
                               includeTimaticInfo="true">
            <ItineraryAndPassengerInfo>
                <Itinerary>
                    <Airline>{airline}</Airline>
                    <Flight>{flight_number}</Flight>
                    <DepartureDate>{departure_date}</DepartureDate>
                    <Origin>{origin}</Origin>
                </Itinerary>
                <PassengerList>
                    <Passenger>
                        <LastName>{last_name}</LastName>
                        {first_name_element}
                        {pnr_element}
                    </Passenger>
                </PassengerList>
            </ItineraryAndPassengerInfo>
            <AncillaryInfoList>
                <ALLGroupCodes/>
            </AncillaryInfoList>
            <Client>WEB</Client>
        </v4:GetPassengerDataRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

TRIP_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>Trip_ReportsRQ</eb:Service>
            <eb:Action>Trip_ReportsRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <Trip_ReportsRQ Version="1.3.0"
                        xmlns="http://webservices.sabre.com/triprecord">
            <Report>
                <ReportName>{report_type}</ReportName>
                <ReportFormat>X</ReportFormat>
                <Criteria>
                    <FlightCriteria>
                        <AirlineCode>{airline}</AirlineCode>
                        <FlightNumber>{flight_number}</FlightNumber>
                        <DepartureAirport>{origin}</DepartureAirport>
                        <DepartureDateTime>{departure_date}</DepartureDateTime>
                    </FlightCriteria>
                    <PosCriteria>{airline}</PosCriteria>
                </Criteria>
            </Report>
        </Trip_ReportsRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

VERIFY_FLIGHT_DETAILS = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                  xmlns:v2="http://webservices.sabre.com/sabreXML/2011/10">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="0" eb:version="2.0.0">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>VerifyFlightDetailsLLSRQ</eb:Service>
            <eb:Action>VerifyFlightDetailsLLSRQ</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <v2:VerifyFlightDetailsRQ Version="2.0.1">
            <v2:OriginDestinationInformation>
                <v2:FlightSegment DepartureDateTime="{departure_datetime}">
                    <v2:DestinationLocation LocationCode="{destination}"/>
                    <v2:MarketingAirline Code="{airline}" FlightNumber="{flight_number}"/>
                    <v2:OriginLocation LocationCode="{origin}"/>
                </v2:FlightSegment>
            </v2:OriginDestinationInformation>
        </v2:VerifyFlightDetailsRQ>
    </soapenv:Body>
</soapenv:Envelope>"""

MULTI_FLIGHT = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
                  xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext"
                  xmlns:mf="http://www.atse.sabre.com/Availability/ws/Multiflight">
    <soapenv:Header>
        <eb:MessageHeader soapenv:mustUnderstand="{must_understand}" eb:version="{ebxml_version}">
            <eb:From><eb:PartyId/></eb:From>
            <eb:To><eb:PartyId/></eb:To>
            <eb:CPAId>{cpaid}</eb:CPAId>
            <eb:ConversationId>{conversation_id}</eb:ConversationId>
            <eb:Service>ASAAOperation</eb:Service>
            <eb:Action>ASAAOperation</eb:Action>
            <eb:MessageData>
                <eb:MessageId>{message_id}</eb:MessageId>
                <eb:Timestamp>{timestamp}</eb:Timestamp>
            </eb:MessageData>
        </eb:MessageHeader>
        <wsse:Security soapenv:mustUnderstand="0">
            <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <mf:MultiFlightRQ version="{version}">
            {origin_destinations_xml}
            {agent_info_xml}
            {point_of_commencement_xml}
            {associate_item_xml}
        </mf:MultiFlightRQ>
    </soapenv:Body>
</soapenv:Envelope>"""
