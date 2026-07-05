#!/usr/bin/env python3
# tests/conftest.py
# Shared pytest fixtures for all test files.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(scope="session", autouse=True)
def _patch_logging_to_null():
    """Silence logging during tests — prevents FIRST BOOT spam."""
    import logging
    logging.disable(logging.CRITICAL)


@pytest.fixture(scope="session")
def shared_auth(tmp_path_factory):
    """
    One isolated AuthManager shared across all tests in the session.
    Prevents each test from creating its own secret key (which would
    invalidate tokens issued by previous tests).
    """
    import core.auth_manager as am

    tmp = tmp_path_factory.mktemp("shared-auth")
    am.DEFAULT_USERS_PATH = str(tmp / "users.json")
    am.DEFAULT_SECRET_PATH = str(tmp / "jwt_secret.key")

    mgr = am.init_auth_manager(min_password_length=4)
    mgr.create_user("t", "test123456", "viewer")
    mgr.create_user("op", "test123456", "operator")
    mgr.create_user("adm", "test123456", "admin")

    return mgr


@pytest.fixture
def auth_tokens(shared_auth):
    """
    Return fresh tokens for all three roles from the shared AuthManager.
    """
    viewer = shared_auth.issue_access_token(
        {"username": "t", "role": "viewer", "must_change_password": False}
    )
    operator = shared_auth.issue_access_token(
        {"username": "op", "role": "operator", "must_change_password": False}
    )
    admin = shared_auth.issue_access_token(
        {"username": "adm", "role": "admin", "must_change_password": False}
    )
    return {"viewer": viewer, "operator": operator, "admin": admin}


@pytest.fixture
def isolated_auth(tmp_path):
    """
    Per-test isolated AuthManager. Use when a test needs to start from
    scratch (e.g., test_auth_manager.py exercises bootstrap logic).
    """
    import core.auth_manager as am

    orig_users = am.DEFAULT_USERS_PATH
    orig_secret = am.DEFAULT_SECRET_PATH

    cfg = tmp_path / "config"
    cfg.mkdir()
    am.DEFAULT_USERS_PATH = str(cfg / "users.json")
    am.DEFAULT_SECRET_PATH = str(cfg / "jwt_secret.key")

    yield

    am.DEFAULT_USERS_PATH = orig_users
    am.DEFAULT_SECRET_PATH = orig_secret
