#!/usr/bin/env python3
# tests/test_can_manager_filter.py
# Unit tests for CANManager's id_filter — pure logic, no hardware.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.can_manager import CANManager
from core.mcp2515_driver import CANMessage


class TestCANManagerFilter:
    def test_matches_filter_passes_all_when_empty(self):
        mgr = CANManager()
        assert mgr.matches_filter(0x100) is True
        assert mgr.matches_filter(0x999) is True

    def test_matches_filter_blocks_non_matching(self):
        mgr = CANManager()
        mgr.set_id_filter([0xF6])
        assert mgr.matches_filter(0xF6) is True
        assert mgr.matches_filter(0x36) is False

    def test_matches_filter_empty_clears(self):
        mgr = CANManager()
        mgr.set_id_filter([0x100])
        mgr.set_id_filter([])
        assert mgr.matches_filter(0x200) is True

    def test_get_status_reports_filter(self):
        mgr = CANManager()
        mgr.set_id_filter([0x200, 0x100])
        assert mgr.get_status()["id_filter"] == [256, 512]

    def test_manager_filter_does_not_gate_subscribers(self):
        """Regression: the MQTT bridge subscriber must see ALL frames
        regardless of the manager's id_filter. Only the dashboard's
        broadcast_can_message() should call matches_filter()."""
        mgr = CANManager()
        mgr.set_id_filter([0xF6])
        received = []
        mgr.subscribe(lambda entry: received.append(entry["can_id"]))
        mgr._handle_rx(CANMessage(can_id=0x36, data=[1], dlc=1))
        assert 0x36 in received, "subscriber must get unfiltered frames"

    def test_handle_rx_logs_all_regardless_of_filter(self):
        mgr = CANManager()
        mgr.set_id_filter([0xF6])
        mgr._handle_rx(CANMessage(can_id=0x36, data=[1], dlc=1))
        mgr._handle_rx(CANMessage(can_id=0xF6, data=[2], dlc=1))
        log_ids = [m["can_id"] for m in mgr.message_log]
        assert 0x36 in log_ids, "all frames logged regardless of filter"
        assert 0xF6 in log_ids
