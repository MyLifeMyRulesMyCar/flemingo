#!/usr/bin/env python3
# core/watchdog.py
# Software watchdog: monitors that the daemon's main loop is still
# alive, and runs a set of registered component health checks on a
# timer.
#
# Deliberately different from efio_daemon/watchdog.py in one way: the
# reference version's check_modbus_health() reaches across modules with
# `from api.modbus_device_routes import active_connections` baked
# directly into this file. That's a layering smell - this module has
# no business importing from the API layer. Instead, WatchdogTimer
# stays generic (no Flemingo-specific code at all), and
# daemon/daemon.py registers health-check closures that already have
# direct references to io_manager/can_manager/modbus_manager, since
# that's where those references naturally live.

import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class WatchdogTimer:
    """
    Usage:
        watchdog = WatchdogTimer(timeout=30)
        watchdog.register_component("gpio", check_gpio_health)
        watchdog.start()

        # In the monitored loop:
        watchdog.feed()
    """

    def __init__(
        self,
        timeout: int = 30,
        check_interval: int = 10,
        on_timeout: Optional[Callable] = None,
    ):
        """
        Args:
            timeout: seconds without a feed() before this is considered
                a hung loop.
            check_interval: how often (seconds) to re-run registered
                component health checks, independent of feed timeout.
            on_timeout: called when a timeout is detected. Defaults to
                a logging-only handler - this does NOT restart anything
                by itself. Wire actual recovery (process restart via
                systemd, etc.) at the call site if you want it.
        """
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_timeout = on_timeout or self._default_timeout_handler

        self.last_feed = time.time()
        self.running = False
        self.thread = None
        self._lock = threading.RLock()

        self.components: Dict[str, Dict] = {}
        self.timeout_count = 0

        logger.info(
            f"Watchdog initialized (timeout={timeout}s, check_interval={check_interval}s)"
        )

    def feed(self):
        """Call this once per iteration of the loop being monitored."""
        with self._lock:
            self.last_feed = time.time()

    def register_component(self, name: str, health_check: Callable[[], bool]):
        """
        `health_check` takes no arguments and returns True if healthy.
        Exceptions raised inside it are caught and treated as unhealthy.
        """
        with self._lock:
            self.components[name] = {
                "check": health_check,
                "last_check": None,
                "status": "unknown",
                "failures": 0,
            }
        logger.info(f"Watchdog: registered component '{name}'")

    def check_component_health(self, name: str) -> bool:
        with self._lock:
            component = self.components.get(name)
            if component is None:
                logger.warning(f"Watchdog: unknown component '{name}'")
                return False

            try:
                is_healthy = bool(component["check"]())
                component["status"] = "healthy" if is_healthy else "unhealthy"
                component["last_check"] = datetime.now().isoformat()
                component["failures"] = 0 if is_healthy else component["failures"] + 1

                if not is_healthy:
                    logger.warning(
                        f"Watchdog: component '{name}' unhealthy "
                        f"(failures: {component['failures']})"
                    )
                return is_healthy

            except Exception as e:
                component["status"] = "error"
                component["failures"] += 1
                component["last_check"] = datetime.now().isoformat()
                logger.error(f"Watchdog: health check for '{name}' raised: {e}")
                return False

    def check_all_components(self) -> Dict[str, bool]:
        with self._lock:
            names = list(self.components.keys())
        return {name: self.check_component_health(name) for name in names}

    def get_health_report(self) -> Dict:
        with self._lock:
            time_since_feed = time.time() - self.last_feed
            return {
                "running": self.running,
                "timeout": self.timeout,
                "last_feed": datetime.fromtimestamp(self.last_feed).isoformat(),
                "time_since_feed": round(time_since_feed, 2),
                "timeout_count": self.timeout_count,
                "status": "healthy" if time_since_feed < self.timeout else "timeout",
                "components": {
                    name: {
                        "status": comp["status"],
                        "last_check": comp["last_check"],
                        "failures": comp["failures"],
                    }
                    for name, comp in self.components.items()
                },
            }

    def _default_timeout_handler(self):
        logger.critical("WATCHDOG TIMEOUT - main loop has not fed the watchdog in time")
        logger.critical(f"  last feed: {datetime.fromtimestamp(self.last_feed)}")
        logger.critical(f"  timeout count: {self.timeout_count}")

        unhealthy = [name for name, ok in self.check_all_components().items() if not ok]
        if unhealthy:
            logger.critical(f"  unhealthy components: {', '.join(unhealthy)}")
        logger.critical(
            "  no automatic recovery action configured - "
            "if this unit has a systemd watchdog binding, consider exiting "
            "the process here so systemd restarts it cleanly"
        )

    def _watchdog_loop(self):
        logger.info("Watchdog monitoring started")
        last_check = 0

        while self.running:
            try:
                with self._lock:
                    time_since_feed = time.time() - self.last_feed
                    if time_since_feed >= self.timeout:
                        self.timeout_count += 1
                        logger.warning(
                            f"Watchdog timeout ({time_since_feed:.1f}s >= {self.timeout}s)"
                        )
                        self.on_timeout()
                        self.last_feed = time.time()  # avoid re-firing every second

                now = time.time()
                if now - last_check >= self.check_interval:
                    self.check_all_components()
                    last_check = now

                time.sleep(1)

            except Exception as e:
                logger.error(f"Watchdog loop error: {e}")
                time.sleep(5)

        logger.info("Watchdog monitoring stopped")

    def start(self):
        if self.running:
            logger.warning("Watchdog already running")
            return
        self.running = True
        self.last_feed = time.time()
        self.thread = threading.Thread(
            target=self._watchdog_loop, name="Watchdog", daemon=True
        )
        self.thread.start()
        logger.info("Watchdog started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Watchdog stopped")


def exit_process_timeout_handler(watchdog_self):
    """Called when the watchdog detects a hung main loop.
    Logs diagnostics then terminates the entire process so systemd's
    Restart=on-failure can restart it. Uses os._exit (not sys.exit)
    because this runs on the watchdog's background thread — sys.exit
    would only raise SystemExit in that thread, not stop the process."""
    watchdog_self._default_timeout_handler()
    logger.critical("Watchdog: terminating process — systemd will restart")
    os._exit(1)
