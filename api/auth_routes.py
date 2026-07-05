#!/usr/bin/env python3
# api/auth_routes.py
# Phase 6 - /api/auth/* blueprint.
#
# Route map:
#   POST /api/auth/login              public - returns access + refresh tokens
#   POST /api/auth/refresh            public - exchange refresh token for new access token
#   POST /api/auth/logout             any auth - revoke refresh token (best-effort)
#   GET  /api/auth/me                 any auth (skips must_change_password block)
#   POST /api/auth/change-password    any auth (skips must_change_password block)
#   GET  /api/auth/users              admin
#   POST /api/auth/users              admin - create a new user
#   DELETE /api/auth/users/<username> admin - delete a user

import logging

from flask import Blueprint, jsonify, request, g

from core import auth_manager as auth_manager_module
from core.auth_manager import (
    InvalidCredentialsError,
    TokenError,
    UserExistsError,
    UserNotFoundError,
    VALID_ROLES,
)
from api.auth_decorators import (
    require_auth,
    require_role,
    _verify_and_set_g,
)

logger = logging.getLogger(__name__)

auth_api = Blueprint("auth_api", __name__)


def _get_auth_manager():
    mgr = auth_manager_module.auth_manager
    if mgr is None:
        return auth_manager_module.init_auth_manager()
    return mgr


# -------------------------------------------------------
# POST /api/auth/login
# Public - no decorator needed.
# Body: { "username": "...", "password": "..." }
# Response: { "access_token", "refresh_token", "user" }
# -------------------------------------------------------
@auth_api.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    mgr = _get_auth_manager()
    try:
        user = mgr.authenticate(username, password)
    except InvalidCredentialsError:
        # No info about which field was wrong - same error either way
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = mgr.issue_access_token(user)
    refresh_token = mgr.issue_refresh_token(user)

    resp = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user,
    }
    if user.get("must_change_password"):
        resp["warning"] = "Password change required before accessing device endpoints"

    logger.info(f"Login: {username} (role={user['role']})")
    return jsonify(resp), 200


# -------------------------------------------------------
# POST /api/auth/refresh
# Public - caller sends refresh token, gets new access token.
# Body: { "refresh_token": "..." }
# Response: { "access_token" }
# -------------------------------------------------------
@auth_api.route("/api/auth/refresh", methods=["POST"])
def refresh():
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token", "")

    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    mgr = _get_auth_manager()
    try:
        access_token = mgr.refresh_access_token(refresh_token)
    except TokenError as e:
        return jsonify({"error": str(e)}), 401

    return jsonify({"access_token": access_token}), 200


# -------------------------------------------------------
# POST /api/auth/logout
# Any authenticated user.
# Body: { "refresh_token": "..." }  (optional but best-effort revokes it)
# -------------------------------------------------------
@auth_api.route("/api/auth/logout", methods=["POST"])
@require_auth
def logout():
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token", "")
    if refresh_token:
        _get_auth_manager().revoke_refresh_token(refresh_token)

    logger.info(f"Logout: {g.current_user.get('sub')}")
    return jsonify({"message": "Logged out"}), 200


# -------------------------------------------------------
# GET /api/auth/me
# Any authenticated user.
# Returns the decoded access-token payload (username, role,
# must_change_password). Works even if must_change_password=True so the
# frontend can read that flag and redirect to change-password UI without
# a separate endpoint.
# -------------------------------------------------------
@auth_api.route("/api/auth/me", methods=["GET"])
def me():
    # Manual verify here because we want to skip the must_change_password
    # block in @require_auth - this endpoint is safe to expose to all
    # valid tokens, even first-boot unconfigured accounts.
    payload, err = _verify_and_set_g()
    if err:
        return jsonify(err[0]), err[1]

    user = g.current_user
    return (
        jsonify(
            {
                "username": user.get("sub"),
                "role": user.get("role"),
                "must_change_password": user.get("must_change_password", False),
            }
        ),
        200,
    )


# -------------------------------------------------------
# POST /api/auth/change-password
# Any authenticated user (including must_change_password accounts).
# Body: { "old_password": "...", "new_password": "..." }
# -------------------------------------------------------
@auth_api.route("/api/auth/change-password", methods=["POST"])
def change_password():
    # Same pattern as /me - manual verify to skip the must_change_password
    # block so first-boot admins can actually change their password.
    payload, err = _verify_and_set_g()
    if err:
        return jsonify(err[0]), err[1]

    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        return jsonify({"error": "old_password and new_password are required"}), 400

    username = g.current_user.get("sub")
    mgr = _get_auth_manager()
    try:
        mgr.change_password(username, old_password, new_password)
    except InvalidCredentialsError as e:
        return jsonify({"error": str(e)}), 401
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    logger.info(f"Password changed: {username}")
    return jsonify({"message": "Password changed. Re-login to get new tokens."}), 200


# -------------------------------------------------------
# GET /api/auth/users
# Admin only.
# -------------------------------------------------------
@auth_api.route("/api/auth/users", methods=["GET"])
@require_role("admin")
def list_users():
    return jsonify({"users": _get_auth_manager().list_users()}), 200


# -------------------------------------------------------
# POST /api/auth/users
# Admin only.
# Body: { "username": "...", "password": "...", "role": "viewer|operator|admin" }
# -------------------------------------------------------
@auth_api.route("/api/auth/users", methods=["POST"])
@require_role("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "").strip()

    if not username:
        return jsonify({"error": "username is required"}), 400
    if not password:
        return jsonify({"error": "password is required"}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": f"role must be one of: {sorted(VALID_ROLES)}"}), 400

    mgr = _get_auth_manager()
    try:
        user = mgr.create_user(username, password, role)
    except UserExistsError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    logger.info(
        f"Admin {g.current_user.get('sub')} created user '{username}' (role={role})"
    )
    return jsonify({"message": "User created", "user": user}), 201


# -------------------------------------------------------
# DELETE /api/auth/users/<username>
# Admin only.
# Cannot delete yourself - that would be a very bad day.
# -------------------------------------------------------
@auth_api.route("/api/auth/users/<username>", methods=["DELETE"])
@require_role("admin")
def delete_user(username):
    caller = g.current_user.get("sub")
    if username == caller:
        return jsonify({"error": "Cannot delete your own account"}), 400

    mgr = _get_auth_manager()
    try:
        mgr.delete_user(username)
    except UserNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    logger.info(f"Admin {caller} deleted user '{username}'")
    return jsonify({"message": f"User '{username}' deleted"}), 200
