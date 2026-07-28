#!/usr/bin/env python3
# core/can_send_channel.py
# CAN send channel definitions for the Modbus TCP server.
#
# A CAN send channel maps a 6-register staging block + 1 trigger coil
# so a SCADA client can assemble a CAN frame via FC 16 (one multi-write)
# and fire it via FC 5 (coil write).
#
# Atomic write guarantee: save writes to a .tmp file first, then
# os.replace() atomically swaps it in. Partial writes from a crash
# mid-save never corrupt the real file.

import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, "config", "can_send_channels.json")

STAGE_REGISTER_COUNT = 6  # id(1) + data(4) + dlc(1)


class CANSendChannel:
    """One named CAN send channel — a 6-register staging block + trigger coil."""

    def __init__(
        self,
        name: str,
        id_address: int,
        data_start_address: int,
        dlc_address: int,
        trigger_coil_address: int,
    ):
        self.name = name
        self.id_address = id_address
        self.data_start_address = data_start_address
        self.dlc_address = dlc_address
        self.trigger_coil_address = trigger_coil_address

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id_address": self.id_address,
            "data_start_address": self.data_start_address,
            "dlc_address": self.dlc_address,
            "trigger_coil_address": self.trigger_coil_address,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CANSendChannel":
        return cls(
            name=str(d["name"]),
            id_address=int(d["id_address"]),
            data_start_address=int(d["data_start_address"]),
            dlc_address=int(d["dlc_address"]),
            trigger_coil_address=int(d["trigger_coil_address"]),
        )

    def register_addresses(self) -> set:
        """Return the set of holding-register addresses this channel owns."""
        return {
            self.id_address,
            self.data_start_address,
            self.data_start_address + 1,
            self.data_start_address + 2,
            self.data_start_address + 3,
            self.dlc_address,
        }

    def staging_addresses(self) -> list:
        """Return the 6 register addresses in order: id, data[0..3], dlc."""
        return [
            self.id_address,
            self.data_start_address,
            self.data_start_address + 1,
            self.data_start_address + 2,
            self.data_start_address + 3,
            self.dlc_address,
        ]


# ═══════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════


def load_channels(path=None) -> List[CANSendChannel]:
    """Load CAN send channels from disk. Empty file or missing file →
    empty list. Never raises — logs a warning on malformed JSON."""
    path = path or _DEFAULT_PATH
    if not os.path.exists(path):
        logger.info(f"No CAN send channels at {path} — starting empty.")
        return []

    try:
        with open(path, "r") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            logger.warning(
                f"CAN send channels at {path} is not a list — starting empty."
            )
            return []
        return [CANSendChannel.from_dict(e) for e in raw]
    except json.JSONDecodeError as e:
        logger.warning(
            f"CAN send channels at {path} is not valid JSON ({e}) — starting empty."
        )
        return []
    except Exception as e:
        logger.warning(
            f"Could not load CAN send channels from {path} ({e}) — starting empty."
        )
        return []


def save_channels(channels: List[CANSendChannel], path=None):
    """Atomically write CAN send channels to disk. Writes to a .tmp file
    first, then os.replace() swaps it in — partial writes from a crash
    mid-save never corrupt the real file."""
    path = path or _DEFAULT_PATH
    tmp = path + ".tmp"

    raw = [c.to_dict() for c in channels]
    with open(tmp, "w") as f:
        json.dump(raw, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)
    logger.info(f"CAN send channels saved: {len(channels)} entries → {path}")


# ═══════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════


def validate_channels(
    channels: List[dict], register_map_entries: Optional[List] = None
) -> List[str]:
    """Validate a list of raw channel dicts. Returns a list of
    human-readable error strings. Empty list = valid.

    If register_map_entries is provided, also checks that no channel
    address overlaps with any existing register-map entry.
    """
    errors = []
    seen_regs = {}
    seen_names = set()

    for i, c in enumerate(channels):
        prefix = f"channel[{i}]"
        name = str(c.get("name", "")).strip()
        if not name:
            errors.append(f"{prefix}: 'name' is required")
            continue
        if name in seen_names:
            errors.append(f"{prefix}: name '{name}' already used")
            seen_names.add(name)
        seen_names.add(name)

        for field in ("id_address", "data_start_address", "dlc_address",
                       "trigger_coil_address"):
            val = c.get(field)
            if val is None:
                errors.append(f"{prefix}: '{field}' is required")
                continue
            try:
                iv = int(val)
            except (ValueError, TypeError):
                errors.append(f"{prefix}: '{field}' must be an integer, got {val!r}")
                continue
            if not (0 <= iv <= 65535):
                errors.append(f"{prefix}: '{field}' {iv} out of range (0–65535)")

        if "data_start_address" in c:
            dsa = int(c["data_start_address"])
            if dsa + 3 > 65535:
                errors.append(
                    f"{prefix}: data_start_address {dsa} + 3 exceeds 65535"
                )

        ch = CANSendChannel.from_dict(c)
        regs = sorted(ch.register_addresses())
        for addr in regs:
            if addr in seen_regs:
                errors.append(
                    f"{prefix}: register {addr} overlaps channel "
                    f"'{seen_regs[addr]}'"
                )
            else:
                seen_regs[addr] = name

        if register_map_entries is not None:
            for e in register_map_entries:
                entry_addr = e.get("address") if isinstance(e, dict) else e.address
                entry_fc = (
                    e.get("function_code") if isinstance(e, dict) else e.function_code
                )
                if entry_fc in (3, 4) and entry_addr in regs:
                    errors.append(
                        f"{prefix}: register {entry_addr} already mapped as "
                        f"FC{entry_fc} in register map"
                    )

    return errors
