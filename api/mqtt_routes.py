#!/usr/bin/env python3
# api/mqtt_routes.py
# Phase 8 — /api/mqtt/* blueprint.
#
# Route map (auth roles in brackets):
#
#   Broker lifecycle:
#     POST   /api/mqtt/connect           [operator]  connect to broker
#     POST   /api/mqtt/disconnect        [operator]  disconnect from broker
#     GET    /api/mqtt/status            [viewer]    broker + all bridge states
#
#   CAN bridge:
#     GET    /api/mqtt/bridges/can       [viewer]    config + stats
#     POST   /api/mqtt/bridges/can/start [operator]  start bridge (body optional)
#     POST   /api/mqtt/bridges/can/stop  [operator]  stop bridge
#     POST   /api/mqtt/bridges/can/config [admin]    update topic config (must be stopped)
#
#   Modbus bridge:
#     GET    /api/mqtt/bridges/modbus       [viewer]
#     POST   /api/mqtt/bridges/modbus/start [operator]  body: {registers:[...], poll_interval_s?}
#     POST   /api/mqtt/bridges/modbus/stop  [operator]
#     POST   /api/mqtt/bridges/modbus/registers [operator]  hot-update register list
#     POST   /api/mqtt/bridges/modbus/config   [admin]
#
#   IO bridge:
#     GET    /api/mqtt/bridges/io       [viewer]
#     POST   /api/mqtt/bridges/io/start [operator]  body: {poll_interval_ms?, publish_on_change?}
#     POST   /api/mqtt/bridges/io/stop  [operator]
#     POST   /api/mqtt/bridges/io/config [admin]

import logging

from flask import Blueprint, jsonify, request

from api.auth_decorators import require_role
from api.validators import (
    ValidationError,
    parse_body,
    parse_int,
    parse_bool,
    validate_mqtt_host,
    validate_mqtt_port,
    validate_mqtt_topic,
    validate_mqtt_qos,
    validate_poll_interval_s,
    validate_poll_interval_ms,
    validate_modbus_address,
)

logger = logging.getLogger(__name__)
mqtt_api = Blueprint("mqtt_api", __name__)

_mqtt_manager = None


def set_mqtt_manager(manager):
    global _mqtt_manager
    _mqtt_manager = manager


# ════════════════════════════════════════════════════════════════════
# Broker lifecycle
# ════════════════════════════════════════════════════════════════════


@mqtt_api.route("/api/mqtt/connect", methods=["POST"])
@require_role("operator")
def connect_broker():
    """
    Connect to an MQTT broker.
    Body: {
      "host":      "192.168.1.x",  required
      "port":      1883,            optional (default 1883)
      "username":  "user",          optional
      "password":  "pass",          optional
      "client_id": "flemingo-01",   optional
      "keepalive": 60               optional
    }
    """
    try:
        data = parse_body(request)
        host = validate_mqtt_host(data.get("host", ""))
        port = validate_mqtt_port(data.get("port", 1883))
        username = data.get("username", "") or None
        password = data.get("password", "") or None
        client_id = data.get("client_id") or None
        keepalive = parse_int(data.get("keepalive", 60), "keepalive")

        _mqtt_manager.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            client_id=client_id,
            keepalive=keepalive,
        )
        return jsonify({"message": f"Connecting to {host}:{port}"}), 200

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409  # already connected


@mqtt_api.route("/api/mqtt/disconnect", methods=["POST"])
@require_role("operator")
def disconnect_broker():
    # Stop any running bridges first so they don't keep trying to publish
    for bridge in (
        _mqtt_manager.can_bridge,
        _mqtt_manager.modbus_bridge,
        _mqtt_manager.io_bridge,
    ):
        if bridge and bridge.running:
            try:
                bridge.stop()
            except Exception:
                pass
    _mqtt_manager.disconnect()
    return jsonify({"message": "MQTT disconnected"}), 200


@mqtt_api.route("/api/mqtt/status", methods=["GET"])
@require_role("viewer")
def get_status():
    return jsonify(_mqtt_manager.get_status()), 200


# ════════════════════════════════════════════════════════════════════
# CAN bridge
# ════════════════════════════════════════════════════════════════════


@mqtt_api.route("/api/mqtt/bridges/can", methods=["GET"])
@require_role("viewer")
def get_can_bridge():
    return jsonify(_mqtt_manager.can_bridge.get_status()), 200


