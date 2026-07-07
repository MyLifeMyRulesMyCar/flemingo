#!/usr/bin/env python3
# core/bridges/modbus_bridge.py
# Phase 8 — Modbus ↔ MQTT bridge.
#
# Publish direction (Modbus → MQTT):
#   A background poll thread reads the configured register_list every
#   poll_interval_s seconds and publishes each value.
#   Topic template: config["publish_topic_template"]
#     e.g. "flemingo/edge-01/modbus/{dev_id}/r{address}"
#   Payload: {"value": 42, "device": "TestDevice", "device_id": "dev1",
#             "address": 0, "function_code": 3, "timestamp": "..."}
#
#   register_list is provided at start() time:
#   [{"device_id": "dev1", "address": 0, "function_code": 3}, ...]
#
#   Disconnected devices are skipped gracefully — the bridge keeps
#   polling other registers and logs a warning, no crash.
#
# Subscribe direction (MQTT → Modbus write):
#   Wildcard subscription: "flemingo/edge-01/modbus/+/set"
#   The {dev_id} segment is parsed from the received topic.
#   Payload: {"address": 0, "value": 42, "function_code": 6}
#   FC6 = write holding register (value 0–65535)
#   FC5 = write coil (value 0 or 1)

import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class ModbusBridge:
    """
    Bridges Modbus register values to/from MQTT.

    Lifecycle:
        bridge = ModbusBridge(mqtt_manager, modbus_manager, config)
        bridge.start(register_list=[...], poll_interval_s=5)
        bridge.stop()
        bridge.get_status()
    """

    def __init__(self, mqtt_manager, modbus_manager, config: dict):
        self._mqtt = mqtt_manager
        self._modbus = modbus_manager
        self._lock = threading.Lock()

        self.poll_interval_s = config.get("poll_interval_s", 5)
        self.publish_topic_tmpl = config.get(
            "publish_topic_template", "flemingo/edge-01/modbus/{dev_id}/r{address}"
        )
        self.subscribe_topic_tmpl = config.get(
            "subscribe_topic_template", "flemingo/edge-01/modbus/+/set"
        )
        self.qos = config.get("qos", 1)

        self.running = False
        self.register_list = []  # [{device_id, address, function_code}, ...]
        self._poll_thread = None
        self.stop_reason = None

        self.stats = {
            "published": 0,
            "received": 0,
            "errors": 0,
            "started_at": None,
        }

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def start(
        self,
        register_list: list = None,
        poll_interval_s: float = None,
        publish_topic_template: str = None,
        subscribe_topic_template: str = None,
        qos: int = None,
    ):
        with self._lock:
            if self.running:
                raise RuntimeError("Modbus bridge is already running")

            if register_list is not None:
                self.register_list = register_list
            if poll_interval_s is not None:
                self.poll_interval_s = poll_interval_s
            if publish_topic_template is not None:
                self.publish_topic_tmpl = publish_topic_template
            if subscribe_topic_template is not None:
                self.subscribe_topic_tmpl = subscribe_topic_template
            if qos is not None:
                self.qos = qos

            self._mqtt.register_subscription(
                self.subscribe_topic_tmpl, self._on_mqtt_write
            )

            self.stop_reason = None
            self.running = True
            self.stats["started_at"] = datetime.now().isoformat()

            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                name="Modbus-MQTT-Poll",
                daemon=True,
            )
            self._poll_thread.start()
            logger.info(
                f"Modbus bridge started | "
                f"{len(self.register_list)} register(s) | "
                f"poll={self.poll_interval_s}s | qos={self.qos}"
            )

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self._poll_thread:
            self._poll_thread.join(timeout=self.poll_interval_s + 2)
            self._poll_thread = None

        self._mqtt.deregister_subscription(self.subscribe_topic_tmpl)
        logger.info("Modbus bridge stopped")

    # ----------------------------------------------------------------
    # Poll loop (Modbus → MQTT)
    # ----------------------------------------------------------------
    def _poll_loop(self):
        consecutive_errors = 0
        max_consecutive = 5

        while self.running:
            try:
                success = self._do_poll()
                if success:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive:
                        self.stop_reason = (
                            f"{consecutive_errors} consecutive all-failed reads"
                        )
                        logger.warning(
                            f"Modbus bridge: {self.stop_reason} — auto-stopping"
                        )
                        self.stop()
                        return
            except Exception as e:
                consecutive_errors += 1
                self.stats["errors"] += 1
                if consecutive_errors >= max_consecutive:
                    self.stop_reason = f"{consecutive_errors} consecutive exceptions"
                    logger.warning(f"Modbus bridge: {self.stop_reason} — auto-stopping")
                    self.stop()
                    return
                logger.warning(f"Modbus bridge poll error: {e}")

            time.sleep(self.poll_interval_s)

    def _do_poll(self):
        had_success = False
        for reg in list(self.register_list):
            if not self.running:
                break

            device_id = reg.get("device_id")
            address = reg.get("address")
            fc = reg.get("function_code", 3)

            if device_id is None or address is None:
                continue

            try:
                value = self._read_register(device_id, address, fc)
                if value is None:
                    continue  # device disconnected or breaker open — skip silently

                had_success = True
                topic = self.publish_topic_tmpl.replace(
                    "{dev_id}", str(device_id)
                ).replace("{address}", str(address))

                # Get human-readable device name if available
                dev = self._modbus.get_device(device_id)
                payload = {
                    "value": value,
                    "device": dev.name if dev else device_id,
                    "device_id": device_id,
                    "address": address,
                    "function_code": fc,
                    "timestamp": datetime.now().isoformat(),
                }
                ok = self._mqtt.publish(topic, payload, qos=self.qos)
                if ok:
                    self.stats["published"] += 1

            except Exception as e:
                self.stats["errors"] += 1
                logger.warning(
                    f"Modbus bridge: error reading {device_id}:addr{address}: {e}"
                )
        return had_success

    def _read_register(self, device_id, address, fc):
        if fc == 3:
            return self._modbus.read_holding_register(device_id, address)
        elif fc == 4:
            return self._modbus.read_input_register(device_id, address)
        elif fc == 1:
            return self._modbus.read_coil(device_id, address)
        elif fc == 2:
            return self._modbus.read_discrete_input(device_id, address)
        else:
            logger.warning(f"Modbus bridge: unsupported function code {fc}")
            return None

    # ----------------------------------------------------------------
    # MQTT → Modbus write
    # ----------------------------------------------------------------
    def _on_mqtt_write(self, payload_str: str, topic: str):
        """
        Called when a message arrives on the write wildcard topic.
        Extracts device_id from the topic, parses the payload, writes.
        Topic shape: {prefix}/{device_id}/modbus/{dev_id}/set
        The {dev_id} segment is at a fixed depth from the subscribe
        wildcard — we locate it by finding "modbus" then taking the
        next segment.
        """
        try:
            # Parse device_id from topic: find segment after "modbus"
            parts = topic.split("/")
            try:
                modbus_idx = parts.index("modbus")
                device_id = parts[modbus_idx + 1]
            except (ValueError, IndexError):
                raise ValueError(f"Cannot extract device_id from topic '{topic}'")

            payload = json.loads(payload_str)
            address = int(payload.get("address", 0))
            value = payload.get("value")
            fc = int(payload.get("function_code", 6))

            if value is None:
                raise ValueError("'value' is required in write payload")

            # Validate value ranges
            if fc == 6:
                value = int(value)
                if not (0 <= value <= 65535):
                    raise ValueError(f"FC6 value must be 0–65535, got {value}")
                self._modbus.write_holding_register(device_id, address, value)

            elif fc == 5:
                value = int(value)
                if value not in (0, 1):
                    raise ValueError(f"FC5 coil value must be 0 or 1, got {value}")
                self._modbus.write_coil(device_id, address, value)

            else:
                raise ValueError(f"Write function_code must be 5 or 6, got {fc}")

            self.stats["received"] += 1
            logger.debug(
                f"Modbus bridge write: {device_id} addr={address} "
                f"fc={fc} value={value}"
            )

        except json.JSONDecodeError as e:
            self.stats["errors"] += 1
            logger.warning(f"Modbus bridge write: invalid JSON on {topic}: {e}")
        except Exception as e:
            self.stats["errors"] += 1
            logger.warning(f"Modbus bridge write error ({topic}): {e}")

    # ----------------------------------------------------------------
    # Register list management
    # ----------------------------------------------------------------
    def update_register_list(self, register_list: list):
        """Hot-update the register list while the bridge is running."""
        with self._lock:
            self.register_list = list(register_list)
        logger.info(
            f"Modbus bridge: register list updated ({len(register_list)} entries)"
        )

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------
    def get_status(self) -> dict:
        return {
            "running": self.running,
            "poll_interval_s": self.poll_interval_s,
            "publish_topic_template": self.publish_topic_tmpl,
            "subscribe_topic_template": self.subscribe_topic_tmpl,
            "qos": self.qos,
            "register_count": len(self.register_list),
            "registers": list(self.register_list),
            "stats": dict(self.stats),
            "stop_reason": self.stop_reason,
        }

    def update_config(
        self,
        poll_interval_s: float = None,
        publish_topic_template: str = None,
        subscribe_topic_template: str = None,
        qos: int = None,
    ):
        if self.running:
            raise RuntimeError("Stop the bridge before changing its config")
        if poll_interval_s is not None:
            self.poll_interval_s = poll_interval_s
        if publish_topic_template is not None:
            self.publish_topic_tmpl = publish_topic_template
        if subscribe_topic_template is not None:
            self.subscribe_topic_tmpl = subscribe_topic_template
        if qos is not None:
            self.qos = qos
