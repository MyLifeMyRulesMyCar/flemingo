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

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "reliability.yaml",
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
    },
    "logging": {
        "level": "INFO",
        "file": "logs/flemingo.log",
        "max_bytes": 5242880,
        "backup_count": 5,
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
            logger.warning("PyYAML not installed - using built-in reliability defaults. "
                            "Run `pip install pyyaml` to use config/reliability.yaml.")
        except Exception as e:
            logger.warning(f"Could not parse {path} ({e}) - using built-in reliability defaults")
    else:
        logger.info(f"No reliability config at {path} - using built-in defaults")

    _cache = _deep_merge(DEFAULTS, loaded)
    return _cache
