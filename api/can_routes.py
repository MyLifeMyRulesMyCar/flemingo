#!/usr/bin/env python3
# api/can_routes.py
# Phase 7: all route bodies and query params validated via api/validators.py.
# ValidationError → 400. Manager errors (RuntimeError) → 400 or 500.

from flask import Blueprint, jsonify, request
from api.auth_decorators import require_role
from api.validators import (
    ValidationError,
    parse_body,
    parse_bool,
    validate_can_bitrate,
    validate_can_crystal,
    validate_can_id,
    validate_can_payload,
    validate_can_id_filter,
    validate_count,
    require_fields,
)

can_api = Blueprint("can_api", __name__)

_can_manager = None


def set_can_manager(manager):
    global _can_manager
    _can_manager = manager


@can_api.route("/api/can/status", methods=["GET"])
@require_role("viewer")
def get_status():
    return jsonify(_can_manager.get_status())


@can_api.route("/api/can/connect", methods=["POST"])
@require_role("operator")
def connect():
    """Body (optional): {"bitrate": 125000, "crystal": 8000000}
    bitrate: 125000 / 250000 / 500000 / 1000000
    crystal: 8000000 / 16000000"""
    try:
        data = parse_body(request)

        if "bitrate" in data:
            _can_manager.bitrate = validate_can_bitrate(data["bitrate"])
        if "crystal" in data:
            _can_manager.crystal = validate_can_crystal(data["crystal"])

        _can_manager.connect()
        return jsonify(
            {"message": "CAN connected", "status": _can_manager.get_status()}
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@can_api.route("/api/can/disconnect", methods=["POST"])
@require_role("operator")
def disconnect():
    _can_manager.disconnect()
    return jsonify({"message": "CAN disconnected"})


@can_api.route("/api/can/messages", methods=["GET"])
@require_role("viewer")
def get_messages():
    """?count=N  — N must be 1–1000 (default 100)."""
    try:
        count = validate_count(request.args.get("count", 100))
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"messages": _can_manager.get_recent_messages(count)})


@can_api.route("/api/can/messages/clear", methods=["POST"])
@require_role("operator")
def clear_messages():
    _can_manager.clear_log()
    return jsonify({"message": "Log cleared"})


@can_api.route("/api/can/send", methods=["POST"])
@require_role("operator")
def send():
    """Body: {"can_id": 291, "data": [1,2,3,4], "extended": false}
    can_id and data bytes may be decimal ints or hex strings ("0x123").
    Standard frame: can_id 0x000–0x7FF.
    Extended frame: can_id 0x00000000–0x1FFFFFFF (set extended=true)."""
    try:
        data = parse_body(request)
        require_fields(data, "can_id", "data")

        extended = parse_bool(data.get("extended", False), "extended")
        can_id = validate_can_id(data["can_id"], extended)
        payload = validate_can_payload(data["data"])

        ok = _can_manager.send_message(can_id, payload, extended=extended)
        return jsonify({"success": ok, "can_id": can_id, "data": payload})

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@can_api.route("/api/can/filter", methods=["POST"])
@require_role("operator")
def set_filter():
    """Body: {"id_filter": ["0x100", "0x200"]}  — int or hex strings.
    Omit or pass [] to clear the filter — all IDs pass through.
    Applies immediately; does not require disconnecting."""
    try:
        data = parse_body(request)
        ids = validate_can_id_filter(data.get("id_filter", []))
        _can_manager.set_id_filter(ids)
        return jsonify(
            {"message": "CAN filter updated", "status": _can_manager.get_status()}
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
