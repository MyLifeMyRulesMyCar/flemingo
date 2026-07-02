#!/usr/bin/env python3
# api/validators.py
# Phase 7 - centralised input validation for all route bodies and query params.
#
# Design rules:
#   - Zero imports from core/ - this file must be importable without any
#     hardware singletons being constructed. Routes pass hardware-specific
#     constants (e.g. MODBUS_PORTS) as arguments where needed.
#   - All functions raise ValidationError on bad input, nothing else.
#   - Routes catch ValidationError and return jsonify({"error": str(e)}), 400.
#   - No magic - routes call validators explicitly so it's obvious what's
#     being checked on each field.
#   - Pure stdlib only - no Pydantic/Marshmallow/Cerberus to avoid C-extension
#     build pain on the ARM SBC.
#
# CAN constants (from MCP2515 / CAN 2.0 spec):
#   Standard frame ID:  11-bit → 0x000 – 0x7FF
#   Extended frame ID:  29-bit → 0x00000000 – 0x1FFFFFFF
#   Data payload:       0–8 bytes, each 0–255
#   Standard bitrates:  125k / 250k / 500k / 1M bps
#   MCP2515 crystal:    8 MHz or 16 MHz (determines timing register values)
#
# Modbus constants (RTU spec + minimalmodbus limits):
#   Slave ID:    1–247  (0 = broadcast, 248–255 reserved)
#   Address:     0–65535
#   Register value (FC6/FC3):  0–65535
#   Coil value   (FC5/FC1):    0 or 1
#   Parity:      N (none) / E (even) / O (odd)
#   Stop bits:   1 or 2
#   Baudrates:   standard UART rates only

from __future__ import annotations

import re
from typing import Any


# ============================================================
# Core error type
# ============================================================
class ValidationError(Exception):
    """Raised by any validate_* function on bad input.
    Message is end-user safe (no stack trace, no internal paths)."""
    pass


# ============================================================
# Low-level parsers
# ============================================================
def parse_body(request) -> dict:
    """
    Extract and return a JSON request body as a dict.
    Returns {} (not an error) if the body is absent or empty —
    matches existing route behaviour where every field has a default.
    Raises ValidationError if the body is present but not valid JSON.
    """
    body = request.get_json(silent=True)
    if body is None:
        # silent=True returns None on parse failure AND on empty body.
        # Distinguish: if Content-Type is json and body is non-empty, it failed.
        ct = request.content_type or ""
        raw = request.get_data(as_text=True)
        if "application/json" in ct and raw.strip():
            raise ValidationError("Request body is not valid JSON")
        return {}
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object")
    return body


