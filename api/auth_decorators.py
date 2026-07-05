#!/usr/bin/env python3
# api/auth_decorators.py
# Phase 6 - route guard decorators.
#
# Two decorators are all the route files need:
#
#   @require_auth               - any valid access token, any role
#   @require_role("operator")   - valid token AND role >= operator
#
# Both pull the bearer token from the Authorization header,
# call auth_manager.verify_token(), and attach the decoded payload
# to flask.g so the route can read g.current_user without re-parsing.
#
# Blocked by must_change_password: any route decorated with either
# guard will return 403 if the account is flagged must_change_password=True,
# except for POST /api/auth/change-password, which uses @require_auth
# but explicitly bypasses this check (see auth_routes.py).
# The intent: a first-boot admin must change their password before they
# can actuate anything on the device.

import functools
import logging

from flask import request, jsonify, g

from core import auth_manager as auth_manager_module
from core.auth_manager import role_at_least, TokenError

logger = logging.getLogger(__name__)


def _extract_token() -> str | None:
    """Pull 'Bearer <token>' from Authorization header."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return None


def _verify_and_set_g() -> tuple[dict | None, tuple | None]:
    """
    Verify the bearer token and populate g.current_user.
    Returns (payload, None) on success.
    Returns (None, (response_body, status_code)) on failure.
    """
    token = _extract_token()
    if not token:
        return None, ({"error": "Authorization header with Bearer token required"}, 401)

    mgr = auth_manager_module.auth_manager
    if mgr is None:
        mgr = auth_manager_module.init_auth_manager()

    try:
        payload = mgr.verify_token(token, expected_type="access")
    except TokenError as e:
        return None, ({"error": str(e)}, 401)

    g.current_user = payload
    return payload, None


def require_auth(fn):
    """
    Gate: any authenticated user (any role).
    Also blocks must_change_password accounts from doing anything except
    changing their password. Use this on /api/auth/change-password too,
    but add skip_password_check=True inside the route (see auth_routes.py).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        payload, err = _verify_and_set_g()
        if err:
            return jsonify(err[0]), err[1]

        if payload.get("must_change_password"):
            return (
                jsonify(
                    {
                        "error": "Password change required before using this endpoint",
                        "must_change_password": True,
                    }
                ),
                403,
            )

        return fn(*args, **kwargs)

    return wrapper


def require_role(min_role: str):
    """
    Gate: authenticated AND role >= min_role.
    Role hierarchy: viewer(0) < operator(1) < admin(2)
    An admin satisfies @require_role("operator") and @require_role("viewer").

    Usage:
        @can_api.route("/api/can/send", methods=["POST"])
        @require_role("operator")
        def send():
            ...
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            payload, err = _verify_and_set_g()
            if err:
                return jsonify(err[0]), err[1]

            if payload.get("must_change_password"):
                return (
                    jsonify(
                        {
                            "error": "Password change required before using this endpoint",
                            "must_change_password": True,
                        }
                    ),
                    403,
                )

            role = payload.get("role", "")
            if not role_at_least(role, min_role):
                return (
                    jsonify(
                        {
                            "error": f"Insufficient permissions (need '{min_role}', have '{role}')"
                        }
                    ),
                    403,
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator
