#!/usr/bin/env python3
# core/bridges/io_bridge.py
# Phase 8 — Digital I/O ↔ MQTT bridge.
#
# Publish direction (DI → MQTT):
#   Polls DI state every poll_interval_ms milliseconds.
#   When publish_on_change=False, publishes every DI channel on every poll.
#   When publish_on_change=True, only publishes when a DI channel's value
#   actually changes from its last published value.
#   Topic: config["publish_topic_di"] with {channel} replaced by 0–3.
#   Payload: {"value": 1, "channel": 0, "name": "DI0", "timestamp": "..."}
#
# Subscribe direction (MQTT → DO write):
#   Wildcard: config["subscribe_topic_do"]  e.g. "flemingo/edge-01/io/do/+/set"
#   Channel is parsed from the received topic (segment before "/set").
#   Payload: {"value": 1}

import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_DI_CHANNELS  = 4   # Purple Pi OH2: DI0–DI3
_DO_CHANNELS  = 4   # Purple Pi OH2: DO0–DO3


class IOBridge:
    """
    Bridges digital I/O to/from MQTT.

    Lifecycle:
        bridge = IOBridge(mqtt_manager, io_manager, state, config)
        bridge.start()
        bridge.stop()
        bridge.get_status()
    """

    def __init__(self, mqtt_manager, io_manager, state, config: dict):
        self._mqtt       = mqtt_manager
        self._io         = io_manager
        self._state      = state
        self._lock       = threading.Lock()

        self.poll_interval_ms   = config.get("poll_interval_ms", 100)
        self.publish_on_change  = config.get("publish_on_change", True)
        self.publish_topic_di   = config.get(
            "publish_topic_di", "flemingo/edge-01/io/di/{channel}"
        )
        self.subscribe_topic_do = config.get(
            "subscribe_topic_do", "flemingo/edge-01/io/do/+/set"
        )
        self.qos = config.get("qos", 1)

        self.running      = False
        self._poll_thread = None
        self._last_di     = [None] * _DI_CHANNELS

        self.stats = {
            "di_published": 0,
            "do_received":  0,
            "errors":       0,
            "started_at":   None,
        }

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def start(self, poll_interval_ms: int = None, publish_on_change: bool = None,
              qos: int = None):
        with self._lock:
            if self.running:
                raise RuntimeError("IO bridge is already running")

            if poll_interval_ms  is not None: self.poll_interval_ms  = poll_interval_ms
            if publish_on_change is not None: self.publish_on_change = publish_on_change
            if qos               is not None: self.qos               = qos

            # Subscribe to DO command wildcard
            self._mqtt.register_subscription(
                self.subscribe_topic_do, self._on_mqtt_do_set
            )

            self.running = True
            self.stats["started_at"] = datetime.now().isoformat()

            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                name="IO-MQTT-Poll",
                daemon=True,
            )
            self._poll_thread.start()
            logger.info(
                f"IO bridge started | poll={self.poll_interval_ms}ms | "
                f"on_change={self.publish_on_change} | qos={self.qos}"
            )

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self._poll_thread:
            self._poll_thread.join(timeout=2)
            self._poll_thread = None

        self._mqtt.deregister_subscription(self.subscribe_topic_do)
        logger.info("IO bridge stopped")

    # ----------------------------------------------------------------
    # Poll loop (DI → MQTT)
    # ----------------------------------------------------------------
    def _poll_loop(self):
        interval = self.poll_interval_ms / 1000.0
        while self.running:
            try:
                self._do_poll()
            except Exception as e:
                self.stats["errors"] += 1
                logger.warning(f"IO bridge poll error: {e}")
            time.sleep(interval)

    def _do_poll(self):
        try:
            current_di = self._state.get_di()
        except Exception as e:
            logger.warning(f"IO bridge: failed to read DI state: {e}")
            return

        now = datetime.now().isoformat()

        for ch in range(min(len(current_di), _DI_CHANNELS)):
            val = int(current_di[ch])

            if self.publish_on_change and self._last_di[ch] == val:
                continue

            topic = self.publish_topic_di.replace("{channel}", str(ch))
            payload = {
                "value":     val,
                "channel":   ch,
                "name":      f"DI{ch}",
                "timestamp": now,
            }
            ok = self._mqtt.publish(topic, payload, qos=self.qos)
            if ok:
                self.stats["di_published"] += 1
                logger.debug(f"IO bridge DI{ch}={val} → {topic}")

            self._last_di[ch] = val

    # ----------------------------------------------------------------
    # MQTT → DO write
    # ----------------------------------------------------------------
    def _on_mqtt_do_set(self, payload_str: str, topic: str):
        """
        Called when a message arrives on the DO command wildcard topic.
        Parses the channel number from the topic:
          flemingo/edge-01/io/do/2/set → channel 2
        The channel is always the segment immediately before "/set".
        """
        try:
            parts = topic.rstrip("/").split("/")
            # Expect: .../{channel}/set — find "set" then step back one
            if parts[-1] != "set":
                raise ValueError(f"Expected topic ending in '/set', got '{topic}'")
            channel = int(parts[-2])

            if not (0 <= channel < _DO_CHANNELS):
                raise ValueError(f"DO channel must be 0–{_DO_CHANNELS - 1}, got {channel}")

            payload = json.loads(payload_str)
            value   = int(payload.get("value", 0))
            if value not in (0, 1):
                raise ValueError(f"DO value must be 0 or 1, got {value}")

            self._io.write_output(channel, value)
            self.stats["do_received"] += 1
            logger.debug(f"IO bridge DO{channel}={value} ← MQTT ({topic})")

        except json.JSONDecodeError as e:
            self.stats["errors"] += 1
            logger.warning(f"IO bridge DO set: invalid JSON on {topic}: {e}")
        except Exception as e:
            self.stats["errors"] += 1
            logger.warning(f"IO bridge DO set error ({topic}): {e}")

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------
    def get_status(self) -> dict:
        last_di = list(self._last_di)
        return {
            "running":           self.running,
            "poll_interval_ms":  self.poll_interval_ms,
            "publish_on_change": self.publish_on_change,
            "publish_topic_di":  self.publish_topic_di,
            "subscribe_topic_do": self.subscribe_topic_do,
            "qos":               self.qos,
            "last_di":           last_di,
            "stats":             dict(self.stats),
        }

    def update_config(self, poll_interval_ms: int = None,
                      publish_on_change: bool = None, qos: int = None):
        if self.running:
            raise RuntimeError("Stop the bridge before changing its config")
        if poll_interval_ms  is not None: self.poll_interval_ms  = poll_interval_ms
        if publish_on_change is not None: self.publish_on_change = publish_on_change
        if qos               is not None: self.qos               = qos