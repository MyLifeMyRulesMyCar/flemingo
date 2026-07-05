#!/usr/bin/env python3
# tests/test_io_manager.py
# Unit tests for IOManager logic — forced simulation mode via gpiod mock.
# Replaces the former duplicate of test_di_monitor.py.

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def sim_io():
    """IOManager forced into simulation mode regardless of gpiod availability."""
    with patch("gpiod.Chip", side_effect=OSError("mock — no hardware")):
        from core.io_manager import IOManager
        io = IOManager()
        io._sim_di = [0, 0, 0, 0]
        io._sim_do = [0, 0, 0, 0]
        return io


class TestIOManagerSimulation:
    def test_construction_sets_simulation(self, sim_io):
        assert sim_io.simulation, "should fall back to sim when gpiod unavailable"

    def test_sim_di_initial_all_zero(self, sim_io):
        assert sim_io.read_all_inputs() == [0, 0, 0, 0]

    def test_sim_di_write_then_read(self, sim_io):
        sim_io._sim_di[1] = 1
        sim_io._sim_di[3] = 1
        assert sim_io.read_all_inputs() == [0, 1, 0, 1]

    def test_sim_do_write_then_readback(self, sim_io):
        sim_io.write_output(0, 1)
        sim_io.write_output(2, 1)
        assert sim_io._sim_do == [1, 0, 1, 0]

    def test_sim_do_write_sets_state(self, sim_io):
        sim_io.write_output(1, 1)
        assert sim_io._sim_do[1] == 1
        sim_io.write_output(1, 0)
        assert sim_io._sim_do[1] == 0

    def test_sim_do_any_truthy_becomes_one(self, sim_io):
        sim_io.write_output(0, True)
        sim_io.write_output(1, 42)
        assert sim_io._sim_do[0] == 1
        assert sim_io._sim_do[1] == 1

    def test_get_status(self, sim_io):
        status = sim_io.get_status()
        assert "simulation" in status
        assert status["simulation"] is True

    def test_channel_lists_exist(self, sim_io):
        from core.io_manager import DI_CHANNELS, DO_CHANNELS
        assert len(DI_CHANNELS) == 4
        assert len(DO_CHANNELS) == 4
