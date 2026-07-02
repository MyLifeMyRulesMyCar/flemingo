#!/usr/bin/env python3
# tests/test_validators.py
# Phase 7 - pure-logic tests for api/validators.py.
# Same style as test_resilience.py / test_watchdog.py / test_auth_manager.py:
# no Flask context, no hardware, just: python3 tests/test_validators.py

import sys
import os

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


def ok(msg):
    print(f"✅ {msg}")


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def must_raise(fn, *args, msg="", **kwargs):
    """Assert fn(*args, **kwargs) raises ValidationError."""
    try:
        fn(*args, **kwargs)
        fail(f"Expected ValidationError but none raised — {msg or fn.__name__}{args}")
    except ValidationError:
        pass


def must_equal(result, expected, msg=""):
    if result != expected:
        fail(f"Expected {expected!r}, got {result!r} — {msg}")


# ─────────────────────────────────────────────────
# parse_int
# ─────────────────────────────────────────────────
def test_parse_int():
    must_equal(parse_int(0, "x"), 0)
    must_equal(parse_int(42, "x"), 42)
    must_equal(parse_int(-1, "x"), -1)
    must_equal(parse_int("99", "x"), 99)
    must_equal(parse_int(3.0, "x"), 3, "whole float")

    must_raise(parse_int, True,   "x", msg="bool rejected")
    must_raise(parse_int, 3.5,    "x", msg="fractional float rejected")
    must_raise(parse_int, "abc",  "x", msg="non-numeric string rejected")
    must_raise(parse_int, "0x1F", "x", msg="hex string rejected (use parse_int_or_hex)")
    must_raise(parse_int, None,   "x", msg="None rejected")
    must_raise(parse_int, [],     "x", msg="list rejected")
    ok("parse_int")


# ─────────────────────────────────────────────────
# parse_int_or_hex
# ─────────────────────────────────────────────────
def test_parse_int_or_hex():
    must_equal(parse_int_or_hex(255, "x"), 255)
    must_equal(parse_int_or_hex("0xFF", "x"), 255)
    must_equal(parse_int_or_hex("0x7FF", "x"), 0x7FF)
    must_equal(parse_int_or_hex("291", "x"), 291)
    must_equal(parse_int_or_hex("0X1A", "x"), 0x1A, "uppercase 0X")

    must_raise(parse_int_or_hex, True,  "x", msg="bool rejected")
    must_raise(parse_int_or_hex, "abc", "x", msg="non-numeric string rejected")
    must_raise(parse_int_or_hex, None,  "x", msg="None rejected")
    ok("parse_int_or_hex")


# ─────────────────────────────────────────────────
# parse_bool
# ─────────────────────────────────────────────────
def test_parse_bool():
    must_equal(parse_bool(True, "x"), True)
    must_equal(parse_bool(False, "x"), False)
    must_equal(parse_bool(1, "x"), True)
    must_equal(parse_bool(0, "x"), False)
    must_equal(parse_bool("true", "x"), True)
    must_equal(parse_bool("False", "x"), False)
    must_equal(parse_bool("1", "x"), True)
    must_equal(parse_bool("0", "x"), False)

    must_raise(parse_bool, "yes_please", "x", msg="arbitrary string rejected")
    must_raise(parse_bool, None, "x", msg="None rejected")
    ok("parse_bool")


# ─────────────────────────────────────────────────
# require_fields
# ─────────────────────────────────────────────────
def test_require_fields():
    require_fields({"a": 1, "b": 2}, "a", "b")        # no error
    must_raise(require_fields, {"a": 1}, "a", "b", msg="missing field raises")
    must_raise(require_fields, {}, "a", msg="empty body missing required field")
    ok("require_fields")


# ─────────────────────────────────────────────────
# CAN: bitrate
# ─────────────────────────────────────────────────
def test_validate_can_bitrate():
    for v in [125_000, 250_000, 500_000, 1_000_000]:
        must_equal(validate_can_bitrate(v), v)
        must_equal(validate_can_bitrate(str(v)), v, "string form")

    must_raise(validate_can_bitrate, 999,    msg="arbitrary value rejected")
    must_raise(validate_can_bitrate, 0,      msg="zero rejected")
    must_raise(validate_can_bitrate, 100_000, msg="100k not in allowed set")
    ok("validate_can_bitrate")


# ─────────────────────────────────────────────────
# CAN: crystal
# ─────────────────────────────────────────────────
def test_validate_can_crystal():
    must_equal(validate_can_crystal(8_000_000), 8_000_000)
    must_equal(validate_can_crystal(16_000_000), 16_000_000)

    must_raise(validate_can_crystal, 12_000_000, msg="12 MHz rejected")
    must_raise(validate_can_crystal, 0,           msg="zero rejected")
    ok("validate_can_crystal")


