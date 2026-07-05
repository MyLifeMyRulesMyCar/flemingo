#!/usr/bin/env python3
# tests/test_validators.py
# Phase 7 - pure-logic tests for api/validators.py.

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.validators import (
    ValidationError,
    parse_int, parse_int_or_hex, parse_bool, require_fields,
    validate_can_bitrate, validate_can_crystal,
    validate_can_id, validate_can_payload, validate_count,
    validate_modbus_slave_id, validate_modbus_address,
    validate_modbus_register_value, validate_modbus_coil_value,
    validate_modbus_baudrate, validate_modbus_parity,
    validate_modbus_stopbits, validate_modbus_port,
    validate_modbus_scan_range, validate_device_name,
)


# ─────────────────────────────────────────────────
# parse_int
# ─────────────────────────────────────────────────
def test_parse_int():
    assert parse_int(0, "x") == 0
    assert parse_int(42, "x") == 42
    assert parse_int(-1, "x") == -1
    assert parse_int("99", "x") == 99
    assert parse_int(3.0, "x") == 3, "whole float"

    with pytest.raises(ValidationError):
        parse_int(True, "x")
    with pytest.raises(ValidationError):
        parse_int(3.5, "x")
    with pytest.raises(ValidationError):
        parse_int("abc", "x")
    with pytest.raises(ValidationError):
        parse_int("0x1F", "x")
    with pytest.raises(ValidationError):
        parse_int(None, "x")
    with pytest.raises(ValidationError):
        parse_int([], "x")


# ─────────────────────────────────────────────────
# parse_int_or_hex
# ─────────────────────────────────────────────────
def test_parse_int_or_hex():
    assert parse_int_or_hex(255, "x") == 255
    assert parse_int_or_hex("0xFF", "x") == 255
    assert parse_int_or_hex("0x7FF", "x") == 0x7FF
    assert parse_int_or_hex("291", "x") == 291
    assert parse_int_or_hex("0X1A", "x") == 0x1A, "uppercase 0X"

    with pytest.raises(ValidationError):
        parse_int_or_hex(True, "x")
    with pytest.raises(ValidationError):
        parse_int_or_hex("abc", "x")
    with pytest.raises(ValidationError):
        parse_int_or_hex(None, "x")


# ─────────────────────────────────────────────────
# parse_bool
# ─────────────────────────────────────────────────
def test_parse_bool():
    assert parse_bool(True, "x") == True
    assert parse_bool(False, "x") == False
    assert parse_bool(1, "x") == True
    assert parse_bool(0, "x") == False
    assert parse_bool("true", "x") == True
    assert parse_bool("False", "x") == False
    assert parse_bool("1", "x") == True
    assert parse_bool("0", "x") == False

    with pytest.raises(ValidationError):
        parse_bool("yes_please", "x")
    with pytest.raises(ValidationError):
        parse_bool(None, "x")


# ─────────────────────────────────────────────────
# require_fields
# ─────────────────────────────────────────────────
def test_require_fields():
    require_fields({"a": 1, "b": 2}, "a", "b")        # no error

    with pytest.raises(ValidationError):
        require_fields({"a": 1}, "a", "b")
    with pytest.raises(ValidationError):
        require_fields({}, "a")


# ─────────────────────────────────────────────────
# CAN: bitrate
# ─────────────────────────────────────────────────
def test_validate_can_bitrate():
    for v in [125_000, 250_000, 500_000, 1_000_000]:
        assert validate_can_bitrate(v) == v
        assert validate_can_bitrate(str(v)) == v, "string form"

    with pytest.raises(ValidationError):
        validate_can_bitrate(999)
    with pytest.raises(ValidationError):
        validate_can_bitrate(0)
    with pytest.raises(ValidationError):
        validate_can_bitrate(100_000)