def parse_int(value: Any, name: str) -> int:
    """
    Coerce value to int. Accepts:
      - int   → as-is (but not bool, which is a subclass of int in Python)
      - float with no fractional part → int(float)
      - str   → decimal only ("291"), NOT hex (use parse_int_or_hex for CAN IDs)
    Raises ValidationError for anything else.
    """
    if isinstance(value, bool):
        raise ValidationError(f"'{name}' must be an integer, got boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise ValidationError(f"'{name}' must be a whole number, got {value}")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value, 10)
        except ValueError:
            raise ValidationError(f"'{name}' must be an integer, got {value!r}")
    raise ValidationError(f"'{name}' must be an integer, got {type(value).__name__}")


def parse_int_or_hex(value: Any, name: str) -> int:
    """
    Like parse_int but also accepts hex strings: "0x123", "0X1FF".
    Used for CAN IDs and CAN data bytes where hex notation is natural.
    """
    if isinstance(value, bool):
        raise ValidationError(f"'{name}' must be an integer, got boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise ValidationError(f"'{name}' must be a whole number, got {value}")
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s, 0)   # int(s, 0) handles "0x1FF", "291", "0b101"
        except ValueError:
            raise ValidationError(f"'{name}' must be an integer or hex string, got {value!r}")
    raise ValidationError(f"'{name}' must be an integer, got {type(value).__name__}")


def parse_bool(value: Any, name: str) -> bool:
    """
    Accepts Python bool, or strings "true"/"false"/"1"/"0" (case-insensitive).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
    raise ValidationError(f"'{name}' must be true or false, got {value!r}")


def require_fields(data: dict, *fields: str):
    """Raise ValidationError listing all missing required fields at once."""
    missing = [f for f in fields if f not in data]
    if missing:
        raise ValidationError(f"Missing required field(s): {missing}")


# ============================================================
# CAN validators
# ============================================================
_CAN_BITRATES = {125_000, 250_000, 500_000, 1_000_000}
_CAN_CRYSTALS = {8_000_000, 16_000_000}
_CAN_STD_MAX  = 0x7FF         # 11-bit standard frame
_CAN_EXT_MAX  = 0x1FFFFFFF    # 29-bit extended frame


def validate_can_bitrate(value: Any) -> int:
    """Must be one of the standard MCP2515 bitrates (Hz)."""
    v = parse_int(value, "bitrate")
    if v not in _CAN_BITRATES:
        raise ValidationError(
            f"bitrate must be one of {sorted(_CAN_BITRATES)}, got {v}"
        )
    return v


def validate_can_crystal(value: Any) -> int:
    """Must be a supported MCP2515 crystal frequency (Hz): 8 MHz or 16 MHz."""
    v = parse_int(value, "crystal")
    if v not in _CAN_CRYSTALS:
        raise ValidationError(
            f"crystal must be one of {sorted(_CAN_CRYSTALS)}, got {v}"
        )
    return v


def validate_can_id(value: Any, extended: bool = False) -> int:
    """
    Parse and range-check a CAN frame ID.
    Standard (11-bit): 0x000 – 0x7FF
    Extended (29-bit): 0x00000000 – 0x1FFFFFFF
    Accepts int or hex string ("0x123").
    """
    v = parse_int_or_hex(value, "can_id")
    if v < 0:
        raise ValidationError(f"can_id must be non-negative, got {v}")
    limit = _CAN_EXT_MAX if extended else _CAN_STD_MAX
    if v > limit:
        frame_type = "extended (29-bit)" if extended else "standard (11-bit)"
        raise ValidationError(
            f"can_id {hex(v)} exceeds {frame_type} maximum {hex(limit)}"
        )
    return v


def validate_can_payload(value: Any) -> list[int]:
    """
    CAN data payload: must be a list of 0–8 byte values (0–255 each).
    Each element may be int or hex string.
    """
    if not isinstance(value, list):
        raise ValidationError(f"'data' must be a list of bytes, got {type(value).__name__}")
    if len(value) > 8:
        raise ValidationError(f"CAN payload must be ≤ 8 bytes, got {len(value)}")
    result = []
    for i, b in enumerate(value):
        bv = parse_int_or_hex(b, f"data[{i}]")
        if not (0 <= bv <= 255):
            raise ValidationError(f"data[{i}] must be 0–255, got {bv}")
        result.append(bv)
    return result


def validate_count(value: Any, name: str = "count",
                   min_val: int = 1, max_val: int = 1000) -> int:
    """
    General-purpose count/limit query param.
    Used for GET /api/can/messages?count=N and GET /api/modbus/logs?count=N.
    Prevents unbounded list allocations on a memory-constrained SBC.
    """
    v = parse_int(value, name)
    if not (min_val <= v <= max_val):
        raise ValidationError(f"'{name}' must be {min_val}–{max_val}, got {v}")
    return v


# ============================================================
# Modbus validators
# ============================================================
_MODBUS_SLAVE_MIN  = 1
_MODBUS_SLAVE_MAX  = 247
_MODBUS_ADDR_MIN   = 0
_MODBUS_ADDR_MAX   = 65535
_MODBUS_REG_MAX    = 65535
_MODBUS_BAUDRATES  = {9600, 19200, 38400, 57600, 115200, 230400}
_MODBUS_PARITIES   = {"N", "E", "O"}
_MODBUS_STOPBITS   = {1, 2}


def validate_modbus_slave_id(value: Any) -> int:
    """Modbus slave ID: 1–247. 0 is broadcast (not used here); 248–255 reserved."""
    v = parse_int(value, "slave_id")
    if not (_MODBUS_SLAVE_MIN <= v <= _MODBUS_SLAVE_MAX):
        raise ValidationError(
            f"slave_id must be {_MODBUS_SLAVE_MIN}–{_MODBUS_SLAVE_MAX}, got {v}"
        )
    return v


def validate_modbus_address(value: Any) -> int:
    """Modbus register/coil address: 0–65535."""
    v = parse_int(value, "address")
    if not (_MODBUS_ADDR_MIN <= v <= _MODBUS_ADDR_MAX):
        raise ValidationError(
            f"address must be {_MODBUS_ADDR_MIN}–{_MODBUS_ADDR_MAX}, got {v}"
        )
    return v


def validate_modbus_register_value(value: Any) -> int:
    """Holding register write value (FC6): 0–65535."""
    v = parse_int(value, "value")
    if not (0 <= v <= _MODBUS_REG_MAX):
        raise ValidationError(f"value must be 0–65535 for FC6, got {v}")
    return v


def validate_modbus_coil_value(value: Any) -> int:
    """Coil write value (FC5): must be 0 or 1."""
    v = parse_int(value, "value")
    if v not in (0, 1):
        raise ValidationError(f"value must be 0 or 1 for FC5 (coil write), got {v}")
    return v


def validate_modbus_baudrate(value: Any) -> int:
    """Must be a standard UART baudrate supported by minimalmodbus."""
    v = parse_int(value, "baudrate")
    if v not in _MODBUS_BAUDRATES:
        raise ValidationError(
            f"baudrate must be one of {sorted(_MODBUS_BAUDRATES)}, got {v}"
        )
    return v


def validate_modbus_parity(value: Any) -> str:
    """Parity: 'N' (none), 'E' (even), or 'O' (odd). Case-insensitive."""
    if not isinstance(value, str):
        raise ValidationError(f"parity must be 'N', 'E', or 'O', got {value!r}")
    v = value.strip().upper()
    if v not in _MODBUS_PARITIES:
        raise ValidationError(
            f"parity must be one of {sorted(_MODBUS_PARITIES)}, got {value!r}"
        )
    return v


def validate_modbus_stopbits(value: Any) -> int:
    """Stop bits: 1 or 2."""
    v = parse_int(value, "stopbits")
    if v not in _MODBUS_STOPBITS:
        raise ValidationError(f"stopbits must be 1 or 2, got {v}")
    return v


def validate_modbus_port(value: Any, valid_ports: dict | set) -> str:
    """
    Port must be a key in valid_ports (the MODBUS_PORTS dict from core/modbus_manager).
    Receives valid_ports as a parameter to avoid importing core/ here.
    """
    if not isinstance(value, str):
        raise ValidationError(f"port must be a string, got {type(value).__name__}")
    v = value.strip()
    if v not in valid_ports:
        raise ValidationError(
            f"port must be one of {sorted(valid_ports)}, got {v!r}"
        )
    return v


def validate_modbus_scan_range(start_id: int, end_id: int):
    """
    Validate a scan slave ID range. Both IDs must be individually valid
    and start must be ≤ end. Call after validating each ID individually.
    """
    if start_id > end_id:
        raise ValidationError(
            f"start_id ({start_id}) must be ≤ end_id ({end_id})"
        )


def validate_device_name(value: Any) -> str:
    """
    Device name: non-empty string, ≤ 64 characters.
    Allows letters, digits, spaces, hyphens, underscores, dots.
    Rejects control characters and shell-special characters.
    """
    if not isinstance(value, str):
        raise ValidationError(f"name must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValidationError("name must not be empty")
    if len(v) > 64:
        raise ValidationError(f"name must be ≤ 64 characters, got {len(v)}")
    if not re.fullmatch(r"[\w\s\-\.]+", v):
        raise ValidationError(
            "name contains invalid characters "
            "(only letters, digits, spaces, hyphens, underscores, dots allowed)"
        )
    return v