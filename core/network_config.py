#!/usr/bin/env python3
# core/network_config.py
# Ethernet static IP/subnet/gateway configuration with a lockout-safety
# auto-revert mechanism.
#
# Confirmed stack (2026-07-24): NetworkManager via nmcli. /etc/netplan/
# has 01-network-manager-all.yaml, i.e. netplan is present but delegates
# to NetworkManager as the renderer — nmcli is the actual point of
# control, not netplan YAML directly.
#
# Confirmed port mapping: eth0 = management (dashboard/REST API), eth1 =
# dedicated Modbus TCP port. DEFAULT_IFACE is eth1. apply_config and
# revert_to_dhcp hard-refuse to run against eth0 via
# _assert_not_management_iface — a default parameter is easy to
# accidentally override in a refactor; this is a hard stop.

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_PATH = os.path.join(_REPO_ROOT, "config", "network_backup.json")

DEFAULT_IFACE = "eth1"
MANAGEMENT_IFACE = "eth0"
NMCLI_TIMEOUT = 10


class NetworkConfigError(Exception):
    """Raised for any failure applying or reading network config."""


@dataclass
class NetworkConfig:
    ip: str
    prefix_len: int
    gateway: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkConfig":
        return cls(ip=d["ip"], prefix_len=int(d["prefix_len"]), gateway=d["gateway"])


def _assert_not_management_iface(iface: str) -> None:
    """Hard stop — never touch the management interface."""
    if iface == MANAGEMENT_IFACE:
        raise NetworkConfigError(
            f"Refusing to modify {iface} — it's the management interface, "
            f"not the Modbus TCP port this feature configures."
        )


