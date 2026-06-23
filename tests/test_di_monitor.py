#!/usr/bin/env python3
"""
Phase 1 Test - Digital Inputs (live state monitor)
Purple Pi OH2: GPIO132, GPIO134, GPIO98, GPIO99

Polls all 4 DI channels and prints whenever any one changes state.
Inputs are configured with PULL_DOWN bias, so they read LOW (0) by
default with nothing wired. Bring a channel HIGH by connecting it
to 3.3V (e.g. a push-button or jumper wire from a 3.3V pin) to see
it flip to 1.

Run:
    sudo chmod 666 /dev/gpiochip1 /dev/gpiochip3 /dev/gpiochip4
    source venv/bin/activate
    python3 tests/test_di_monitor.py
    (Ctrl+C to stop)
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.io_manager import IOManager, DI_CHANNELS, INPUT_PINS


def main():
    print("=" * 60)
    print("Phase 1 - Digital Input Monitor")
    print("=" * 60)

    mgr = IOManager()
    status = mgr.get_status()
    print(f"Mode: {'SIMULATION' if status['simulation'] else 'HARDWARE'}")

    for name in DI_CHANNELS:
        chip, line = INPUT_PINS[name]
        print(f"  {name}: {chip} offset {line}")

    print("\nWatching for changes... Ctrl+C to stop\n")

    previous = mgr.read_all_inputs()
    print(f"Initial state: {dict(zip(DI_CHANNELS, previous))}")

    try:
        while True:
            current = mgr.read_all_inputs()
            for i, name in enumerate(DI_CHANNELS):
                if current[i] != previous[i]:
                    state = "HIGH (1)" if current[i] else "LOW (0)"
                    print(f"🔘 {name} -> {state}")
            previous = current
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped")
    finally:
        mgr.close()


if __name__ == "__main__":
    main()