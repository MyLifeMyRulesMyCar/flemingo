#!/usr/bin/env python3
# core/system_metrics.py
# Phase 9 — OS-level system metrics collector.
#
# Every psutil call is wrapped in try/except so the endpoint never 500s
# on a container or restricted environment. Missing values return None.
#
# Temperature: tries /sys/class/thermal/thermal_zone*/temp first
# (always present on ARM Linux SoCs like RK3566), falls back to
# psutil.sensors_temperatures().

import glob as _glob
import logging
import os
import socket
import time

logger = logging.getLogger(__name__)

try:
    import psutil as _psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    logger.warning(
        "psutil not installed — system metrics limited. " "Run: pip install psutil"
    )


def get_temperature() -> float | None:
    """
    Return CPU/SoC temperature in degrees Celsius, or None if unreadable.
    Tries sysfs first (ARM SoC standard), then psutil fallback.
    Never raises.
    """
    for path in sorted(_glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
                return int(raw) / 1000.0
        except (OSError, ValueError):
            continue

    if _PSUTIL_AVAILABLE:
        try:
            temps = _psutil.sensors_temperatures()
            if temps:
                for chip_name, entries in temps.items():
                    for entry in entries:
                        if entry.current is not None:
                            return entry.current
        except Exception:
            pass

    return None


def _get_primary_ip() -> str:
    """
    First non-loopback IPv4 address from psutil, fallback to 127.0.0.1.
    Never raises.
    """
    if _PSUTIL_AVAILABLE:
        try:
            for iface_name, addrs in _psutil.net_if_addrs().items():
                if iface_name == "lo":
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        return addr.address
        except Exception:
            pass

    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def collect_metrics(mqtt_manager=None) -> dict:
    """
    Gather all OS-level and process-level metrics into a flat dict.
    Every metric block is wrapped in its own try/except — one failing
    sensor doesn't block the rest.

    Pass the MQTTManager instance to include MQTT bridge stats;
    omit or pass None to skip.
    """
    metrics = {}

    # ── CPU ───────────────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            metrics["cpu_percent"] = _psutil.cpu_percent(interval=0.2)
        except Exception as e:
            logger.debug(f"cpu_percent failed: {e}")
            metrics["cpu_percent"] = None
    else:
        metrics["cpu_percent"] = None

    # ── Load average ──────────────────────────────────────────────────
    try:
        la = os.getloadavg()
        metrics["load_average"] = {"1min": la[0], "5min": la[1], "15min": la[2]}
    except Exception:
        metrics["load_average"] = None

    # ── Memory ────────────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            mem = _psutil.virtual_memory()
            metrics["memory"] = {
                "total": mem.total,
                "used": mem.used,
                "available": mem.available,
                "percent": mem.percent,
            }
        except Exception as e:
            logger.debug(f"virtual_memory failed: {e}")
            metrics["memory"] = None
    else:
        metrics["memory"] = None

    # ── Disk ──────────────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            disk = _psutil.disk_usage("/")
            metrics["disk"] = {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
            }
        except Exception as e:
            logger.debug(f"disk_usage failed: {e}")
            metrics["disk"] = None
    else:
        metrics["disk"] = None

    # ── Temperature ───────────────────────────────────────────────────
    metrics["temperature_c"] = get_temperature()

    # ── Network ───────────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            net = _psutil.net_io_counters()
            if net:
                metrics["network"] = {
                    "bytes_sent": net.bytes_sent,
                    "bytes_recv": net.bytes_recv,
                }
            else:
                metrics["network"] = None
        except Exception as e:
            logger.debug(f"net_io_counters failed: {e}")
            metrics["network"] = None
    else:
        metrics["network"] = None

    # ── This process ──────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            proc = _psutil.Process(os.getpid())
            with proc.oneshot():
                metrics["process"] = {
                    "pid": proc.pid,
                    "rss_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
                    "cpu_percent": proc.cpu_percent(),
                    "open_files": len(proc.open_files()),
                    "threads": proc.num_threads(),
                }
        except Exception as e:
            logger.debug(f"Process metrics failed: {e}")
            metrics["process"] = None
    else:
        metrics["process"] = None

    # ── Uptime ────────────────────────────────────────────────────────
    if _PSUTIL_AVAILABLE:
        try:
            metrics["uptime_seconds"] = int(time.time() - _psutil.boot_time())
        except Exception:
            metrics["uptime_seconds"] = None
    else:
        metrics["uptime_seconds"] = None

    # ── MQTT ──────────────────────────────────────────────────────────
    if mqtt_manager is not None:
        try:
            metrics["mqtt"] = mqtt_manager.get_status()
        except Exception as e:
            logger.debug(f"MQTT status failed: {e}")
            metrics["mqtt"] = None
    else:
        metrics["mqtt"] = None

    return metrics
