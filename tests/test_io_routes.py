#!/usr/bin/env python3
# tests/test_io_routes.py
# Route-level tests for /api/io/* — mocked IOManager, shared auth.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from flask import Flask

from api.io_routes import io_api, set_io_manager


class TestIORoutes:
    @pytest.fixture
    def client(self, auth_tokens):
        mock_io = MagicMock()
        mock_io.get_status.return_value = {"simulation": True}
        set_io_manager(mock_io)
        app = Flask(__name__)
        app.register_blueprint(io_api)
        c = app.test_client()
        c.mock_io = mock_io
        c.tokens = auth_tokens
        return c

    def test_get_io_requires_viewer(self, client):
        resp = client.get(
            "/api/io", headers={"Authorization": f"Bearer {client.tokens['viewer']}"}
        )
        assert resp.status_code == 200
        assert "di" in resp.get_json()

    def test_get_io_rejects_no_token(self, client):
        resp = client.get("/api/io")
        assert resp.status_code == 401

    def test_set_do_requires_operator(self, client):
        resp = client.post(
            "/api/io/do/0",
            json={"state": True},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["channel"] == 0
        client.mock_io.write_output.assert_called_once_with(0, 1)

    def test_set_do_rejects_viewer(self, client):
        resp = client.post(
            "/api/io/do/0",
            json={"state": True},
            headers={"Authorization": f"Bearer {client.tokens['viewer']}"},
        )
        assert resp.status_code == 403

    def test_set_do_invalid_channel(self, client):
        resp = client.post(
            "/api/io/do/5",
            json={"state": True},
            headers={"Authorization": f"Bearer {client.tokens['operator']}"},
        )
        assert resp.status_code == 400
        assert "Invalid channel" in resp.get_json()["error"]
