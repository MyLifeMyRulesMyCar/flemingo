#!/usr/bin/env python3
"""
Phase 5 Test - core/resilience.py

Pure logic, no hardware needed. Run directly:
    python3 tests/test_resilience.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.resilience import (
    CircuitBreaker, CircuitState, CircuitOpenError,
    retry_with_backoff, HealthStatus,
)

failures = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"{status} {label}")
    if not condition:
        failures.append(label)


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

    check("breaker opens after reaching failure_threshold",
          breaker.state == CircuitState.OPEN)

    try:
        always_fails()
        check("OPEN breaker rejects calls without running them", False)
    except CircuitOpenError:
        check("OPEN breaker rejects calls without running them", True)


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
    check("breaker open after 2 failures", breaker.state == CircuitState.OPEN)

    time.sleep(0.35)  # let the timeout elapse

    result = flaky(False)  # this call should be allowed through as a probe
    check("HALF_OPEN probe succeeds and closes the breaker", result == "ok")
    check("breaker is CLOSED after a successful probe",
          breaker.state == CircuitState.CLOSED)


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

    check("breaker A opened independently", breaker_a.state == CircuitState.OPEN)
    check("breaker B unaffected by breaker A", breaker_b.state == CircuitState.CLOSED)
    check("breaker B still works", succeeds() == "ok")


def test_retry_with_backoff_eventually_succeeds():
    attempts = {"count": 0}

    @retry_with_backoff(max_retries=3, initial_delay=0.05, max_delay=0.1)
    def succeeds_on_third_try():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ValueError("not yet")
        return "ok"

    result = succeeds_on_third_try()
    check("retry_with_backoff returns the eventual success value", result == "ok")
    check("retry_with_backoff made exactly 3 attempts", attempts["count"] == 3)


def test_retry_with_backoff_exhausts():
    @retry_with_backoff(max_retries=2, initial_delay=0.05, max_delay=0.1)
    def always_fails():
        raise ValueError("boom")

    try:
        always_fails()
        check("retry_with_backoff raises after exhausting retries", False)
    except ValueError:
        check("retry_with_backoff raises after exhausting retries", True)


def test_health_status_aggregation():
    hs = HealthStatus()
    hs.update("gpio", "healthy")
    hs.update("can", "healthy")
    check("overall status is healthy when all components are healthy",
          hs.get_overall_status() == "healthy")

    hs.update("modbus", "degraded")
    check("overall status is degraded when one component is degraded",
          hs.get_overall_status() == "degraded")

    hs.update("can", "unhealthy")
    check("overall status is unhealthy when any component is unhealthy",
          hs.get_overall_status() == "unhealthy")


if __name__ == "__main__":
    test_circuit_breaker_opens_after_threshold()
    test_circuit_breaker_half_open_recovery()
    test_circuit_breaker_independent_instances()
    test_retry_with_backoff_eventually_succeeds()
    test_retry_with_backoff_exhausts()
    test_health_status_aggregation()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All resilience checks passed.")
