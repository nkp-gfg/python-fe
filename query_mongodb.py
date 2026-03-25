#!/usr/bin/env python
"""Query MongoDB for flight GF2016 and passenger MILIVOJEVIC, MILICA MS"""

import os
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import json

load_dotenv()

uri = os.environ.get("MONGODB_URI")
if not uri:
    print("MONGODB_URI not found in .env")
    exit(1)

# Create a new client and connect to the server
client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("[OK] Connected to MongoDB!")
except Exception as e:
    print(f"[ERROR] MongoDB connection failed: {e}")
    exit(1)

# List all databases
print("\n=== Databases on MongoDB ===")
try:
    all_dbs = client.list_database_names()
    for db_name in sorted(all_dbs):
        print(f"  {db_name}")
except Exception as e:
    print(f"  Error listing databases: {e}")

# Get the database
db = client['falconeye']

# List collections
print("\n=== Collections in falconeye database ===")
collections = db.list_collection_names()
print(f"Total collections: {len(collections)}")
for col in sorted(collections):
    try:
        count = db[col].count_documents({})
        print(f"  {col}: {count} documents")
    except Exception as e:
        print(f"  {col}: Error - {e}")

# Query flight GF2016
print("\n=== Flight GF2016 Data ===")
flight_query = {"flightNumber": "GF2016"}

# Try different collections
for collection_name in ["flight_status", "flights", "passenger_list", "snapshots"]:
    result = db[collection_name].find_one(flight_query)
    if result:
        print(f"\n{collection_name}:")
        # Remove MongoDB ID for cleaner output
        result.pop("_id", None)
        result.pop("_raw", None)
        # Print first 2000 chars
        result_str = json.dumps(result, indent=2, default=str)
        if len(result_str) > 2000:
            print(
                result_str[:2000] + f"\n... (truncated, total length: {len(result_str)} chars)")
        else:
            print(result_str)

        # Get the specific passenger if this is passenger_list
        if collection_name == "passenger_list":
            passengers = result.get("passengers", [])
            print(f"\n  Total passengers: {len(passengers)}")

            # Find MILIVOJEVIC, MILICA MS with PNR JHIFCW
            for p in passengers:
                if p.get("pnr") == "JHIFCW":
                    print(
                        f"\n  === Found Passenger: {p.get('firstName')} {p.get('lastName')} ===")
                    print(json.dumps(p, indent=2, default=str))
        break

# Also query reserves separately for passenger
print("\n=== Direct Passenger Search (PNR: JHIFCW) - Get Flight Info ===")
pax_query = {"passengers.pnr": "JHIFCW"}
result = db["passenger_list"].find_one(pax_query)
if result:
    flight_num = result.get("flightNumber", "")
    origin = result.get("origin", "")
    dep_date = result.get("departureDate", "")
    print(f"\nFlight containing PNR JHIFCW:")
    print(f"  Flight Number: {flight_num}")
    print(f"  Origin: {origin}")
    print(f"  Departure Date: {dep_date}")

    matching = [p for p in result.get(
        "passengers", []) if p.get("pnr") == "JHIFCW"]
    for p in matching:
        print(f"\n=== Passenger JSON Object ===")
        print(json.dumps(p, indent=2, default=str))

    # Now get the full flight passenger_list document
    print(f"\n=== Full Flight Passenger List Document ===")
    result_copy = result.copy()
    result_copy.pop("_id", None)
    result_copy.pop("_raw", None)

    # Save full flight passenger list to file
    with open('flight_GF2016_data.json', 'w', encoding='utf-8') as f:
        json.dump(result_copy, f, indent=2, default=str)
    print("\n[SAVED] Full flight data: flight_GF2016_data.json")

    # Save passenger only to file
    with open('passenger_JHIFCW_data.json', 'w', encoding='utf-8') as f:
        json.dump(matching[0], f, indent=2, default=str)
    print("[SAVED] Passenger data: passenger_JHIFCW_data.json")

    result_str = json.dumps(result_copy, indent=2, default=str)
    if len(result_str) > 3000:
        print(
            result_str[:3000] + f"\n... (truncated, total length: {len(result_str)} chars)")
    else:
        print(result_str)
else:
    print("  Not found in passenger_list")

client.close()
