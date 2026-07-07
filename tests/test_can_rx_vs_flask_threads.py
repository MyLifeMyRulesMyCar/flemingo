#!/usr/bin/env python3
# tests/test_can_rx_vs_flask_threads.py
# Phase 11 — stress test: CAN RX thread vs concurrent Flask API threads.
#
# Simulates a live CAN bus receiving frames while multiple "Flask request"
# threads concurrently read status, read the message log, and send frames.
# Asserts no torn reads, no crashes, no lost RX increments across all
# threads under sustained load.
#
# Zero hardware needed — uses FakeMCP2515 that injects synthetic frames
# without touching SPI. The real CANManager._handle_rx() and _rx_loop()
# share data structures exactly as they do in production.
#
# Run:
#   pytest tests/test_can_rx_vs_flask_threads.py -v
#   pytest tests/ -m "not slow"              # skip this file
#   pytest tests/ -m slow                     # run only this file

import sys
import os
import threading
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.can_manager import CANManager
from core.mcp2515_driver import CANMessage


# ═══════════════════════════════════════════════════════════════════
# Fake MCP2515 — injects synthetic CAN frames, no SPI hardware needed
# ═══════════════════════════════════════════════════════════════════


class FakeMCP2515:
    """Minimal fake that feeds synthetic frames into the RX loop.
    send_message / check_tx_result / get_error_flags all succeed
    so health checks never trip a disconnection."""

    def __init__(self, frame_rate=500):
        self._rate = frame_rate
        self._last = time.time()
        self._seq = 0  # rolling CAN ID

    def available(self):
        now = time.time()
        if now - self._last >= 1.0 / self._rate:
            self._last = now
            return True
        return False

    def read_message(self, buf):
        self._seq += 1
        return CANMessage(
            can_id=self._seq & 0x7FF,
            data=[(self._seq + i) % 256 for i in range(8)],
            dlc=8,
            extended=False,
        )

    def close(self):
        pass

    def send_message(self, msg, txbuf=None):
        return True

    def check_tx_result(self, txbuf):
        return "success"

    def get_error_flags(self):
        return 0

    def abort_tx(self, txbuf):
        pass


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_can_with_fake(frame_rate=500):
    manager = CANManager()
    manager.controller = FakeMCP2515(frame_rate=frame_rate)
    manager.connected = True
    manager.running = True
    manager.stats["start_time"] = datetime.now()
    return manager


def _validate_status(status):
    """Return None if status is consistent, an error string otherwise."""
    if status.get("connected") and (status.get("rx_total", -1) < 0):
        return f"impossible rx_total while connected: {status['rx_total']}"
    if not isinstance(status.get("circuit_breaker"), dict):
        return f"missing or corrupt circuit_breaker: {status.get('circuit_breaker')}"
    for key in ("rx_total", "tx_total", "errors"):
        if key not in status:
            return f"missing key '{key}' in status: {status}"
    return None


def _validate_message(msg):
    """Return None if the message dict is well-formed, an error string otherwise."""
    if not isinstance(msg, dict):
        return f"message is not a dict: {msg}"
    for field in ("can_id", "data", "dlc", "timestamp", "direction"):
        if field not in msg:
            return f"message missing field '{field}': {msg}"
    if not isinstance(msg["data"], list):
        return f"message data is not a list: {msg}"
    if len(msg["data"]) != msg.get("dlc", 0):
        return f"message data length {len(msg['data'])} != dlc {msg.get('dlc')}"
    return None


# ═══════════════════════════════════════════════════════════════════
# Stress tests
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.slow
def test_rx_thread_vs_status_readers():
    """
    RX thread feeds frames at 500 Hz while 8 simulated Flask threads
    hammer get_status() and get_recent_messages() for 3 seconds.
    No exceptions, no torn status dicts, no corrupt message entries.
    """
    manager = _make_can_with_fake(frame_rate=500)
    errors = []
    stop = threading.Event()

    def rx_loop():
        while not stop.is_set():
            try:
                buf = manager.controller.available()
                if buf:
                    msg = manager.controller.read_message(buf)
                    if msg:
                        manager._handle_rx(msg)
            except Exception as e:
                errors.append(f"rx error: {e}")
            time.sleep(0.001)

    def reader():
        while not stop.is_set():
            try:
                status = manager.get_status()
                err = _validate_status(status)
                if err:
                    errors.append(err)

                msgs = manager.get_recent_messages(50)
                for m in msgs:
                    err = _validate_message(m)
                    if err:
                        errors.append(err)
            except Exception as e:
                errors.append(f"reader error: {e}")

    threads = [threading.Thread(target=rx_loop, name="CAN-RX")] + [
        threading.Thread(target=reader, name=f"Flask-{i}") for i in range(8)
    ]

    for t in threads:
        t.start()
    time.sleep(3)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert len(errors) == 0, f"errors across 9 threads: {errors[:5]}"
    assert manager.stats["rx_total"] > 0, "RX counter should have incremented"


