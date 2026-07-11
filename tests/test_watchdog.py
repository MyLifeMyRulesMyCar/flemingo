#!/usr/bin/env python3
"""
Phase 5 Test - core/watchdog.py

Pure logic, no hardware needed. Run with pytest:
    pytest tests/test_watchdog.py
"""

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

    assert wd.check_component_health(
        "fake_subsystem"
    ), "registered component reports healthy"

    healthy["value"] = False
    assert (
        wd.check_component_health("fake_subsystem") is False
    ), "registered component reports unhealthy after state change"

    report = wd.get_health_report()
    assert (
        "fake_subsystem" in report["components"]
    ), "health report includes the registered component"
    assert (
        report["components"]["fake_subsystem"]["status"] == "unhealthy"
    ), "health report reflects unhealthy status"


def test_component_health_check_exception_is_caught():
    wd = WatchdogTimer(timeout=10, check_interval=10)

    def raises():
        raise RuntimeError("sensor read failed")

    wd.register_component("flaky", raises)
    result = wd.check_component_health("flaky")
    assert result is False, "exception in a health check is caught, not propagated"
    assert (
        wd.components["flaky"]["status"] == "error"
    ), "component status reflects the error"


def test_exit_process_timeout_handler_calls_os_exit():
    from core.watchdog import exit_process_timeout_handler
    from unittest.mock import patch

    wd = WatchdogTimer(timeout=1, check_interval=10)
    wd.feed()

    with patch("os._exit") as mock_exit:
        exit_process_timeout_handler(wd)

    mock_exit.assert_called_once_with(1)


def test_watchdog_timeout_triggers_exit_through_loop():
    """Verify the full path: _watchdog_loop detects timeout → calls
    on_timeout → exit handler fires. The previous test only tested
    the handler in isolation and could not catch the signature
    mismatch bug where self.on_timeout() was called with zero args."""
    from core.watchdog import exit_process_timeout_handler
    from unittest.mock import patch
    import time

    wd = WatchdogTimer(timeout=1, check_interval=10)
    wd.on_timeout = lambda: exit_process_timeout_handler(wd)
    wd.feed()
    wd.start()

    with patch("os._exit") as mock_exit:
        time.sleep(3)
        wd.stop()

    mock_exit.assert_any_call(1)
    assert mock_exit.call_count >= 1, "os._exit should be called at least once"
