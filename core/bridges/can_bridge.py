#!/usr/bin/env python3
# core/bridges/can_bridge.py
# Phase 8 — CAN ↔ MQTT bridge.
#
# Publish direction (CAN → MQTT):
#   Hooks into can_manager.subscribe() so every RX frame is forwarded
#   to the MQTT broker immediately — no polling, pure push.
#   Topic: config["publish_topic"]  (default "flemingo/edge-01/can/rx")
#   Payload: {"can_id": 291, "data": [1,2,3], "dlc": 3,
#             "extended": false, "timestamp": "2026-07-03T..."}
#
# Subscribe direction (MQTT → CAN TX):
#   Listens on config["subscribe_topic"] (default "flemingo/edge-01/can/tx")
#   Payload: {"can_id": 291, "data": [1,2,3], "extended": false}
#   The payload is validated before hitting the CAN manager so malformed
#   MQTT messages don't crash the RX loop.

import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class CANBridge:
    """
    Bridges CAN traffic to/from MQTT.

    Lifecycle:
        bridge = CANBridge(mqtt_manager, can_manager, config)
        bridge.start()          # register CAN subscriber + MQTT subscription
        bridge.stop()           # clean teardown, idempotent
        bridge.get_status()     # dict safe to jsonify
    """

    def __init__(self, mqtt_manager, can_manager, config: dict):
        self._mqtt = mqtt_manager
        self._can = can_manager
        self._lock = threading.Lock()

        # Topic config — overridable at start() time
        self.publish_topic = config.get("publish_topic", "flemingo/edge-01/can/rx")
        self.subscribe_topic = config.get("subscribe_topic", "flemingo/edge-01/can/tx")
        self.qos = config.get("qos", 0)

        self.running = False
        self.stop_reason = None
        self._health_thread = None
        self.stats = {
            "published": 0,  # CAN → MQTT
            "received": 0,  # MQTT → CAN TX
            "errors": 0,
            "started_at": None,
        }

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def start(
        self, publish_topic: str = None, subscribe_topic: str = None, qos: int = None
    ):
        with self._lock:
            if self.running:
                raise RuntimeError("CAN bridge is already running")

            if publish_topic is not None:
                self.publish_topic = publish_topic
            if subscribe_topic is not None:
                self.subscribe_topic = subscribe_topic
            if qos is not None:
                self.qos = qos

            # Hook into CAN RX stream
            self._can.subscribe(self._on_can_rx)

            # Register inbound MQTT command subscription
            self._mqtt.register_subscription(self.subscribe_topic, self._on_mqtt_tx)

            self.running = True
            self.stop_reason = None
            self.stats["started_at"] = datetime.now().isoformat()

            self._health_thread = threading.Thread(
                target=self._health_loop,
                name="CAN-Bridge-Health",
                daemon=True,
            )
            self._health_thread.start()

            logger.info(
                f"CAN bridge started | "
                f"publish={self.publish_topic} | "
                f"subscribe={self.subscribe_topic} | qos={self.qos}"
            )

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self._can.unsubscribe(self._on_can_rx)
            self._mqtt.deregister_subscription(self.subscribe_topic)
            self.running = False

        if self._health_thread:
            self._health_thread.join(timeout=8)
            self._health_thread = None

        logger.info("CAN bridge stopped")

    # ----------------------------------------------------------------
    # CAN → MQTT  (called from can_manager's RX thread)
    # ----------------------------------------------------------------
    def _on_can_rx(self, entry: dict):
        """
        Callback registered with can_manager.subscribe().
        `entry` is the dict that can_manager._handle_rx() produces:
          {"can_id":..., "data":..., "dlc":..., "extended":..., "timestamp":...}
        """
        try:
            payload = {
                "can_id": entry.get("can_id"),
                "data": entry.get("data", []),
                "dlc": entry.get("dlc", len(entry.get("data", []))),
                "extended": entry.get("extended", False),
                "timestamp": entry.get("timestamp", datetime.now().isoformat()),
            }
            ok = self._mqtt.publish(self.publish_topic, payload, qos=self.qos)
            if ok:
                self.stats["published"] += 1
        except Exception as e:
            self.stats["errors"] += 1
            logger.warning(f"CAN bridge publish error: {e}")

    # ----------------------------------------------------------------
    # Health check thread — auto-stops the bridge when CAN disconnects
    # ----------------------------------------------------------------
    def _health_loop(self):
        interval = 5
        max_consecutive = 3
        # Grace period — don't check immediately after start
        time.sleep(interval)

        while self.running:
            try:
                status = self._can.get_status()
                if not status.get("connected"):
                    max_consecutive -= 1
                    if max_consecutive <= 0:
                        self.stop_reason = "CAN bus disconnected"
                        logger.warning(
                            f"CAN bridge: {self.stop_reason} — auto-stopping"
                        )
                        self.stop()
                        return
                else:
                    max_consecutive = 3
            except Exception:
                max_consecutive -= 1
                if max_consecutive <= 0:
                    self.stop_reason = "CAN manager unresponsive"
                    logger.warning(f"CAN bridge: {self.stop_reason} — auto-stopping")
                    self.stop()
                    return
            time.sleep(interval)

    # ----------------------------------------------------------------
    # MQTT → CAN TX  (called from mqtt_manager's network thread)
    # ----------------------------------------------------------------
    def _on_mqtt_tx(self, payload_str: str, topic: str):
        """
        Receives a JSON command from the MQTT TX topic and sends a CAN frame.
        Validates before calling can_manager to avoid corrupting the bus.
        """
        try:
            payload = json.loads(payload_str)
            can_id = payload.get("can_id")
            data = payload.get("data", [])
            extended = bool(payload.get("extended", False))

            if can_id is None:
                raise ValueError("'can_id' is required")

            can_id = int(can_id, 0) if isinstance(can_id, str) else int(can_id)
            data = [int(b, 0) if isinstance(b, str) else int(b) for b in data]

            max_id = 0x1FFFFFFF if extended else 0x7FF
            if not (0 <= can_id <= max_id):
                raise ValueError(
                    f"can_id {hex(can_id)} out of range for {'ext' if extended else 'std'} frame"
                )
            if len(data) > 8:
                raise ValueError(f"CAN payload must be ≤8 bytes, got {len(data)}")
            if any(not (0 <= b <= 255) for b in data):
                raise ValueError("All data bytes must be 0–255")

            self._can.send_message(can_id, data, extended=extended)
            self.stats["received"] += 1
            logger.debug(f"CAN bridge TX: id={hex(can_id)} data={data}")

        except json.JSONDecodeError as e:
            self.stats["errors"] += 1
            logger.warning(f"CAN bridge TX: invalid JSON on {topic}: {e}")
        except Exception as e:
            self.stats["errors"] += 1
            logger.warning(f"CAN bridge TX error: {e}")

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------
    def get_status(self) -> dict:
        return {
            "running": self.running,
            "publish_topic": self.publish_topic,
            "subscribe_topic": self.subscribe_topic,
            "qos": self.qos,
            "stats": dict(self.stats),
            "stop_reason": self.stop_reason,
        }

    def update_config(
        self, publish_topic: str = None, subscribe_topic: str = None, qos: int = None
    ):
        """Update topic config. Bridge must be stopped before calling this."""
        if self.running:
            raise RuntimeError("Stop the bridge before changing its config")
        if publish_topic is not None:
            self.publish_topic = publish_topic
        if subscribe_topic is not None:
            self.subscribe_topic = subscribe_topic
        if qos is not None:
            self.qos = qos