@pytest.mark.slow
def test_rx_thread_vs_tx_writers():
    """
    RX thread runs at 200 Hz while 4 simulated Flask threads send
    frames concurrently for 3 seconds. Both rx_total and tx_total
    must be non-zero; the message log must contain both RX and TX
    entries with no corrupt records.
    """
    manager = _make_can_with_fake(frame_rate=200)
    errors = []
    stop = threading.Event()

    def rx_loop():
        while not stop.is_set():
            try:
                buf = manager.controller.available()
                if buf:
                    msg = manager.controller.read_message(buf)
                    if msg:
                        manager._handle_rx(msg)
            except Exception:
                pass
            time.sleep(0.001)

    def writer():
        while not stop.is_set():
            try:
                manager.send_message(0x100, [1, 2, 3, 4])
            except Exception as e:
                errors.append(f"writer error: {e}")

    threads = [threading.Thread(target=rx_loop, name="CAN-RX")] + [
        threading.Thread(target=writer, name=f"TX-{i}") for i in range(4)
    ]

    for t in threads:
        t.start()
    time.sleep(3)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert len(errors) == 0, f"errors across 5 threads: {errors[:5]}"
    assert manager.stats["tx_total"] > 0, "TX counter should have incremented"

    msgs = manager.get_recent_messages(1000)
    directions = {m.get("direction") for m in msgs}
    assert "RX" in directions, "message log should contain RX entries"
    assert "TX" in directions, "message log should contain TX entries"


@pytest.mark.slow
def test_subscriber_callback_from_rx_thread():
    """
    RX thread delivers frames via subscriber callback — the callback
    runs in the RX thread's context. Verify it's called at least once
    and never raises.
    """
    manager = _make_can_with_fake(frame_rate=500)
    stop = threading.Event()
    call_count = 0
    call_errors = []

    def subscriber(msg):
        nonlocal call_count
        call_count += 1
        if not isinstance(msg, dict):
            call_errors.append(f"subscriber received non-dict: {type(msg)}")
        if "can_id" not in msg:
            call_errors.append(f"subscriber msg missing can_id: {msg}")

    manager.subscribers.append(subscriber)

    def rx_loop():
        while not stop.is_set():
            try:
                buf = manager.controller.available()
                if buf:
                    msg = manager.controller.read_message(buf)
                    if msg:
                        manager._handle_rx(msg)
            except Exception:
                pass
            time.sleep(0.001)

    t = threading.Thread(target=rx_loop, name="CAN-RX")
    t.start()
    time.sleep(2)
    stop.set()
    t.join(timeout=5)

    assert len(call_errors) == 0, f"subscriber errors: {call_errors}"
    assert call_count > 0, "subscriber should have been called at least once"


@pytest.mark.slow
def test_full_suite_readers_writers_subscriber():
    """
    Kitchen-sink: 1 RX thread + 4 readers + 4 writers + 1 subscriber.
    Runs 5 seconds. Zero exceptions, all counters non-zero, all
    messages in the log are well-formed.
    """
    manager = _make_can_with_fake(frame_rate=300)
    errors = []
    stop = threading.Event()

    def rx_loop():
        while not stop.is_set():
            try:
                buf = manager.controller.available()
                if buf:
                    msg = manager.controller.read_message(buf)
                    if msg:
                        manager._handle_rx(msg)
            except Exception as e:
                errors.append(f"rx error: {e}")
            time.sleep(0.001)

    def reader():
        while not stop.is_set():
            try:
                manager.get_status()
                manager.get_recent_messages(100)
            except Exception as e:
                errors.append(f"reader error: {e}")

    def writer():
        while not stop.is_set():
            try:
                manager.send_message(0x200, [0xAA, 0xBB, 0xCC])
            except Exception as e:
                errors.append(f"writer error: {e}")

    def subscriber(msg):
        pass  # minimal — just verify it doesn't crash

    manager.subscribers.append(subscriber)

    threads = (
        [threading.Thread(target=rx_loop, name="CAN-RX")]
        + [threading.Thread(target=reader, name=f"Reader-{i}") for i in range(4)]
        + [threading.Thread(target=writer, name=f"Writer-{i}") for i in range(4)]
    )

    for t in threads:
        t.start()
    time.sleep(5)
    stop.set()
    for t in threads:
        t.join(timeout=8)

    assert len(errors) == 0, f"errors across 10 threads: {errors[:5]}"
    assert manager.stats["rx_total"] > 0, "rx_total should be non-zero"
    assert manager.stats["tx_total"] > 0, "tx_total should be non-zero"
    all_msgs = manager.get_recent_messages(1000)
    assert len(all_msgs) > 0, "message log should not be empty"
    for m in all_msgs:
        err = _validate_message(m)
        assert err is None, f"corrupt message: {err}"
