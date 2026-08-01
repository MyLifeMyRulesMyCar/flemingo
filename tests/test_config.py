#!/usr/bin/env python3
# tests/test_config.py
# Unit tests for load_hardware_config() — GPIO pin map + CAN settings.

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import (
    load_hardware_config,
    HARDWARE_DEFAULTS,
)


class TestHardwareConfigDefaults:
    def test_missing_file_returns_defaults(self, monkeypatch, tmp_path):
        path = str(tmp_path / "nonexistent.yaml")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg == HARDWARE_DEFAULTS
        assert cfg["gpio"]["outputs"]["DO0"]["line"] == 24
        assert cfg["can"]["bitrate"] == 125000

    def test_cache_is_used_on_second_call(self, tmp_path):
        path = str(tmp_path / "nonexistent.yaml")
        cfg1 = load_hardware_config(path=path)
        cfg2 = load_hardware_config(path=path)
        assert cfg1 is cfg2

    def test_force_reload_bypasses_cache(self, tmp_path):
        path = str(tmp_path / "nonexistent.yaml")
        cfg1 = load_hardware_config(path=path)
        cfg2 = load_hardware_config(path=path, force_reload=True)
        assert cfg1 == cfg2
        # force_reload creates a fresh dict, not the cached reference
        assert cfg1 is not cfg2


class TestHardwareConfigMerge:
    def test_override_single_pin(self, tmp_path):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write("gpio:\n  outputs:\n    DO2:\n      line: 99\n")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg["gpio"]["outputs"]["DO2"]["line"] == 99
        assert cfg["gpio"]["outputs"]["DO0"]["line"] == 24
        assert cfg["gpio"]["inputs"]["DI0"]["chip"] == "/dev/gpiochip4"

    def test_override_can_bitrate(self, tmp_path):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write("can:\n  bitrate: 500000\n")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg["can"]["bitrate"] == 500000
        assert cfg["can"]["crystal"] == 8000000

    def test_override_can_crystal(self, tmp_path):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write("can:\n  crystal: 16000000\n")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg["can"]["crystal"] == 16000000


class TestHardwareConfigValidation:
    def _save_and_load(self, tmp_path, yaml_content):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write(yaml_content)
        return load_hardware_config(path=path, force_reload=True)

    def test_unknown_output_channel_raises(self, tmp_path):
        with pytest.raises(ValueError, match="unknown output channel"):
            self._save_and_load(
                tmp_path,
                "gpio:\n  outputs:\n    DO99:\n      chip: /dev/gpiochip1\n      line: 1\n",
            )

    def test_unknown_input_channel_raises(self, tmp_path):
        with pytest.raises(ValueError, match="unknown input channel"):
            self._save_and_load(
                tmp_path,
                "gpio:\n  inputs:\n    DI99:\n      chip: /dev/gpiochip1\n      line: 1\n",
            )

    def test_bad_chip_path_raises(self, tmp_path):
        with pytest.raises(ValueError, match="invalid chip path"):
            self._save_and_load(
                tmp_path,
                "gpio:\n  outputs:\n    DO0:\n      chip: /dev/wrong\n      line: 24\n",
            )

    def test_bad_line_number_raises(self, tmp_path):
        with pytest.raises(ValueError, match="invalid line number"):
            self._save_and_load(
                tmp_path,
                "gpio:\n  outputs:\n    DO0:\n      chip: /dev/gpiochip1\n      line: -1\n",
            )

    def test_bad_line_type_raises(self, tmp_path):
        with pytest.raises(ValueError, match="invalid line number"):
            self._save_and_load(
                tmp_path,
                "gpio:\n  outputs:\n    DO0:\n      chip: /dev/gpiochip1\n      line: abc\n",
            )

    def test_bad_can_bitrate_raises(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported CAN bitrate"):
            self._save_and_load(tmp_path, "can:\n  bitrate: 9999\n")

    def test_bad_can_crystal_raises(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported CAN crystal"):
            self._save_and_load(tmp_path, "can:\n  crystal: 9999\n")

    def test_bad_yaml_falls_back(self, tmp_path):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write(": invalid yaml ::::\n")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg == HARDWARE_DEFAULTS

    def test_empty_file_merges_defaults(self, tmp_path):
        path = str(tmp_path / "hw.yaml")
        with open(path, "w") as f:
            f.write("")
        cfg = load_hardware_config(path=path, force_reload=True)
        assert cfg == HARDWARE_DEFAULTS
