#!/usr/bin/env python3
# api/health_routes.py
# Aggregate health/status across GPIO, CAN, and Modbus.
# Mirrors the reference project's health_routes.py shape, scoped to
# what's actually built so far.

from flask import Blueprint, jsonify
from datetime import datetime
import time

health_api = Blueprint("health_api", __name__)

_io_manager = None
_can_manager = None
_modbus_manager = None
_start_time = time.time()


def set_managers(io_manager, can_manager, modbus_manager):
    global _io_manager, _can_manager, _modbus_manager
    _io_manager = io_manager
    _can_manager = can_manager
    _modbus_manager = modbus_manager


@health_api.route("/api/health", methods=["GET"])
def health():
    """Basic liveness check - always 200 if the process is up."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": int(time.time() - _start_time),
    })


@health_api.route("/api/health/detailed", methods=["GET"])
def health_detailed():
    """Per-subsystem status snapshot."""
    io_status = _io_manager.get_status()
    can_status = _can_manager.get_status()
    devices = _modbus_manager.get_all_devices()

    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": int(time.time() - _start_time),
        "gpio": {
            "simulation": io_status["simulation"],
            "note": "simulation=true means GPIO permissions weren't applied "
                    "(sudo chmod 666 /dev/gpiochip1 /dev/gpiochip3 /dev/gpiochip4)",
        },
        "can": {
            "connected": can_status["connected"],
            "bitrate": can_status["bitrate"],
            "rx_total": can_status["rx_total"],
            "tx_total": can_status["tx_total"],
            "errors": can_status["errors"],
        },
        "modbus": {
            "devices_count": len(devices),
            "connected_count": sum(1 for d in devices if d["connected"]),
            "devices": devices,
        },
    })