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

from flask import Flask, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from core.io_manager import IOManager
from core.can_manager import can_manager       # module-level singleton
from core.modbus_manager import modbus_manager  # module-level singleton
from core.state import state
from daemon.daemon import PurpleIODaemon

from api.io_routes import io_api, set_io_manager
from api.can_routes import can_api, set_can_manager
from api.modbus_routes import modbus_api, set_modbus_manager
from api.health_routes import health_api, set_managers
from api.auth_routes import auth_api
from core.auth_manager import init_auth_manager
from core.config import load_reliability_config

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

daemon = PurpleIODaemon(io_manager, poll_interval=0.1,
                         can_manager=can_manager, modbus_manager=modbus_manager)

set_io_manager(io_manager)
set_can_manager(can_manager)
set_modbus_manager(modbus_manager)
set_managers(io_manager, can_manager, modbus_manager, watchdog=daemon.watchdog)

app.register_blueprint(io_api)
app.register_blueprint(can_api)
app.register_blueprint(modbus_api)
app.register_blueprint(health_api)
app.register_blueprint(auth_api)

print("=" * 60)
print("PurpleIO API Server - Phase 7 (auth + validation + CORS)")
print("=" * 60)

# ============================================
# WebSocket: live CAN message broadcast
# ============================================
def broadcast_can_message(message):
    try:
        socketio.emit("can_message", message, namespace="/")
    except Exception as e:
        logger.warning(f"WebSocket CAN broadcast error: {e}")


can_manager.subscribe(broadcast_can_message)

# ============================================
# WebSocket events
# ============================================
@socketio.on("connect")
def handle_connect():
    logger.info("WebSocket: client connected")
    emit("io_update", {"di": state.get_di(), "do": state.get_do()})
    emit("can_status", can_manager.get_status())


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("WebSocket: client disconnected")


@socketio.on("request_io")
def handle_request_io():
    emit("io_update", {"di": state.get_di(), "do": state.get_do()})


@socketio.on("set_do")
def handle_set_do(data):
    ch = data.get("channel")
    value = data.get("value")

    if ch is None or value is None:
        emit("error", {"message": "Missing channel or value"})
        return
    if not (0 <= ch < 4):
        emit("error", {"message": "Invalid channel"})
        return

    try:
        io_manager.write_output(ch, value)
    except Exception as e:
        emit("error", {"message": str(e)})
        return

    state.set_do(ch, value)

    socketio.emit("io_update", {"di": state.get_di(), "do": state.get_do()}, namespace="/")
    logger.info(f"WebSocket: DO{ch} set to {value}")


@socketio.on("request_can_status")
def handle_request_can_status():
    emit("can_status", can_manager.get_status())


# ============================================
# Background broadcast thread
# (separate from the daemon's poll rate - daemon polls hardware at
#  10Hz, this just pushes the current state to clients every 2s)
# ============================================
def background_broadcast():
    logger.info("Background broadcast thread started")
    last_io = {"di": [], "do": []}

    while True:
        try:
            current_io = {"di": state.get_di(), "do": state.get_do()}
            socketio.emit("io_update", current_io, namespace="/")

            if current_io != last_io:
                logger.info(f"I/O state changed: {current_io}")
                last_io = current_io

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
    return jsonify({
        "status": "ok",
        "message": "PurpleIO API online",
        "version": "0.3.0",
        "websocket": "enabled",
        "auth": "JWT - roles: viewer / operator / admin",
        "validation": "Phase 7 - all route bodies validated",
    })


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
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# Main
# ============================================
if __name__ == "__main__":
    daemon.start()
    start_background_thread()

    HOST = os.getenv("PURPLEIO_HOST", "0.0.0.0")
    PORT = int(os.getenv("PURPLEIO_PORT", "5000"))

    print(f"📡 HTTP API:  http://{HOST}:{PORT}")
    print(f"🔌 WebSocket: ws://{HOST}:{PORT}")
    print(f"🔑 Auth:      JWT (login at POST /api/auth/login)")
    print(f"   Roles:     viewer < operator < admin")
    print("=" * 60)

    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)