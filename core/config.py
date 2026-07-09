#!/usr/bin/env python3
# core/config.py
# Loads config/reliability.yaml - the thresholds for circuit breakers,
# retry/backoff, and the watchdog. Kept separate from hardcoded
# constants in can_manager.py/modbus_manager.py/watchdog.py so tuning
# these (e.g. "give the watchdog more slack on a slow RS485 device")
# doesn't require touching code or redeploying.
#
# If the file is missing or PyYAML isn't installed, this falls back to
# sane defaults and logs a warning - it never raises, since a missing
# config file shouldn't be the reason your daemon won't start.

import logging
import os

VERSION = "0.13.0"

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "reliability.yaml",
)

DEFAULTS = {
    "circuit_breaker": {
        "can": {"failure_threshold": 5, "timeout": 60},
        "modbus": {"failure_threshold": 5, "timeout": 60},
    },
    "retry": {
        "max_retries": 3,
        "initial_delay": 1,
        "max_delay": 30,
    },
    "watchdog": {
        "timeout": 30,
        "check_interval": 10,
        "exit_on_timeout": True,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/flemingo.log",
        "max_bytes": 5242880,
        "backup_count": 5,
    },
    "security": {
        "login_max_attempts": 5,
        "login_window_minutes": 15,
    },
}

_cache = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge `override` into a copy of `base`, recursively for nested dicts."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_reliability_config(path: str = None, force_reload: bool = False) -> dict:
    """
    Returns the merged reliability config (file values override
    DEFAULTS; missing keys fall back to DEFAULTS). Cached after the
    first successful load - pass force_reload=True to re-read the file.
    """
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    path = path or _DEFAULT_CONFIG_PATH
    loaded = {}

    if os.path.exists(path):
        try:
            import yaml

            with open(path, "r") as f:
                loaded = yaml.safe_load(f) or {}
        except ImportError:
            logger.warning(
                "PyYAML not installed - using built-in reliability defaults. "
                "Run `pip install pyyaml` to use config/reliability.yaml."
            )
        except Exception as e:
            logger.warning(
                f"Could not parse {path} ({e}) - using built-in reliability defaults"
            )
    else:
        logger.info(f"No reliability config at {path} - using built-in defaults")

    _cache = _deep_merge(DEFAULTS, loaded)
    return _cache


# ─── MQTT config ─────────────────────────────────────────────────────
_MQTT_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "mqtt.yaml",
)

_MQTT_DEFAULTS = {
    "broker": {
        "host": "127.0.0.1",
        "port": 1883,
        "client_id": "flemingo-edge-01",
        "username": "",
        "password": "",
        "keepalive": 60,
    },
    "bridges": {
        "prefix": "flemingo",
        "device_id": "edge-01",
        "can": {
            "publish_topic": "{prefix}/{device_id}/can/rx",
            "subscribe_topic": "{prefix}/{device_id}/can/tx",
            "qos": 0,
        },
        "modbus": {
            "poll_interval_s": 5,
            "publish_topic_template": "{prefix}/{device_id}/modbus/{dev_id}/r{address}",
            "subscribe_topic_template": "{prefix}/{device_id}/modbus/+/set",
            "qos": 1,
            "registers": [],
        },
        "io": {
            "poll_interval_ms": 100,
            "publish_on_change": True,
            "publish_topic_di": "{prefix}/{device_id}/io/di/{channel}",
            "subscribe_topic_do": "{prefix}/{device_id}/io/do/+/set",
            "qos": 1,
        },
    },
}

_mqtt_cache = None


def load_mqtt_config(path=None, force_reload=False):
    """
    Load config/mqtt.yaml and merge with built-in defaults.
    Resolves {prefix} and {device_id} in all topic strings so bridges
    receive ready-to-use topic strings, not raw templates.
    Never raises — missing file falls back to defaults silently.
    """
    global _mqtt_cache
    if _mqtt_cache is not None and not force_reload:
        return _mqtt_cache

    path = path or _MQTT_DEFAULT_CONFIG_PATH
    loaded = {}

    if os.path.exists(path):
        try:
            import yaml

            with open(path, "r") as f:
                loaded = yaml.safe_load(f) or {}
        except ImportError:
            logger.warning("PyYAML not installed - using built-in MQTT defaults")
        except Exception as e:
            logger.warning(f"Could not parse {path} ({e}) - using MQTT defaults")
    else:
        logger.info(f"No MQTT config at {path} - using built-in defaults")

    merged = _deep_merge(_MQTT_DEFAULTS, loaded)

    # Resolve {prefix} and {device_id} in every topic string
    prefix = merged["bridges"]["prefix"]
    device_id = merged["bridges"]["device_id"]

    def _resolve(v):
        if isinstance(v, str):
            return v.replace("{prefix}", prefix).replace("{device_id}", device_id)
        return v

    for bridge in ("can", "modbus", "io"):
        cfg = merged["bridges"][bridge]
        for k, v in cfg.items():
            cfg[k] = _resolve(v)

    _mqtt_cache = merged
    return _mqtt_cache
