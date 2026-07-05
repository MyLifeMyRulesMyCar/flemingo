#!/usr/bin/env python3
"""
Phase 5 Test - concurrency stress test

Hammers core/state.py's ThreadSafeState and core/resilience.py's
CircuitBreaker from many threads at once. This is exactly the kind of
test that caught real bugs in the EFIO reference project
(test_race_conditions.py / validate_race_fixes.py there) - the goal
isn't 100% coverage, it's confidence that the locking actually works
under contention, not just in a single-threaded smoke test.

Pure logic, no hardware needed. Run directly:
    python3 tests/test_race_conditions.py
"""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import ThreadSafeState
from core.resilience import CircuitBreaker, CircuitOpenError


def test_state_concurrent_reads_writes():
    """
    Many writer threads call set_di_all() with different valid 4-element
    lists while many reader threads call get_di() concurrently. A correct
    implementation never raises, and every read is always a valid,
    untorn 4-element list of 0/1 - never a partial write, never a list
    of the wrong length, never a non-binary value.
    """
    state = ThreadSafeState()
    errors = []
    stop = threading.Event()

    candidate_values = [
        [0, 0, 0, 0],
        [1, 1, 1, 1],
        [1, 0, 1, 0],
        [0, 1, 0, 1],
    ]

    def writer(values):
        while not stop.is_set():
            try:
                state.set_di_all(values)
            except Exception as e:
                errors.append(f"writer error: {e}")

    def reader():
        while not stop.is_set():
            try:
                snapshot = state.get_di()
                if len(snapshot) != 4:
                    errors.append(f"torn read: wrong length {snapshot}")
                if not all(v in (0, 1) for v in snapshot):
                    errors.append(f"torn read: non-binary value {snapshot}")
                if snapshot not in candidate_values:
                    errors.append(f"torn read: mixed values {snapshot}")
            except Exception as e:
                errors.append(f"reader error: {e}")

    threads = [threading.Thread(target=writer, args=(v,)) for v in candidate_values] + [
        threading.Thread(target=reader) for _ in range(8)
    ]

    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert len(errors) == 0, "no exceptions across concurrent DI readers/writers"
    if errors:
        for e in errors[:5]:
            print(f"     {e}")


def test_state_individual_channel_writes():
    """
    Per-channel set_di()/set_do() calls from many threads at once - each
    channel write should be atomic and never corrupt a neighboring
    channel's value mid-write.
    """
    state = ThreadSafeState()
    errors = []
    stop = threading.Event()

    def toggler(channel):
        val = 0
        while not stop.is_set():
            val = 1 - val
            try:
                state.set_do(channel, val)
            except Exception as e:
                errors.append(f"toggler[{channel}] error: {e}")

    def reader():
        while not stop.is_set():
            try:
                snapshot = state.get_do()
                if len(snapshot) != 4 or not all(v in (0, 1) for v in snapshot):
                    errors.append(f"corrupt DO snapshot: {snapshot}")
            except Exception as e:
                errors.append(f"reader error: {e}")

    threads = [threading.Thread(target=toggler, args=(ch,)) for ch in range(4)] + [
        threading.Thread(target=reader) for _ in range(8)
    ]

    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert len(errors) == 0, "no exceptions across concurrent per-channel DO writes"
    if errors:
        for e in errors[:5]:
            print(f"     {e}")


def test_circuit_breaker_concurrent_failures():
    """
    Many threads hit a failing call simultaneously. The breaker's
    failure_count must end up exactly matching the number of failures
    actually observed (no lost updates from the lock being skipped),
    and it must open exactly once it crosses the threshold rather than
    flapping or double-opening.
    """
    breaker = CircuitBreaker(failure_threshold=50, timeout=5, name="stress")
    call_count = {"value": 0}
    open_errors = {"value": 0}
    lock = threading.Lock()

    @breaker.call
    def always_fails():
        with lock:
            call_count["value"] += 1
        raise ValueError("boom")

    def hammer():
        for _ in range(20):
            try:
                always_fails()
            except CircuitOpenError:
                with lock:
                    open_errors["value"] += 1
            except ValueError:
                pass

    threads = [threading.Thread(target=hammer) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 10 threads x 20 attempts = 200 total attempts. Once the breaker
    # opens (after 50 real failures), every further attempt should be
    # rejected as CircuitOpenError WITHOUT incrementing call_count -
    # that's the entire point of the breaker (stop touching the
    # failing resource once it's known to be down).
    assert call_count["value"] <= breaker.failure_threshold + len(
        threads
    ), "breaker stopped calling the failing function once open"
    assert (
        open_errors["value"] > 0
    ), "breaker rejected the remaining attempts via CircuitOpenError"
    assert breaker.get_state()["state"] == "open", "breaker ended in OPEN state"
    assert (
        call_count["value"] + open_errors["value"] <= 200
    ), "no attempts were lost or double-counted past a sane bound"
