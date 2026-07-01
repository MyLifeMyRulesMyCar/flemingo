#!/usr/bin/env python3
"""Regression tests for the auth login route."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from api.auth_routes import auth_api
from core import auth_manager as auth_manager_module


def test_login_route_uses_initialized_auth_manager(tmp_path):
    auth_manager_module.DEFAULT_USERS_PATH = str(tmp_path / "users.json")
    auth_manager_module.DEFAULT_SECRET_PATH = str(tmp_path / "jwt_secret.key")

    auth_manager_module.init_auth_manager(min_password_length=8)
    mgr = auth_manager_module.auth_manager
    mgr.create_user("tester", "longenoughpassword", "viewer")

    app = Flask(__name__)
    app.register_blueprint(auth_api)

    with app.test_client() as client:
        resp = client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "longenoughpassword"},
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["access_token"]
    assert payload["user"]["username"] == "tester"
