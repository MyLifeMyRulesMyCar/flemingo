#!/usr/bin/env python3
# core/resilience.py
# Circuit breakers, retry/backoff, and aggregated health tracking.
#
# Same role as efio_daemon/resilience.py in the reference project, but
# trimmed of MQTT-specific assumptions: the reference version was built
# around a single MQTT circuit breaker. Flemingo needs *multiple*
# independent breakers (CAN, and one per Modbus device), so this module
# stays purely generic - nothing in here knows what CAN or Modbus is.
# core/can_manager.py and core/modbus_manager.py instantiate their own
# CircuitBreaker objects using this.

import logging
import threading
import time
from datetime import datetime
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


# ============================================
# Circuit Breaker
# ============================================
class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, block calls
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """
    Standard circuit breaker pattern.

    CLOSED:    calls pass through normally.
    OPEN:      calls are rejected immediately (no hardware access at all)
               until `timeout` seconds have passed.
    HALF_OPEN: the next call is allowed through as a recovery probe. If
               it succeeds, the breaker closes again; if it fails, it
               reopens and the timeout restarts.

    Usage as a decorator (works fine on bound methods too):
        breaker = CircuitBreaker(failure_threshold=5, timeout=60, name="CAN")

        @breaker.call
        def connect():
            ...

    Or wrap an existing callable inline, which is the more common shape
    in this codebase since `connect()`/`read_register()` etc. are
    instance methods already defined elsewhere:

        def connect(self):
            @self.breaker.call
            def _attempt():
                return self._do_connect()
            return _attempt()
    """

    def __init__(
        self,
        failure_threshold=5,
        timeout=60,
        expected_exception=Exception,
        name="unnamed",
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        self.name = name

        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        self.lock = threading.RLock()

    def call(self, func):
        """Decorator that wraps `func` with this breaker's gating logic."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            with self.lock:
                if self.state == CircuitState.OPEN:
                    if self._should_attempt_reset():
                        logger.info(
                            f"[{self.name}] circuit breaker: HALF_OPEN (testing recovery)"
                        )
                        self.state = CircuitState.HALF_OPEN
                    else:
                        remaining = self.timeout - (
                            time.time() - self.last_failure_time
                        )
                        raise CircuitOpenError(
                            f"Circuit breaker '{self.name}' is open, retry in {max(0, int(remaining))}s"
                        )

            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            except self.expected_exception:
                self._on_failure()
                raise

        return wrapper

    def _should_attempt_reset(self):
        if self.last_failure_time is None:
            return False
        return (time.time() - self.last_failure_time) >= self.timeout

    def _on_success(self):
        with self.lock:
            if self.state == CircuitState.HALF_OPEN:
                logger.info(f"[{self.name}] circuit breaker: CLOSED (recovered)")
            self.failure_count = 0
            self.state = CircuitState.CLOSED

    def _on_failure(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    logger.warning(
                        f"[{self.name}] circuit breaker: OPEN ({self.failure_count} failures)"
                    )
                self.state = CircuitState.OPEN
            else:
                logger.warning(
                    f"[{self.name}] failure {self.failure_count}/{self.failure_threshold}"
                )

    def reset(self):
        """Manually force the breaker back to CLOSED (e.g. after a manual reconnect)."""
        with self.lock:
            logger.info(f"[{self.name}] circuit breaker: manual reset")
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.last_failure_time = None

    def get_state(self) -> dict:
        with self.lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self.failure_count,
                "failure_threshold": self.failure_threshold,
                "last_failure": (
                    datetime.fromtimestamp(self.last_failure_time).isoformat()
                    if self.last_failure_time
                    else None
                ),
            }


class CircuitOpenError(Exception):
    """Raised when a call is rejected because its circuit breaker is OPEN."""

    pass


# ============================================
# Retry with exponential backoff
# ============================================
def retry_with_backoff(
    max_retries=3,
    initial_delay=1,
    max_delay=30,
    exponential_base=2,
    expected_exception=Exception,
):
    """
    Retry decorator with exponential backoff. Intended for one-shot
    operations like opening a serial port or an SPI device - NOT for
    wrapping things already protected by a CircuitBreaker (the breaker
    handles repeated-failure suppression; stacking both just delays
    the breaker from ever opening).

    Usage:
        @retry_with_backoff(max_retries=3, initial_delay=1)
        def open_port():
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except expected_exception as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__}: all {max_retries} retries failed"
                        )
                        raise

                    wait_time = min(delay, max_delay)
                    logger.warning(
                        f"{func.__name__}: retry {attempt + 1}/{max_retries} in {wait_time}s "
                        f"(error: {str(e)[:80]})"
                    )
                    time.sleep(wait_time)
                    delay *= exponential_base

            raise last_exception

        return wrapper

    return decorator


# ============================================
# Timeout decorator
# ============================================
def timeout(seconds=10):
    """
    Run `func` in a worker thread and raise TimeoutError if it doesn't
    finish in time. Creates a new thread per call - use only around
    operations that can genuinely hang (e.g. a serial read with no
    response), not in hot paths.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = [None]
            exception = [None]

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            thread.join(timeout=seconds)

            if thread.is_alive():
                logger.error(f"{func.__name__}: timeout after {seconds}s")
                raise TimeoutError(f"{func.__name__} timeout after {seconds}s")

            if exception[0]:
                raise exception[0]

            return result[0]

        return wrapper

    return decorator


# ============================================
# Aggregated health status
# ============================================
class HealthStatus:
    """
    Process-wide registry of subsystem health, independent of any one
    manager's internal state. core/watchdog.py and api/health_routes.py
    both read from this; core/can_manager.py and core/modbus_manager.py
    write to it.
    """

    def __init__(self):
        self.components = {}
        self.lock = threading.RLock()

    def update(
        self, component: str, status: str, message: str = "", details: dict = None
    ):
        """status should be one of: 'healthy' | 'degraded' | 'unhealthy'."""
        with self.lock:
            self.components[component] = {
                "status": status,
                "message": message,
                "last_update": datetime.now().isoformat(),
                "details": details or {},
            }

    def get_status(self, component: str = None):
        with self.lock:
            if component:
                return self.components.get(
                    component,
                    {
                        "status": "unknown",
                        "message": "Component not registered",
                    },
                )
            return dict(self.components)

    def is_healthy(self, component: str = None) -> bool:
        with self.lock:
            if component:
                return self.components.get(component, {}).get("status") == "healthy"
            return all(c.get("status") == "healthy" for c in self.components.values())

    def get_overall_status(self) -> str:
        with self.lock:
            if not self.components:
                return "unknown"
            statuses = [c.get("status") for c in self.components.values()]
            if any(s == "unhealthy" for s in statuses):
                return "unhealthy"
            if any(s == "degraded" for s in statuses):
                return "degraded"
            if all(s == "healthy" for s in statuses):
                return "healthy"
            # Nothing unhealthy/degraded, but not everything healthy
            # either - this is the fresh-boot case (components still
            # "unknown" because nothing has connected yet). Reporting
            # "degraded" here would be misleading - nothing is actually
            # wrong, the system just hasn't reported in yet.
            return "unknown"


# Module-level singleton - import `health_status` directly, same
# pattern as the can_manager/modbus_manager/io_manager singletons.
health_status = HealthStatus()
