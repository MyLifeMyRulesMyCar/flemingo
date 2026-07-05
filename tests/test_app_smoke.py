#!/usr/bin/env python3
"""Smoke test for the health endpoint using Flask's test client."""

import pytest
from flask import Flask
from api.health_routes import health_api


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(health_api)
    return app.test_client()


def test_health_endpoint_returns_200(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "uptime" in data
    assert isinstance(data["uptime"], int)
