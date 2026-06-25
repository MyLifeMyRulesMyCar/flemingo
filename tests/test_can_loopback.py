#!/usr/bin/env python3
"""
Phase 2 Test - MCP2515 Loopback Self-Test (no CAN bus / other nodes needed)

Verifies SPI wiring and chip communication only - does NOT require
CAN_H/CAN_L to be connected to anything else. Puts the MCP2515 in
internal loopback mode, transmits a frame, and checks it's received
back identically. Run this FIRST, before wiring up a second node.

Run:
    sudo chmod 666 /dev/spidev0.*
    source venv/bin/activate
    python3 tests/test_can_loopback.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mcp2515_driver import MCP2515, CANMessage

CRYSTAL = 8_000_000
BITRATE = 125_000


def main():
    print("=" * 52)
    print("Phase 2 - MCP2515 Loopback Self-Test")
    print("=" * 52)

    try:
        mcp = MCP2515(spi_bus=0, spi_device=None, crystal=CRYSTAL)
    except RuntimeError as e:
        print(f"❌ {e}")
        print("   Check: sudo chmod 666 /dev/spidev0.*")
        print("   Also check SPI is enabled and wiring matches pins 19/21/23/26.")
        sys.exit(1)

    try:
        if not mcp.init(bitrate=BITRATE, loopback=True):
            print("❌ Init failed")
            return

        tx = CANMessage(
            can_id=0x7FF,
            data=[0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04],
            dlc=8,
        )
        print(f"\n📤 TX: {tx}")

        if not mcp.send_message(tx):
            print("❌ TX buffer busy")
            return

        time.sleep(0.05)
        buf = mcp.available()
        if buf:
            rx = mcp.read_message(buf)
            print(f"📥 RX: {rx}")
            match = (rx.can_id == tx.can_id and rx.data[:rx.dlc] == tx.data[:tx.dlc])
            if match:
                print("\n✅ PASSED - SPI wiring and chip logic are good.")
                print("   Safe to move on to wiring a real CAN_H/CAN_L bus.")
            else:
                print("\n⚠️  Data mismatch - chip responded but data was corrupted.")
                print("   Check SPI clock speed / wiring quality.")
        else:
            print("\n❌ No message received.")
            print("   This is a 3.3V SPI-side problem, not CAN_H/CAN_L wiring.")
            print("   Check: pin19(SI)/pin21(SO)/pin23(SCK)/pin26(CS), and 3.3V/GND.")

    finally:
        mcp.close()


if __name__ == "__main__":
    main()