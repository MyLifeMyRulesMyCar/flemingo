#!/usr/bin/env python3
# core/auth_manager.py
# Phase 6 - user store, password hashing, JWT issue/verify, role hierarchy.
#
# Same role as core/can_manager.py / core/modbus_manager.py: a backend
# manager that owns its own state and gets imported as a module-level
# singleton (`auth_manager`) by the API layer. api/auth_decorators.py
# and api/auth_routes.py are the only things that should import this -
# keep route-shape concerns out of this file the same way can_manager.py
# stays free of Flask imports.
#
# Storage: a small JSON file (config/users.json), not SQLite - this is
# a handful of accounts (you + a few integrators/techs), not a multi-
# tenant system. Passwords are hashed with Werkzeug's PBKDF2 helper
# (already a transitive Flask dependency - no new C-extension to build
# on an ARM SBC, unlike bcrypt). The JWT signing secret is a random
# value generated on first run and stored in config/jwt_secret.key
# (0600 permissions) - never hardcoded, never committed.
#
# First-boot behavior: if config/users.json doesn't exist yet, a single
# default admin account is created with a randomly generated password.
# That password is printed to stdout/log ONCE at creation time and is
# never recoverable after that - the account is flagged
# must_change_password=True so api/auth_routes.py can force a change
# before anything else happens.

import json
import logging
import os
import secrets
import stat
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import jwt
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

# ============================================
# Role hierarchy
# ============================================
# Higher number = more privilege. role_at_least() does a numeric
# comparison so "admin" satisfies a route that only requires
# "operator" or "viewer", same idea as Unix permission bits.
ROLE_RANK = {
    "viewer": 0,
    "operator": 1,
    "admin": 2,
}

VALID_ROLES = set(ROLE_RANK)


def role_at_least(role: str, required: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required, 99)


# ============================================
# Errors
# ============================================
class AuthError(Exception):
    """Base class - bad credentials, expired/invalid token, unknown user, etc."""

    pass


class InvalidCredentialsError(AuthError):
    pass


class TokenError(AuthError):
    pass


class UserExistsError(AuthError):
    pass


class UserNotFoundError(AuthError):
    pass


# ============================================
# Paths
# ============================================
_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"
)
DEFAULT_USERS_PATH = os.path.join(_CONFIG_DIR, "users.json")
DEFAULT_SECRET_PATH = os.path.join(_CONFIG_DIR, "jwt_secret.key")

# ============================================
# Defaults (overridable via config/reliability.yaml's `auth:` section -
# loaded by the caller and passed in, same pattern as can_manager.py
# reading load_reliability_config()["circuit_breaker"]["can"])
# ============================================
DEFAULT_ACCESS_TOKEN_MINUTES = 30
DEFAULT_REFRESH_TOKEN_DAYS = 7
DEFAULT_MIN_PASSWORD_LENGTH = 10


