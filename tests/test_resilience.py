#!/usr/bin/env python3
"""
Phase 5 Test - core/resilience.py

Pure logic, no hardware needed. Run directly:
    pytest tests/test_resilience.py
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.resilience import (
    CircuitBreaker, CircuitState, CircuitOpenError,
    retry_with_backoff, HealthStatus,
)


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")

    @breaker.call
    def always_fails():
        raise ValueError("boom")

    for _ in range(3):
        try:
            always_fails()
        except ValueError:
            pass

    assert breaker.state == CircuitState.OPEN, "breaker opens after reaching failure_threshold"

    with pytest.raises(CircuitOpenError):
        always_fails()


def test_circuit_breaker_half_open_recovery():
    breaker = CircuitBreaker(failure_threshold=2, timeout=0.3, name="test")

    @breaker.call
    def flaky(should_fail):
        if should_fail:
            raise ValueError("boom")
        return "ok"

    for _ in range(2):
        try:
            flaky(True)
        except ValueError:
            pass
    assert breaker.state == CircuitState.OPEN, "breaker open after 2 failures"

    time.sleep(0.35)  # let the timeout elapse

    result = flaky(False)  # this call should be allowed through as a probe
    assert result == "ok", "HALF_OPEN probe succeeds and closes the breaker"
    assert breaker.state == CircuitState.CLOSED, "breaker is CLOSED after a successful probe"


def test_circuit_breaker_independent_instances():
    """The whole point of per-device breakers: one tripping must not affect another."""
    breaker_a = CircuitBreaker(failure_threshold=1, timeout=60, name="A")
    breaker_b = CircuitBreaker(failure_threshold=1, timeout=60, name="B")

    @breaker_a.call
    def fails():
        raise ValueError("boom")

    @breaker_b.call
    def succeeds():
        return "ok"

    try:
        fails()
    except ValueError:
        pass

    assert breaker_a.state == CircuitState.OPEN, "breaker A opened independently"
    assert breaker_b.state == CircuitState.CLOSED, "breaker B unaffected by breaker A"
    assert succeeds() == "ok", "breaker B still works"


def test_retry_with_backoff_eventually_succeeds():
    attempts = {"count": 0}

    @retry_with_backoff(max_retries=3, initial_delay=0.05, max_delay=0.1)
    def succeeds_on_third_try():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ValueError("not yet")
        return "ok"

    result = succeeds_on_third_try()
    assert result == "ok", "retry_with_backoff returns the eventual success value"
    assert attempts["count"] == 3, "retry_with_backoff made exactly 3 attempts"


def test_retry_with_backoff_exhausts():
    @retry_with_backoff(max_retries=2, initial_delay=0.05, max_delay=0.1)
    def always_fails():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        always_fails()


def test_health_status_aggregation():
    hs = HealthStatus()
    hs.update("gpio", "healthy")
    hs.update("can", "healthy")
    assert hs.get_overall_status() == "healthy", "overall status is healthy when all components are healthy"

    hs.update("modbus", "degraded")
    assert hs.get_overall_status() == "degraded", "overall status is degraded when one component is degraded"

    hs.update("can", "unhealthy")
    assert hs.get_overall_status() == "unhealthy", "overall status is unhealthy when any component is unhealthy"
