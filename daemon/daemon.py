#!/usr/bin/env python3
# daemon/daemon.py
# Background polling loop - same role as efio_daemon/daemon.py in the
# reference project: keep `state` in sync with hardware.
#
# CAN already runs its own RX thread inside core/can_manager.py, and
# Modbus is on-demand/REST-driven (matches the reference project's
# modbus_device_routes, which also requires explicit connect calls -
# no continuous background poll there either). So this daemon's main
# loop still only touches GPIO.
#
# Phase 5 reliability addition: this daemon is also the one place a
# WatchdogTimer naturally lives, since it's the only continuously
# running supervisory loop in the process. It feeds the watchdog once
# per iteration and registers health checks for GPIO, CAN, and Modbus -
# can_manager/modbus_manager are passed in (optional) purely so those
# checks have something to look at; the daemon's own loop() still
# never touches CAN/Modbus state directly.

import logging
import threading
import time
import traceback

from core.state import state
from core.watchdog import WatchdogTimer, exit_process_timeout_handler
from core.config import load_reliability_config

logger = logging.getLogger(__name__)


class PurpleIODaemon:
    """
    Polls DI lines at `poll_interval` and writes them into the shared
    `state`. Does NOT touch CAN or Modbus control flow - those manage
    themselves. can_manager/modbus_manager, if provided, are only used
    read-only by the watchdog's health checks.
    """

    def __init__(
        self,
        io_manager,
        poll_interval: float = 0.1,
        can_manager=None,
        modbus_manager=None,
    ):
        self.manager = io_manager
        self.can_manager = can_manager
        self.modbus_manager = modbus_manager

        self.poll_interval = poll_interval
        self.running = True
        self.loop_count = 0
        self.last_di = [0, 0, 0, 0]
        self._di_candidate = [0, 0, 0, 0]
        self._di_stable_count = [0, 0, 0, 0]
        self.debounce_reads = 3  # ~300ms at 0.1s poll (3 reads × 0.1s)
        self._di_initialized = False
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        self._thread = None

        wd_config = load_reliability_config()["watchdog"]
        self.watchdog = WatchdogTimer(
            timeout=wd_config["timeout"],
            check_interval=wd_config["check_interval"],
        )
        if wd_config.get("exit_on_timeout", True):
            self.watchdog.on_timeout = lambda: exit_process_timeout_handler(
                self.watchdog
            )
        self.watchdog.register_component("gpio", self._check_gpio_health)
        if self.can_manager is not None:
            self.watchdog.register_component("can", self._check_can_health)
        if self.modbus_manager is not None:
            self.watchdog.register_component("modbus", self._check_modbus_health)

    # ----------------------------------------
    # Watchdog health checks
    # ----------------------------------------
    def _check_gpio_health(self) -> bool:
        return self.consecutive_errors < self.max_consecutive_errors

    def _check_can_health(self) -> bool:
        status = self.can_manager.get_status()
        breaker_state = status.get("circuit_breaker", {}).get("state")
        # Unhealthy only once the breaker has actually tripped open -
        # "never connected yet" isn't a daemon-level failure.
        return breaker_state != "open"

    def _check_modbus_health(self) -> bool:
        devices = list(self.modbus_manager.devices.values())
        if not devices:
            return True
        return not any(d.breaker.state.value == "open" for d in devices)

    # ----------------------------------------
    # Main loop
    # ----------------------------------------
    def loop(self):
        logger.info("PurpleIO daemon: main loop started")

        while self.running:
            try:
                self.loop_count += 1
                self.watchdog.feed()

                try:
                    di_values = self.manager.read_all_inputs()
                except Exception as e:
                    logger.warning(f"Daemon: GPIO read error: {e}")
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self.max_consecutive_errors:
                        logger.error(
                            f"Daemon: too many consecutive GPIO errors "
                            f"({self.consecutive_errors})"
                        )
                    time.sleep(1)
                    continue

                self.consecutive_errors = 0

                if not self._di_initialized:
                    self.last_di = list(di_values)
                    self._di_candidate = list(di_values)
                    self._di_stable_count = [self.debounce_reads] * 4
                    self._di_initialized = True

                for i, val in enumerate(di_values):
                    if val == self._di_candidate[i]:
                        self._di_stable_count[i] += 1
                    else:
                        self._di_candidate[i] = val
                        self._di_stable_count[i] = 1

                    if (
                        self._di_stable_count[i] >= self.debounce_reads
                        and val != self.last_di[i]
                    ):
                        logger.info(f"Daemon: DI{i} changed {self.last_di[i]} -> {val}")
                        self.last_di[i] = val

                state.set_di_all(self.last_di)
                time.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Daemon: unexpected error in main loop: {e}")
                traceback.print_exc()
                time.sleep(1)

        logger.info("PurpleIO daemon: main loop stopped")

    def start(self):
        self.watchdog.start()
        self._thread = threading.Thread(
            target=self.loop, name="PurpleIO-Daemon", daemon=True
        )
        self._thread.start()
        logger.info("purpleio-daemon running...")

    def stop(self):
        logger.info("Stopping purpleio-daemon...")
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.watchdog.stop()
