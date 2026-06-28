#!/usr/bin/env python3
"""
Phase 5 Test - core/watchdog.py

Pure logic, no hardware needed. Run directly:
    python3 tests/test_watchdog.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.watchdog import WatchdogTimer

failures = []


def check(label, condition):
    status = "✅" if condition else "❌"
    print(f"{status} {label}")
    if not condition:
        failures.append(label)


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

    check("watchdog does not fire while fed regularly", timed_out["value"] is False)
    wd.stop()


def test_timeout_fires_when_not_fed():
    timed_out = {"value": False}

    def on_timeout():
        timed_out["value"] = True

    wd = WatchdogTimer(timeout=0.5, check_interval=10, on_timeout=on_timeout)
    wd.start()
    wd.feed()

    time.sleep(1.2)  # let it exceed the 0.5s timeout with no further feed()

    check("watchdog fires on_timeout when not fed in time", timed_out["value"] is True)
    check("timeout_count incremented", wd.timeout_count >= 1)
    wd.stop()


def test_component_health_check_failure():
    wd = WatchdogTimer(timeout=10, check_interval=10)

    healthy = {"value": True}
    wd.register_component("fake_subsystem", lambda: healthy["value"])

    check("registered component reports healthy", wd.check_component_health("fake_subsystem"))

    healthy["value"] = False
    check("registered component reports unhealthy after state change",
          wd.check_component_health("fake_subsystem") is False)

    report = wd.get_health_report()
    check("health report includes the registered component",
          "fake_subsystem" in report["components"])
    check("health report reflects unhealthy status",
          report["components"]["fake_subsystem"]["status"] == "unhealthy")


def test_component_health_check_exception_is_caught():
    wd = WatchdogTimer(timeout=10, check_interval=10)

    def raises():
        raise RuntimeError("sensor read failed")

    wd.register_component("flaky", raises)
    result = wd.check_component_health("flaky")
    check("exception in a health check is caught, not propagated", result is False)
    check("component status reflects the error", wd.components["flaky"]["status"] == "error")


if __name__ == "__main__":
    test_feed_prevents_timeout()
    test_timeout_fires_when_not_fed()
    test_component_health_check_failure()
    test_component_health_check_exception_is_caught()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All watchdog checks passed.")
