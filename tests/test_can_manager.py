#!/usr/bin/env python3
"""
Phase 2 Test - CANManager (the backend layer) end-to-end

Connects via the real CANManager in NORMAL mode (not loopback), so it
needs an actual live bus: CAN_H/CAN_L wired to at least one other node
(e.g. the Arduino sender sketch from the docs, with matching bitrate
and crystal), plus 120 ohm termination resistors at both ends.

Run loopback test first (test_can_loopback.py) to rule out SPI-side
wiring problems before trying this one.

Usage:
    python3 tests/test_can_manager.py
    python3 tests/test_can_manager.py --listen-seconds 20
    python3 tests/test_can_manager.py --send 0x123 DE AD BE EF
"""

import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.can_manager import CANManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send", nargs="+",
        help="CAN_ID followed by data bytes in hex, e.g. --send 0x123 DE AD BE EF"
    )
    parser.add_argument("--listen-seconds", type=int, default=10)
    parser.add_argument("--bitrate", type=int, default=125_000)
    parser.add_argument("--crystal", type=int, default=8_000_000)
    args = parser.parse_args()

    mgr = CANManager(bitrate=args.bitrate, crystal=args.crystal)

    try:
        mgr.connect()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print("\nStatus right after connect:")
    print(mgr.get_status())

    if args.send:
        can_id = int(args.send[0], 16)
        data = [int(b, 16) for b in args.send[1:]]
        ok = mgr.send_message(can_id, data)
        print(f"\nSend {'OK' if ok else 'FAILED'}: ID=0x{can_id:03X} data={[f'0x{b:02X}' for b in data]}")

    print(f"\nListening for {args.listen_seconds}s on the bus...")
    time.sleep(args.listen_seconds)

    print("\n--- Recent messages (newest last) ---")
    messages = mgr.get_recent_messages(50)
    if not messages:
        print("(none received - check CAN_H/CAN_L wiring, termination resistors, "
              "and that another node is actually transmitting)")
    for entry in messages:
        print(entry)

    print("\n--- Final status ---")
    print(mgr.get_status())

    mgr.disconnect()


if __name__ == "__main__":
    main()