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


def test_login_rate_limit_blocks_after_n_failures(tmp_path):
    """5 bad logins → 401 each, 6th → 429."""
    auth_manager_module.DEFAULT_USERS_PATH = str(tmp_path / "users.json")
    auth_manager_module.DEFAULT_SECRET_PATH = str(tmp_path / "jwt_secret.key")
    auth_manager_module.init_auth_manager(min_password_length=8)
    auth_manager_module.auth_manager.create_user("u", "goodpassword", "viewer")

    import core.rate_limiter
    import api.auth_routes

    api.auth_routes._rate_limiter = core.rate_limiter.LoginRateLimiter(
        max_attempts=5, window_minutes=15
    )

    app = Flask(__name__)
    app.register_blueprint(api.auth_routes.auth_api)

    with app.test_client() as client:
        for i in range(5):
            resp = client.post(
                "/api/auth/login",
                json={"username": "u", "password": "wrong"},
            )
            assert resp.status_code == 401, f"attempt {i + 1} should be 401"

        resp = client.post(
            "/api/auth/login",
            json={"username": "u", "password": "wrong"},
        )
        assert resp.status_code == 429, "6th attempt should be 429"


def test_login_success_clears_rate_counter(tmp_path):
    """A successful login resets the counter."""
    auth_manager_module.DEFAULT_USERS_PATH = str(tmp_path / "users.json")
    auth_manager_module.DEFAULT_SECRET_PATH = str(tmp_path / "jwt_secret.key")
    auth_manager_module.init_auth_manager(min_password_length=8)
    auth_manager_module.auth_manager.create_user("u", "goodpassword", "viewer")

    import core.rate_limiter

    _rate_limiter = core.rate_limiter.LoginRateLimiter(
        max_attempts=5, window_minutes=15
    )
    import api.auth_routes

    api.auth_routes._rate_limiter = _rate_limiter

    app = Flask(__name__)
    app.register_blueprint(api.auth_routes.auth_api)

    with app.test_client() as client:
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "u", "password": "wrong"})
        client.post(
            "/api/auth/login", json={"username": "u", "password": "goodpassword"}
        )
        resp = client.post(
            "/api/auth/login", json={"username": "u", "password": "wrong"}
        )
        assert resp.status_code == 401, "counter should reset after success"


def test_login_rate_limit_per_key_isolation(tmp_path):
    """Rate-locking one user does not affect another."""
    auth_manager_module.DEFAULT_USERS_PATH = str(tmp_path / "users.json")
    auth_manager_module.DEFAULT_SECRET_PATH = str(tmp_path / "jwt_secret.key")
    auth_manager_module.init_auth_manager(min_password_length=8)
    auth_manager_module.auth_manager.create_user("u1", "password1", "viewer")
    auth_manager_module.auth_manager.create_user("u2", "password2", "viewer")

    import core.rate_limiter

    _rate_limiter = core.rate_limiter.LoginRateLimiter(
        max_attempts=2, window_minutes=15
    )
    import api.auth_routes

    api.auth_routes._rate_limiter = _rate_limiter

    app = Flask(__name__)
    app.register_blueprint(api.auth_routes.auth_api)

    with app.test_client() as client:
        # lock out u1
        for _ in range(2):
            client.post("/api/auth/login", json={"username": "u1", "password": "wrong"})
        assert (
            client.post(
                "/api/auth/login", json={"username": "u1", "password": "wrong"}
            ).status_code
            == 429
        )

        # u2 is unaffected
        resp = client.post(
            "/api/auth/login", json={"username": "u2", "password": "wrong"}
        )
        assert resp.status_code == 401, "u2 should not be rate-limited by u1"
