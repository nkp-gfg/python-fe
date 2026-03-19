# Data Preservation Strategy

## Principle: Never Lose Data

Every byte from Sabre is preserved. We may transform it, normalize it, index it — but the raw original is always kept unchanged in an append-only collection.

## Three-Layer Architecture

### Layer 1: Raw Archive (sabre_requests)

- **What**: Complete raw XML request + response for every API call
- **Why**: If our parser misses a field, we can re-parse from raw XML later
- **How**: Store as plain strings, no transformation
- **Retention**: Forever (data warehouse source of truth)

### Layer 2: Parsed Snapshots (snapshots)

- **What**: Normalized JSON extracted from the XML
- **Why**: Fast querying, indexing, and comparison
- **How**: Parse on ingest, store as structured documents
- **Retention**: Forever (queryable layer)

### Layer 3: Computed Changes (changes)

- **What**: Diffs between consecutive snapshots
- **Why**: Audit trail, event stream, analytics
- **How**: Computed when new snapshot arrives, stored for fast access
- **Retention**: Forever (can be recomputed from Layer 2)

## What NOT to Do

- ❌ Don't update documents in place (overwriting previous values)
- ❌ Don't strip namespace prefixes before storing raw XML
- ❌ Don't discard SOAP envelope — store the complete response
- ❌ Don't throw away "empty" or "duplicate" responses
- ❌ Don't skip storing a snapshot if it looks the same (store it, mark as no-change)

## Data Flow

```
Sabre API Call
    │
    ├──► sabre_requests.insert({raw_xml, raw_response, timestamp})
    │
    ├──► Parse XML → normalized JSON
    │
    ├──► snapshots.insert({normalized_data, checksum, sequence})
    │
    ├──► Load previous snapshot (same flight + type)
    │
    ├──► If checksums differ → run diff algorithm
    │       │
    │       └──► changes.insert_many([...detected changes...])
    │
    └──► Update flights collection (current state)
```

## Namespace Handling

Sabre XML uses multiple namespace prefixes:

- `soap-env:` — SOAP envelope elements
- `ns3:`, `ns4:`, etc. — API-specific elements (numbers vary between calls!)
- `ns2:` — Often used for cabin info, edit codes
- `stl19:` — Sabre STL framework elements

**Strategy**:

1. Store raw XML with all namespaces intact (Layer 1)
2. In the parser, use a namespace-aware approach:
   - `xmltodict.parse()` preserves prefixes as-is
   - Navigate using the prefixed keys found at runtime
   - For output JSON, strip prefixes for clean field names
   - Always store the mapping of prefix→namespace URI used in that response

## Request Tracking

Every API call is assigned a UUID `requestId`. This links:

- The raw request/response in `sabre_requests`
- The parsed snapshot(s) produced from it
- Any changes detected from comparing it to the previous snapshot

This enables full traceability:

```
"Why did this passenger's seat change?"
→ changes collection: SEAT_CHANGE detected in snapshot #47
→ snapshot #47 was produced from request abc-123
→ sabre_requests abc-123 has the raw XML showing the actual Sabre data
```

## Idempotency & Replay

- Even if we call the same API twice in a row and get identical data, we store both requests
- The `checksum` field on snapshots makes it trivial to identify duplicates
- Snapshot `sequenceNumber` provides deterministic ordering
- We can replay the entire history from `sabre_requests` alone
