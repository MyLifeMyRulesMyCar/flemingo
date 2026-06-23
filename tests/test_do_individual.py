#!/usr/bin/env python3
"""
Phase 1 Test - Digital Outputs (individual pin verification)
Purple Pi OH2: GPIO56, GPIO57, GPIO58, GPIO59

Cycles each DO channel ON for 1.5s, then OFF, one at a time, so you
can confirm with an LED (or multimeter) which physical pin maps to
which DO channel.

Wiring per channel:
    DO pin -> [220 ohm resistor] -> LED (+)
    GND    -> LED (-)

Run:
    sudo chmod 666 /dev/gpiochip1 /dev/gpiochip3 /dev/gpiochip4
    source venv/bin/activate
    python3 tests/test_do_individual.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.io_manager import IOManager, DO_CHANNELS, OUTPUT_PINS


def main():
    print("=" * 60)
    print("Phase 1 - Digital Output Test (individual channels)")
    print("=" * 60)

    mgr = IOManager()
    status = mgr.get_status()
    print(f"Mode: {'SIMULATION' if status['simulation'] else 'HARDWARE'}\n")

    if status['simulation']:
        print("⚠️  Running in simulation - no physical pins will toggle.")
        print("   Check permissions: sudo chmod 666 /dev/gpiochip1 /dev/gpiochip3 /dev/gpiochip4\n")

    try:
        for ch, name in enumerate(DO_CHANNELS):
            chip, line = OUTPUT_PINS[name]
            print(f"--- {name}  ({chip}, offset {line}) ---")

            print(f"  {name} ON")
            mgr.write_output(ch, 1)
            time.sleep(1.5)

            print(f"  {name} OFF")
            mgr.write_output(ch, 0)
            time.sleep(0.5)

        print("\n✅ All 4 DO channels cycled once.")
        print("   Confirm the LED order you saw matched:")
        for name in DO_CHANNELS:
            print(f"   {name}")

    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mgr.write_all_outputs([0, 0, 0, 0])
        mgr.close()


if __name__ == "__main__":
    main()