#!/usr/bin/env python3
# tests/test_auth_manager.py
# Phase 6 - pure-logic tests for core/auth_manager.py.
# Uses pytest conventions.

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.auth_manager import (
    AuthManager,
    InvalidCredentialsError,
    TokenError,
    UserExistsError,
    UserNotFoundError,
    role_at_least,
)


def make_manager(tmp_dir, **kwargs) -> AuthManager:
    """Create an AuthManager backed by a fresh temp directory."""
    return AuthManager(
        users_path=os.path.join(tmp_dir, "users.json"),
        secret_path=os.path.join(tmp_dir, "jwt_secret.key"),
        min_password_length=8,
        **kwargs,
    )


# -------------------------------------------------------
# Section 1: Role hierarchy
# -------------------------------------------------------
def test_role_hierarchy():
    assert role_at_least("admin", "viewer"), "admin satisfies viewer"
    assert role_at_least("admin", "operator"), "admin satisfies operator"
    assert role_at_least("admin", "admin"), "admin satisfies admin"
    assert role_at_least("operator", "viewer"), "operator satisfies viewer"
    assert role_at_least("operator", "operator"), "operator satisfies operator"
    assert not role_at_least("operator", "admin"), "operator does NOT satisfy admin"
    assert role_at_least("viewer", "viewer"), "viewer satisfies viewer"
    assert not role_at_least("viewer", "operator"), "viewer does NOT satisfy operator"
    assert not role_at_least("viewer", "admin"), "viewer does NOT satisfy admin"
    assert not role_at_least("ghost", "viewer"), "unknown role does NOT satisfy viewer"


# -------------------------------------------------------
# Section 2: First-boot bootstrap
# -------------------------------------------------------
def test_first_boot_creates_admin(tmp_path):
    mgr = make_manager(tmp_path)
    users = mgr.list_users()
    assert len(users) == 1, f"Expected 1 user, got {len(users)}"
    assert users[0]["username"] == "admin"
    assert users[0]["role"] == "admin"
    assert users[0]["must_change_password"] is True


def test_users_json_persists(tmp_path):
    """A second AuthManager pointing at the same file doesn't re-bootstrap."""
    mgr1 = make_manager(tmp_path)
    mgr1.create_user("bob", "securepassword1", "viewer")

    mgr2 = make_manager(tmp_path)
    names = {u["username"] for u in mgr2.list_users()}
    assert "admin" in names
    assert "bob" in names


def test_secret_key_persists(tmp_path):
    """Two managers sharing the same secret file can verify each other's tokens."""
    mgr1 = make_manager(tmp_path)
    mgr1.create_user("carol", "verylongpassword", "operator")

    user = mgr1.authenticate("carol", "verylongpassword")
    token = mgr1.issue_access_token(user)

    mgr2 = make_manager(tmp_path)
    payload = mgr2.verify_token(token, expected_type="access")
    assert payload["sub"] == "carol"


