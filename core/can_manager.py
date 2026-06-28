#!/usr/bin/env python3
# core/can_manager.py
# CAN bus backend manager for Purple Pi OH2 (MCP2515 over SPI0 CS1).
#
# This is the layer a Flask API (Phase 4) will sit on top of, same role
# as core/io_manager.py plays for DI/DO. It wraps the raw MCP2515 driver
# with a background RX thread, a rolling message log, basic statistics,
# and a subscriber hook for live message streaming (e.g. WebSocket later).
#
# Phase 5 reliability additions: connect() goes through a CircuitBreaker
# so a wiring/power problem doesn't get hammered with reconnect attempts
# forever, and the RX loop now attempts a breaker-gated reconnect on
# repeated failures instead of permanently killing itself after 10
# consecutive errors. Status is mirrored into core.resilience.health_status
# so api/health_routes.py and core/watchdog.py can see it without reaching
# into this module's internals.

import logging
import threading
import time
from datetime import datetime
from collections import deque
from typing import List, Dict, Optional, Callable

from core.mcp2515_driver import MCP2515, CANMessage
from core.resilience import CircuitBreaker, CircuitOpenError, health_status
from core.config import load_reliability_config

logger = logging.getLogger(__name__)

DEFAULT_BITRATE = 125_000
DEFAULT_CRYSTAL = 8_000_000
MAX_CONSECUTIVE_RX_ERRORS = 10


