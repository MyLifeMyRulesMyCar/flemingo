#!/usr/bin/env python3
# api/modbus_tcp_routes.py
# Phase 14 — /api/modbus-tcp/* blueprint.

from flask import Blueprint, jsonify, request

from api.auth_decorators import require_role
from api.validators import (
    ValidationError,
    parse_body,
    validate_modbus_tcp_port,
)
from core.network_config import get_current_config, NetworkConfigError, DEFAULT_IFACE

modbus_tcp_api = Blueprint("modbus_tcp_api", __name__)

_modbus_tcp_server = None
_modbus_manager = None


def set_modbus_tcp_server(server):
    global _modbus_tcp_server
    _modbus_tcp_server = server


def set_modbus_tcp_modbus_manager(manager):
    global _modbus_manager
    _modbus_manager = manager


def _resolve_bind_host():
    """Returns (host, isolated). isolated=False means eth1 has no IP
    configured yet or no cable is plugged — falls back to 0.0.0.0 with
    a warning so the operator knows the server is not isolated."""
    try:
        cfg = get_current_config(DEFAULT_IFACE)
        # Even with a static IP configured, verify the cable is plugged.
        try:
            with open(f"/sys/class/net/{DEFAULT_IFACE}/carrier") as f:
                if f.read().strip() != "1":
                    return "0.0.0.0", False
        except FileNotFoundError:
            pass
        return cfg.ip, True
    except NetworkConfigError:
        return "0.0.0.0", False


def _check_rtu_devices_exist(raw_entries):
    """Return a list of error strings for entries referencing unknown RTU devices."""
    errors = []
    for e in raw_entries:
        if e.get("source_type") == "modbus_rtu":
            device_id = e.get("rtu_device_id", "")
            if _modbus_manager is None or _modbus_manager.get_device(device_id) is None:
                errors.append(
                    f"rtu_device_id '{device_id}' is not a configured Modbus RTU device"
                )
    return errors


# ═══════════════════════════════════════════════════════════════════
# Status
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/status", methods=["GET"])
@require_role("viewer")
def get_status():
    return jsonify(_modbus_tcp_server.get_status()), 200


# ═══════════════════════════════════════════════════════════════════
# Start / Stop
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/start", methods=["POST"])
@require_role("operator")
def start_server():
    try:
        data = parse_body(request)
        port = validate_modbus_tcp_port(data.get("port", 5020))
        host, isolated = _resolve_bind_host()
        _modbus_tcp_server.start(host=host, port=port)
        resp = {
            "message": "Modbus TCP server started",
            "status": _modbus_tcp_server.get_status(),
        }
        if not isolated:
            resp["warning"] = (
                "eth1 has no IP configured — listening on all interfaces "
                "(0.0.0.0), not isolated from the management network. "
                "Configure eth1's static IP first."
            )
        return jsonify(resp), 200
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@modbus_tcp_api.route("/api/modbus-tcp/stop", methods=["POST"])
@require_role("operator")
def stop_server():
    _modbus_tcp_server.stop()
    return jsonify({"message": "Modbus TCP server stopped"}), 200


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/config", methods=["GET"])
@require_role("viewer")
def get_config():
    status = _modbus_tcp_server.get_status()
    return jsonify({"host": status["host"], "port": status["port"]}), 200