# ─────────────────────────────────────────────────
# CAN: frame ID
# ─────────────────────────────────────────────────
def test_validate_can_id():
    # Standard frame (11-bit): 0x000 – 0x7FF
    must_equal(validate_can_id(0, False), 0)
    must_equal(validate_can_id(0x7FF, False), 0x7FF)
    must_equal(validate_can_id("0x7FF", False), 0x7FF, "hex string")
    must_equal(validate_can_id(291, False), 291, "decimal int")

    must_raise(validate_can_id, 0x800, False, msg="0x800 exceeds std 11-bit limit")
    must_raise(validate_can_id, -1,    False, msg="negative rejected")

    # Extended frame (29-bit): 0 – 0x1FFFFFFF
    must_equal(validate_can_id(0x1FFFFFFF, True), 0x1FFFFFFF)
    must_raise(validate_can_id, 0x20000000, True,  msg="exceeds ext 29-bit limit")

    # What's invalid for std is valid for extended
    must_equal(validate_can_id(0x800, True), 0x800, "0x800 valid as extended")
    ok("validate_can_id")


# ─────────────────────────────────────────────────
# CAN: payload
# ─────────────────────────────────────────────────
def test_validate_can_payload():
    must_equal(validate_can_payload([]),         [])
    must_equal(validate_can_payload([0, 255]),    [0, 255])
    must_equal(validate_can_payload(["0xFF", 0]), [255, 0], "hex strings in list")
    must_equal(validate_can_payload([1]*8),       [1]*8,    "exactly 8 bytes")

    must_raise(validate_can_payload, [1]*9,  msg="9 bytes exceeds limit")
    must_raise(validate_can_payload, [256],  msg="byte value 256 rejected")
    must_raise(validate_can_payload, [-1],   msg="negative byte rejected")
    must_raise(validate_can_payload, {},     msg="dict rejected")
    must_raise(validate_can_payload, "0xFF", msg="string rejected (not a list)")
    ok("validate_can_payload")


# ─────────────────────────────────────────────────
# validate_count
# ─────────────────────────────────────────────────
def test_validate_count():
    must_equal(validate_count(1),    1)
    must_equal(validate_count(1000), 1000)
    must_equal(validate_count(100),  100)
    must_equal(validate_count("50"), 50, "string form")

    must_raise(validate_count, 0,    msg="below minimum")
    must_raise(validate_count, 1001, msg="above maximum")
    must_raise(validate_count, -5,   msg="negative rejected")
    ok("validate_count")


# ─────────────────────────────────────────────────
# Modbus: slave ID
# ─────────────────────────────────────────────────
def test_validate_modbus_slave_id():
    must_equal(validate_modbus_slave_id(1),   1)
    must_equal(validate_modbus_slave_id(247), 247)
    must_equal(validate_modbus_slave_id("1"), 1, "string form")

    must_raise(validate_modbus_slave_id, 0,   msg="0 is broadcast, not allowed")
    must_raise(validate_modbus_slave_id, 248, msg="248 is reserved")
    must_raise(validate_modbus_slave_id, -1,  msg="negative rejected")
    ok("validate_modbus_slave_id")


# ─────────────────────────────────────────────────
# Modbus: address
# ─────────────────────────────────────────────────
def test_validate_modbus_address():
    must_equal(validate_modbus_address(0),     0)
    must_equal(validate_modbus_address(65535), 65535)
    must_equal(validate_modbus_address("0"),   0, "string form")

    must_raise(validate_modbus_address, -1,    msg="negative rejected")
    must_raise(validate_modbus_address, 65536, msg="above max")
    ok("validate_modbus_address")


# ─────────────────────────────────────────────────
# Modbus: register value (FC6)
# ─────────────────────────────────────────────────
def test_validate_modbus_register_value():
    must_equal(validate_modbus_register_value(0),     0)
    must_equal(validate_modbus_register_value(65535), 65535)

    must_raise(validate_modbus_register_value, -1,    msg="negative rejected")
    must_raise(validate_modbus_register_value, 65536, msg="above 16-bit range")
    ok("validate_modbus_register_value")


# ─────────────────────────────────────────────────
# Modbus: coil value (FC5)
# ─────────────────────────────────────────────────
def test_validate_modbus_coil_value():
    must_equal(validate_modbus_coil_value(0), 0)
    must_equal(validate_modbus_coil_value(1), 1)

    must_raise(validate_modbus_coil_value, 2,  msg="2 rejected (not 0 or 1)")
    must_raise(validate_modbus_coil_value, -1, msg="negative rejected")
    ok("validate_modbus_coil_value")


