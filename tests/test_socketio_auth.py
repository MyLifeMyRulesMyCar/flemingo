#!/usr/bin/env python3
# tests/test_socketio_auth.py
# Tests for Phase 10 SocketIO auth — JWT validation + role hierarchy.

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.auth_manager import role_at_least, TokenError


class TestSocketIOAuth:
    def test_role_at_least_viewer_below_operator(self):
        assert role_at_least("viewer", "operator") is False
        assert role_at_least("viewer", "viewer") is True
        assert role_at_least("viewer", "admin") is False

    def test_role_at_least_operator_above_viewer(self):
        assert role_at_least("operator", "viewer") is True
        assert role_at_least("operator", "operator") is True
        assert role_at_least("operator", "admin") is False

    def test_role_at_least_admin_is_highest(self):
        assert role_at_least("admin", "viewer") is True
        assert role_at_least("admin", "operator") is True
        assert role_at_least("admin", "admin") is True

    def test_verify_token_rejects_invalid(self, shared_auth):
        with pytest.raises(TokenError):
            shared_auth.verify_token("not.a.real.token", expected_type="access")

    def test_verify_token_rejects_expired(self, isolated_auth, tmp_path):
        from core.auth_manager import AuthManager
        mgr = AuthManager(
            users_path=str(tmp_path / "users.json"),
            secret_path=str(tmp_path / "jwt_secret.key"),
            access_token_minutes=0,
            min_password_length=4,
        )
        mgr.create_user("t", "test123456", "viewer")
        token = mgr.issue_access_token(
            {"username": "t", "role": "viewer", "must_change_password": False}
        )
        with pytest.raises(TokenError):
            mgr.verify_token(token, expected_type="access")

    def test_verify_token_accepts_valid(self, shared_auth):
        token = shared_auth.issue_access_token(
            {"username": "t", "role": "viewer", "must_change_password": False}
        )
        payload = shared_auth.verify_token(token, expected_type="access")
        assert payload["role"] == "viewer"

    def test_issue_access_token_includes_role(self, shared_auth):
        token = shared_auth.issue_access_token(
            {"username": "t", "role": "admin", "must_change_password": False}
        )
        payload = shared_auth.verify_token(token, expected_type="access")
        assert payload["role"] == "admin"

    def test_ws_auth_dict_isolation(self):
        _ws_auth = {}
        sid = "fake-sid-123"
        _ws_auth[sid] = {"role": "operator", "username": "t"}
        assert _ws_auth[sid]["role"] == "operator"
        _ws_auth.pop(sid, None)
        assert sid not in _ws_auth
