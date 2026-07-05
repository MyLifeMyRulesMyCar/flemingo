#!/usr/bin/env python3
# api/system_routes.py
# Phase 9 — /api/system/* blueprint.
#
# Route map (auth roles in brackets):
#   GET  /api/system/info      [viewer]   hostname, IP, version, uptime
#   GET  /api/system/metrics   [viewer]   OS + process metrics
#   GET  /api/system/backup    [admin]    config backup zip download
#   POST /api/system/restore   [admin]    upload zip, validate, write, restart hint

import logging
import os
import platform
import socket
import time
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from api.auth_decorators import require_role
from api.validators import ValidationError, validate_backup_file
from core.config import VERSION
from core.system_metrics import collect_metrics, _get_primary_ip
from core.backup_manager import create_backup, restore_backup

logger = logging.getLogger(__name__)
system_api = Blueprint("system_api", __name__)

_io_mgr = None
_can_mgr = None
_modbus_mgr = None
_mqtt_mgr = None


def set_system_managers(io_manager, can_manager, modbus_manager, mqtt_manager=None):
    global _io_mgr, _can_mgr, _modbus_mgr, _mqtt_mgr
    _io_mgr = io_manager
    _can_mgr = can_manager
    _modbus_mgr = modbus_manager
    _mqtt_mgr = mqtt_manager


# ════════════════════════════════════════════════════════════════════
# System info
# ════════════════════════════════════════════════════════════════════


@system_api.route("/api/system/info", methods=["GET"])
@require_role("viewer")
def system_info():
    try:
        uptime = int(time.time() - os.stat("/proc/1").st_ctime)
    except Exception:
        uptime = None

    return (
        jsonify(
            {
                "hostname": socket.gethostname(),
                "ip": _get_primary_ip(),
                "python_version": platform.python_version(),
                "firmware_version": VERSION,
                "uptime_seconds": uptime,
                "node": platform.node(),
                "system": platform.system(),
                "machine": platform.machine(),
            }
        ),
        200,
    )


# ════════════════════════════════════════════════════════════════════
# System metrics
# ════════════════════════════════════════════════════════════════════


@system_api.route("/api/system/metrics", methods=["GET"])
@require_role("viewer")
def system_metrics():
    return jsonify(collect_metrics(mqtt_manager=_mqtt_mgr)), 200


# ════════════════════════════════════════════════════════════════════
# Backup download
# ════════════════════════════════════════════════════════════════════


@system_api.route("/api/system/backup", methods=["GET"])
@require_role("admin")
def backup_download():
    device_id = socket.gethostname()
    try:
        from core.config import load_mqtt_config

        device_id = load_mqtt_config().get("bridges", {}).get("device_id", device_id)
    except Exception:
        pass

    buf = create_backup(version=VERSION, device_id=device_id)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"flemingo-backup-{timestamp}.zip",
    )


# ════════════════════════════════════════════════════════════════════
# Restore upload
# ════════════════════════════════════════════════════════════════════


@system_api.route("/api/system/restore", methods=["POST"])
@require_role("admin")
def restore_upload():
    try:
        if "file" in request.files:
            raw = validate_backup_file(request.files["file"])
        else:
            raw = validate_backup_file(request)

    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    try:
        restored = restore_backup(raw, strict_version=False)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Unexpected error during restore")
        return jsonify({"error": f"Restore failed: {e}"}), 500

    return (
        jsonify(
            {
                "message": (
                    "Configuration restored successfully. "
                    "Restart required (sudo systemctl restart flemingo "
                    "or restart api/app.py)."
                ),
                "restart_required": True,
                "restored_files": restored,
                "note": (
                    "Backup includes mqtt.yaml which may contain broker "
                    "credentials. The operator is responsible for the security "
                    "of the backup file."
                ),
            }
        ),
        200,
    )
