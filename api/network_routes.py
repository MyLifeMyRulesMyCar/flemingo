#!/usr/bin/env python3
# api/network_routes.py
# Stage 2 — /api/network/* blueprint.

from flask import Blueprint, jsonify, request

from api.auth_decorators import require_role
from api.validators import (
    ValidationError,
    parse_body,
    validate_static_ip,
    validate_prefix_len,
    validate_gateway_ip,
    validate_gateway_in_subnet,
)
from core.network_config import (
    NetworkConfig,
    NetworkConfigError,
    get_current_config,
    has_carrier,
)

network_api = Blueprint("network_api", __name__)
_scheduler = None


def set_network_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


@network_api.route("/api/network/config", methods=["GET"])
@require_role("viewer")
def get_config():
    resp = {"carrier": has_carrier()}
    try:
        cfg = get_current_config()
        resp.update(cfg.to_dict())
    except NetworkConfigError:
        resp.update({"ip": "unknown", "prefix_len": 0, "gateway": "0.0.0.0"})
    return jsonify(resp), 200


@network_api.route("/api/network/apply", methods=["POST"])
@require_role("admin")
def apply():
    try:
        data = parse_body(request)
        ip = validate_static_ip(data.get("ip", ""))
        prefix = validate_prefix_len(data.get("prefix_len", 24))
        gw = validate_gateway_ip(data.get("gateway", ""))
        validate_gateway_in_subnet(ip, prefix, gw)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    candidate = NetworkConfig(ip=ip, prefix_len=prefix, gateway=gw)
    try:
        revert_at = _scheduler.apply_with_revert(candidate)
    except NetworkConfigError as e:
        return jsonify({"error": str(e)}), 400

    return (
        jsonify(
            {
                "pending": True,
                "revert_at": revert_at,
                "message": "Config applied — confirm within 60s",
            }
        ),
        200,
    )


@network_api.route("/api/network/confirm", methods=["POST"])
@require_role("admin")
def confirm():
    if not _scheduler.confirm():
        return jsonify({"message": "Nothing pending to confirm"}), 200
    return jsonify({"message": "Network config confirmed — change is permanent"}), 200


@network_api.route("/api/network/status", methods=["GET"])
@require_role("viewer")
def get_status():
    return jsonify(_scheduler.status()), 200
