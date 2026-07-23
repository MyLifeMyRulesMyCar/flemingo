#!/usr/bin/env python3
# api/app.py
# PurpleIO Flask + SocketIO entrypoint.
#
# Wires io_manager / can_manager / modbus_manager together the way
# efio_daemon feeds into api/app.py in the reference EFIO project:
#   - PurpleIODaemon keeps DI/DO `state` fresh in the background
#   - CAN manages its own RX thread internally (core/can_manager.py)
#   - Modbus is on-demand/REST-driven, no background poll
#   - A separate broadcast thread pushes `state` to WebSocket clients
#     on its own cadence, decoupled from the daemon's poll rate
#
# Phase 6: JWT auth + role model is now active on all routes except
#   GET /api/health and POST /api/auth/login|refresh.
#   On first boot, a single admin account is created and its generated
#   password is printed ONCE to stdout - store it immediately.

import sys
import os
import signal
import time
import threading
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logging_config import setup_logging

setup_logging()

from flask import Flask, jsonify, send_from_directory
from flask_socketio import SocketIO
from flask_cors import CORS

from core.io_manager import IOManager
from core.can_manager import can_manager  # module-level singleton
from core.modbus_manager import modbus_manager  # module-level singleton
from core.state import state
from daemon.daemon import PurpleIODaemon

from api.io_routes import io_api, set_io_manager
from api.can_routes import can_api, set_can_manager
from api.modbus_routes import modbus_api, set_modbus_manager
from api.health_routes import health_api, set_managers
from api.auth_routes import auth_api
from core.auth_manager import init_auth_manager
import core.auth_manager as _auth_mod
from core.config import load_reliability_config, load_mqtt_config, VERSION
from core.mqtt_manager import init_mqtt_manager
from api.mqtt_routes import mqtt_api, set_mqtt_manager
from api.system_routes import system_api, set_system_managers
from api.modbus_tcp_routes import modbus_tcp_api, set_modbus_tcp_server
from api.socket_handlers import register_socket_handlers
from core.modbus_tcp_server import ModbusTCPServer

logger = logging.getLogger(__name__)

# ============================================
# Auth - must come before app construction so the first-boot password
# banner appears before anything else tries to start.
# Tuning values come from config/reliability.yaml's `auth:` section.
# ============================================
_cfg = load_reliability_config()
_auth_cfg = _cfg.get("auth", {})
init_auth_manager(
    access_token_minutes=_auth_cfg.get("access_token_minutes", 30),
    refresh_token_days=_auth_cfg.get("refresh_token_days", 7),
    min_password_length=_auth_cfg.get("min_password_length", 10),
)

# ============================================
# App + extensions
# ============================================
app = Flask(__name__)

# Phase 7: CORS origins from config/reliability.yaml `security.cors_origins`
# with an env-var override for field deployments.
# Default is ["*"] (bench-safe), should be locked to specific origins
# before shipping to a customer site, e.g.:
#   cors_origins: ["http://192.168.1.50:3000"]
# or via env:
#   PURPLEIO_CORS_ORIGINS="http://192.168.1.50:3000" python3 api/app.py
_security_cfg = _cfg.get("security", {})
_cors_env = os.getenv("PURPLEIO_CORS_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_origins = _security_cfg.get("cors_origins", ["*"])

CORS(app, resources={r"/*": {"origins": _cors_origins}})
socketio = SocketIO(app, cors_allowed_origins=_cors_origins, async_mode="threading")

# ============================================
# Hardware managers
# ============================================
# IOManager has no module-level singleton (Phase 1 test scripts each
# construct their own), so it's instantiated fresh here, once.
io_manager = IOManager()

# can_manager / modbus_manager are the singletons already created at
# import time in their own modules - reuse them, don't re-instantiate.

# ============================================
# Modbus TCP server (Phase 14)
# Created before the daemon so it can be passed in for watchdog registration.
# ============================================
_modbus_tcp = ModbusTCPServer(io_manager, state, can_manager)
_modbus_tcp.load_register_map()
set_modbus_tcp_server(_modbus_tcp)

daemon = PurpleIODaemon(
    io_manager,
    poll_interval=0.1,
    can_manager=can_manager,
    modbus_manager=modbus_manager,
    modbus_tcp_server=_modbus_tcp,
)

set_io_manager(io_manager)
set_can_manager(can_manager)
set_modbus_manager(modbus_manager)

# ============================================
# MQTT bridge (Phase 8)
# All three bridges share one paho client. Not connected to a broker
# yet — operator calls POST /api/mqtt/connect to do that.
# ============================================
_mqtt_cfg = load_mqtt_config()
_mgr = init_mqtt_manager(can_manager, modbus_manager, io_manager, state, _mqtt_cfg)
set_mqtt_manager(_mgr)
set_system_managers(io_manager, can_manager, modbus_manager, mqtt_manager=_mgr)
set_managers(io_manager, can_manager, modbus_manager, watchdog=daemon.watchdog)

app.register_blueprint(io_api)
app.register_blueprint(can_api)
app.register_blueprint(modbus_api)
app.register_blueprint(health_api)
app.register_blueprint(auth_api)
app.register_blueprint(mqtt_api)
app.register_blueprint(system_api)
app.register_blueprint(modbus_tcp_api)

print("=" * 60)
print("PurpleIO API Server - Phase 10 (dashboard)")
print("=" * 60)

# ============================================
# Phase 10 — Serve dashboard static files (Vite build output).
# API routes (/api/*) take priority because blueprints are registered
# above. This catch-all only fires for dashboard page requests.
# ============================================
_DASHBOARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dashboard",
    "dist",
)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_dashboard(path):
    if path and os.path.isfile(os.path.join(_DASHBOARD_PATH, path)):
        return send_from_directory(_DASHBOARD_PATH, path)
    return send_from_directory(_DASHBOARD_PATH, "index.html")