# ─────────────────────────────────────────────────
# Modbus: baudrate
# ─────────────────────────────────────────────────
def test_validate_modbus_baudrate():
    for v in [9600, 19200, 38400, 57600, 115200, 230400]:
        must_equal(validate_modbus_baudrate(v), v)

    must_raise(validate_modbus_baudrate, 1234,   msg="non-standard baudrate rejected")
    must_raise(validate_modbus_baudrate, 0,      msg="zero rejected")
    must_raise(validate_modbus_baudrate, 460800, msg="non-standard baudrate rejected")
    ok("validate_modbus_baudrate")


# ─────────────────────────────────────────────────
# Modbus: parity
# ─────────────────────────────────────────────────
def test_validate_modbus_parity():
    must_equal(validate_modbus_parity("N"), "N")
    must_equal(validate_modbus_parity("E"), "E")
    must_equal(validate_modbus_parity("O"), "O")
    must_equal(validate_modbus_parity("n"), "N", "lowercase accepted")
    must_equal(validate_modbus_parity("e"), "E", "lowercase accepted")

    must_raise(validate_modbus_parity, "X", msg="invalid parity char rejected")
    must_raise(validate_modbus_parity, "",  msg="empty string rejected")
    must_raise(validate_modbus_parity, 0,   msg="int rejected")
    ok("validate_modbus_parity")


# ─────────────────────────────────────────────────
# Modbus: stop bits
# ─────────────────────────────────────────────────
def test_validate_modbus_stopbits():
    must_equal(validate_modbus_stopbits(1), 1)
    must_equal(validate_modbus_stopbits(2), 2)

    must_raise(validate_modbus_stopbits, 0, msg="0 rejected")
    must_raise(validate_modbus_stopbits, 3, msg="3 rejected")
    ok("validate_modbus_stopbits")


# ─────────────────────────────────────────────────
# Modbus: port
# ─────────────────────────────────────────────────
def test_validate_modbus_port():
    ports = {"ttyUSB0": {}, "ttyUSB1": {}}
    must_equal(validate_modbus_port("ttyUSB0", ports), "ttyUSB0")
    must_equal(validate_modbus_port("ttyUSB1", ports), "ttyUSB1")

    must_raise(validate_modbus_port, "ttyUSB9", ports, msg="unknown port rejected")
    must_raise(validate_modbus_port, "",        ports, msg="empty string rejected")
    must_raise(validate_modbus_port, 0,         ports, msg="int rejected")
    ok("validate_modbus_port")


# ─────────────────────────────────────────────────
# Modbus: scan range
# ─────────────────────────────────────────────────
def test_validate_modbus_scan_range():
    validate_modbus_scan_range(1, 10)    # valid, no error
    validate_modbus_scan_range(5, 5)     # single-ID scan, valid

    must_raise(validate_modbus_scan_range, 10, 1, msg="start > end rejected")
    ok("validate_modbus_scan_range")


# ─────────────────────────────────────────────────
# Device name
# ─────────────────────────────────────────────────
def test_validate_device_name():
    must_equal(validate_device_name("TestDevice"),      "TestDevice")
    must_equal(validate_device_name("RS485 Adapter 1"), "RS485 Adapter 1")
    must_equal(validate_device_name("sensor-01"),       "sensor-01")
    must_equal(validate_device_name("  trimmed  "),     "trimmed", "whitespace trimmed")
    must_equal(validate_device_name("A" * 64),          "A" * 64, "exactly 64 chars")

    must_raise(validate_device_name, "",           msg="empty string rejected")
    must_raise(validate_device_name, "  ",         msg="whitespace-only rejected")
    must_raise(validate_device_name, "A" * 65,    msg="65 chars exceeds limit")
    must_raise(validate_device_name, "bad;name",  msg="semicolon rejected")
    must_raise(validate_device_name, "name\x00",  msg="null byte rejected")
    must_raise(validate_device_name, 123,          msg="int rejected")
    ok("validate_device_name")


# ─────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────
if __name__ == "__main__":
    test_parse_int()
    test_parse_int_or_hex()
    test_parse_bool()
    test_require_fields()

    test_validate_can_bitrate()
    test_validate_can_crystal()
    test_validate_can_id()
    test_validate_can_payload()
    test_validate_count()

    test_validate_modbus_slave_id()
    test_validate_modbus_address()
    test_validate_modbus_register_value()
    test_validate_modbus_coil_value()
    test_validate_modbus_baudrate()
    test_validate_modbus_parity()
    test_validate_modbus_stopbits()
    test_validate_modbus_port()
    test_validate_modbus_scan_range()
    test_validate_device_name()

    print("\nAll validator checks passed.")