class CANManager:
    """
    Background-threaded CAN bus manager.

    Usage:
        mgr = CANManager(bitrate=125_000, crystal=8_000_000)
        mgr.connect()
        mgr.send_message(0x123, [1, 2, 3, 4])
        mgr.get_recent_messages(50)
        mgr.get_status()
        mgr.disconnect()
    """

    def __init__(self, spi_bus=0, spi_device=None, bitrate=DEFAULT_BITRATE, crystal=DEFAULT_CRYSTAL):
        self.spi_bus = spi_bus
        self.spi_device = spi_device   # None = auto-probe spidev0.1 then spidev0.0
        self.bitrate = bitrate
        self.crystal = crystal

        self.controller: Optional[MCP2515] = None
        self.connected = False

        self._lock = threading.RLock()
        self.rx_thread = None
        self.running = False

        self.message_log = deque(maxlen=1000)
        self.subscribers: List[Callable] = []

        self.stats = {
            "rx_total": 0,
            "tx_total": 0,
            "errors": 0,
            "start_time": None,
        }

        cb_config = load_reliability_config()["circuit_breaker"]["can"]
        self.breaker = CircuitBreaker(
            failure_threshold=cb_config["failure_threshold"],
            timeout=cb_config["timeout"],
            expected_exception=Exception,
            name="CAN",
        )
        health_status.update("can", "unknown", "Not yet connected")

    # ----------------------------------------
    # Connection
    # ----------------------------------------
    def connect(self) -> bool:
        if self.connected:
            logger.warning("CAN already connected")
            return True

        @self.breaker.call
        def _attempt():
            return self._do_connect()

        try:
            result = _attempt()
            health_status.update("can", "healthy", "Connected")
            return result
        except CircuitOpenError as e:
            health_status.update("can", "unhealthy", str(e))
            raise RuntimeError(str(e)) from e
        except Exception as e:
            health_status.update("can", "unhealthy", f"Connect failed: {e}")
            raise

    def _do_connect(self) -> bool:
        """Used by the public connect() path: brings up the controller AND
        starts the RX thread. Not used by the RX loop's own reconnect path -
        that calls _init_controller() directly since it IS the RX thread
        already and starting a second one would leak a thread."""
        result = self._init_controller()
        self._start_rx_thread()
        return result

    def _init_controller(self) -> bool:
        """Raw hardware bring-up only - no thread management. Called through
        the circuit breaker by both connect() (via _do_connect) and the RX
        loop's _attempt_reconnect()."""
        with self._lock:
            logger.info(f"Connecting MCP2515 (bus={self.spi_bus}, device={self.spi_device}, "
                        f"{self.bitrate} bps, crystal={self.crystal})...")

            controller = MCP2515(
                spi_bus=self.spi_bus,
                spi_device=self.spi_device,
                crystal=self.crystal,
            )

            if not controller.init(bitrate=self.bitrate):
                controller.close()
                raise RuntimeError("MCP2515 init failed - check wiring, 5V supply, and crystal value")

            self.controller = controller
            self.connected = True
            self.stats["start_time"] = datetime.now()

            logger.info("CAN connected")
            return True

    def disconnect(self):
        with self._lock:
            if not self.connected:
                return
            self.running = False

        if self.rx_thread:
            self.rx_thread.join(timeout=2)

        with self._lock:
            if self.controller:
                try:
                    self.controller.close()
                except Exception:
                    pass
                self.controller = None
            self.connected = False
            logger.info("CAN disconnected")
            health_status.update("can", "degraded", "Disconnected by request")

    # ----------------------------------------
    # RX thread
    # ----------------------------------------
    def _start_rx_thread(self):
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, name="CAN-RX", daemon=True)
        self.rx_thread.start()

    def _rx_loop(self):
        logger.info("CAN RX loop started")
        consecutive_errors = 0
        next_reconnect_attempt = 0.0

        while self.running:
            try:
                if not self.connected or not self.controller:
                    now = time.time()
                    if now >= next_reconnect_attempt:
                        if self._attempt_reconnect():
                            consecutive_errors = 0
                        else:
                            # Either the breaker is open or the reconnect
                            # itself failed - don't busy-loop hammering it,
                            # but do keep checking every 5s. Once the
                            # breaker's own timeout elapses it'll go
                            # HALF_OPEN and this will actually touch
                            # hardware again.
                            next_reconnect_attempt = now + 5
                    time.sleep(0.5)
                    continue

                buf = self.controller.available()
                if buf:
                    msg = self.controller.read_message(buf)
                    if msg:
                        consecutive_errors = 0
                        self._handle_rx(msg)
                else:
                    time.sleep(0.001)

            except Exception as e:
                consecutive_errors += 1
                self.stats["errors"] += 1
                logger.warning(f"CAN RX error ({consecutive_errors}): {e}")
                time.sleep(0.1)

                if consecutive_errors >= MAX_CONSECUTIVE_RX_ERRORS:
                    logger.error(f"CAN: {consecutive_errors} consecutive RX errors, "
                                 f"tearing down and attempting reconnect")
                    self._attempt_reconnect()
                    consecutive_errors = 0
                    next_reconnect_attempt = time.time() + 5

        logger.info("CAN RX loop stopped")

    def _attempt_reconnect(self) -> bool:
        """Tears down whatever controller handle exists and tries to bring
        CAN back up, gated by self.breaker. Safe to call repeatedly - once
        the breaker is OPEN this returns False almost instantly without
        touching SPI at all, so a dead bus doesn't get hammered."""
        if self.controller:
            try:
                self.controller.close()
            except Exception:
                pass
            self.controller = None
        self.connected = False

        @self.breaker.call
        def _attempt():
            return self._init_controller()

        try:
            _attempt()
            health_status.update("can", "healthy", "Reconnected")
            return True
        except CircuitOpenError as e:
            health_status.update("can", "unhealthy", str(e))
            return False
        except Exception as e:
            logger.warning(f"CAN: reconnect attempt failed: {e}")
            health_status.update("can", "unhealthy", f"Reconnect failed: {e}")
            return False

    def _handle_rx(self, msg: CANMessage):
        self.stats["rx_total"] += 1
        entry = {
            "timestamp": datetime.now().isoformat(),
            "direction": "RX",
            "can_id": msg.can_id,
            "dlc": msg.dlc,
            "data": list(msg.data[:msg.dlc]),
            "extended": msg.extended,
            "rtr": msg.rtr,
        }
        self.message_log.append(entry)

        for sub in self.subscribers:
            try:
                sub(entry)
            except Exception as e:
                logger.warning(f"CAN subscriber error: {e}")

    # ----------------------------------------
    # TX
    # ----------------------------------------
    def send_message(self, can_id: int, data: List[int], extended: bool = False) -> bool:
        if not self.connected or not self.controller:
            raise RuntimeError("CAN not connected")
        if len(data) > 8:
            raise ValueError("CAN data must be <= 8 bytes")

        with self._lock:
            msg = CANMessage(can_id=can_id, data=data, dlc=len(data), extended=extended)
            ok = self.controller.send_message(msg)

            if ok:
                self.stats["tx_total"] += 1
                self.message_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "direction": "TX",
                    "can_id": can_id,
                    "dlc": len(data),
                    "data": data,
                    "extended": extended,
                    "rtr": False,
                })
            return ok

    # ----------------------------------------
    # Status / introspection
    # ----------------------------------------
    def get_status(self) -> Dict:
        with self._lock:
            uptime = None
            if self.stats["start_time"]:
                uptime = (datetime.now() - self.stats["start_time"]).total_seconds()
            return {
                "connected": self.connected,
                "bitrate": self.bitrate,
                "crystal": self.crystal,
                "rx_total": self.stats["rx_total"],
                "tx_total": self.stats["tx_total"],
                "errors": self.stats["errors"],
                "uptime": uptime,
                "circuit_breaker": self.breaker.get_state(),
            }

    def get_recent_messages(self, count: int = 100) -> List[Dict]:
        return list(self.message_log)[-count:]

    def clear_log(self):
        self.message_log.clear()

    def subscribe(self, callback: Callable):
        if callback not in self.subscribers:
            self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        if callback in self.subscribers:
            self.subscribers.remove(callback)


# Module-level singleton - import `can_manager` directly once this is
# wired into a Flask app, same pattern as efio_daemon/can_manager.py
can_manager = CANManager()


if __name__ == "__main__":
    mgr = CANManager()
    try:
        mgr.connect()
        print(mgr.get_status())
        time.sleep(5)
        print(mgr.get_recent_messages())
    finally:
        mgr.disconnect()