@modbus_tcp_api.route("/api/modbus-tcp/config", methods=["PUT"])
@require_role("admin")
def update_config():
    try:
        data = parse_body(request)
        port = validate_modbus_tcp_port(data.get("port", 5020))
        was_running = _modbus_tcp_server.running
        if was_running:
            _modbus_tcp_server.stop()
        try:
            host, _ = _resolve_bind_host()
            _modbus_tcp_server.start(host=host, port=port)
        except RuntimeError:
            pass
        return (
            jsonify(
                {"message": "Config updated", "status": _modbus_tcp_server.get_status()}
            ),
            200,
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


# ═══════════════════════════════════════════════════════════════════
# Register map
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/register-map", methods=["GET"])
@require_role("viewer")
def get_register_map():
    return jsonify({"entries": _modbus_tcp_server.get_register_map()}), 200


@modbus_tcp_api.route("/api/modbus-tcp/register-map", methods=["POST"])
@require_role("admin")
def save_register_map_route():
    try:
        data = parse_body(request)
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            return jsonify({"error": "'entries' must be a list"}), 400

        from core.modbus_tcp_register_map import (
            validate_entries,
            save_register_map,
            RegisterMapEntry,
        )

        device_errors = _check_rtu_devices_exist(raw_entries)
        if device_errors:
            return (
                jsonify({"error": "Validation failed", "details": device_errors}),
                400,
            )

        errors = validate_entries(raw_entries)
        if errors:
            return jsonify({"error": "Validation failed", "details": errors}), 400

        entries = [RegisterMapEntry.from_dict(e) for e in raw_entries]
        save_register_map(entries)
        _modbus_tcp_server.reload_register_map()

        return jsonify({"message": f"Register map saved ({len(entries)} entries)"}), 200
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


@modbus_tcp_api.route("/api/modbus-tcp/register-map/validate", methods=["POST"])
@require_role("operator")
def validate_register_map():
    """Dry-run — validates without saving. Returns errors if any."""
    try:
        data = parse_body(request)
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            return jsonify({"error": "'entries' must be a list"}), 400

        from core.modbus_tcp_register_map import validate_entries

        device_errors = _check_rtu_devices_exist(raw_entries)
        errors = device_errors + validate_entries(raw_entries)
        if errors:
            return jsonify({"valid": False, "errors": errors}), 200
        return jsonify({"valid": True, "errors": []}), 200
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


# ═══════════════════════════════════════════════════════════════════
# Test write (one-off check before marking an entry writable)
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/register-map/test-write", methods=["POST"])
@require_role("operator")
def test_write_register():
    try:
        data = parse_body(request)
        device_id = str(data.get("device_id", ""))
        address = data.get("address")
        value = data.get("value")

        if not device_id:
            return jsonify({"error": "'device_id' is required"}), 400
        if address is None:
            return jsonify({"error": "'address' is required"}), 400
        if value is None:
            return jsonify({"error": "'value' is required"}), 400

        from api.validators import (
            validate_modbus_address,
            validate_modbus_register_value,
        )

        addr = validate_modbus_address(address)
        val = validate_modbus_register_value(value)

        if _modbus_manager is None:
            return jsonify({"error": "Modbus manager not available"}), 503

        ok = _modbus_manager.write_holding_register(device_id, addr, val)
        if ok:
            return (
                jsonify(
                    {
                        "message": "Write succeeded",
                        "device_id": device_id,
                        "address": addr,
                        "value": val,
                    }
                ),
                200,
            )
        return (
            jsonify(
                {
                    "error": "Write failed — device may be disconnected or address invalid",
                    "device_id": device_id,
                    "address": addr,
                    "value": val,
                }
            ),
            502,
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except (ValueError, RuntimeError) as e:
        return (
            jsonify(
                {
                    "error": "Write failed — " + str(e),
                    "device_id": device_id,
                    "address": addr,
                    "value": val,
                }
            ),
            502,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "error": "Write failed — " + str(e),
                    "device_id": device_id,
                    "address": addr,
                    "value": val,
                }
            ),
            502,
        )


# ═══════════════════════════════════════════════════════════════════
# CAN send channels
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/can-send-channels", methods=["GET"])
@require_role("viewer")
def get_can_send_channels():
    channels = _modbus_tcp_server._can_send_channels if _modbus_tcp_server else []
    result = []
    for c in channels:
        d = c.to_dict()
        d["last_trigger"] = (
            _modbus_tcp_server.last_trigger_result.get(c.name)
            if _modbus_tcp_server
            else None
        )
        result.append(d)
    return jsonify({"channels": result}), 200


@modbus_tcp_api.route("/api/modbus-tcp/can-send-channels", methods=["POST"])
@require_role("admin")
def save_can_send_channels():
    try:
        data = parse_body(request)
        raw_channels = data.get("channels", [])
        if not isinstance(raw_channels, list):
            return jsonify({"error": "'channels' must be a list"}), 400

        from core.can_send_channel import (
            validate_channels,
            save_channels,
            CANSendChannel,
        )

        existing_entries = (
            _modbus_tcp_server.get_register_map() if _modbus_tcp_server else []
        )
        errors = validate_channels(raw_channels, existing_entries)
        if errors:
            return jsonify({"error": "Validation failed", "details": errors}), 400

        channels = [CANSendChannel.from_dict(c) for c in raw_channels]
        save_channels(channels)
        if _modbus_tcp_server:
            _modbus_tcp_server._can_send_channels = channels

        return (
            jsonify({"message": f"CAN send channels saved ({len(channels)} entries)"}),
            200,
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


# ═══════════════════════════════════════════════════════════════════
# Recent exceptions
# ═══════════════════════════════════════════════════════════════════


@modbus_tcp_api.route("/api/modbus-tcp/recent-exceptions", methods=["GET"])
@require_role("viewer")
def get_recent_exceptions():
    if _modbus_tcp_server is None:
        return jsonify({"exceptions": []}), 200
    return (
        jsonify({"exceptions": list(_modbus_tcp_server.recent_exceptions)}),
        200,
    )
