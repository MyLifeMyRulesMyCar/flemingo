#!/usr/bin/env python3
# tools/can_concurrent_hw_test.py
# Hardware test: proves the CAN SPI lock (Task 2) holds under real load.
#
# Creates a CANManager in loopback mode, spawns 4 TX threads hammering
# send_message() for 30 seconds while the RX thread and health-check
# run concurrently. Without the self._lock wrapping in _rx_loop /
# _do_health_check / send_message, this test would fail within seconds
# due to SPI bus corruption (garbled frames, MCP2515 errors).
#
# Run on real Purple Pi OH2 + Flemingo-Board hardware:
#   sudo chmod 666 /dev/spidev0.*
#   source venv/bin/activate
#   python3 tools/can_concurrent_hw_test.py

import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.can_manager import CANManager

DURATION = 30
BITRATE = 125_000
TX_THREADS = 4

tx_ok = 0
tx_fail = 0
tx_lock = threading.Lock()
exceptions = []
stop = threading.Event()


def tx_worker(mgr, seed):
    global tx_ok, tx_fail
    can_id = seed
    while not stop.is_set():
        data = [(can_id + i) % 256 for i in range(4)]
        try:
            ok = mgr.send_message(can_id & 0x7FF, data)
            with tx_lock:
                if ok:
                    tx_ok += 1
                else:
                    tx_fail += 1
        except Exception as e:
            exceptions.append(f"tx thread error: {e}")
        can_id += 1


def main():
    print("=" * 60)
    print("CAN Concurrent TX/RX Hardware Test (Loopback)")
    print(
        f"Bitrate: {BITRATE / 1000:.0f}K  |  TX threads: {TX_THREADS}  "
        f"|  Duration: {DURATION}s"
    )
    print("Expected: connected=True  errors=0  tx_total≈tx_ok  no exceptions")
    print("=" * 60)

    mgr = CANManager(bitrate=BITRATE, loopback=True)
    try:
        mgr.connect()
    except Exception as e:
        print(f"\nFAIL: connect failed — {e}")
        sys.exit(1)

    if not mgr.connected:
        print("\nFAIL: not connected after connect() returned")
        sys.exit(1)

    print("Connected. Starting TX threads...")
    threads = [
        threading.Thread(target=tx_worker, args=(mgr, i), name=f"TX-{i}")
        for i in range(TX_THREADS)
    ]
    for t in threads:
        t.start()

    start = time.time()
    while time.time() - start < DURATION:
        time.sleep(5)
        elapsed = int(time.time() - start)
        status = mgr.get_status()
        print(
            f"  [{elapsed:3d}s] rx={status['rx_total']}  "
            f"tx={status['tx_total']}  "
            f"errors={status['errors']}  "
            f"breaker={status['circuit_breaker']['state']}"
        )

    stop.set()
    for t in threads:
        t.join(timeout=3)

    status = mgr.get_status()
    print("\nFinal status:")
    print(f"  connected: {status['connected']}")
    print(f"  rx_total:  {status['rx_total']}")
    print(f"  tx_total:  {status['tx_total']}")
    print(f"  errors:    {status['errors']}")
    print(f"  tx_ok:     {tx_ok}")
    print(f"  tx_fail:   {tx_fail}")

    mgr.disconnect()

    # ── Assertions ───────────────────────────────────────────────────
    passed = True

    if exceptions:
        print(f"\nFAIL: {len(exceptions)} exceptions in TX threads")
        for e in exceptions:
            print(f"  {e}")
        passed = False

    if not status["connected"]:
        print("\nFAIL: bus disconnected — health check may have falsely tripped")
        passed = False

    if status["errors"] != 0:
        print(
            f"\nFAIL: {status['errors']} SPI errors — concurrent access "
            f"caused bus corruption"
        )
        passed = False

    if tx_ok == 0:
        print("\nFAIL: zero successful TX frames — bus may be dead")
        passed = False

    if passed:
        print("\n✅ PASS — SPI lock holds under concurrent TX/RX load")
    else:
        print("\n❌ FAIL — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