class AuthManager:
    """
    Usage:
        mgr = AuthManager()
        user = mgr.authenticate("admin", "...")
        access = mgr.issue_access_token(user)
        refresh = mgr.issue_refresh_token(user)
        payload = mgr.verify_token(access, expected_type="access")
    """

    def __init__(
        self,
        users_path: str = None,
        secret_path: str = None,
        access_token_minutes: int = DEFAULT_ACCESS_TOKEN_MINUTES,
        refresh_token_days: int = DEFAULT_REFRESH_TOKEN_DAYS,
        min_password_length: int = DEFAULT_MIN_PASSWORD_LENGTH,
    ):
        self.users_path = users_path or DEFAULT_USERS_PATH
        self.secret_path = secret_path or DEFAULT_SECRET_PATH
        self.access_token_minutes = access_token_minutes
        self.refresh_token_days = refresh_token_days
        self.min_password_length = min_password_length

        self._lock = threading.RLock()
        self._secret = self._load_or_create_secret()
        self._users: Dict[str, dict] = {}
        # Refresh tokens that have been explicitly revoked (logout) -
        # checked in addition to JWT expiry. Cleared lazily of expired
        # entries on revoke; fine for a handful of users on a LAN box.
        self._revoked_jti: set = set()

        self._load_users()
        if not self._users:
            self._bootstrap_default_admin()

    # ----------------------------------------
    # Secret key
    # ----------------------------------------
    def _load_or_create_secret(self) -> str:
        if os.path.exists(self.secret_path):
            with open(self.secret_path, "r") as f:
                return f.read().strip()

        os.makedirs(os.path.dirname(self.secret_path), exist_ok=True)
        secret = secrets.token_hex(32)
        with open(self.secret_path, "w") as f:
            f.write(secret)
        os.chmod(self.secret_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        logger.info(f"Generated new JWT signing secret at {self.secret_path}")
        return secret

    # ----------------------------------------
    # User store (load/save)
    # ----------------------------------------
    def _load_users(self):
        if not os.path.exists(self.users_path):
            self._users = {}
            return
        try:
            with open(self.users_path, "r") as f:
                self._users = json.load(f)
        except Exception as e:
            logger.error(
                f"Could not read {self.users_path} ({e}) - starting with no users"
            )
            self._users = {}

    def _save_users(self):
        os.makedirs(os.path.dirname(self.users_path), exist_ok=True)
        tmp_path = self.users_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(self._users, f, indent=2)
        os.replace(tmp_path, self.users_path)  # atomic on POSIX
        try:
            os.chmod(self.users_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass

    def _bootstrap_default_admin(self):
        """First-boot only: creates a single admin account with a random
        password and prints it once. There is no way to recover this
        password later - if it's lost before first login, delete
        config/users.json to re-trigger bootstrap (which also means
        re-creating any other accounts you'd added by then)."""
        password = secrets.token_urlsafe(12)
        with self._lock:
            self._users["admin"] = {
                "password_hash": generate_password_hash(password),
                "role": "admin",
                "must_change_password": True,
                "created_at": datetime.now().isoformat(),
            }
            self._save_users()

        banner = "=" * 60
        logger.warning(banner)
        logger.warning("FIRST BOOT - default admin account created")
        logger.warning("  username: admin")
        logger.warning(f"  password: {password}")
        logger.warning("  This password is shown ONCE and is not recoverable.")
        logger.warning("  You will be required to change it on first login.")
        logger.warning(banner)

    # ----------------------------------------
    # User CRUD
    # ----------------------------------------
    def create_user(self, username: str, password: str, role: str) -> dict:
        if role not in VALID_ROLES:
            raise ValueError(
                f"Invalid role '{role}', must be one of {sorted(VALID_ROLES)}"
            )
        if len(password) < self.min_password_length:
            raise ValueError(
                f"Password must be at least {self.min_password_length} characters"
            )

        with self._lock:
            if username in self._users:
                raise UserExistsError(f"User '{username}' already exists")
            self._users[username] = {
                "password_hash": generate_password_hash(password),
                "role": role,
                "must_change_password": False,
                "created_at": datetime.now().isoformat(),
            }
            self._save_users()
            logger.info(f"User '{username}' created (role={role})")
            return self._public_user(username)

    def delete_user(self, username: str):
        with self._lock:
            if username not in self._users:
                raise UserNotFoundError(f"User '{username}' not found")
            del self._users[username]
            self._save_users()
            logger.info(f"User '{username}' deleted")

    def list_users(self) -> List[dict]:
        with self._lock:
            return [self._public_user(u) for u in self._users]

    def _public_user(self, username: str) -> dict:
        u = self._users[username]
        return {
            "username": username,
            "role": u["role"],
            "must_change_password": u.get("must_change_password", False),
            "created_at": u.get("created_at"),
        }

    def change_password(self, username: str, old_password: str, new_password: str):
        with self._lock:
            user = self._users.get(username)
            if not user:
                raise UserNotFoundError(f"User '{username}' not found")
            if not check_password_hash(user["password_hash"], old_password):
                raise InvalidCredentialsError("Current password is incorrect")
            if len(new_password) < self.min_password_length:
                raise ValueError(
                    f"Password must be at least {self.min_password_length} characters"
                )
            user["password_hash"] = generate_password_hash(new_password)
            user["must_change_password"] = False
            self._save_users()
            logger.info(f"Password changed for '{username}'")

    # ----------------------------------------
    # Authentication
    # ----------------------------------------
    def authenticate(self, username: str, password: str) -> dict:
        with self._lock:
            user = self._users.get(username)
        if not user or not check_password_hash(user["password_hash"], password):
            # Deliberately the same error for "no such user" and "wrong
            # password" - don't leak which one it was.
            raise InvalidCredentialsError("Invalid username or password")
        return self._public_user(username)

    # ----------------------------------------
    # JWT issue / verify
    # ----------------------------------------
    def issue_access_token(self, user: dict) -> str:
        now = int(time.time())
        payload = {
            "sub": user["username"],
            "role": user["role"],
            "type": "access",
            "iat": now,
            "exp": now + self.access_token_minutes * 60,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def issue_refresh_token(self, user: dict) -> str:
        now = int(time.time())
        payload = {
            "sub": user["username"],
            "type": "refresh",
            "jti": secrets.token_hex(8),
            "iat": now,
            "exp": now + self.refresh_token_days * 86400,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def verify_token(self, token: str, expected_type: str = "access") -> dict:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise TokenError("Token expired")
        except jwt.InvalidTokenError as e:
            raise TokenError(f"Invalid token: {e}")

        if payload.get("type") != expected_type:
            raise TokenError(
                f"Expected a {expected_type} token, got {payload.get('type')}"
            )

        if expected_type == "refresh" and payload.get("jti") in self._revoked_jti:
            raise TokenError("Token has been revoked")

        username = payload.get("sub")
        with self._lock:
            user = self._users.get(username)
        if not user:
            raise TokenError("User no longer exists")

        # For access tokens, role is read back from the token itself
        # (issued at login time) rather than re-checked against the
        # live user store, matching standard JWT statelessness - if a
        # role changes mid-session the change takes effect on the next
        # token refresh, not retroactively on outstanding access tokens.
        return payload

    def refresh_access_token(self, refresh_token: str) -> str:
        payload = self.verify_token(refresh_token, expected_type="refresh")
        username = payload["sub"]
        with self._lock:
            user = self._users.get(username)
        if not user:
            raise TokenError("User no longer exists")
        return self.issue_access_token(self._public_user(username))

    def revoke_refresh_token(self, refresh_token: str):
        """Best-effort logout: blacklist this token's jti. Lost on
        process restart (in-memory only) - acceptable since refresh
        tokens are also short-lived (days, not months) and this is a
        single-process LAN device, not a distributed system."""
        try:
            payload = jwt.decode(
                refresh_token,
                self._secret,
                algorithms=["HS256"],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                self._revoked_jti.add(jti)
        except jwt.InvalidTokenError:
            pass  # already garbage - nothing to revoke


# Module-level singleton, same pattern as can_manager/modbus_manager
auth_manager: Optional[AuthManager] = None


def init_auth_manager(
    access_token_minutes=DEFAULT_ACCESS_TOKEN_MINUTES,
    refresh_token_days=DEFAULT_REFRESH_TOKEN_DAYS,
    min_password_length=DEFAULT_MIN_PASSWORD_LENGTH,
) -> AuthManager:
    """Called once from api/app.py at startup, after reliability.yaml's
    `auth:` section has been read - mirrors how can_manager/modbus_manager
    pull their tuning from core.config.load_reliability_config(), except
    auth_manager can't construct itself as an import-time module-level
    singleton like those do, since the JSON user store and JWT secret
    need to exist on disk before anything else touches them, and Flask
    routes shouldn't pay that I/O cost at import time."""
    global auth_manager
    auth_manager = AuthManager(
        access_token_minutes=access_token_minutes,
        refresh_token_days=refresh_token_days,
        min_password_length=min_password_length,
    )
    return auth_manager
