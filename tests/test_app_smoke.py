#!/usr/bin/env python3
"""
Phase 4 Test - API smoke test

Hits the running Flask app's REST endpoints to confirm all three
subsystems are wired up correctly. Run this AFTER starting api/app.py
in another terminal - it doesn't touch hardware directly itself.

Run:
    # terminal 1
    python3 api/app.py

    # terminal 2
    python3 tests/test_app_smoke.py
"""

import sys
import requests

BASE = "http://localhost:5000"


def check(label, method, path, **kwargs):
    url = f"{BASE}{path}"
    try:
        resp = requests.request(method, url, timeout=5, **kwargs)
        ok = resp.status_code < 500
        icon = "✅" if ok else "❌"
        print(f"{icon} {label}: {resp.status_code}")
        try:
            print(f"   {resp.json()}")
        except Exception:
            print(f"   {resp.text[:200]}")
        return resp
    except requests.exceptions.ConnectionError:
        print(f"❌ {label}: Connection refused - is api/app.py running on {BASE}?")
        sys.exit(1)


def main():
    print("=" * 52)
    print("Phase 4 - API Smoke Test")
    print("=" * 52)

    print("\n--- Top-level status ---")
    check("GET /api/status", "GET", "/api/status")
    check("GET /api/health", "GET", "/api/health")
    check("GET /api/health/detailed", "GET", "/api/health/detailed")

    print("\n--- GPIO ---")
    check("GET /api/io", "GET", "/api/io")
    check("GET /api/io/status", "GET", "/api/io/status")
    check("POST /api/io/do/0 (ON)", "POST", "/api/io/do/0", json={"state": True})
    check("POST /api/io/do/0 (OFF)", "POST", "/api/io/do/0", json={"state": False})

    print("\n--- CAN ---")
    check("GET /api/can/status", "GET", "/api/can/status")
    check("GET /api/can/messages", "GET", "/api/can/messages")

    print("\n--- Modbus ---")
    check("GET /api/modbus/ports", "GET", "/api/modbus/ports")
    check("GET /api/modbus/devices", "GET", "/api/modbus/devices")
    resp = check(
        "POST /api/modbus/devices (create)", "POST", "/api/modbus/devices",
        json={"name": "Smoke Test Device", "port": "ttyUSB0", "slave_id": 1},
    )
    if resp is not None and resp.status_code == 201:
        device_id = resp.json().get("device_id")
        print(f"\n--- Cleaning up test device {device_id} ---")
        check(f"DELETE /api/modbus/devices/{device_id}", "DELETE", f"/api/modbus/devices/{device_id}")

    print("\nDone. Anything that returned a hardware error above is expected")
    print("if CAN/Modbus haven't been connected yet - the point of this test")
    print("is confirming the routes and wiring respond at all.")


if __name__ == "__main__":
    main()