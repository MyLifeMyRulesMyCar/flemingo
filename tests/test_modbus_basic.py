#!/usr/bin/env python3
"""
Phase 3 Test - Modbus RTU basic read (mirrors your tested single-read script)

Connects to one slave over a USB-to-RS485 adapter and reads one
holding register. Defaults match what you already validated:
/dev/ttyUSB0, 115200 8N1, slave ID 1, register 0.

Run:
    source venv/bin/activate
    python3 tests/test_modbus_basic.py
    python3 tests/test_modbus_basic.py --port ttyUSB1 --slave 2 --register 0
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.modbus_manager import ModbusManager, MODBUS_PORTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="ttyUSB0", choices=list(MODBUS_PORTS))
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--register", type=int, default=0)
    args = parser.parse_args()

    print("=" * 52)
    print("Phase 3 - Modbus RTU Basic Read")
    print("=" * 52)
    print(f"Port: {MODBUS_PORTS[args.port]['device']}  "
          f"Slave: {args.slave}  Baud: {args.baudrate}\n")

    mgr = ModbusManager()
    dev_id = mgr.add_device(
        "Basic Test Device", port=args.port, slave_id=args.slave,
        baudrate=args.baudrate,
    )

    try:
        mgr.connect(dev_id)
        value = mgr.read_holding_register(dev_id, args.register)
        if value is not None:
            print(f"\n✅ Register {args.register} = {value}")
        else:
            print("\n❌ No response - check wiring, COM port, and slave ID.")
    finally:
        mgr.disconnect(dev_id)


if __name__ == "__main__":
    main()