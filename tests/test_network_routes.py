#!/usr/bin/env python3
# tests/test_network_routes.py
# Role-check tests for /api/network/* routes.

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

import pytest
from flask import Flask

from api.network_routes import network_api, set_network_scheduler
from core.network_config import NetworkConfig, NetworkConfigError, RevertScheduler


class TestNetworkRoutes:
    @pytest.fixture
    def client(self, auth_tokens, monkeypatch):
        mock_sched = MagicMock()
        mock_sched.status.return_value = {
            "pending": False,
            "revert_at": None,
            "candidate": None,
        }
        set_network_scheduler(mock_sched)

        class FakeCfg:
            def to_dict(self):
                return {
                    "ip": "192.168.1.150",
                    "prefix_len": 24,
                    "gateway": "192.168.1.1",
                }

        monkeypatch.setattr(
            "api.network_routes.get_current_config",
            lambda iface=None: FakeCfg(),
        )

        app = Flask(__name__)
        app.register_blueprint(network_api)
        c = app.test_client()
        c.tokens = auth_tokens
        c.mock_sched = mock_sched
        return c

    def test_get_config_requires_viewer(self, client):
        resp = client.get(
            "/api/network/config",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ip"] == "192.168.1.150"

    def test_apply_requires_admin(self, client):
        resp = client.post(
            "/api/network/apply",
            json={
                "ip": "192.168.2.100",
                "prefix_len": 24,
                "gateway": "192.168.2.1",
            },
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 403

    def test_apply_rejects_localhost(self, client):
        resp = client.post(
            "/api/network/apply",
            json={
                "ip": "127.0.0.1",
                "prefix_len": 24,
                "gateway": "127.0.0.254",
            },
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400

    def test_apply_rejects_gateway_outside_subnet(self, client):
        resp = client.post(
            "/api/network/apply",
            json={
                "ip": "192.168.2.100",
                "prefix_len": 24,
                "gateway": "10.0.0.1",
            },
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400

    def test_status_requires_viewer(self, client):
        resp = client.get(
            "/api/network/status",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200

    def test_apply_without_confirm_reverts_config(self, auth_tokens, monkeypatch):
        """Apply a candidate and don't confirm — the scheduler must revert."""
        original = NetworkConfig(ip="192.168.1.150", prefix_len=24, gateway="192.168.1.1")
        candidate = NetworkConfig(ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1")

        applied = []

        def fake_get_current_config(iface=None):
            return original

        def fake_apply_config(cfg, iface=None):
            applied.append(cfg)

        # Use a short timer so the test doesn't wait the production 60s.
        _orig_apply = RevertScheduler.apply_with_revert

        def fast_apply_with_revert(self, candidate, delay_seconds=60):
            return _orig_apply(self, candidate, delay_seconds=0.1)

        monkeypatch.setattr(
            RevertScheduler, "apply_with_revert", fast_apply_with_revert
        )
        monkeypatch.setattr(
            "core.network_config.get_current_config", fake_get_current_config
        )
        monkeypatch.setattr("core.network_config.apply_config", fake_apply_config)
        monkeypatch.setattr(
            "core.network_config.save_backup", lambda cfg, path=None: None
        )
        monkeypatch.setattr(
            "core.network_config.load_backup", lambda path=None: original
        )

        real_sched = RevertScheduler(iface="eth1")
        set_network_scheduler(real_sched)

        app = Flask(__name__)
        app.register_blueprint(network_api)
        c = app.test_client()

        resp = c.post(
            "/api/network/apply",
            json=candidate.to_dict(),
            headers={"Authorization": f"Bearer {auth_tokens['admin']}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["pending"] is True

        # Wait slightly longer than the test delay for the timer to fire.
        time.sleep(0.3)

        status = (
            c.get(
                "/api/network/status",
                headers={"Authorization": f"Bearer {auth_tokens['viewer']}"},
            )
            .get_json()
        )
        assert status["pending"] is False

        # apply_config called twice: once for candidate, once for revert.
        assert len(applied) == 2
        assert applied[0].ip == candidate.ip
        assert applied[1].ip == original.ip

    def test_apply_without_prior_ip_reverts_to_dhcp(self, auth_tokens, monkeypatch):
        """If eth1 has no current IP, apply should still work and revert to DHCP."""
        candidate = NetworkConfig(ip="192.168.2.100", prefix_len=24, gateway="192.168.2.1")

        applied = []

        def fake_get_current_config(iface=None):
            raise NetworkConfigError("No IPv4 address currently on eth1")

        def fake_apply_config(cfg, iface=None):
            applied.append(cfg)

        _orig_apply = RevertScheduler.apply_with_revert

        def fast_apply_with_revert(self, candidate, delay_seconds=60):
            return _orig_apply(self, candidate, delay_seconds=0.1)

        monkeypatch.setattr(RevertScheduler, "apply_with_revert", fast_apply_with_revert)
        monkeypatch.setattr(
            "core.network_config.get_current_config", fake_get_current_config
        )
        monkeypatch.setattr("core.network_config.apply_config", fake_apply_config)
        monkeypatch.setattr(
            "core.network_config.save_backup", lambda cfg, path=None: None
        )
        monkeypatch.setattr(
            "core.network_config.load_backup", lambda path=None: None
        )
        monkeypatch.setattr("core.network_config.revert_to_dhcp", lambda iface=None: None)
        monkeypatch.setattr("core.network_config.os.remove", lambda path: None)

        real_sched = RevertScheduler(iface="eth1")
        set_network_scheduler(real_sched)

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(network_api)
        c = app.test_client()

        resp = c.post(
            "/api/network/apply",
            json=candidate.to_dict(),
            headers={"Authorization": f"Bearer {auth_tokens['admin']}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["pending"] is True

        time.sleep(0.3)

        status = (
            c.get(
                "/api/network/status",
                headers={"Authorization": f"Bearer {auth_tokens['viewer']}"},
            )
            .get_json()
        )
        assert status["pending"] is False

        # Candidate applied once; no prior config existed, so revert goes to DHCP.
        assert len(applied) == 1
        assert applied[0].ip == candidate.ip
