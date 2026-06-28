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
# NOTE: no auth yet. Every route below is open. Don't expose this
# past your LAN until that's added.

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

logger = logging.getLogger(__name__)

# ============================================
# App + extensions
# ============================================
app = Flask(__name__)

# Wide open for now - tighten this once you have a real auth/CORS story.
CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

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

print("=" * 60)
print("PurpleIO API Server")
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
        "version": "0.1.0",
        "websocket": "enabled",
        "auth": "NOT IMPLEMENTED - all routes are open",
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

    print(f"📡 HTTP API: http://{HOST}:{PORT}")
    print(f"🔌 WebSocket: ws://{HOST}:{PORT}")
    print("⚠️  No auth configured - every route above is open")
    print("=" * 60)

    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)