# -------------------------------------------------------
# Section 3: User CRUD
# -------------------------------------------------------
def test_create_and_list(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("alice", "mypassword1", "operator")
    users = {u["username"]: u for u in mgr.list_users()}
    assert "alice" in users
    assert users["alice"]["role"] == "operator"
    assert users["alice"]["must_change_password"] is False


def test_duplicate_user_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("dup", "password123", "viewer")
    with pytest.raises(UserExistsError):
        mgr.create_user("dup", "anotherpass", "viewer")


def test_invalid_role_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    with pytest.raises(ValueError):
        mgr.create_user("x", "longpassword1", "superadmin")


def test_short_password_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    with pytest.raises(ValueError):
        mgr.create_user("short", "abc", "viewer")


def test_delete_user(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("temp", "longpassword!", "viewer")
    mgr.delete_user("temp")
    names = {u["username"] for u in mgr.list_users()}
    assert "temp" not in names


def test_delete_nonexistent_user(tmp_path):
    mgr = make_manager(tmp_path)
    with pytest.raises(UserNotFoundError):
        mgr.delete_user("nobody")


# -------------------------------------------------------
# Section 4: Authentication
# -------------------------------------------------------
def test_authenticate_success(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("dave", "goodpassword1", "operator")
    user = mgr.authenticate("dave", "goodpassword1")
    assert user["username"] == "dave"
    assert user["role"] == "operator"


def test_authenticate_wrong_password(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("eve", "correctpassword", "viewer")
    with pytest.raises(InvalidCredentialsError):
        mgr.authenticate("eve", "wrongpassword")


def test_authenticate_unknown_user(tmp_path):
    mgr = make_manager(tmp_path)
    with pytest.raises(InvalidCredentialsError):
        mgr.authenticate("nobody", "anything")


# -------------------------------------------------------
# Section 5: JWT issue / verify
# -------------------------------------------------------
def test_access_token_roundtrip(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("frank", "longenoughpassword", "admin")
    user = mgr.authenticate("frank", "longenoughpassword")
    token = mgr.issue_access_token(user)
    payload = mgr.verify_token(token, expected_type="access")
    assert payload["sub"] == "frank"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("grace", "longenoughpassword", "viewer")
    user = mgr.authenticate("grace", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    payload = mgr.verify_token(refresh, expected_type="refresh")
    assert payload["sub"] == "grace"
    assert payload["type"] == "refresh"


def test_wrong_token_type_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("heidi", "longenoughpassword", "viewer")
    user = mgr.authenticate("heidi", "longenoughpassword")
    access = mgr.issue_access_token(user)
    with pytest.raises(TokenError):
        mgr.verify_token(access, "refresh")


def test_expired_token_rejected(tmp_path):
    mgr = make_manager(tmp_path, access_token_minutes=0)
    mgr.create_user("ivan", "longenoughpassword", "viewer")
    user = mgr.authenticate("ivan", "longenoughpassword")
    token = mgr.issue_access_token(user)
    time.sleep(1)
    with pytest.raises(TokenError):
        mgr.verify_token(token, "access")


def test_tampered_token_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("judy", "longenoughpassword", "viewer")
    user = mgr.authenticate("judy", "longenoughpassword")
    token = mgr.issue_access_token(user)
    tampered = token[:-4] + "XXXX"
    with pytest.raises(TokenError):
        mgr.verify_token(tampered, "access")


def test_refresh_access_token(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("kate", "longenoughpassword", "operator")
    user = mgr.authenticate("kate", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    new_access = mgr.refresh_access_token(refresh)
    payload = mgr.verify_token(new_access, expected_type="access")
    assert payload["sub"] == "kate"


def test_revoked_refresh_token_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("leo", "longenoughpassword", "viewer")
    user = mgr.authenticate("leo", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    mgr.revoke_refresh_token(refresh)
    with pytest.raises(TokenError):
        mgr.verify_token(refresh, "refresh")


# -------------------------------------------------------
# Section 6: change_password
# -------------------------------------------------------
def test_change_password(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("mike", "oldpassword1", "viewer")
    mgr.change_password("mike", "oldpassword1", "newpassword1")
    with pytest.raises(InvalidCredentialsError):
        mgr.authenticate("mike", "oldpassword1")
    user = mgr.authenticate("mike", "newpassword1")
    assert user["username"] == "mike"


def test_change_password_wrong_old(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("nina", "correctpassword", "viewer")
    with pytest.raises(InvalidCredentialsError):
        mgr.change_password("nina", "wrongpassword", "newpassword!")


# -------------------------------------------------------
# Section 7: deleted user's tokens are rejected
# -------------------------------------------------------
def test_deleted_user_token_rejected(tmp_path):
    mgr = make_manager(tmp_path)
    mgr.create_user("omar", "longenoughpassword", "viewer")
    user = mgr.authenticate("omar", "longenoughpassword")
    token = mgr.issue_access_token(user)
    mgr.delete_user("omar")
    with pytest.raises(TokenError):
        mgr.verify_token(token, "access")
