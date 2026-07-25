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


def set_modbus_tcp_server(server):
    global _modbus_tcp_server
    _modbus_tcp_server = server


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

        errors = validate_entries(raw_entries)
        if errors:
            return jsonify({"valid": False, "errors": errors}), 200
        return jsonify({"valid": True, "errors": []}), 200
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