# ─────────────────────────────────────────────────
# CAN: crystal
# ─────────────────────────────────────────────────
def test_validate_can_crystal():
    assert validate_can_crystal(8_000_000) == 8_000_000
    assert validate_can_crystal(16_000_000) == 16_000_000

    with pytest.raises(ValidationError):
        validate_can_crystal(12_000_000)
    with pytest.raises(ValidationError):
        validate_can_crystal(0)


# ─────────────────────────────────────────────────
# CAN: frame ID
# ─────────────────────────────────────────────────
def test_validate_can_id():
    # Standard frame (11-bit): 0x000 – 0x7FF
    assert validate_can_id(0, False) == 0
    assert validate_can_id(0x7FF, False) == 0x7FF
    assert validate_can_id("0x7FF", False) == 0x7FF, "hex string"
    assert validate_can_id(291, False) == 291, "decimal int"

    with pytest.raises(ValidationError):
        validate_can_id(0x800, False)
    with pytest.raises(ValidationError):
        validate_can_id(-1, False)

    # Extended frame (29-bit): 0 – 0x1FFFFFFF
    assert validate_can_id(0x1FFFFFFF, True) == 0x1FFFFFFF
    with pytest.raises(ValidationError):
        validate_can_id(0x20000000, True)

    # What's invalid for std is valid for extended
    assert validate_can_id(0x800, True) == 0x800, "0x800 valid as extended"


# ─────────────────────────────────────────────────
# CAN: payload
# ─────────────────────────────────────────────────
def test_validate_can_payload():
    assert validate_can_payload([]) == []
    assert validate_can_payload([0, 255]) == [0, 255]
    assert validate_can_payload(["0xFF", 0]) == [255, 0], "hex strings in list"
    assert validate_can_payload([1]*8) == [1]*8, "exactly 8 bytes"

    with pytest.raises(ValidationError):
        validate_can_payload([1]*9)
    with pytest.raises(ValidationError):
        validate_can_payload([256])
    with pytest.raises(ValidationError):
        validate_can_payload([-1])
    with pytest.raises(ValidationError):
        validate_can_payload({})
    with pytest.raises(ValidationError):
        validate_can_payload("0xFF")


# ─────────────────────────────────────────────────
# validate_count
# ─────────────────────────────────────────────────
def test_validate_count():
    assert validate_count(1) == 1
    assert validate_count(1000) == 1000
    assert validate_count(100) == 100
    assert validate_count("50") == 50, "string form"

    with pytest.raises(ValidationError):
        validate_count(0)
    with pytest.raises(ValidationError):
        validate_count(1001)
    with pytest.raises(ValidationError):
        validate_count(-5)


# ─────────────────────────────────────────────────
# Modbus: slave ID
# ─────────────────────────────────────────────────
def test_validate_modbus_slave_id():
    assert validate_modbus_slave_id(1) == 1
    assert validate_modbus_slave_id(247) == 247
    assert validate_modbus_slave_id("1") == 1, "string form"

    with pytest.raises(ValidationError):
        validate_modbus_slave_id(0)
    with pytest.raises(ValidationError):
        validate_modbus_slave_id(248)
    with pytest.raises(ValidationError):
        validate_modbus_slave_id(-1)


# ─────────────────────────────────────────────────
# Modbus: address
# ─────────────────────────────────────────────────
def test_validate_modbus_address():
    assert validate_modbus_address(0) == 0
    assert validate_modbus_address(65535) == 65535
    assert validate_modbus_address("0") == 0, "string form"

    with pytest.raises(ValidationError):
        validate_modbus_address(-1)
    with pytest.raises(ValidationError):
        validate_modbus_address(65536)


# ─────────────────────────────────────────────────
# Modbus: register value (FC6)
# ─────────────────────────────────────────────────
def test_validate_modbus_register_value():
    assert validate_modbus_register_value(0) == 0
    assert validate_modbus_register_value(65535) == 65535

    with pytest.raises(ValidationError):
        validate_modbus_register_value(-1)
    with pytest.raises(ValidationError):
        validate_modbus_register_value(65536)


