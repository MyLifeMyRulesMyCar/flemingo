#!/usr/bin/env python3
# api/socket_handlers.py
# Phase 10 — WebSocket event handlers extracted from api/app.py.
#
# Extracted so they can be tested independently with a bare
# SocketIO(Flask(__name__)) via flask_socketio.test_client() without
# importing the full api/app.py (which has hardware deps).
#
# api/app.py calls register_socket_handlers(socketio, state, io_manager,
# can_manager). The imported globals (request, emit, ConnectionRefusedError)
# are provided by Flask-SocketIO at handler execution time.

import logging

from flask import request
from flask_socketio import emit, ConnectionRefusedError
from core.auth_manager import role_at_least as _role_at_least

logger = logging.getLogger(__name__)

_ws_auth = {}  # sid → decoded JWT payload
_io_manager = None
_can_manager = None
_state = None
_auth_mgr = None


def register_socket_handlers(
    socketio, state, io_manager, can_manager, auth_manager_module
):
    """Register all SocketIO event handlers on the given socketio instance."""
    global _io_manager, _can_manager, _state, _auth_mgr
    _io_manager = io_manager
    _can_manager = can_manager
    _state = state
    _auth_mgr = auth_manager_module

    @socketio.on("connect")
    def _handle_connect(auth=None):
        token = auth.get("token") if auth else None
        if not token:
            logger.warning("WebSocket: rejected — missing auth token")
            raise ConnectionRefusedError("unauthorized")

        mgr = _auth_mgr.auth_manager
        if mgr is None:
            mgr = _auth_mgr.init_auth_manager()

        try:
            payload = mgr.verify_token(token, expected_type="access")
        except Exception as e:
            logger.warning(f"WebSocket: rejected — invalid token: {e}")
            raise ConnectionRefusedError("unauthorized")

        _ws_auth[request.sid] = payload
        logger.info(f"WebSocket: client connected (role={payload.get('role', '?')})")
        emit("io_update", {"di": _state.get_di(), "do": _state.get_do()})
        emit("can_status", _can_manager.get_status())

    @socketio.on("disconnect")
    def _handle_disconnect():
        _ws_auth.pop(request.sid, None)
        logger.info("WebSocket: client disconnected")

    @socketio.on("request_io")
    def _handle_request_io(data=None):
        emit("io_update", {"di": _state.get_di(), "do": _state.get_do()})

    @socketio.on("set_do")
    def _handle_set_do(data):
        payload = _ws_auth.get(request.sid, {})
        role = payload.get("role", "")
        if not _role_at_least(role, "operator"):
            emit("error", {"message": "Operator role required"})
            return

        ch = data.get("channel")
        value = data.get("value")

        if ch is None or value is None:
            emit("error", {"message": "Missing channel or value"})
            return
        if not (0 <= ch < 4):
            emit("error", {"message": "Invalid channel"})
            return

        try:
            _io_manager.write_output(ch, value)
        except Exception as e:
            emit("error", {"message": str(e)})
            return

        _state.set_do(ch, value)
        socketio.emit(
            "io_update",
            {"di": _state.get_di(), "do": _state.get_do()},
            namespace="/",
        )
        logger.info(f"WebSocket: DO{ch} set to {value}")

    @socketio.on("request_can_status")
    def _handle_request_can_status(data=None):
        emit("can_status", _can_manager.get_status())
