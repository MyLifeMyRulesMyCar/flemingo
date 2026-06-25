#!/usr/bin/env python3
# api/modbus_routes.py
# REST endpoints for RS485/Modbus devices (core/modbus_manager.py).
# Mirrors the reference project's modbus_device_routes.py shape.

from flask import Blueprint, jsonify, request
from core.modbus_manager import MODBUS_PORTS

modbus_api = Blueprint("modbus_api", __name__)

_modbus_manager = None


def set_modbus_manager(manager):
    global _modbus_manager
    _modbus_manager = manager


@modbus_api.route("/api/modbus/ports", methods=["GET"])
def get_ports():
    return jsonify({"ports": MODBUS_PORTS})


@modbus_api.route("/api/modbus/devices", methods=["GET"])
def get_devices():
    return jsonify({"devices": _modbus_manager.get_all_devices()})


@modbus_api.route("/api/modbus/devices", methods=["POST"])
def create_device():
    """Body: {"name": "...", "port": "ttyUSB0", "slave_id": 1,
              "baudrate": 115200, "parity": "N", "stopbits": 1}"""
    data = request.get_json(silent=True) or {}

    required = ["name", "port", "slave_id"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    try:
        device_id = _modbus_manager.add_device(
            name=data["name"],
            port=data["port"],
            slave_id=int(data["slave_id"]),
            baudrate=int(data.get("baudrate", 115200)),
            parity=data.get("parity", "N"),
            stopbits=int(data.get("stopbits", 1)),
        )
        return jsonify({"message": "Device added", "device_id": device_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>", methods=["DELETE"])
def delete_device(device_id):
    if _modbus_manager.remove_device(device_id):
        return jsonify({"message": "Device removed"})
    return jsonify({"error": "Device not found"}), 404


@modbus_api.route("/api/modbus/devices/<device_id>/connect", methods=["POST"])
def connect_device(device_id):
    try:
        _modbus_manager.connect(device_id)
        return jsonify({"message": "Connected"})
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>/disconnect", methods=["POST"])
def disconnect_device(device_id):
    _modbus_manager.disconnect(device_id)
    return jsonify({"message": "Disconnected"})


@modbus_api.route("/api/modbus/devices/<device_id>/read", methods=["POST"])
def read_register(device_id):
    """Body: {"address": 0, "function_code": 3}
    FC3=holding, FC4=input, FC1=coil, FC2=discrete input"""
    data = request.get_json(silent=True) or {}
    address = int(data.get("address", 0))
    function_code = int(data.get("function_code", 3))

    try:
        if function_code == 3:
            value = _modbus_manager.read_holding_register(device_id, address)
        elif function_code == 4:
            value = _modbus_manager.read_input_register(device_id, address)
        elif function_code == 1:
            value = _modbus_manager.read_coil(device_id, address)
        elif function_code == 2:
            value = _modbus_manager.read_discrete_input(device_id, address)
        else:
            return jsonify({"error": "Unsupported function_code"}), 400

        if value is None:
            return jsonify({"error": "No response from device"}), 504

        return jsonify({"address": address, "value": value, "function_code": function_code})

    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/devices/<device_id>/write", methods=["POST"])
def write_register(device_id):
    """Body: {"address": 0, "value": 42, "function_code": 6}
    FC6=holding register write, FC5=coil write"""
    data = request.get_json(silent=True) or {}
    address = int(data.get("address", 0))
    value = data.get("value")
    function_code = int(data.get("function_code", 6))

    if value is None:
        return jsonify({"error": "value required"}), 400

    try:
        if function_code == 6:
            ok = _modbus_manager.write_holding_register(device_id, address, int(value))
        elif function_code == 5:
            ok = _modbus_manager.write_coil(device_id, address, int(value))
        else:
            return jsonify({"error": "Unsupported function_code"}), 400

        if not ok:
            return jsonify({"error": "No response from device"}), 504

        return jsonify({"address": address, "value": value, "function_code": function_code})

    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/scan", methods=["POST"])
def scan():
    """Body: {"port": "ttyUSB0", "start_id": 1, "end_id": 10, "baudrate": 115200}"""
    data = request.get_json(silent=True) or {}
    port = data.get("port", "ttyUSB0")
    start_id = int(data.get("start_id", 1))
    end_id = int(data.get("end_id", 10))
    baudrate = int(data.get("baudrate", 115200))

    try:
        found = _modbus_manager.scan_port(port, start_id, end_id, baudrate)
        return jsonify({"found": found, "count": len(found)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@modbus_api.route("/api/modbus/logs", methods=["GET"])
def get_logs():
    count = int(request.args.get("count", 100))
    return jsonify({"logs": _modbus_manager.get_logs(count)})