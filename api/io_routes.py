#!/usr/bin/env python3
# api/io_routes.py
# REST endpoints for digital I/O (DI/DO).
# No auth yet - matches the rest of Phase 4. Add @jwt_required() here
# once auth is wired in.

from flask import Blueprint, jsonify, request
from core.state import state
from api.auth_decorators import require_role

io_api = Blueprint("io_api", __name__)

_io_manager = None


def set_io_manager(manager):
    """Called once from api/app.py at startup (same pattern as the
    reference project's set_bridge_instance() helpers)."""
    global _io_manager
    _io_manager = manager


@io_api.route("/api/io", methods=["GET"])
@require_role("viewer")
def get_io():
    """Current DI/DO state."""
    return jsonify(
        {
            "di": state.get_di(),
            "do": state.get_do(),
        }
    )


@io_api.route("/api/io/do/<int:channel>", methods=["POST"])
@require_role("operator")
def set_do(channel):
    """Set a single digital output. Body: {"state": true/false}"""
    if not (0 <= channel < 4):
        return jsonify({"error": "Invalid channel (must be 0-3)"}), 400

    data = request.get_json(silent=True) or {}
    new_val = 1 if data.get("state") else 0

    try:
        _io_manager.write_output(channel, new_val)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    state.set_do(channel, new_val)

    return jsonify({"channel": channel, "value": new_val})


@io_api.route("/api/io/status", methods=["GET"])
@require_role("viewer")
def get_io_hw_status():
    """Whether GPIO is actually driving hardware or running simulated."""
    return jsonify(_io_manager.get_status())
