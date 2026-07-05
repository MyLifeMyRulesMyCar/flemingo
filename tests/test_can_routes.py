#!/usr/bin/env python3
# tests/test_can_routes.py
# Route-level tests for /api/can/* — mocked CANManager, shared auth.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from flask import Flask

from api.can_routes import can_api, set_can_manager


class TestCANRoutes:
    @pytest.fixture
    def client(self, auth_tokens):
        mock_can = MagicMock()
        mock_can.get_status.return_value = {
            "connected": False,
            "bitrate": 125000,
            "rx_total": 0,
            "tx_total": 0,
            "errors": 0,
            "circuit_breaker": {"state": "closed"},
        }
        mock_can.get_recent_messages.return_value = []
        mock_can.send_message.return_value = True
        set_can_manager(mock_can)

        app = Flask(__name__)
        app.register_blueprint(can_api)
        c = app.test_client()
        c.mock_can = mock_can
        c.tokens = auth_tokens
        return c

    def test_get_status_requires_viewer(self, client):
        resp = client.get(
            "/api/can/status",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200
        assert "connected" in resp.get_json()

    def test_get_status_rejects_no_token(self, client):
        resp = client.get("/api/can/status")
        assert resp.status_code == 401

    def test_connect_requires_operator(self, client):
        resp = client.post(
            "/api/can/connect",
            json={"bitrate": 500000},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 200
        assert client.mock_can.bitrate == 500000
        client.mock_can.connect.assert_called_once()

    def test_connect_rejects_viewer(self, client):
        resp = client.post(
            "/api/can/connect",
            json={"bitrate": 500000},
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 403

    def test_connect_rejects_invalid_bitrate(self, client):
        resp = client.post(
            "/api/can/connect",
            json={"bitrate": 999},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_send_requires_operator(self, client):
        resp = client.post(
            "/api/can/send",
            json={"can_id": 291, "data": [0xDE, 0xAD]},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        client.mock_can.send_message.assert_called_once()

    def test_send_rejects_oversized_can_id(self, client):
        resp = client.post(
            "/api/can/send",
            json={"can_id": 0x800, "data": [1]},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_send_rejects_oversized_payload(self, client):
        resp = client.post(
            "/api/can/send",
            json={"can_id": 100, "data": [1] * 9},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400

    def test_messages_count_unbounded_rejected(self, client):
        resp = client.get(
            "/api/can/messages?count=1000000",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 400

    def test_messages_default_count(self, client):
        resp = client.get(
            "/api/can/messages",
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 200
