#!/usr/bin/env python3
# tests/test_modbus_routes.py
# Route-level tests for /api/modbus/* — mocked ModbusManager, shared auth.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from flask import Flask

from api.modbus_routes import modbus_api, set_modbus_manager


@pytest.fixture(autouse=True)
def _patch_ports(monkeypatch):
    import api.modbus_routes

    monkeypatch.setattr(
        api.modbus_routes, "MODBUS_PORTS", {"ttyUSB0": {}, "ttyUSB1": {}}
    )


class TestModbusRoutes:
    @pytest.fixture
    def client(self, auth_tokens):
        mock_modbus = MagicMock()
        mock_modbus.get_all_devices.return_value = []
        mock_modbus.add_device.return_value = "dev1"
        mock_modbus.get_logs.return_value = []
        mock_modbus.read_holding_register.return_value = 42
        mock_modbus.write_holding_register.return_value = True
        set_modbus_manager(mock_modbus)

        app = Flask(__name__)
        app.register_blueprint(modbus_api)
        c = app.test_client()
        c.mock_modbus = mock_modbus
        c.tokens = auth_tokens
        return c

    def test_get_ports_requires_auth(self, client):
        resp = client.get("/api/modbus/ports")
        assert resp.status_code == 401

    def test_get_devices_requires_viewer(self, client):
        resp = client.get(
            "/api/modbus/devices",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200

    def test_create_device_slave_id_zero_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices",
            json={"name": "Test", "port": "ttyUSB0", "slave_id": 0},
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400
        assert "slave_id" in resp.get_json()["error"].lower()

    def test_create_device_bad_baudrate_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices",
            json={"name": "Test", "port": "ttyUSB0", "slave_id": 1, "baudrate": 12345},
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400

    def test_create_device_bad_parity_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices",
            json={"name": "Test", "port": "ttyUSB0", "slave_id": 1, "parity": "X"},
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400

    def test_create_device_requires_admin(self, client):
        resp = client.post(
            "/api/modbus/devices",
            json={"name": "Test", "port": "ttyUSB0", "slave_id": 1},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 403

    def test_read_bad_function_code_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices/dev1/read",
            json={"address": 0, "function_code": 99},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_write_fc6_range_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices/dev1/write",
            json={"address": 0, "value": 65536, "function_code": 6},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_write_fc5_non_binary_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices/dev1/write",
            json={"address": 0, "value": 2, "function_code": 5},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_scan_start_greater_than_end_rejected(self, client):
        resp = client.post(
            "/api/modbus/scan",
            json={"port": "ttyUSB0", "start_id": 10, "end_id": 1},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_logs_count_unbounded_rejected(self, client):
        resp = client.get(
            "/api/modbus/logs?count=99999",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 400

    def test_malformed_json_body_rejected(self, client):
        resp = client.post(
            "/api/modbus/devices",
            data="{broken",
            content_type="application/json",
            headers={"Authorization": f"Bearer {client.tokens['admin']}"},
        )
        assert resp.status_code == 400
