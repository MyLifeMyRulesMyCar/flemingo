#!/usr/bin/env python3
# api/can_routes.py
# REST endpoints for the CAN bus backend (core/can_manager.py).
# Mirrors the shape of the reference project's can_routes.py, scoped
# to what core/can_manager.py actually supports (no device registry
# yet - that was deliberately left out of Phase 2 until validated on
# hardware).

from flask import Blueprint, jsonify, request

can_api = Blueprint("can_api", __name__)

_can_manager = None


def set_can_manager(manager):
    global _can_manager
    _can_manager = manager


@can_api.route("/api/can/status", methods=["GET"])
def get_status():
    return jsonify(_can_manager.get_status())


@can_api.route("/api/can/connect", methods=["POST"])
def connect():
    """Body (optional): {"bitrate": 125000, "crystal": 8000000}"""
    data = request.get_json(silent=True) or {}

    if "bitrate" in data:
        _can_manager.bitrate = int(data["bitrate"])
    if "crystal" in data:
        _can_manager.crystal = int(data["crystal"])

    try:
        _can_manager.connect()
        return jsonify({"message": "CAN connected", "status": _can_manager.get_status()})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@can_api.route("/api/can/disconnect", methods=["POST"])
def disconnect():
    _can_manager.disconnect()
    return jsonify({"message": "CAN disconnected"})


@can_api.route("/api/can/messages", methods=["GET"])
def get_messages():
    count = int(request.args.get("count", 100))
    return jsonify({"messages": _can_manager.get_recent_messages(count)})


@can_api.route("/api/can/messages/clear", methods=["POST"])
def clear_messages():
    _can_manager.clear_log()
    return jsonify({"message": "Log cleared"})


@can_api.route("/api/can/send", methods=["POST"])
def send():
    """Body: {"can_id": 291, "data": [1,2,3,4], "extended": false}
    can_id and data entries may be given as ints or hex strings ("0x123")."""
    data = request.get_json(silent=True) or {}

    if "can_id" not in data or "data" not in data:
        return jsonify({"error": "can_id and data required"}), 400

    def to_int(v):
        return int(v, 16) if isinstance(v, str) and v.lower().startswith("0x") else int(v)

    try:
        can_id = to_int(data["can_id"])
        payload = [to_int(b) for b in data["data"]]
        extended = bool(data.get("extended", False))

        if len(payload) > 8:
            return jsonify({"error": "data must be <= 8 bytes"}), 400

        ok = _can_manager.send_message(can_id, payload, extended=extended)
        return jsonify({"success": ok, "can_id": can_id, "data": payload})

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500