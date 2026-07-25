#!/usr/bin/env python3
# tests/test_network_config.py
# Unit tests for network backup, revert timer, and nmcli arg building.
# Mock subprocess.run so tests don't need root or a real interface.

import os
import sys
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.network_config import (
    NetworkConfig,
    NetworkConfigError,
    RevertScheduler,
    save_backup,
    load_backup,
    apply_config,
    get_current_config,
    _get_or_create_connection,
)


class TestNetworkBackup:
    def test_save_load_round_trip(self, tmp_path):
        path = str(tmp_path / "backup.json")
        cfg = NetworkConfig(ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1")
        save_backup(cfg, path)
        loaded = load_backup(path)
        assert loaded is not None
        assert loaded.ip == "192.168.2.100"
        assert loaded.prefix_len == 24
        assert loaded.gateway == "192.168.2.1"

    def test_load_missing_returns_none(self, tmp_path):
        assert load_backup(str(tmp_path / "nonexistent.json")) is None


class TestGetCurrentConfig:
    def test_parses_ip_addr_json(self):
        with patch("core.network_config._run") as mock_run:
            mock_run.side_effect = [
                MagicMock(
                    returncode=0,
                    stdout='[{"ifname":"eth1","addr_info":[{"family":"inet","local":"192.168.2.100","prefixlen":24}]}]',
                ),
                MagicMock(returncode=0, stdout="[]"),
            ]
            cfg = get_current_config("eth1")
            assert cfg.ip == "192.168.2.100"
            assert cfg.prefix_len == 24

    def test_no_ip_raises(self):
        with patch("core.network_config._run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='[{"ifname":"eth1","addr_info":[]}]',
            )
            try:
                get_current_config("eth1")
                assert False, "should have raised"
            except NetworkConfigError:
                pass


class TestRevertTimer:
    def test_timer_fires_and_reapplies_backup(self):
        with (
            patch("core.network_config.get_current_config") as mock_get,
            patch("core.network_config.apply_config") as mock_apply,
            patch("core.network_config.save_backup") as mock_save,
            patch("core.network_config.load_backup") as mock_load,
        ):

            original = NetworkConfig(
                ip="192.168.1.150", prefix_len=24, gateway="192.168.1.1"
            )
            candidate = NetworkConfig(
                ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1"
            )
            mock_get.return_value = original
            mock_load.return_value = original

            sched = RevertScheduler(iface="eth1")
            sched.apply_with_revert(candidate, delay_seconds=0.1)

            time.sleep(0.3)

            # Timer should have fired — backup reapplied
            mock_save.assert_called_once()
            # Called TWICE: once for candidate, once for revert
            assert mock_apply.call_count == 2
            assert sched.status()["pending"] is False

    def test_confirm_prevents_revert(self):
        with (
            patch("core.network_config.get_current_config") as mock_get,
            patch("core.network_config.apply_config") as mock_apply,
            patch("core.network_config.save_backup"),
            patch("core.network_config.load_backup") as mock_load,
        ):

            original = NetworkConfig(
                ip="192.168.1.150", prefix_len=24, gateway="192.168.1.1"
            )
            candidate = NetworkConfig(
                ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1"
            )
            mock_get.return_value = original
            mock_load.return_value = original

            sched = RevertScheduler(iface="eth1")
            sched.apply_with_revert(candidate, delay_seconds=0.1)

            assert sched.confirm() is True

            time.sleep(0.3)

            # Called only ONCE — confirm prevented the revert
            assert mock_apply.call_count == 1
            assert sched.status()["pending"] is False

    def test_revert_falls_back_to_dhcp_when_no_current_config(self):
        with (
            patch("core.network_config.get_current_config") as mock_get,
            patch("core.network_config.apply_config") as mock_apply,
            patch("core.network_config.save_backup") as mock_save,
            patch("core.network_config.load_backup") as mock_load,
            patch("core.network_config.revert_to_dhcp") as mock_dhcp,
            patch("core.network_config.os.remove") as mock_remove,
        ):
            mock_get.side_effect = NetworkConfigError(
                "No IPv4 address currently on eth1"
            )
            mock_load.return_value = None

            candidate = NetworkConfig(
                ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1"
            )

            sched = RevertScheduler(iface="eth1")
            sched.apply_with_revert(candidate, delay_seconds=0.1)

            time.sleep(0.3)

            # No prior config to snapshot, so no backup should be saved.
            mock_save.assert_not_called()
            mock_remove.assert_called_once()

            # Candidate applied, then revert falls back to DHCP.
            assert mock_apply.call_count == 1
            mock_dhcp.assert_called_once_with("eth1")
            assert sched.status()["pending"] is False

    def test_revert_management_iface_raises(self):
        try:
            apply_config(
                NetworkConfig("10.0.0.1", 24, "10.0.0.254"),
                iface="eth0",
            )
            assert False, "should have raised"
        except NetworkConfigError:
            pass


class TestAutoCreateConnection:
    def test_creates_connection_if_none_exists(self):
        with patch("core.network_config._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="flemingo-eth1\n")
            name = _get_or_create_connection("eth1")
            assert name == "flemingo-eth1"
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args == [
                "sudo",
                "/usr/local/bin/flemingo-net-apply",
                "ensure-connection",
            ]


class TestApplyConfigArgShape:
    """Lock in the exact command shapes apply_config passes to _run."""

    def test_apply_config_builds_correct_wrapper_args(self, tmp_path):
        with (
            patch("core.network_config._run") as mock_run,
            patch("core.network_config._get_or_create_connection") as mock_conn,
            patch("core.network_config._assert_not_management_iface"),
        ):

            mock_conn.return_value = "flemingo-eth1"
            mock_run.return_value = MagicMock(returncode=0)

            # Create a fake carrier file
            carrier = tmp_path / "carrier"
            carrier.write_text("1")

            with patch("builtins.open") as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = "1"
                apply_config(
                    NetworkConfig("192.168.2.100", 24, "192.168.2.1"),
                    iface="eth1",
                )

            assert mock_run.call_count == 2

            apply_args = mock_run.call_args_list[0][0][0]
            assert apply_args == [
                "sudo",
                "/usr/local/bin/flemingo-net-apply",
                "apply",
                "192.168.2.100/24",
                "192.168.2.1",
            ]

            activate_args = mock_run.call_args_list[1][0][0]
            assert activate_args == [
                "sudo",
                "/usr/local/bin/flemingo-net-apply",
                "activate",
            ]
