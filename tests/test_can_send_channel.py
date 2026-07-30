#!/usr/bin/env python3
# tests/test_can_send_channel.py
# Unit tests for CANSendChannel persistence and validation.

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.can_send_channel import (
    CANSendChannel,
    load_channels,
    save_channels,
    validate_channels,
    STAGE_REGISTER_COUNT,
)


class TestCANSendChannel:
    def test_round_trip(self):
        ch = CANSendChannel("test", 100, 101, 105, 50)
        d = ch.to_dict()
        ch2 = CANSendChannel.from_dict(d)
        assert ch2.name == "test"
        assert ch2.id_address == 100
        assert ch2.data_start_address == 101
        assert ch2.dlc_address == 105
        assert ch2.trigger_coil_address == 50

    def test_register_addresses(self):
        ch = CANSendChannel("test", 100, 101, 105, 50)
        regs = ch.register_addresses()
        assert regs == {100, 101, 102, 103, 104, 105}

    def test_staging_addresses(self):
        ch = CANSendChannel("test", 100, 101, 105, 50)
        staging = ch.staging_addresses()
        assert staging == [100, 101, 102, 103, 104, 105]
        assert len(staging) == STAGE_REGISTER_COUNT

    def test_save_load_round_trip(self, tmp_path):
        path = str(tmp_path / "can_channels.json")
        channels = [
            CANSendChannel("ch1", 100, 101, 105, 50),
            CANSendChannel("ch2", 200, 201, 205, 51),
        ]
        save_channels(channels, path)
        loaded = load_channels(path)
        assert len(loaded) == 2
        assert loaded[0].name == "ch1"
        assert loaded[1].name == "ch2"

    def test_load_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        loaded = load_channels(path)
        assert loaded == []

    def test_load_non_list(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            json.dump({"not": "a list"}, f)
        loaded = load_channels(path)
        assert loaded == []

    def test_load_malformed_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("not valid json")
        loaded = load_channels(path)
        assert loaded == []

    def test_atomic_write(self, tmp_path):
        path = str(tmp_path / "atomic.json")
        channels = [CANSendChannel("ch1", 100, 101, 105, 50)]
        save_channels(channels, path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")


class TestValidateChannels:
    def test_valid_passes(self):
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 100,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
            ]
        )
        assert errors == []

    def test_missing_name(self):
        errors = validate_channels(
            [
                {
                    "name": "",
                    "id_address": 100,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
            ]
        )
        assert len(errors) > 0
        assert "name" in errors[0].lower()

    def test_duplicate_name(self):
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 100,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
                {
                    "name": "ch1",
                    "id_address": 200,
                    "data_start_address": 201,
                    "dlc_address": 205,
                    "trigger_coil_address": 51,
                },
            ]
        )
        assert len(errors) > 0
        assert "already used" in errors[0].lower()

    def test_overlapping_registers(self):
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 100,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
                {
                    "name": "ch2",
                    "id_address": 102,
                    "data_start_address": 200,
                    "dlc_address": 204,
                    "trigger_coil_address": 51,
                },
            ]
        )
        assert len(errors) > 0
        assert "overlaps" in errors[0].lower()

    def test_data_start_overflow(self):
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 100,
                    "data_start_address": 65534,
                    "dlc_address": 200,
                    "trigger_coil_address": 50,
                },
            ]
        )
        assert len(errors) > 0
        assert "exceeds 65535" in errors[0].lower()

    def test_out_of_range(self):
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 70000,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
            ]
        )
        assert len(errors) > 0
        assert "out of range" in errors[0].lower()

    def test_register_map_overlap(self):
        from core.modbus_tcp_register_map import RegisterMapEntry

        existing = [
            RegisterMapEntry.from_dict(
                {
                    "function_code": 3,
                    "address": 101,
                    "source_key": "can:status.rx_total",
                }
            ),
        ]
        errors = validate_channels(
            [
                {
                    "name": "ch1",
                    "id_address": 100,
                    "data_start_address": 101,
                    "dlc_address": 105,
                    "trigger_coil_address": 50,
                },
            ],
            register_map_entries=existing,
        )
        assert len(errors) > 0
        assert "already mapped" in errors[0].lower()
