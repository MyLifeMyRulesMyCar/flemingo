#!/usr/bin/env python3
# core/modbus_tcp_register_map.py
# Modbus TCP server register map — configurable mapping of DI/DO/CAN state
# to Modbus register addresses for external SCADA/HMI clients.
#
# Register map entries define which function_code + address pair is mapped
# to which Flemingo data source. The map persists to disk as JSON and
# survives daemon restarts (unlike the in-memory-only RTU device list).
#
# Atomic write guarantee: save writes to a .tmp file first, then
# os.replace() atomically swaps it in. Partial writes from a crash
# mid-save never corrupt the real file.

import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, "config", "modbus_tcp_map.json")

VALID_SOURCE_PATTERNS = {
    "di:0",
    "di:1",
    "di:2",
    "di:3",
    "do:0",
    "do:1",
    "do:2",
    "do:3",
    "can:status.rx_total",
    "can:status.tx_total",
    "can:status.errors",
    "can:status.connected",
    "can:status.uptime",
}

VALID_FC_READ = {1, 2, 3, 4}
VALID_FC_WRITE = {5, 15}
# FC 6/16 not yet implemented
VALID_FC = VALID_FC_READ | VALID_FC_WRITE


class RegisterMapEntry:
    """One mapping from a Modbus (function_code, address) pair to a
    Flemingo data source."""

    def __init__(
        self, function_code: int, address: int, source_key: str, label: str = ""
    ):
        self.function_code = function_code
        self.address = address
        self.source_key = source_key
        self.label = label

    def to_dict(self) -> dict:
        return {
            "function_code": self.function_code,
            "address": self.address,
            "source_key": self.source_key,
            "label": self.label,
        }

    @staticmethod
    def from_dict(d: dict) -> "RegisterMapEntry":
        return RegisterMapEntry(
            function_code=int(d["function_code"]),
            address=int(d["address"]),
            source_key=str(d["source_key"]),
            label=str(d.get("label", "")),
        )

    def overlap_key(self) -> tuple:
        """Key for detecting overlapping entries — same fc+addr can't
        map to two different sources."""
        return (self.function_code, self.address)

    def __eq__(self, other):
        if not isinstance(other, RegisterMapEntry):
            return False
        return self.overlap_key() == other.overlap_key()

    def __hash__(self):
        return hash(self.overlap_key())


# ═══════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════


def load_register_map(path=None) -> List[RegisterMapEntry]:
    """Load the register map from disk. Empty file or missing file →
    empty list. Never raises — logs a warning on malformed JSON."""
    path = path or _DEFAULT_PATH
    if not os.path.exists(path):
        logger.info(f"No register map at {path} — starting empty.")
        return []

    try:
        with open(path, "r") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            logger.warning(f"Register map at {path} is not a list — starting empty.")
            return []
        return [RegisterMapEntry.from_dict(e) for e in raw]
    except json.JSONDecodeError as e:
        logger.warning(
            f"Register map at {path} is not valid JSON ({e}) — starting empty."
        )
        return []
    except Exception as e:
        logger.warning(
            f"Could not load register map from {path} ({e}) — starting empty."
        )
        return []


def save_register_map(entries: List[RegisterMapEntry], path=None):
    """Atomically write the register map to disk. Writes to a .tmp file
    first, then os.replace() swaps it in — partial writes from a crash
    mid-save never corrupt the real file."""
    path = path or _DEFAULT_PATH
    tmp = path + ".tmp"

    raw = [e.to_dict() for e in entries]
    with open(tmp, "w") as f:
        json.dump(raw, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)
    logger.info(f"Register map saved: {len(entries)} entries → {path}")


# ═══════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════


def validate_entries(entries: List[dict]) -> List[str]:
    """Validate a list of raw register-map dicts. Returns a list of
    human-readable error strings. Empty list = valid."""
    errors = []
    seen = {}

    for i, e in enumerate(entries):
        prefix = f"entry[{i}]"
        fc = e.get("function_code")
        addr = e.get("address")
        src = e.get("source_key", "")

        if fc is None or addr is None:
            errors.append(f"{prefix}: 'function_code' and 'address' are required")
            continue

        if fc not in VALID_FC:
            errors.append(f"{prefix}: function_code {fc} not in {sorted(VALID_FC)}")
        if not (0 <= int(addr) <= 65535):
            errors.append(f"{prefix}: address {addr} out of range (0–65535)")
        if src and src not in VALID_SOURCE_PATTERNS:
            errors.append(
                f"{prefix}: unknown source_key '{src}'. "
                f"Valid keys: {sorted(VALID_SOURCE_PATTERNS)}"
            )

        key = (int(fc), int(addr))
        if key in seen:
            errors.append(
                f"{prefix}: overlaps entry[{seen[key]}] — "
                f"function_code {fc}, address {addr} already mapped"
            )
        else:
            seen[key] = i

    return errors
