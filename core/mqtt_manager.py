#!/usr/bin/env python3
# core/mqtt_manager.py
# Phase 8 — MQTT client + bridge registry.
#
# MQTTManager owns exactly one paho-mqtt client connection.
# The three bridges (CAN, Modbus, IO) register their MQTT subscriptions
# here and receive inbound messages via callbacks. The manager handles:
#   - async connect / reconnect (paho loop_start)
#   - re-subscription on reconnect (on_connect re-subscribes everything)
#   - thread-safe publish
#   - topic routing to the right bridge callback (+ and # wildcard aware)
#   - connection and message stats
#
# The module exposes a singleton `mqtt_manager` (None until
# init_mqtt_manager() is called from api/app.py at startup).

import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as _mqtt_lib

    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False
    logger.warning(
        "paho-mqtt not installed — MQTT bridge unavailable. "
        "Run: pip install paho-mqtt"
    )


class MQTTConnectionError(Exception):
    pass


class MQTTManager:
    """
    Manages one paho-mqtt client connection and routes inbound messages
    to registered bridge callbacks.

    Usage (from api/app.py):
        mgr = MQTTManager()
        mgr.configure_bridges(can_bridge, modbus_bridge, io_bridge)
        ...
        mgr.connect(host="192.168.1.x", port=1883)

    Bridges call:
        mgr.register_subscription(topic, callback)   # at bridge.start()
        mgr.deregister_subscription(topic)            # at bridge.stop()
        mgr.publish(topic, payload_dict, qos)

    Callback signature:
        def callback(payload_str: str, topic: str) -> None
    """

    def __init__(self):
        self._client: Optional[object] = None
        self._lock = threading.RLock()
        self._connected = False

        # Registered topic patterns → callbacks
        # Key: topic string (may include + or # wildcards)
        # Value: callable(payload_str, actual_topic)
        self._subscriptions: Dict[str, Callable] = {}

        # Broker config stored for get_status()
        self._broker_config: dict = {}

        # Bridge references — set by configure_bridges()
        self.can_bridge = None
        self.modbus_bridge = None
        self.io_bridge = None

        self.stats = {
            "messages_published": 0,
            "messages_received": 0,
            "reconnects": 0,
            "connected_at": None,
            "disconnected_at": None,
        }

    # ----------------------------------------------------------------
    # Bridge wiring
    # ----------------------------------------------------------------
    def configure_bridges(self, can_bridge, modbus_bridge, io_bridge):
        self.can_bridge = can_bridge
        self.modbus_bridge = modbus_bridge
        self.io_bridge = io_bridge

    # ----------------------------------------------------------------
    # Broker lifecycle
    # ----------------------------------------------------------------
    def connect(
        self,
        host: str,
        port: int = 1883,
        username: str = None,
        password: str = None,
        client_id: str = None,
        keepalive: int = 60,
    ):
        if not _PAHO_AVAILABLE:
            raise MQTTConnectionError(
                "paho-mqtt is not installed. Run: pip install paho-mqtt"
            )

        with self._lock:
            if self._connected:
                raise MQTTConnectionError(
                    "Already connected — disconnect first before reconnecting"
                )

            client_id = client_id or f"flemingo-{int(time.time())}"
            client = _mqtt_lib.Client(client_id=client_id)

            if username:
                client.username_pw_set(username, password or "")

            # Paho will call these from its own network thread
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message

            # Automatic reconnect with exponential backoff (1s → 120s)
            client.reconnect_delay_set(min_delay=1, max_delay=120)

            self._broker_config = {
                "host": host,
                "port": port,
                "client_id": client_id,
                "keepalive": keepalive,
                "has_auth": bool(username),
            }
            self._client = client

        # connect_async returns immediately; loop_start() starts the
        # background network thread that handles the actual socket.
        try:
            client.connect_async(host, port, keepalive=keepalive)
            client.loop_start()
            logger.info(f"MQTT connecting to {host}:{port} (client_id={client_id})")
        except Exception as e:
            with self._lock:
                self._client = None
                self._broker_config = {}
            raise MQTTConnectionError(f"MQTT connect failed: {e}") from e

    def disconnect(self):
        with self._lock:
            if self._client is None:
                return
            client = self._client
            self._client = None
            self._connected = False

        try:
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            logger.warning(f"MQTT disconnect error (ignored): {e}")

        self.stats["disconnected_at"] = datetime.now().isoformat()
        logger.info("MQTT disconnected")

    # ----------------------------------------------------------------
    # Publish
    # ----------------------------------------------------------------
    def publish(
        self, topic: str, payload: dict, qos: int = 0, retain: bool = False
    ) -> bool:
        """
        Publish a dict as JSON to `topic`.
        Returns True on success, False if not connected or paho errors.
        Never raises.
        """
        with self._lock:
            if not self._connected or self._client is None:
                return False
            client = self._client

        try:
            payload_str = json.dumps(payload)
            result = client.publish(topic, payload_str, qos=qos, retain=retain)
            if result.rc == 0:
                self.stats["messages_published"] += 1
                return True
            logger.warning(f"MQTT publish failed: rc={result.rc} topic={topic}")
            return False
        except Exception as e:
            logger.warning(f"MQTT publish error: {e}")
            return False

    # ----------------------------------------------------------------
    # Subscription management
    # ----------------------------------------------------------------
    def register_subscription(self, topic: str, callback: Callable):
        """
        Register a callback for `topic` (may use + and # wildcards).
        If already connected, subscribes immediately. If not connected,
        the on_connect handler will subscribe when connection is established.
        """
        with self._lock:
            self._subscriptions[topic] = callback
            if self._connected and self._client:
                self._client.subscribe(topic)
                logger.debug(f"MQTT subscribed: {topic}")

    def deregister_subscription(self, topic: str):
        with self._lock:
            self._subscriptions.pop(topic, None)
            if self._connected and self._client:
                try:
                    self._client.unsubscribe(topic)
                    logger.debug(f"MQTT unsubscribed: {topic}")
                except Exception:
                    pass

    # ----------------------------------------------------------------
    # paho callbacks (called from paho's network thread)
    # ----------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            with self._lock:
                self._connected = True
                self.stats["connected_at"] = datetime.now().isoformat()
                topics = list(self._subscriptions.keys())

            # Re-subscribe all registered topics (handles broker restart)
            for topic in topics:
                client.subscribe(topic)
                logger.debug(f"MQTT re-subscribed: {topic}")

            logger.info(
                f"MQTT connected to "
                f"{self._broker_config.get('host')}:"
                f"{self._broker_config.get('port')}"
            )
        else:
            _RC_MESSAGES = {
                1: "unacceptable protocol version",
                2: "identifier rejected",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized",
            }
            reason = _RC_MESSAGES.get(rc, f"unknown rc={rc}")
            logger.error(f"MQTT connection refused: {reason}")

    def _on_disconnect(self, client, userdata, rc):
        with self._lock:
            was_connected = self._connected
            self._connected = False

        if rc != 0 and was_connected:
            self.stats["reconnects"] += 1
            self.stats["disconnected_at"] = datetime.now().isoformat()
            logger.warning(
                f"MQTT disconnected unexpectedly (rc={rc}), "
                f"paho will attempt reconnect"
            )
        else:
            logger.info("MQTT disconnected cleanly")

    def _on_message(self, client, userdata, message):
        topic = message.topic
        payload_str = message.payload.decode("utf-8", errors="replace")
        self.stats["messages_received"] += 1

        with self._lock:
            subscriptions = dict(self._subscriptions)

        matched = False
        for pattern, callback in subscriptions.items():
            if self._topic_matches(pattern, topic):
                matched = True
                try:
                    callback(payload_str, topic)
                except Exception as e:
                    logger.warning(
                        f"MQTT callback error for topic '{topic}' "
                        f"(pattern '{pattern}'): {e}"
                    )

        if not matched:
            logger.debug(f"MQTT: no handler for topic '{topic}'")

    # ----------------------------------------------------------------
    # MQTT topic pattern matching (+ and # wildcards)
    # ----------------------------------------------------------------
    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """
        Returns True if `topic` matches the MQTT `pattern`.
        Rules per MQTT 3.1.1 spec §4.7:
          +  matches exactly one level
          #  matches zero or more remaining levels (must be last segment)
        """
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        pi = 0  # pattern index
        ti = 0  # topic index

        while pi < len(pattern_parts):
            pp = pattern_parts[pi]
            if pp == "#":
                return True  # matches anything remaining
            if ti >= len(topic_parts):
                return False
            if pp != "+" and pp != topic_parts[ti]:
                return False
            pi += 1
            ti += 1

        return ti == len(topic_parts)

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------
    def get_status(self) -> dict:
        with self._lock:
            broker = dict(self._broker_config)
            stats = dict(self.stats)
            connected = self._connected
            sub_count = len(self._subscriptions)

        return {
            "connected": connected,
            "broker": broker,
            "active_subscriptions": sub_count,
            "stats": stats,
            "bridges": {
                "can": self.can_bridge.get_status() if self.can_bridge else None,
                "modbus": (
                    self.modbus_bridge.get_status() if self.modbus_bridge else None
                ),
                "io": self.io_bridge.get_status() if self.io_bridge else None,
            },
            "paho_available": _PAHO_AVAILABLE,
        }