@mqtt_api.route("/api/mqtt/bridges/can/start", methods=["POST"])
@require_role("operator")
def start_can_bridge():
    """
    Start the CAN bridge. All body fields are optional — omit to use
    the values from config/mqtt.yaml or the last config update.
    Body: {
      "publish_topic":   "flemingo/edge-01/can/rx",
      "subscribe_topic": "flemingo/edge-01/can/tx",
      "qos":             0
    }
    """
    try:
        data = parse_body(request)
        kwargs = {}
        if "publish_topic" in data:
            kwargs["publish_topic"] = validate_mqtt_topic(data["publish_topic"])
        if "subscribe_topic" in data:
            kwargs["subscribe_topic"] = validate_mqtt_topic(data["subscribe_topic"])
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.can_bridge.start(**kwargs)
        return (
            jsonify(
                {
                    "message": "CAN bridge started",
                    "status": _mqtt_manager.can_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@mqtt_api.route("/api/mqtt/bridges/can/stop", methods=["POST"])
@require_role("operator")
def stop_can_bridge():
    _mqtt_manager.can_bridge.stop()
    return jsonify({"message": "CAN bridge stopped"}), 200


@mqtt_api.route("/api/mqtt/bridges/can/config", methods=["POST"])
@require_role("admin")
def config_can_bridge():
    """Update CAN bridge topic config. Bridge must be stopped first."""
    try:
        data = parse_body(request)
        kwargs = {}
        if "publish_topic" in data:
            kwargs["publish_topic"] = validate_mqtt_topic(data["publish_topic"])
        if "subscribe_topic" in data:
            kwargs["subscribe_topic"] = validate_mqtt_topic(data["subscribe_topic"])
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.can_bridge.update_config(**kwargs)
        return (
            jsonify(
                {
                    "message": "CAN bridge config updated",
                    "status": _mqtt_manager.can_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


# ════════════════════════════════════════════════════════════════════
# Modbus bridge
# ════════════════════════════════════════════════════════════════════


@mqtt_api.route("/api/mqtt/bridges/modbus", methods=["GET"])
@require_role("viewer")
def get_modbus_bridge():
    return jsonify(_mqtt_manager.modbus_bridge.get_status()), 200


@mqtt_api.route("/api/mqtt/bridges/modbus/start", methods=["POST"])
@require_role("operator")
def start_modbus_bridge():
    """
    Start the Modbus bridge.
    Body: {
      "registers": [
        {"device_id": "dev1", "address": 0, "function_code": 3},
        {"device_id": "dev1", "address": 1}
      ],
      "poll_interval_s": 5
    }
    `registers` is required — the bridge has nothing to poll without it.
    function_code defaults to 3 (read holding register) if omitted.
    """
    try:
        data = parse_body(request)
        register_list = data.get("registers")
        if not register_list:
            return (
                jsonify(
                    {"error": "'registers' list is required to start Modbus bridge"}
                ),
                400,
            )
        if not isinstance(register_list, list):
            return jsonify({"error": "'registers' must be a list"}), 400

        # Validate each register entry
        validated = []
        for i, reg in enumerate(register_list):
            if not isinstance(reg, dict):
                return jsonify({"error": f"registers[{i}] must be an object"}), 400
            if "device_id" not in reg:
                return jsonify({"error": f"registers[{i}] missing 'device_id'"}), 400
            if "address" not in reg:
                return jsonify({"error": f"registers[{i}] missing 'address'"}), 400

            address = validate_modbus_address(reg["address"])
            fc = parse_int(reg.get("function_code", 3), "function_code")
            if fc not in (1, 2, 3, 4):
                return (
                    jsonify({"error": f"registers[{i}] function_code must be 1–4"}),
                    400,
                )

            validated.append(
                {
                    "device_id": str(reg["device_id"]),
                    "address": address,
                    "function_code": fc,
                }
            )

        kwargs = {"register_list": validated}
        if "poll_interval_s" in data:
            kwargs["poll_interval_s"] = validate_poll_interval_s(
                data["poll_interval_s"]
            )
        if "publish_topic_template" in data:
            kwargs["publish_topic_template"] = validate_mqtt_topic(
                data["publish_topic_template"]
            )
        if "subscribe_topic_template" in data:
            kwargs["subscribe_topic_template"] = validate_mqtt_topic(
                data["subscribe_topic_template"]
            )
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.modbus_bridge.start(**kwargs)
        return (
            jsonify(
                {
                    "message": "Modbus bridge started",
                    "status": _mqtt_manager.modbus_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@mqtt_api.route("/api/mqtt/bridges/modbus/stop", methods=["POST"])
@require_role("operator")
def stop_modbus_bridge():
    _mqtt_manager.modbus_bridge.stop()
    return jsonify({"message": "Modbus bridge stopped"}), 200


@mqtt_api.route("/api/mqtt/bridges/modbus/registers", methods=["POST"])
@require_role("operator")
def update_modbus_registers():
    """
    Hot-update the register list while the Modbus bridge is running.
    The new list takes effect on the next poll cycle.
    Body: {"registers": [...]}  — same format as start.
    """
    try:
        data = parse_body(request)
        register_list = data.get("registers", [])
        if not isinstance(register_list, list):
            return jsonify({"error": "'registers' must be a list"}), 400

        validated = []
        for i, reg in enumerate(register_list):
            if not isinstance(reg, dict):
                return jsonify({"error": f"registers[{i}] must be an object"}), 400
            address = validate_modbus_address(reg.get("address", 0))
            fc = parse_int(reg.get("function_code", 3), "function_code")
            validated.append(
                {
                    "device_id": str(reg.get("device_id", "")),
                    "address": address,
                    "function_code": fc,
                }
            )

        _mqtt_manager.modbus_bridge.update_register_list(validated)
        return (
            jsonify(
                {
                    "message": f"Register list updated ({len(validated)} entries)",
                    "registers": validated,
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


@mqtt_api.route("/api/mqtt/bridges/modbus/config", methods=["POST"])
@require_role("admin")
def config_modbus_bridge():
    """Update Modbus bridge config. Bridge must be stopped first."""
    try:
        data = parse_body(request)
        kwargs = {}
        if "poll_interval_s" in data:
            kwargs["poll_interval_s"] = validate_poll_interval_s(
                data["poll_interval_s"]
            )
        if "publish_topic_template" in data:
            kwargs["publish_topic_template"] = validate_mqtt_topic(
                data["publish_topic_template"]
            )
        if "subscribe_topic_template" in data:
            kwargs["subscribe_topic_template"] = validate_mqtt_topic(
                data["subscribe_topic_template"]
            )
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.modbus_bridge.update_config(**kwargs)
        return (
            jsonify(
                {
                    "message": "Modbus bridge config updated",
                    "status": _mqtt_manager.modbus_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


# ════════════════════════════════════════════════════════════════════
# IO bridge
# ════════════════════════════════════════════════════════════════════


@mqtt_api.route("/api/mqtt/bridges/io", methods=["GET"])
@require_role("viewer")
def get_io_bridge():
    return jsonify(_mqtt_manager.io_bridge.get_status()), 200


@mqtt_api.route("/api/mqtt/bridges/io/start", methods=["POST"])
@require_role("operator")
def start_io_bridge():
    """
    Start the IO bridge.
    Body (all optional): {
      "poll_interval_ms":  100,
      "publish_on_change": true,
      "qos":               1
    }
    """
    try:
        data = parse_body(request)
        kwargs = {}
        if "poll_interval_ms" in data:
            kwargs["poll_interval_ms"] = validate_poll_interval_ms(
                data["poll_interval_ms"]
            )
        if "publish_on_change" in data:
            kwargs["publish_on_change"] = parse_bool(
                data["publish_on_change"], "publish_on_change"
            )
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.io_bridge.start(**kwargs)
        return (
            jsonify(
                {
                    "message": "IO bridge started",
                    "status": _mqtt_manager.io_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@mqtt_api.route("/api/mqtt/bridges/io/stop", methods=["POST"])
@require_role("operator")
def stop_io_bridge():
    _mqtt_manager.io_bridge.stop()
    return jsonify({"message": "IO bridge stopped"}), 200


@mqtt_api.route("/api/mqtt/bridges/io/config", methods=["POST"])
@require_role("admin")
def config_io_bridge():
    """Update IO bridge config. Bridge must be stopped first."""
    try:
        data = parse_body(request)
        kwargs = {}
        if "poll_interval_ms" in data:
            kwargs["poll_interval_ms"] = validate_poll_interval_ms(
                data["poll_interval_ms"]
            )
        if "publish_on_change" in data:
            kwargs["publish_on_change"] = parse_bool(
                data["publish_on_change"], "publish_on_change"
            )
        if "qos" in data:
            kwargs["qos"] = validate_mqtt_qos(data["qos"])

        _mqtt_manager.io_bridge.update_config(**kwargs)
        return (
            jsonify(
                {
                    "message": "IO bridge config updated",
                    "status": _mqtt_manager.io_bridge.get_status(),
                }
            ),
            200,
        )

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