# ============================================
# WebSocket: live CAN message broadcast
# ============================================
def broadcast_can_message(message):
    try:
        if not can_manager.matches_filter(message.get("can_id")):
            return
        socketio.emit("can_message", message, namespace="/")
    except Exception as e:
        logger.warning(f"WebSocket CAN broadcast error: {e}")


can_manager.subscribe(broadcast_can_message)

# ============================================
# WebSocket events — Phase 10 security: JWT validated on connect,
# role checked on actuation commands.
# Handlers extracted to api/socket_handlers.py so they can be tested
# independently with flask_socketio.test_client().
# ============================================
register_socket_handlers(
    socketio, state, io_manager, can_manager, _auth_mod
)  # core.auth_manager module


# ============================================
# Background broadcast thread
# (separate from the daemon's poll rate - daemon polls hardware at
#  10Hz, this just pushes the current state to clients every 2s)
# ============================================
def background_broadcast():
    logger.info("Background broadcast thread started")
    last_io = {"di": [], "do": []}
    _ticks = 0

    while True:
        try:
            current_io = {"di": state.get_di(), "do": state.get_do()}
            socketio.emit("io_update", current_io, namespace="/")

            if current_io != last_io:
                logger.info(f"I/O state changed: {current_io}")
                last_io = current_io

            # Phase 10 — emit system_metrics every 10s (5 * 2s ticks)
            if _ticks % 5 == 0:
                try:
                    from core.system_metrics import collect_metrics
                    from core.mqtt_manager import mqtt_manager as _mm

                    m = collect_metrics(mqtt_manager=_mm)
                    socketio.emit("system_metrics", m, namespace="/")
                except Exception:
                    pass

            # Phase 12 — emit CAN/Modbus status so the dashboard reflects
            # disconnections and circuit-breaker trips without a refresh
            try:
                socketio.emit("can_status", can_manager.get_status(), namespace="/")
            except Exception:
                pass
            try:
                devices = modbus_manager.get_all_devices()
                socketio.emit("modbus_devices", {"devices": devices}, namespace="/")
            except Exception:
                pass

            _ticks += 1

        except Exception as e:
            logger.error(f"Background broadcast error: {e}")

        time.sleep(2)


def start_background_thread():
    t = threading.Thread(target=background_broadcast, name="Broadcast", daemon=True)
    t.start()
    logger.info("Background broadcast thread started")


# ============================================
# REST: top-level status
# ============================================
@app.get("/api/status")
def status():
    return jsonify(
        {
            "status": "ok",
            "message": "PurpleIO API online",
            "version": VERSION,
            "websocket": "enabled",
            "auth": "JWT - roles: viewer / operator / admin",
            "validation": "Phase 7 - all route bodies validated",
            "mqtt": (
                "Phase 8 - CAN / Modbus / IO bridges "
                "(POST /api/mqtt/connect to activate)"
            ),
            "backup": "Phase 9 - GET /api/system/backup, POST /api/system/restore",
        }
    )


# ============================================
# Graceful shutdown
# ============================================
def signal_handler(sig, frame):
    logger.warning(f"Received signal {sig}, shutting down...")
    daemon.stop()
    try:
        can_manager.disconnect()
    except Exception:
        pass
    try:
        modbus_manager._stop_health_check()
    except Exception:
        pass
    try:
        from core.mqtt_manager import mqtt_manager as _mm

        if _mm:
            for b in (_mm.can_bridge, _mm.modbus_bridge, _mm.io_bridge):
                if b and b.running:
                    b.stop()
            _mm.disconnect()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ============================================
# Runtime startup — called by both gunicorn and local dev
# ============================================
def _start_runtime():
    """Start the daemon and broadcast threads. Idempotent — safe to call
    multiple times. Skipped when FLEMINGO_SKIP_AUTOSTART is set so pytest
    imports don't spin up hardware threads."""
    if os.getenv("FLEMINGO_SKIP_AUTOSTART"):
        return
    try:
        daemon.start()
        start_background_thread()
    except Exception as e:
        logger.warning(f"_start_runtime: {e}")


# ============================================
# Main — two paths: gunicorn import vs python3 api/app.py
# ============================================
if __name__ != "__main__":
    _start_runtime()
elif __name__ == "__main__":
    _start_runtime()

    HOST = os.getenv("PURPLEIO_HOST", "0.0.0.0")
    PORT = int(os.getenv("PURPLEIO_PORT", "5000"))

    print(f"📡 HTTP API:  http://{HOST}:{PORT}")
    print(f"🔌 WebSocket: ws://{HOST}:{PORT}")
    print("🔑 Auth:      JWT (login at POST /api/auth/login)")
    print("   Roles:     viewer < operator < admin")
    print("=" * 60)

    # Dev-only: Werkzeug built-in server. Production uses gunicorn via
    # the systemd unit (deploy/flemingo.service.template).
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
