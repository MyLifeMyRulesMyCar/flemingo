#!/usr/bin/env python3
# tests/test_auth_manager.py
# Phase 6 - pure-logic tests for core/auth_manager.py.
# Same pattern as test_resilience.py / test_watchdog.py:
#   - no Flask app context needed
#   - no hardware needed
#   - runs with just `python3 tests/test_auth_manager.py`
#
# Uses a temp directory for users.json / jwt_secret.key so it
# never touches config/ and leaves no state behind.

import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.auth_manager import (
    AuthManager,
    InvalidCredentialsError,
    TokenError,
    UserExistsError,
    UserNotFoundError,
    role_at_least,
    ROLE_RANK,
)

# -------------------------------------------------------
# Helpers
# -------------------------------------------------------
def make_manager(tmp_dir, **kwargs) -> AuthManager:
    """Create an AuthManager backed by a fresh temp directory."""
    return AuthManager(
        users_path=os.path.join(tmp_dir, "users.json"),
        secret_path=os.path.join(tmp_dir, "jwt_secret.key"),
        min_password_length=8,   # shorter for test convenience
        **kwargs,
    )


def ok(msg):
    print(f"✅ {msg}")


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def expect_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return None
    except exc_type as e:
        return e
    except Exception as e:
        fail(f"Expected {exc_type.__name__}, got {type(e).__name__}: {e}")


# -------------------------------------------------------
# Section 1: Role hierarchy
# -------------------------------------------------------
def test_role_hierarchy():
    assert role_at_least("admin", "viewer"),   "admin satisfies viewer"
    assert role_at_least("admin", "operator"), "admin satisfies operator"
    assert role_at_least("admin", "admin"),    "admin satisfies admin"
    assert role_at_least("operator", "viewer"),   "operator satisfies viewer"
    assert role_at_least("operator", "operator"), "operator satisfies operator"
    assert not role_at_least("operator", "admin"), "operator does NOT satisfy admin"
    assert role_at_least("viewer", "viewer"),      "viewer satisfies viewer"
    assert not role_at_least("viewer", "operator"), "viewer does NOT satisfy operator"
    assert not role_at_least("viewer", "admin"),    "viewer does NOT satisfy admin"
    assert not role_at_least("ghost", "viewer"),    "unknown role does NOT satisfy viewer"
    ok("role hierarchy is correct")


# -------------------------------------------------------
# Section 2: First-boot bootstrap
# -------------------------------------------------------
def test_first_boot_creates_admin(tmp_dir):
    mgr = make_manager(tmp_dir)
    users = mgr.list_users()
    assert len(users) == 1, f"Expected 1 user, got {len(users)}"
    assert users[0]["username"] == "admin"
    assert users[0]["role"] == "admin"
    assert users[0]["must_change_password"] is True
    ok("first boot creates admin with must_change_password=True")


def test_users_json_persists(tmp_dir):
    """A second AuthManager pointing at the same file doesn't re-bootstrap."""
    mgr1 = make_manager(tmp_dir)
    # Add another user via mgr1
    mgr1.create_user("bob", "securepassword1", "viewer")

    mgr2 = make_manager(tmp_dir)
    names = {u["username"] for u in mgr2.list_users()}
    assert "admin" in names
    assert "bob" in names
    ok("users.json persists across AuthManager instances")


def test_secret_key_persists(tmp_dir):
    """Two managers sharing the same secret file can verify each other's tokens."""
    mgr1 = make_manager(tmp_dir)
    # Authenticate with the generated admin password is not easy here
    # (it's random), so we change it first via create_user with known password.
    mgr1.create_user("carol", "verylongpassword", "operator")

    user = mgr1.authenticate("carol", "verylongpassword")
    token = mgr1.issue_access_token(user)

    mgr2 = make_manager(tmp_dir)
    payload = mgr2.verify_token(token, expected_type="access")
    assert payload["sub"] == "carol"
    ok("JWT secret persists: tokens from mgr1 verified by mgr2")


