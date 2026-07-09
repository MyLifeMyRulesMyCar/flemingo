#!/usr/bin/env python3
# core/rate_limiter.py
# Phase 13 — in-memory sliding-window rate limiter for login attempts.
#
# Keyed by (username, remote_addr) pair. Stores a list of failure
# timestamps per key. On each check(), expired timestamps are pruned.
# On record_success(), the entire key is cleared.
#
# Thread-safe — uses threading.RLock, same pattern as core/state.py.
# In-memory only — does not survive process restart. Fine for LAN
# device threat model where a restart already resets all state.

import threading
import time


class LoginRateLimiter:
    def __init__(self, max_attempts=5, window_minutes=15):
        self._lock = threading.RLock()
        self._failures = {}  # key → [timestamps]
        self.max_attempts = max_attempts
        self.window_seconds = window_minutes * 60

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------
    def check(self, key: str) -> int:
        """Return how many failures have been recorded for `key` in
        the current window. Prunes expired timestamps first."""
        with self._lock:
            self._prune(key)
            return len(self._failures.get(key, []))

    def record_failure(self, key: str):
        with self._lock:
            if key not in self._failures:
                self._failures[key] = []
            self._failures[key].append(time.time())

    def record_success(self, key: str):
        """Clear all history for this key — successful login resets."""
        with self._lock:
            self._failures.pop(key, None)

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------
    def _prune(self, key: str):
        cutoff = time.time() - self.window_seconds
        timestamps = self._failures.get(key)
        if timestamps is None:
            return
        self._failures[key] = [t for t in timestamps if t > cutoff]
        if not self._failures[key]:
            del self._failures[key]
