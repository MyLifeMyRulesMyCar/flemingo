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
# forever. The RX loop detects physical CAN bus disconnection via periodic
# TX health-checks: it sends a probe frame on a dedicated TX buffer every
# 5 s; if the MCP2515 can't get an ACK (TXERR flag) three times in a row
# the controller is torn down and reported as disconnected. SPI-level
# errors (kernel unload / power loss) are also caught as a backup.
# No auto-reconnect — the user must explicitly POST /api/can/connect.
# Status is mirrored into core.resilience.health_status.

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
MAX_CONSECUTIVE_RX_ERRORS = 3    # SPI errors before declaring disconnect

# TX-based health check (detects CAN bus physical disconnection)
HEALTH_CHECK_INTERVAL = 5.0       # seconds between TX probes
HEALTH_CHECK_CAN_ID   = 0x7FF     # CAN ID for probe frame (reserved, no user traffic)
MAX_HEALTH_FAILURES   = 3         # consecutive TX errors → disconnect
HEALTH_TXBUF          = 2         # TX buffer used for probes (0 is user-facing send)


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
        """Brings up the controller and starts the RX thread if not already
        running. The RX thread guard in _start_rx_thread() avoids creating
        a duplicate when reconnecting after a detected disconnection."""
        result = self._init_controller()
        self._start_rx_thread()
        return result

    def _init_controller(self) -> bool:
        """Raw hardware bring-up only - no thread management. Called through
        the circuit breaker by connect() (via _do_connect)."""
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
        if self.rx_thread and self.rx_thread.is_alive():
            # Thread already running (idle, waiting for reconnect) —
            # don't create a second one; the existing loop will pick
            # up the new connection automatically.
            return
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, name="CAN-RX", daemon=True)
        self.rx_thread.start()

    def _rx_loop(self):
        logger.info("CAN RX loop started")
        consecutive_errors = 0
        health_failures = 0
        last_health_check = 0.0

        while self.running:
            try:
                if not self.connected or not self.controller:
                    # Disconnected — wait for manual reconnect via the API.
                    time.sleep(0.5)
                    continue

                # 1. Process received messages
                buf = self.controller.available()
                if buf:
                    msg = self.controller.read_message(buf)
                    if msg:
                        consecutive_errors = 0
                        health_failures = 0  # traffic flowing → bus alive
                        self._handle_rx(msg)

                # 2. Periodic TX health-check (detects CAN bus disconnection
                #    by sending a probe frame. On a dead bus the MCP2515
                #    goes bus-off after repeated no-ACK errors, setting
                #    TXBO in EFLG and TXERR in TXBxCTRL.)
                now = time.time()
                if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                    last_health_check = now
                    if self._do_health_check():
                        health_failures = 0
                    else:
                        health_failures += 1
                        logger.warning(
                            f"CAN health check failed ({health_failures}/"
                            f"{MAX_HEALTH_FAILURES})"
                        )

                # 3. Disconnect on repeated TX failures
                if health_failures >= MAX_HEALTH_FAILURES:
                    logger.error(
                        f"CAN disconnected — {health_failures} consecutive "
                        f"health-check failures"
                    )
                    if self.controller:
                        try:
                            self.controller.close()
                        except Exception:
                            pass
                        self.controller = None
                    self.connected = False
                    health_status.update(
                        "can", "unhealthy",
                        f"Disconnected ({health_failures} health-check failures)"
                    )
                    health_failures = 0
                    continue

                if not buf:
                    time.sleep(0.001)

            except Exception as e:
                # SPI-level error (kernel module unloaded / power loss) —
                # backup detection for when the SPI device itself vanishes
                consecutive_errors += 1
                self.stats["errors"] += 1
                logger.warning(f"CAN SPI error ({consecutive_errors}): {e}")
                time.sleep(0.1)

                if consecutive_errors >= MAX_CONSECUTIVE_RX_ERRORS:
                    logger.error(f"CAN disconnected — {consecutive_errors} consecutive SPI errors")
                    if self.controller:
                        try:
                            self.controller.close()
                        except Exception:
                            pass
                        self.controller = None
                    self.connected = False
                    health_status.update("can", "unhealthy", "Disconnected (SPI error)")
                    consecutive_errors = 0

        logger.info("CAN RX loop stopped")

    def _do_health_check(self) -> bool:
        """Send a probe frame on TX buffer 2 and wait for the result.
        Returns True if the CAN bus is alive (TX acknowledged by another
        node), False if the bus appears dead (TX error or bus-off).

        Detection is two-pronged:
        1. Poll TXB2CTRL.TXERR — set when transmission fails after all
           retries (MCP2515 goes bus-off, TXREQ clears, TXERR latches).
        2. Read EFLG.TXBO — bus-off flag set when TEC hits 256.
        Either flag means the bus has no other node to ACK frames."""
        try:
            probe = CANMessage(
                can_id=HEALTH_CHECK_CAN_ID,
                data=[0x00],
                dlc=1,
            )

            if self.controller.send_message(probe, txbuf=HEALTH_TXBUF):
                deadline = time.time() + 0.08
                result = 'pending'
                while result == 'pending' and time.time() < deadline:
                    time.sleep(0.002)
                    try:
                        result = self.controller.check_tx_result(HEALTH_TXBUF)
                        if result == 'error':
                            return False
                        if result == 'success':
                            return True
                        # Still pending — check if chip hit bus-off
                        eflg = self.controller.get_error_flags()
                        if eflg & 0x20:  # TXBO
                            return False
                    except Exception:
                        break
                return False   # timed out while pending → assume failure
            else:
                # TX buffer still busy from a prior (or user) message.
                # Check if the chip is already in bus-off / error-passive.
                try:
                    eflg = self.controller.get_error_flags()
                    if eflg & 0x30:  # TXBO | TXEP
                        return False
                except Exception:
                    pass
                return True   # busy but no error flags → inconclusive, assume OK
        except Exception:
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