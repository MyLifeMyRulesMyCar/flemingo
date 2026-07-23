#!/usr/bin/env python3
# tests/test_modbus_tcp_routes.py
# Role-check tests for /api/modbus-tcp/* routes.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from flask import Flask

from api.modbus_tcp_routes import modbus_tcp_api, set_modbus_tcp_server


class TestModbusTCPRoutes:
    @pytest.fixture
    def client(self, auth_tokens):
        mock_srv = MagicMock()
        mock_srv.get_status.return_value = {
            "running": False,
            "host": "0.0.0.0",
            "port": 5020,
            "entries": 0,
            "client_count": 0,
            "exceptions": 0,
        }
        mock_srv.get_register_map.return_value = []
        mock_srv.running = False
        set_modbus_tcp_server(mock_srv)

        app = Flask(__name__)
        app.register_blueprint(modbus_tcp_api)
        c = app.test_client()
        c.mock_srv = mock_srv
        c.tokens = auth_tokens
        return c

    def test_get_status_requires_viewer(self, client):
        resp = client.get(
            "/api/modbus-tcp/status",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200

    def test_start_requires_operator(self, client):
        resp = client.post(
            "/api/modbus-tcp/start",
            json={"port": 5020},
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 403

        resp = client.post(
            "/api/modbus-tcp/start",
            json={"port": 5020},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 200

    def test_update_config_requires_admin(self, client):
        resp = client.put(
            "/api/modbus-tcp/config",
            json={"port": 5020},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 403

    def test_save_register_map_requires_admin(self, client):
        resp = client.post(
            "/api/modbus-tcp/register-map",
            json={"entries": []},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 403

    def test_validate_register_map_operator_ok(self, client):
        resp = client.post(
            "/api/modbus-tcp/register-map/validate",
            json={"entries": []},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 200

    def test_start_rejects_privileged_port(self, client):
        resp = client.post(
            "/api/modbus-tcp/start",
            json={"port": 81},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_start_rejects_flask_port(self, client):
        resp = client.post(
            "/api/modbus-tcp/start",
            json={"port": 5000},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400
