#!/usr/bin/env python3
# core/can_manager.py
# CAN bus backend manager for Purple Pi OH2 (MCP2515 over SPI0 CS1).
#
# This is the layer a Flask API (Phase 4) will sit on top of, same role
# as core/io_manager.py plays for DI/DO. It wraps the raw MCP2515 driver
# with a background RX thread, a rolling message log, basic statistics,
# and a subscriber hook for live message streaming (e.g. WebSocket later).

import threading
import time
from datetime import datetime
from collections import deque
from typing import List, Dict, Optional, Callable

from core.mcp2515_driver import MCP2515, CANMessage

DEFAULT_BITRATE = 125_000
DEFAULT_CRYSTAL = 8_000_000


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

    # ----------------------------------------
    # Connection
    # ----------------------------------------
    def connect(self) -> bool:
        with self._lock:
            if self.connected:
                print("⚠️  CAN already connected")
                return True

            print(f"🔌 Connecting MCP2515 (bus={self.spi_bus}, device={self.spi_device}, "
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
            self._start_rx_thread()

            print("✅ CAN connected")
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
            print("✅ CAN disconnected")

    # ----------------------------------------
    # RX thread
    # ----------------------------------------
    def _start_rx_thread(self):
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, name="CAN-RX", daemon=True)
        self.rx_thread.start()

    def _rx_loop(self):
        print("📡 CAN RX loop started")
        consecutive_errors = 0

        while self.running:
            try:
                if not self.connected or not self.controller:
                    time.sleep(0.1)
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
                print(f"⚠️  CAN RX error ({consecutive_errors}): {e}")
                time.sleep(0.1)
                if consecutive_errors >= 10:
                    print("❌ Too many consecutive CAN RX errors - stopping RX thread")
                    self.running = False
                    self.connected = False
                    break

        print("🛑 CAN RX loop stopped")

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
                print(f"⚠️  CAN subscriber error: {e}")

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