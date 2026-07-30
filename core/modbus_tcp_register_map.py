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
VALID_FC_REGISTER_WRITE = {6, 16}
VALID_FC = VALID_FC_READ | VALID_FC_WRITE | VALID_FC_REGISTER_WRITE


class RegisterMapEntry:
    """One mapping from a Modbus (function_code, address) pair to a
    Flemingo data source.

    source_type values:
      - "local"     — resolved from DI/DO/CAN state (existing behaviour)
      - "modbus_rtu" — forwarded to a Modbus RTU device
    """

    def __init__(
        self,
        function_code: int,
        address: int,
        source_key: str,
        label: str = "",
        source_type: str = "local",
        rtu_device_id: str = "",
        rtu_address: int = 0,
        writable: bool = False,
    ):
        self.function_code = function_code
        self.address = address
        self.source_key = source_key
        self.label = label
        self.source_type = source_type
        self.rtu_device_id = rtu_device_id
        self.rtu_address = rtu_address
        self.writable = writable

    def to_dict(self) -> dict:
        return {
            "function_code": self.function_code,
            "address": self.address,
            "source_key": self.source_key,
            "label": self.label,
            "source_type": self.source_type,
            "rtu_device_id": self.rtu_device_id,
            "rtu_address": self.rtu_address,
            "writable": self.writable,
        }

    @staticmethod
    def from_dict(d: dict) -> "RegisterMapEntry":
        return RegisterMapEntry(
            function_code=int(d["function_code"]),
            address=int(d["address"]),
            source_key=str(d.get("source_key", "")),
            label=str(d.get("label", "")),
            source_type=str(d.get("source_type", "local")),
            rtu_device_id=str(d.get("rtu_device_id", "")),
            rtu_address=int(d.get("rtu_address", 0)),
            writable=bool(d.get("writable", False)),
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
        src_type = e.get("source_type", "local")
        writable = e.get("writable", False)

        if fc is None or addr is None:
            errors.append(f"{prefix}: 'function_code' and 'address' are required")
            continue

        fc_int = int(fc)
        addr_int = int(addr)

        if fc_int not in VALID_FC:
            errors.append(f"{prefix}: function_code {fc_int} not in {sorted(VALID_FC)}")
        if not (0 <= addr_int <= 65535):
            errors.append(f"{prefix}: address {addr_int} out of range (0–65535)")

        if src_type not in ("local", "modbus_rtu"):
            errors.append(
                f"{prefix}: source_type must be 'local' or 'modbus_rtu', got {src_type!r}"
            )

        if src_type == "local":
            if (
                src
                and src not in VALID_SOURCE_PATTERNS
                and fc_int not in VALID_FC_REGISTER_WRITE
            ):
                errors.append(
                    f"{prefix}: unknown source_key '{src}'. "
                    f"Valid keys: {sorted(VALID_SOURCE_PATTERNS)}"
                )
        elif src_type == "modbus_rtu":
            if not e.get("rtu_device_id"):
                errors.append(
                    f"{prefix}: 'rtu_device_id' is required when source_type is 'modbus_rtu'"
                )

        key = (fc_int, addr_int)
        if key in seen:
            errors.append(
                f"{prefix}: overlaps entry[{seen[key]}] — "
                f"function_code {fc_int}, address {addr_int} already mapped"
            )
        else:
            seen[key] = i

    from core.can_send_channel import load_channels

    channels = load_channels()
    occupied_by_channels = set()
    for ch in channels:
        occupied_by_channels.add(("coil", ch.trigger_coil_address))
        occupied_by_channels.add(("holding", ch.id_address))
        for offset in range(4):
            occupied_by_channels.add(("holding", ch.data_start_address + offset))
        occupied_by_channels.add(("holding", ch.dlc_address))

    for i, e in enumerate(entries):
        fc = e.get("function_code")
        addr = e.get("address")
        if fc is None or addr is None:
            continue
        kind = "coil" if int(fc) in (1, 5, 15) else "holding"
        if (kind, int(addr)) in occupied_by_channels:
            errors.append(
                f"entry[{i}]: address {int(addr)} already used by a CAN send channel"
            )

    return errors
