#!/usr/bin/env python3
# tests/test_modbus_tcp_register_map.py
# Unit tests for the register map model and persistence.

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.modbus_tcp_register_map import (
    RegisterMapEntry,
    load_register_map,
    save_register_map,
    validate_entries,
)


def test_round_trip_load_save(tmp_path):
    """Save, then load — entries must match byte-for-byte."""
    path = str(tmp_path / "map.json")
    entries = [
        RegisterMapEntry(1, 0, "do:0", "Output 0"),
        RegisterMapEntry(3, 100, "can:status.rx_total", "CAN RX count"),
    ]
    save_register_map(entries, path)

    loaded = load_register_map(path)
    assert len(loaded) == 2
    assert loaded[0].function_code == 1
    assert loaded[0].address == 0
    assert loaded[0].source_key == "do:0"
    assert loaded[0].label == "Output 0"

    assert loaded[1].function_code == 3
    assert loaded[1].source_key == "can:status.rx_total"


def test_missing_file_returns_empty(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    assert load_register_map(path) == []


def test_non_list_file_returns_empty(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as f:
        json.dump({"not": "a list"}, f)
    assert load_register_map(path) == []


def test_malformed_json_returns_empty(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as f:
        f.write("{not valid json")
    assert load_register_map(path) == []


def test_atomic_write_safe(tmp_path):
    """Verify the .tmp file is deleted after successful save."""
    path = str(tmp_path / "map.json")
    tmp = path + ".tmp"
    entries = [RegisterMapEntry(3, 0, "can:status.rx_total", "")]
    save_register_map(entries, path)

    assert os.path.exists(path), "real file must exist"
    assert not os.path.exists(tmp), "tmp file must be gone after replace"


def test_validate_entries_overlap(tmp_path):
    errors = validate_entries(
        [
            {"function_code": 3, "address": 100, "source_key": "can:status.rx_total"},
            {"function_code": 3, "address": 100, "source_key": "can:status.tx_total"},
        ]
    )
    assert any("overlap" in e.lower() for e in errors)


def test_validate_entries_unknown_source(tmp_path):
    errors = validate_entries(
        [
            {"function_code": 3, "address": 0, "source_key": "di:99"},
        ]
    )
    assert any("unknown source_key" in e for e in errors)


def test_validate_entries_valid_passes(tmp_path):
    errors = validate_entries(
        [
            {"function_code": 3, "address": 0, "source_key": "can:status.rx_total"},
            {"function_code": 1, "address": 0, "source_key": "di:0"},
        ]
    )
    assert errors == []
