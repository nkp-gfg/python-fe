#!/usr/bin/env python
"""Test FastAPI endpoints with requests library"""

import requests
import json
import time

# Wait for server to start
time.sleep(2)

base_url = "http://127.0.0.1:8000"

print("=== Testing FastAPI Endpoints ===\n")

# Test 1: Get all passengers for flight 2016
print("1. GET /flights/2016/passengers")
try:
    url = f"{base_url}/flights/2016/passengers?origin=FRA&date=2026-03-23"
    response = requests.get(url, timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   Total passengers: {data.get('totalPassengers', 'N/A')}")
        print(f"   Flight: {data.get('flightNumber')}")
        with open('api_response_all_passengers.json', 'w') as f:
            json.dump(data, f, indent=2)
        print(f"   [SAVED] Passenger list: api_response_all_passengers.json")
    else:
        print(f"   Error: {response.text}")
except Exception as e:
    print(f"   Exception: {e}")

print("\n2. GET /flights/2016/passengers/JHIFCW")
try:
    url = f"{base_url}/flights/2016/passengers/JHIFCW?date=2026-03-23"
    response = requests.get(url, timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        passengers = data.get('passengers', [])
        print(f"   Found {len(passengers)} passenger(s)")
        if passengers:
            p = passengers[0]
            print(f"   Name: {p.get('firstName')} {p.get('lastName')}")
            print(f"   PNR: {p.get('pnr')}")
            print(f"   Seat: {p.get('seat')}")
            print(f"   Cabin: {p.get('cabin')}")
            with open('api_response_pnr_JHIFCW.json', 'w') as f:
                json.dump(data, f, indent=2)
            print(f"   [SAVED] Passenger data: api_response_pnr_JHIFCW.json")
    else:
        print(f"   Error: {response.text}")
except Exception as e:
    print(f"   Exception: {e}")

print("\n3. GET /flights/2016/passengers/summary")
try:
    url = f"{base_url}/flights/2016/passengers/summary?origin=FRA&date=2026-03-23"
    response = requests.get(url, timeout=5)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   Total passengers: {data.get('totalPassengers', 'N/A')}")
        print(f"   Checked-in: {data.get('checkedIn', 'N/A')}")
        print(f"   Boarded: {data.get('boarded', 'N/A')}")
        with open('api_response_summary.json', 'w') as f:
            json.dump(data, f, indent=2)
        print(f"   [SAVED] Summary: api_response_summary.json")
    else:
        print(f"   Error: {response.text}")
except Exception as e:
    print(f"   Exception: {e}")

print("\n=== Validation Complete ===")
