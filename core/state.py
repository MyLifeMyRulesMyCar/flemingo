#!/usr/bin/env python3
# core/state.py
# Thread-safe shared DI/DO state - same role as
# efio_daemon/thread_safe_state.py in the reference project, scoped
# down to GPIO only. CAN and Modbus already keep their own internal
# state inside core/can_manager.py and core/modbus_manager.py, so
# there's no need to duplicate that here.

import threading
from typing import List, Any


class ThreadSafeState:
    """
    Shared DI/DO arrays, read by the daemon (writer) and by the
    Flask/WebSocket layer (readers) without racing each other.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._di = [0, 0, 0, 0]
        self._do = [0, 0, 0, 0]

    # ----------------------------------------
    # Digital Inputs
    # ----------------------------------------
    def get_di(self, channel: int = None) -> Any:
        with self._lock:
            if channel is None:
                return self._di.copy()
            return self._di[channel]

    def set_di(self, channel: int, value: int):
        with self._lock:
            self._di[channel] = 1 if value else 0

    def set_di_all(self, values: List[int]):
        with self._lock:
            if len(values) != 4:
                raise ValueError(f"Expected 4 DI values, got {len(values)}")
            self._di = [1 if v else 0 for v in values]

    # ----------------------------------------
    # Digital Outputs
    # ----------------------------------------
    def get_do(self, channel: int = None) -> Any:
        with self._lock:
            if channel is None:
                return self._do.copy()
            return self._do[channel]

    def set_do(self, channel: int, value: int):
        with self._lock:
            self._do[channel] = 1 if value else 0

    def set_do_all(self, values: List[int]):
        with self._lock:
            if len(values) != 4:
                raise ValueError(f"Expected 4 DO values, got {len(values)}")
            self._do = [1 if v else 0 for v in values]


# Module-level singleton - safe to import anywhere, holds no hardware
# handles itself (just plain lists + a lock), unlike IOManager/CANManager.
state = ThreadSafeState()