# ----------------------------------------------------------------
# Module-level singleton + factory
# ----------------------------------------------------------------
mqtt_manager: Optional[MQTTManager] = None


def init_mqtt_manager(
    can_mgr, modbus_mgr, io_mgr, state_obj, mqtt_cfg: dict
) -> MQTTManager:
    """
    Called once from api/app.py at startup.

    Imports bridge classes here (not at module level) to avoid circular
    imports — bridges import from core/, not from mqtt_manager.
    """
    from core.bridges.can_bridge import CANBridge
    from core.bridges.modbus_bridge import ModbusBridge
    from core.bridges.io_bridge import IOBridge

    global mqtt_manager
    mqtt_manager = MQTTManager()

    bridge_cfg = mqtt_cfg.get("bridges", {})
    can_bridge = CANBridge(mqtt_manager, can_mgr, bridge_cfg.get("can", {}))
    modbus_bridge = ModbusBridge(mqtt_manager, modbus_mgr, bridge_cfg.get("modbus", {}))
    io_bridge = IOBridge(mqtt_manager, io_mgr, state_obj, bridge_cfg.get("io", {}))

    mqtt_manager.configure_bridges(can_bridge, modbus_bridge, io_bridge)
    logger.info("MQTT manager initialised (not yet connected to broker)")
    return mqtt_manager
