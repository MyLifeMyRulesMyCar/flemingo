#!/usr/bin/env python3
"""
Phase 3 Test - Modbus RTU slave scan

Probes a range of slave IDs on a port by attempting a register read
on each. Useful once you have more than one slave and aren't sure
what ID is currently set on a given board.

Run:
    python3 tests/test_modbus_scan.py
    python3 tests/test_modbus_scan.py --port ttyUSB1 --start 1 --end 20
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.modbus_manager import ModbusManager, MODBUS_PORTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="ttyUSB0", choices=list(MODBUS_PORTS))
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=10)
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args()

    print("=" * 52)
    print(f"Phase 3 - Modbus Scan on {MODBUS_PORTS[args.port]['device']}")
    print(f"Slave IDs {args.start}-{args.end} @ {args.baudrate}bps")
    print("=" * 52)

    mgr = ModbusManager()
    found = mgr.scan_port(
        args.port, start_id=args.start, end_id=args.end, baudrate=args.baudrate
    )

    if found:
        print(f"\n✅ Found {len(found)} device(s): {found}")
    else:
        print("\n❌ No devices responded in that range.")


if __name__ == "__main__":
    main()