def _run(args: list, timeout: int = NMCLI_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a command as an explicit argument list — never a shell string.
    Every argument must already be validated before reaching here."""
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        logger.error(
            f"Command failed ({args[0]}): {' '.join(args)} -> {result.stderr.strip()}"
        )
    return result


# ----------------------------------------------------------------------
# Reading current config — stack-agnostic (kernel state via netlink)
# ----------------------------------------------------------------------
def get_current_config(iface: str = DEFAULT_IFACE) -> NetworkConfig:
    addr_result = _run(["ip", "-j", "addr", "show", iface])
    if addr_result.returncode != 0:
        raise NetworkConfigError(
            f"Could not read interface {iface}: {addr_result.stderr.strip()}"
        )

    try:
        addr_data = json.loads(addr_result.stdout)
    except json.JSONDecodeError as e:
        raise NetworkConfigError(f"Unexpected `ip addr` output: {e}")

    if not addr_data:
        raise NetworkConfigError(f"Interface {iface} not found")

    ipv4_info = next(
        (a for a in addr_data[0].get("addr_info", []) if a.get("family") == "inet"),
        None,
    )
    if ipv4_info is None:
        raise NetworkConfigError(f"No IPv4 address currently on {iface}")

    gateway = "0.0.0.0"
    route_result = _run(["ip", "-j", "route", "show", "default"])
    if route_result.returncode == 0:
        try:
            routes = json.loads(route_result.stdout)
            default_route = next((r for r in routes if r.get("dev") == iface), None)
            if default_route:
                gateway = default_route.get("gateway", "0.0.0.0")
        except json.JSONDecodeError:
            pass

    return NetworkConfig(
        ip=ipv4_info["local"],
        prefix_len=int(ipv4_info["prefixlen"]),
        gateway=gateway,
    )


# ----------------------------------------------------------------------
# Applying config — nmcli-specific (the confirmed stack)
# ----------------------------------------------------------------------
def _get_active_connection_name(iface: str = DEFAULT_IFACE) -> str:
    """The nmcli connection profile name active on this device."""
    result = _run(
        ["sudo", "nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", iface]
    )
    if result.returncode != 0:
        raise NetworkConfigError(
            f"Could not find active connection for {iface}: {result.stderr.strip()}"
        )
    line = result.stdout.strip()
    if ":" not in line:
        raise NetworkConfigError(f"Unexpected nmcli output for {iface}: {line!r}")
    name = line.split(":", 1)[1].strip()
    if not name or name == "--":
        raise NetworkConfigError(f"No active NetworkManager connection on {iface}")
    return name


def _get_or_create_connection(iface: str) -> str:
    """Get the active connection name, or auto-create one if eth1 has never
    been configured before (first-cable-plugged-in use case)."""
    try:
        return _get_active_connection_name(iface)
    except NetworkConfigError:
        logger.info(f"No connection found on {iface} — creating one")
        conn_name = f"flemingo-{iface}"
        result = _run(
            [
                "sudo",
                "nmcli",
                "con",
                "add",
                "type",
                "ethernet",
                "con-name",
                conn_name,
                "ifname",
                iface,
            ]
        )
        if result.returncode != 0:
            raise NetworkConfigError(
                f"Cannot create connection for {iface}: {result.stderr.strip()}"
            )
        return conn_name


def apply_config(candidate: NetworkConfig, iface: str = DEFAULT_IFACE) -> None:
    """Apply a candidate static IP/subnet/gateway via nmcli."""
    _assert_not_management_iface(iface)

    try:
        with open(f"/sys/class/net/{iface}/carrier") as f:
            if f.read().strip() != "1":
                raise NetworkConfigError(
                    f"No cable detected on {iface} — "
                    f"plug in an Ethernet cable first"
                )
    except FileNotFoundError:
        raise NetworkConfigError(f"Interface {iface} does not exist")

    conn_name = _get_or_create_connection(iface)

    mod_result = _run(
        [
            "sudo",
            "nmcli",
            "con",
            "mod",
            conn_name,
            "ipv4.addresses",
            f"{candidate.ip}/{candidate.prefix_len}",
            "ipv4.gateway",
            candidate.gateway,
            "ipv4.method",
            "manual",
        ]
    )
    if mod_result.returncode != 0:
        raise NetworkConfigError(f"nmcli con mod failed: {mod_result.stderr.strip()}")

    up_result = _run(["sudo", "nmcli", "con", "up", conn_name], timeout=20)
    if up_result.returncode != 0:
        raise NetworkConfigError(f"nmcli con up failed: {up_result.stderr.strip()}")

    logger.info(
        f"Applied network config on {iface} via '{conn_name}': "
        f"{candidate.ip}/{candidate.prefix_len} gw {candidate.gateway}"
    )


def revert_to_dhcp(iface: str = DEFAULT_IFACE) -> None:
    """Last-resort fallback — switch back to DHCP. Does NOT run con up
    because that blocks indefinitely when no DHCP server is on the subnet."""
    _assert_not_management_iface(iface)
    conn_name = _get_or_create_connection(iface)
    _run(["sudo", "nmcli", "con", "mod", conn_name, "ipv4.method", "auto"])
    _run(["sudo", "nmcli", "con", "mod", conn_name, "ipv4.addresses", ""])
    logger.warning(f"Reverted {iface} to DHCP (no valid backup config found)")


# ----------------------------------------------------------------------
# Backup persistence — atomic write (same pattern as modbus_tcp_register_map)
# ----------------------------------------------------------------------
def save_backup(config: NetworkConfig, path: str = BACKUP_PATH) -> None:
    tmp_path = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp_path, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def load_backup(path: str = BACKUP_PATH) -> Optional[NetworkConfig]:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return NetworkConfig.from_dict(json.load(f))
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Could not parse network backup at {path}: {e}")
        return None


# ----------------------------------------------------------------------
# The safety mechanism — apply now, auto-revert on timer unless confirmed
# ----------------------------------------------------------------------
class RevertScheduler:
    def __init__(self, iface: str = DEFAULT_IFACE):
        self.iface = iface
        self._timer: Optional[threading.Timer] = None
        self._pending_candidate: Optional[NetworkConfig] = None
        self._revert_at: Optional[float] = None
        self._lock = threading.Lock()

    def apply_with_revert(
        self, candidate: NetworkConfig, delay_seconds: int = 60
    ) -> float:
        with self._lock:
            try:
                current = get_current_config(self.iface)
            except NetworkConfigError as e:
                logger.warning(
                    f"No current IPv4 config on {self.iface} to snapshot: {e}"
                )
                # Remove any stale backup so a revert falls back to DHCP.
                try:
                    os.remove(BACKUP_PATH)
                except FileNotFoundError:
                    pass
            else:
                save_backup(current)

            apply_config(candidate, self.iface)

            if self._timer is not None:
                self._timer.cancel()

            self._pending_candidate = candidate
            self._revert_at = time.time() + delay_seconds
            self._timer = threading.Timer(delay_seconds, self._do_revert)
            self._timer.daemon = True
            self._timer.start()
            return self._revert_at

    def _do_revert(self):
        with self._lock:
            backup = load_backup()
            if backup is None:
                logger.error(
                    "Network revert triggered but no backup found — "
                    "falling back to DHCP"
                )
                try:
                    revert_to_dhcp(self.iface)
                except Exception as e2:
                    logger.error(f"DHCP fallback also failed: {e2}")
            else:
                logger.warning(
                    f"Network config not confirmed — reverting to {backup.ip}"
                )
                try:
                    apply_config(backup, self.iface)
                except NetworkConfigError as e:
                    logger.error(f"Revert itself failed: {e} — falling back to DHCP")
                    try:
                        revert_to_dhcp(self.iface)
                    except Exception as e2:
                        logger.error(f"DHCP fallback also failed: {e2}")
            self._pending_candidate = None
            self._revert_at = None
            self._timer = None

    def confirm(self) -> bool:
        with self._lock:
            if self._timer is None:
                return False
            self._timer.cancel()
            self._timer = None
            self._pending_candidate = None
            self._revert_at = None
            return True

    def status(self) -> dict:
        with self._lock:
            return {
                "pending": self._timer is not None,
                "revert_at": self._revert_at,
                "candidate": (
                    self._pending_candidate.to_dict()
                    if self._pending_candidate
                    else None
                ),
            }
