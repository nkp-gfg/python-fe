# Sabre Web Services Collection

Bruno API collection for Sabre Web Services integration.

## Prerequisites

- [Bruno](https://www.usebruno.com/) - API client for testing

## Setup

1. Clone this repository
2. Open the `sabre-webservices` folder in Bruno
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```
   SABRE_USERNAME=your_username
   SABRE_PASSWORD=your_password
   ```
4. Select the **Production** environment in Bruno
5. Run requests!

## Collection Structure

```
sabre-webservices/
├── bruno.json              # Bruno collection config
├── collection.bru          # Collection settings
├── .env.example            # Template for local Sabre credentials
├── .env                    # Local credentials for Bruno (gitignored)
├── .gitignore              # Prevents local secrets from being committed
├── environments/
│   └── Production.bru      # Production environment (airline.webservices...)
├── Create Session.bru      # 1. Authenticate and get session token
├── Get Reservation.bru     # 2. Trip_SearchRQ by flight criteria
├── Get Passenger List.bru  # 3. GetPassengerListRQ for a flight manifest
├── Get Passenger Data.bru  # 4. GetPassengerDataRQ for per-passenger detail
├── Flight Status.bru       # 5. ACS_FlightDetailRQ for flight status
└── Close Session.bru       # 6. Close the session
```

## Workflow

1. **Create Session** - Authenticates with Sabre and saves the `sessionToken`
   - Generates fresh `ConversationId`, `MessageId`, and `Timestamp` each run
   - Each team member gets their own independent session
2. **Use APIs** - Run any of the following (all use the saved token):
   - Flight Status
   - Get Reservation
   - Get Passenger List
3. **Close Session** - Closes the authenticated session (optional - sessions timeout after ~15 min)

## Implemented Sabre Operations

- `SessionCreateRQ` — implemented in [backend/sabre/client.py](backend/sabre/client.py#L115)
- `SessionCloseRQ` — implemented in [backend/sabre/client.py](backend/sabre/client.py#L141)
- `ACS_FlightDetailRQ` — implemented in [backend/sabre/client.py](backend/sabre/client.py#L156)
- `GetPassengerListRQ` — implemented in [backend/sabre/client.py](backend/sabre/client.py#L183)
- `Trip_SearchRQ` — implemented in [backend/sabre/client.py](backend/sabre/client.py#L210)

These Bruno files are aligned to the SOAP request templates in [backend/sabre/templates.py](backend/sabre/templates.py).

## Environment Variables

| Variable         | Description                                 |
| ---------------- | ------------------------------------------- |
| `baseUrl`        | Sabre API base URL                          |
| `cpaid`          | CPA ID (airline code)                       |
| `pseudoCityCode` | Pseudo city code                            |
| `organization`   | Organization code                           |
| `domain`         | Domain code                                 |
| `sessionToken`   | Auto-populated after Create Session         |
| `conversationId` | Auto-generated UUID for each session        |
| `messageId`      | Auto-generated per request in Bruno scripts |
| `timestamp`      | Auto-generated per request in Bruno scripts |

## Credentials (via .env file)

| Variable         | Description            |
| ---------------- | ---------------------- |
| `SABRE_USERNAME` | Sabre account username |
| `SABRE_PASSWORD` | Sabre account password |

Each team member creates their own `.env` file with their credentials. This file is gitignored so credentials are never committed.

## Security

- Store Sabre credentials only in your local environment or Bruno secrets
- Never commit credentials to version control
- `sessionToken` is generated dynamically and cleared by `Close Session.bru`
- Each colleague should use their own local credentials

## License

Private - Internal use only
