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
        <eb:MessageHeader soapenv:mustUnderstand="0">
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
                <Origin>{origin}</Origin>
            </FlightInfo>
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
                               messageID="MSG-000001"
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
                                  SearchType="ACTIVE">
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
                        </SubjectAreas>
                    </ReturnOptions>
                </ReservationReadRequest>
            </ReadRequests>
        </Trip_SearchRQ>
    </soapenv:Body>
</soapenv:Envelope>"""
