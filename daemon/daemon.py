#!/usr/bin/env python3
# daemon/daemon.py
# Background polling loop - same role as efio_daemon/daemon.py in the
# reference project: keep `state` in sync with hardware.
#
# CAN already runs its own RX thread inside core/can_manager.py, and
# Modbus is on-demand/REST-driven (matches the reference project's
# modbus_device_routes, which also requires explicit connect calls -
# no continuous background poll there either). So this daemon's only
# job is GPIO DI polling.

import threading
import time
import traceback

from core.state import state


class PurpleIODaemon:
    """
    Polls DI lines at `poll_interval` and writes them into the shared
    `state`. Does NOT touch CAN or Modbus - those manage themselves.
    """

    def __init__(self, io_manager, poll_interval: float = 0.1):
        self.manager = io_manager
        self.poll_interval = poll_interval
        self.running = True
        self.loop_count = 0
        self.last_di = [0, 0, 0, 0]
        self._thread = None

    def loop(self):
        print("🔄 PurpleIO daemon: main loop started")
        consecutive_errors = 0
        max_consecutive_errors = 10

        while self.running:
            try:
                self.loop_count += 1

                try:
                    di_values = self.manager.read_all_inputs()
                except Exception as e:
                    print(f"⚠️  Daemon: GPIO read error: {e}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        print(f"❌ Daemon: too many consecutive GPIO errors "
                              f"({consecutive_errors})")
                    time.sleep(1)
                    continue

                consecutive_errors = 0

                for i, val in enumerate(di_values):
                    if val != self.last_di[i]:
                        print(f"🔄 Daemon: DI{i} changed {self.last_di[i]} -> {val}")
                        self.last_di[i] = val

                state.set_di_all(di_values)
                time.sleep(self.poll_interval)

            except Exception as e:
                print(f"❌ Daemon: unexpected error in main loop: {e}")
                traceback.print_exc()
                time.sleep(1)

        print("🛑 PurpleIO daemon: main loop stopped")

    def start(self):
        self._thread = threading.Thread(target=self.loop, name="PurpleIO-Daemon", daemon=True)
        self._thread.start()
        print("✅ purpleio-daemon running...")

    def stop(self):
        print("🛑 Stopping purpleio-daemon...")
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)