#!/usr/bin/env python3
"""
Phase 5 Test - core/watchdog.py

Pure logic, no hardware needed. Run with pytest:
    pytest tests/test_watchdog.py
"""

import pytest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.watchdog import WatchdogTimer


def test_feed_prevents_timeout():
    timed_out = {"value": False}

    def on_timeout():
        timed_out["value"] = True

    wd = WatchdogTimer(timeout=1, check_interval=10, on_timeout=on_timeout)
    wd.start()

    # Feed it faster than the timeout, for longer than the timeout window.
    for _ in range(15):
        wd.feed()
        time.sleep(0.1)

    assert timed_out["value"] is False, "watchdog does not fire while fed regularly"
    wd.stop()


def test_timeout_fires_when_not_fed():
    timed_out = {"value": False}

    def on_timeout():
        timed_out["value"] = True

    wd = WatchdogTimer(timeout=0.5, check_interval=10, on_timeout=on_timeout)
    wd.start()
    wd.feed()

    time.sleep(1.2)  # let it exceed the 0.5s timeout with no further feed()

    assert timed_out["value"] is True, "watchdog fires on_timeout when not fed in time"
    assert wd.timeout_count >= 1, "timeout_count incremented"
    wd.stop()


def test_component_health_check_failure():
    wd = WatchdogTimer(timeout=10, check_interval=10)

    healthy = {"value": True}
    wd.register_component("fake_subsystem", lambda: healthy["value"])

    assert wd.check_component_health("fake_subsystem"), "registered component reports healthy"

    healthy["value"] = False
    assert wd.check_component_health("fake_subsystem") is False, "registered component reports unhealthy after state change"

    report = wd.get_health_report()
    assert "fake_subsystem" in report["components"], "health report includes the registered component"
    assert report["components"]["fake_subsystem"]["status"] == "unhealthy", "health report reflects unhealthy status"


def test_component_health_check_exception_is_caught():
    wd = WatchdogTimer(timeout=10, check_interval=10)

    def raises():
        raise RuntimeError("sensor read failed")

    wd.register_component("flaky", raises)
    result = wd.check_component_health("flaky")
    assert result is False, "exception in a health check is caught, not propagated"
    assert wd.components["flaky"]["status"] == "error", "component status reflects the error"
