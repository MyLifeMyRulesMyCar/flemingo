#!/usr/bin/env python3
# api/modbus_routes.py
# Phase 7: all route bodies and query params validated via api/validators.py.
# ValidationError → 400. Manager errors (ValueError/RuntimeError) → 400 or 504.

from flask import Blueprint, jsonify, request
from core.modbus_manager import MODBUS_PORTS
from api.auth_decorators import require_role
from api.validators import (
    ValidationError, parse_body, parse_int,
    require_fields,
    validate_modbus_slave_id, validate_modbus_address,
    validate_modbus_register_value, validate_modbus_coil_value,
    validate_modbus_baudrate, validate_modbus_parity,
    validate_modbus_stopbits, validate_modbus_port,
    validate_modbus_scan_range, validate_device_name,
    validate_count,
)

modbus_api = Blueprint("modbus_api", __name__)

_modbus_manager = None


def set_modbus_manager(manager):
    global _modbus_manager
    _modbus_manager = manager


@modbus_api.route("/api/modbus/ports", methods=["GET"])
@require_role("viewer")
def get_ports():
    return jsonify({"ports": MODBUS_PORTS})


@modbus_api.route("/api/modbus/devices", methods=["GET"])
@require_role("viewer")
def get_devices():
    return jsonify({"devices": _modbus_manager.get_all_devices()})


@modbus_api.route("/api/modbus/devices", methods=["POST"])
@require_role("admin")
def create_device():
    """Body: {"name":"...", "port":"ttyUSB0", "slave_id":1,
              "baudrate":115200, "parity":"N", "stopbits":1}
    port     — must be a key in /api/modbus/ports
    slave_id — 1–247
    baudrate — 9600 / 19200 / 38400 / 57600 / 115200 / 230400
    parity   — N / E / O
    stopbits — 1 or 2"""
    try:
        data = parse_body(request)
        require_fields(data, "name", "port", "slave_id")

        name      = validate_device_name(data["name"])
        port      = validate_modbus_port(data["port"], MODBUS_PORTS)
        slave_id  = validate_modbus_slave_id(data["slave_id"])
        baudrate  = validate_modbus_baudrate(data.get("baudrate", 115200))
        parity    = validate_modbus_parity(data.get("parity", "N"))
        stopbits  = validate_modbus_stopbits(data.get("stopbits", 1))

        device_id = _modbus_manager.add_device(
            name=name, port=port, slave_id=slave_id,
            baudrate=baudrate, parity=parity, stopbits=stopbits,
        )
        return jsonify({"message": "Device added", "device_id": device_id}), 201

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>", methods=["DELETE"])
@require_role("admin")
def delete_device(device_id):
    if _modbus_manager.remove_device(device_id):
        return jsonify({"message": "Device removed"})
    return jsonify({"error": "Device not found"}), 404


@modbus_api.route("/api/modbus/devices/<device_id>/connect", methods=["POST"])
@require_role("operator")
def connect_device(device_id):
    try:
        _modbus_manager.connect(device_id)
        return jsonify({"message": "Connected"})
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>/disconnect", methods=["POST"])
@require_role("operator")
def disconnect_device(device_id):
    _modbus_manager.disconnect(device_id)
    return jsonify({"message": "Disconnected"})


@modbus_api.route("/api/modbus/devices/<device_id>/read", methods=["POST"])
@require_role("operator")
def read_register(device_id):
    """Body: {"address": 0, "function_code": 3}
    address       — 0–65535
    function_code — 1 (coil) / 2 (discrete input) / 3 (holding) / 4 (input register)"""
    try:
        data          = parse_body(request)
        address       = validate_modbus_address(data.get("address", 0))
        function_code = parse_int(data.get("function_code", 3), "function_code")

        if function_code == 3:
            value = _modbus_manager.read_holding_register(device_id, address)
        elif function_code == 4:
            value = _modbus_manager.read_input_register(device_id, address)
        elif function_code == 1:
            value = _modbus_manager.read_coil(device_id, address)
        elif function_code == 2:
            value = _modbus_manager.read_discrete_input(device_id, address)
        else:
            return jsonify({"error": "function_code must be 1, 2, 3, or 4"}), 400

        if value is None:
            return jsonify({"error": "No response from device"}), 504

        return jsonify({"address": address, "value": value, "function_code": function_code})

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>/write", methods=["POST"])
@require_role("operator")
def write_register(device_id):
    """Body: {"address": 0, "value": 42, "function_code": 6}
    address       — 0–65535
    function_code — 6 (holding register, value 0–65535)
                  / 5 (coil,             value 0 or 1)"""
    try:
        data          = parse_body(request)
        require_fields(data, "value")

        address       = validate_modbus_address(data.get("address", 0))
        function_code = parse_int(data.get("function_code", 6), "function_code")

        if function_code == 6:
            value = validate_modbus_register_value(data["value"])
            ok = _modbus_manager.write_holding_register(device_id, address, value)
        elif function_code == 5:
            value = validate_modbus_coil_value(data["value"])
            ok = _modbus_manager.write_coil(device_id, address, value)
        else:
            return jsonify({"error": "function_code must be 5 (coil) or 6 (holding register)"}), 400

        if not ok:
            return jsonify({"error": "No response from device"}), 504

        return jsonify({"address": address, "value": value, "function_code": function_code})

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/scan", methods=["POST"])
@require_role("operator")
def scan():
    """Body: {"port":"ttyUSB0", "start_id":1, "end_id":10, "baudrate":115200}
    Scans for responding slave IDs in the range [start_id, end_id].
    Both IDs must be 1–247 and start_id must be ≤ end_id."""
    try:
        data     = parse_body(request)
        port     = validate_modbus_port(data.get("port", "ttyUSB0"), MODBUS_PORTS)
        start_id = validate_modbus_slave_id(data.get("start_id", 1))
        end_id   = validate_modbus_slave_id(data.get("end_id", 10))
        baudrate = validate_modbus_baudrate(data.get("baudrate", 115200))
        validate_modbus_scan_range(start_id, end_id)

        found = _modbus_manager.scan_port(port, start_id, end_id, baudrate)
        return jsonify({"found": found, "count": len(found)})

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/logs", methods=["GET"])
@require_role("viewer")
def get_logs():
    """?count=N  — N must be 1–1000 (default 100)."""
    try:
        count = validate_count(request.args.get("count", 100))
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"logs": _modbus_manager.get_logs(count)})