# ─────────────────────────────────────────────────
# Modbus: coil value (FC5)
# ─────────────────────────────────────────────────
def test_validate_modbus_coil_value():
    assert validate_modbus_coil_value(0) == 0
    assert validate_modbus_coil_value(1) == 1

    with pytest.raises(ValidationError):
        validate_modbus_coil_value(2)
    with pytest.raises(ValidationError):
        validate_modbus_coil_value(-1)


# ─────────────────────────────────────────────────
# Modbus: baudrate
# ─────────────────────────────────────────────────
def test_validate_modbus_baudrate():
    for v in [9600, 19200, 38400, 57600, 115200, 230400]:
        assert validate_modbus_baudrate(v) == v

    with pytest.raises(ValidationError):
        validate_modbus_baudrate(1234)
    with pytest.raises(ValidationError):
        validate_modbus_baudrate(0)
    with pytest.raises(ValidationError):
        validate_modbus_baudrate(460800)


# ─────────────────────────────────────────────────
# Modbus: parity
# ─────────────────────────────────────────────────
def test_validate_modbus_parity():
    assert validate_modbus_parity("N") == "N"
    assert validate_modbus_parity("E") == "E"
    assert validate_modbus_parity("O") == "O"
    assert validate_modbus_parity("n") == "N", "lowercase accepted"
    assert validate_modbus_parity("e") == "E", "lowercase accepted"

    with pytest.raises(ValidationError):
        validate_modbus_parity("X")
    with pytest.raises(ValidationError):
        validate_modbus_parity("")
    with pytest.raises(ValidationError):
        validate_modbus_parity(0)


# ─────────────────────────────────────────────────
# Modbus: stop bits
# ─────────────────────────────────────────────────
def test_validate_modbus_stopbits():
    assert validate_modbus_stopbits(1) == 1
    assert validate_modbus_stopbits(2) == 2

    with pytest.raises(ValidationError):
        validate_modbus_stopbits(0)
    with pytest.raises(ValidationError):
        validate_modbus_stopbits(3)


# ─────────────────────────────────────────────────
# Modbus: port
# ─────────────────────────────────────────────────
def test_validate_modbus_port():
    ports = {"ttyUSB0": {}, "ttyUSB1": {}}
    assert validate_modbus_port("ttyUSB0", ports) == "ttyUSB0"
    assert validate_modbus_port("ttyUSB1", ports) == "ttyUSB1"

    with pytest.raises(ValidationError):
        validate_modbus_port("ttyUSB9", ports)
    with pytest.raises(ValidationError):
        validate_modbus_port("", ports)
    with pytest.raises(ValidationError):
        validate_modbus_port(0, ports)


# ─────────────────────────────────────────────────
# Modbus: scan range
# ─────────────────────────────────────────────────
def test_validate_modbus_scan_range():
    validate_modbus_scan_range(1, 10)    # valid, no error
    validate_modbus_scan_range(5, 5)     # single-ID scan, valid

    with pytest.raises(ValidationError):
        validate_modbus_scan_range(10, 1)


# ─────────────────────────────────────────────────
# Device name
# ─────────────────────────────────────────────────
def test_validate_device_name():
    assert validate_device_name("TestDevice") == "TestDevice"
    assert validate_device_name("RS485 Adapter 1") == "RS485 Adapter 1"
    assert validate_device_name("sensor-01") == "sensor-01"
    assert validate_device_name("  trimmed  ") == "trimmed", "whitespace trimmed"
    assert validate_device_name("A" * 64) == "A" * 64, "exactly 64 chars"

    with pytest.raises(ValidationError):
        validate_device_name("")
    with pytest.raises(ValidationError):
        validate_device_name("  ")
    with pytest.raises(ValidationError):
        validate_device_name("A" * 65)
    with pytest.raises(ValidationError):
        validate_device_name("bad;name")
    with pytest.raises(ValidationError):
        validate_device_name("name\x00")
    with pytest.raises(ValidationError):
        validate_device_name(123)