# -------------------------------------------------------
# Section 3: User CRUD
# -------------------------------------------------------
def test_create_and_list(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("alice", "mypassword1", "operator")
    users = {u["username"]: u for u in mgr.list_users()}
    assert "alice" in users
    assert users["alice"]["role"] == "operator"
    assert users["alice"]["must_change_password"] is False
    ok("create_user and list_users work correctly")


def test_duplicate_user_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("dup", "password123", "viewer")
    e = expect_raises(UserExistsError, mgr.create_user, "dup", "anotherpass", "viewer")
    assert e is not None
    ok("duplicate username raises UserExistsError")


def test_invalid_role_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    e = expect_raises(ValueError, mgr.create_user, "x", "longpassword1", "superadmin")
    assert e is not None
    ok("invalid role raises ValueError")


def test_short_password_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)   # min_password_length=8 in tests
    e = expect_raises(ValueError, mgr.create_user, "short", "abc", "viewer")
    assert e is not None
    ok("password shorter than min_password_length raises ValueError")


def test_delete_user(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("temp", "longpassword!", "viewer")
    mgr.delete_user("temp")
    names = {u["username"] for u in mgr.list_users()}
    assert "temp" not in names
    ok("delete_user removes the account")


def test_delete_nonexistent_user(tmp_dir):
    mgr = make_manager(tmp_dir)
    e = expect_raises(UserNotFoundError, mgr.delete_user, "nobody")
    assert e is not None
    ok("deleting nonexistent user raises UserNotFoundError")


# -------------------------------------------------------
# Section 4: Authentication
# -------------------------------------------------------
def test_authenticate_success(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("dave", "goodpassword1", "operator")
    user = mgr.authenticate("dave", "goodpassword1")
    assert user["username"] == "dave"
    assert user["role"] == "operator"
    ok("authenticate succeeds with correct credentials")


def test_authenticate_wrong_password(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("eve", "correctpassword", "viewer")
    e = expect_raises(InvalidCredentialsError, mgr.authenticate, "eve", "wrongpassword")
    assert e is not None
    ok("authenticate rejects wrong password")


def test_authenticate_unknown_user(tmp_dir):
    mgr = make_manager(tmp_dir)
    e = expect_raises(InvalidCredentialsError, mgr.authenticate, "nobody", "anything")
    assert e is not None
    ok("authenticate rejects unknown username (same error as wrong password)")


# -------------------------------------------------------
# Section 5: JWT issue / verify
# -------------------------------------------------------
def test_access_token_roundtrip(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("frank", "longenoughpassword", "admin")
    user = mgr.authenticate("frank", "longenoughpassword")
    token = mgr.issue_access_token(user)
    payload = mgr.verify_token(token, expected_type="access")
    assert payload["sub"] == "frank"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"
    ok("access token issues and verifies correctly")


def test_refresh_token_roundtrip(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("grace", "longenoughpassword", "viewer")
    user = mgr.authenticate("grace", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    payload = mgr.verify_token(refresh, expected_type="refresh")
    assert payload["sub"] == "grace"
    assert payload["type"] == "refresh"
    ok("refresh token issues and verifies correctly")


def test_wrong_token_type_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("heidi", "longenoughpassword", "viewer")
    user = mgr.authenticate("heidi", "longenoughpassword")
    access = mgr.issue_access_token(user)
    # Try passing an access token where a refresh token is expected
    e = expect_raises(TokenError, mgr.verify_token, access, "refresh")
    assert e is not None
    ok("wrong token type (access as refresh) raises TokenError")


def test_expired_token_rejected(tmp_dir):
    # Issue a token that expires in -1 second (already expired)
    mgr = make_manager(tmp_dir, access_token_minutes=0)
    mgr.create_user("ivan", "longenoughpassword", "viewer")
    user = mgr.authenticate("ivan", "longenoughpassword")
    token = mgr.issue_access_token(user)
    # Sleep briefly to ensure exp < now
    time.sleep(1)
    e = expect_raises(TokenError, mgr.verify_token, token, "access")
    assert e is not None
    ok("expired access token raises TokenError")


def test_tampered_token_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("judy", "longenoughpassword", "viewer")
    user = mgr.authenticate("judy", "longenoughpassword")
    token = mgr.issue_access_token(user)
    # Flip the last few chars of the signature
    tampered = token[:-4] + "XXXX"
    e = expect_raises(TokenError, mgr.verify_token, tampered, "access")
    assert e is not None
    ok("tampered token signature raises TokenError")


def test_refresh_access_token(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("kate", "longenoughpassword", "operator")
    user = mgr.authenticate("kate", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    new_access = mgr.refresh_access_token(refresh)
    payload = mgr.verify_token(new_access, expected_type="access")
    assert payload["sub"] == "kate"
    ok("refresh_access_token returns valid new access token")


def test_revoked_refresh_token_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("leo", "longenoughpassword", "viewer")
    user = mgr.authenticate("leo", "longenoughpassword")
    refresh = mgr.issue_refresh_token(user)
    mgr.revoke_refresh_token(refresh)
    e = expect_raises(TokenError, mgr.verify_token, refresh, "refresh")
    assert e is not None
    ok("revoked refresh token raises TokenError")


# -------------------------------------------------------
# Section 6: change_password
# -------------------------------------------------------
def test_change_password(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("mike", "oldpassword1", "viewer")
    mgr.change_password("mike", "oldpassword1", "newpassword1")
    # Old password now rejected
    e = expect_raises(InvalidCredentialsError, mgr.authenticate, "mike", "oldpassword1")
    assert e is not None
    # New password accepted
    user = mgr.authenticate("mike", "newpassword1")
    assert user["username"] == "mike"
    ok("change_password replaces the password and clears must_change_password")


def test_change_password_wrong_old(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("nina", "correctpassword", "viewer")
    e = expect_raises(InvalidCredentialsError, mgr.change_password, "nina", "wrongpassword", "newpassword!")
    assert e is not None
    ok("change_password rejects wrong old password")


# -------------------------------------------------------
# Section 7: deleted user's tokens are rejected
# -------------------------------------------------------
def test_deleted_user_token_rejected(tmp_dir):
    mgr = make_manager(tmp_dir)
    mgr.create_user("omar", "longenoughpassword", "viewer")
    user = mgr.authenticate("omar", "longenoughpassword")
    token = mgr.issue_access_token(user)
    mgr.delete_user("omar")
    e = expect_raises(TokenError, mgr.verify_token, token, "access")
    assert e is not None
    ok("token for a deleted user raises TokenError on verify")


# -------------------------------------------------------
# Runner
# -------------------------------------------------------
if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        # role tests need no tmp dir
        test_role_hierarchy()

        # Each stateful test gets its own subdirectory so they don't
        # share users.json and don't interfere with each other.
        def subdir(name):
            p = os.path.join(tmp, name)
            os.makedirs(p, exist_ok=True)
            return p

        test_first_boot_creates_admin(subdir("boot"))
        test_users_json_persists(subdir("persist"))
        test_secret_key_persists(subdir("secret"))

        test_create_and_list(subdir("crud_create"))
        test_duplicate_user_rejected(subdir("crud_dup"))
        test_invalid_role_rejected(subdir("crud_role"))
        test_short_password_rejected(subdir("crud_short"))
        test_delete_user(subdir("crud_del"))
        test_delete_nonexistent_user(subdir("crud_del_missing"))

        test_authenticate_success(subdir("auth_ok"))
        test_authenticate_wrong_password(subdir("auth_bad_pw"))
        test_authenticate_unknown_user(subdir("auth_bad_user"))

        test_access_token_roundtrip(subdir("jwt_access"))
        test_refresh_token_roundtrip(subdir("jwt_refresh"))
        test_wrong_token_type_rejected(subdir("jwt_type"))
        test_expired_token_rejected(subdir("jwt_exp"))
        test_tampered_token_rejected(subdir("jwt_tamper"))
        test_refresh_access_token(subdir("jwt_refresh_flow"))
        test_revoked_refresh_token_rejected(subdir("jwt_revoke"))

        test_change_password(subdir("pw_change"))
        test_change_password_wrong_old(subdir("pw_change_bad"))

        test_deleted_user_token_rejected(subdir("del_token"))

    print("\nAll auth manager checks passed.")