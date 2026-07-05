#!/usr/bin/env python3
"""
Phase 3 Test - Modbus RTU bidirectional read/write (mirrors your tested script)

Reads, writes, reads back, then restores original-ish values across
two holding registers and one coil - the exact sequence you already
validated directly against minimalmodbus, now routed through
ModbusManager instead.

Run:
    python3 tests/test_modbus_bidirectional.py
    python3 tests/test_modbus_bidirectional.py --port ttyUSB1 --slave 1
"""

import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.modbus_manager import ModbusManager, MODBUS_PORTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="ttyUSB0", choices=list(MODBUS_PORTS))
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args()

    print("=" * 52)
    print("Phase 3 - Modbus RTU Bidirectional Test")
    print("=" * 52)

    mgr = ModbusManager()
    dev_id = mgr.add_device(
        "Bidirectional Test Device",
        port=args.port,
        slave_id=args.slave,
        baudrate=args.baudrate,
    )

    try:
        mgr.connect(dev_id)

        print("\n--- Reading initial values ---")
        mgr.read_holding_register(dev_id, 0)
        mgr.read_holding_register(dev_id, 1)
        mgr.read_coil(dev_id, 0)

        print("\n--- Writing new values ---")
        mgr.write_holding_register(dev_id, 0, 42)
        mgr.write_holding_register(dev_id, 1, 99)
        mgr.write_coil(dev_id, 0, 1)

        time.sleep(0.2)  # brief delay for slave to update

        print("\n--- Reading back to verify ---")
        mgr.read_holding_register(dev_id, 0)
        mgr.read_holding_register(dev_id, 1)
        mgr.read_coil(dev_id, 0)

        print("\n--- Restoring original-ish values ---")
        mgr.write_holding_register(dev_id, 0, 1234)
        mgr.write_holding_register(dev_id, 1, 0)
        mgr.write_coil(dev_id, 0, 0)

        time.sleep(0.2)

        print("\n--- Final read ---")
        mgr.read_holding_register(dev_id, 0)
        mgr.read_holding_register(dev_id, 1)
        mgr.read_coil(dev_id, 0)

        print("\n--- Event log ---")
        for entry in mgr.get_logs():
            print(entry)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        mgr.disconnect(dev_id)
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
