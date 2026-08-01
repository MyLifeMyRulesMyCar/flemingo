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
            "id_filter": [],
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


# ═══════════════════════════════════════════════════════════════════
# Hardware config (GPIO pin map, CAN bitrate / crystal)
# ═══════════════════════════════════════════════════════════════════

_HARDWARE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "hardware.yaml",
)

HARDWARE_DEFAULTS = {
    "gpio": {
        "outputs": {
            "DO0": {"chip": "/dev/gpiochip1", "line": 24},
            "DO1": {"chip": "/dev/gpiochip1", "line": 25},
            "DO2": {"chip": "/dev/gpiochip1", "line": 26},
            "DO3": {"chip": "/dev/gpiochip1", "line": 27},
        },
        "inputs": {
            "DI0": {"chip": "/dev/gpiochip4", "line": 4},
            "DI1": {"chip": "/dev/gpiochip4", "line": 6},
            "DI2": {"chip": "/dev/gpiochip3", "line": 2},
            "DI3": {"chip": "/dev/gpiochip3", "line": 3},
        },
    },
    "can": {"bitrate": 125000, "crystal": 8000000},
}

_VALID_OUTPUTS = {"DO0", "DO1", "DO2", "DO3"}
_VALID_INPUTS = {"DI0", "DI1", "DI2", "DI3"}
_VALID_CAN_BITRATES = {125000, 250000, 500000, 1000000}
_VALID_CAN_CRYSTALS = {8000000, 16000000}

_hardware_cache = None


def load_hardware_config(path=None, force_reload=False):
    global _hardware_cache
    if _hardware_cache is not None and not force_reload:
        return _hardware_cache

    path = path or _HARDWARE_CONFIG_PATH
    if not os.path.exists(path):
        _hardware_cache = dict(HARDWARE_DEFAULTS)
        return _hardware_cache

    loaded = {}
    try:
        import yaml

        with open(path, "r") as f:
            loaded = yaml.safe_load(f) or {}
    except ImportError:
        logger.warning("PyYAML not installed — using built-in hardware defaults.")
        _hardware_cache = dict(HARDWARE_DEFAULTS)
        return _hardware_cache
    except Exception as e:
        logger.error(
            "Could not parse %s (%s) — using built-in hardware defaults.", path, e
        )
        _hardware_cache = dict(HARDWARE_DEFAULTS)
        return _hardware_cache

    merged = _deep_merge(HARDWARE_DEFAULTS, loaded)
    _validate_hardware_config(merged)
    _hardware_cache = merged
    return _hardware_cache


def _validate_hardware_config(cfg):
    """Fail loudly on malformed hardware.yaml — this describes real wiring,
    not tuneable thresholds. A wrong pin mapping is the difference between
    DO2 switching the right actuator and one it shouldn't."""
    for name, pin in cfg["gpio"]["outputs"].items():
        if name not in _VALID_OUTPUTS:
            raise ValueError(
                f"hardware.yaml: unknown output channel '{name}' "
                f"(valid: {sorted(_VALID_OUTPUTS)})"
            )
        chip = str(pin.get("chip", ""))
        if not chip.startswith("/dev/gpiochip"):
            raise ValueError(
                f"hardware.yaml: {name} has an invalid chip path: {chip!r}"
            )
        line = pin.get("line")
        if not isinstance(line, int) or line < 0:
            raise ValueError(
                f"hardware.yaml: {name} has an invalid line number: {line!r}"
            )

    for name, pin in cfg["gpio"]["inputs"].items():
        if name not in _VALID_INPUTS:
            raise ValueError(
                f"hardware.yaml: unknown input channel '{name}' "
                f"(valid: {sorted(_VALID_INPUTS)})"
            )
        chip = str(pin.get("chip", ""))
        if not chip.startswith("/dev/gpiochip"):
            raise ValueError(
                f"hardware.yaml: {name} has an invalid chip path: {chip!r}"
            )
        line = pin.get("line")
        if not isinstance(line, int) or line < 0:
            raise ValueError(
                f"hardware.yaml: {name} has an invalid line number: {line!r}"
            )

    bitrate = cfg["can"]["bitrate"]
    if bitrate not in _VALID_CAN_BITRATES:
        raise ValueError(
            f"hardware.yaml: unsupported CAN bitrate {bitrate} "
            f"(valid: {sorted(_VALID_CAN_BITRATES)})"
        )

    crystal = cfg["can"]["crystal"]
    if crystal not in _VALID_CAN_CRYSTALS:
        raise ValueError(
            f"hardware.yaml: unsupported CAN crystal {crystal} "
            f"(valid: {sorted(_VALID_CAN_CRYSTALS)})"
        )
