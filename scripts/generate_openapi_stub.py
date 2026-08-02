#!/usr/bin/env python3
# scripts/generate_openapi_stub.py
#
# Introspects the live Flask app (api.app:app) to build docs/openapi.yaml.
# Path, method, and required-role are pulled from the real url_map and
# real decorators — never hand-typed — so the spec can't silently drift
# from the route table the way a hand-maintained doc would.
#
# What it can't see: two auth_api endpoints (`me`, `change_password`)
# check auth manually in the function body instead of via @require_role,
# so they'd introspect as "public" without the AUTH_OVERRIDES map below.
# If you add another manually-checked endpoint, add it there too, or
# this script will silently document it as unauthenticated.
#
# Usage:
#   . venv/bin/activate
#   python3 scripts/generate_openapi_stub.py
#   openapi-spec-validator docs/openapi.yaml
#
# The auth_api block below is the fully-specified worked example —
# request/response schemas, error codes, descriptions. Extend the other
# 8 blueprints the same way; PATHS_DETAIL is where hand-written detail
# overrides the auto-generated stub for any operationId.

import inspect
import os
import re
import sys
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("FLEMINGO_SKIP_AUTOSTART", "1")
from api.app import app  # noqa: E402

# endpoint -> required role, for routes that check auth in-body instead
# of via decorator (see module docstring above)
AUTH_OVERRIDES = {
    "auth_api.me": "authenticated",
    "auth_api.change_password": "authenticated",
}

ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2, "authenticated": -1}


def _extract_role(view_func, endpoint):
    if endpoint in AUTH_OVERRIDES:
        return AUTH_OVERRIDES[endpoint]
    try:
        src = inspect.getsource(view_func)
    except (OSError, TypeError):
        return None
    m = re.search(r'@require_role\("(\w+)"\)', src)
    if m:
        return m.group(1)
    if re.search(r"@require_auth\b", src):
        return "authenticated"
    return None  # public


def _security_block(role):
    if role is None:
        return []
    if role == "authenticated":
        return [{"bearerAuth": []}]
    return [{"bearerAuth": [role]}]


def _path_params(flask_rule):
    params = []
    for conv, name in re.findall(r"<(?:(\w+):)?(\w+)>", flask_rule):
        schema_type = "integer" if conv == "int" else "string"
        params.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": schema_type},
            }
        )
    return params


def _stub_operation(method, endpoint, role, summary, path_params):
    op = {
        "operationId": endpoint.replace(".", "_"),
        "summary": summary or endpoint,
        "tags": [endpoint.split(".")[0]],
        "security": _security_block(role),
        "responses": {
            "200": {"description": "TODO: fill in actual response schema"},
        },
    }
    if path_params:
        op["parameters"] = path_params
    if role:
        op["responses"]["401"] = {"description": "Missing or invalid token"}
        if role != "authenticated":
            op["responses"]["403"] = {"description": f"Requires role: {role}"}
    if method in ("POST", "PUT", "DELETE"):
        op["requestBody"] = {
            "content": {"application/json": {"schema": {"type": "object"}}},
            "description": "TODO: fill in actual request schema",
        }
    return op


# ---------------------------------------------------------------------
# Fully worked example: auth_api. Replace the stub for every other
# blueprint with detail like this, one blueprint at a time.
# ---------------------------------------------------------------------
PATHS_DETAIL = {
    "/api/auth/login": {
        "post": {
            "operationId": "auth_api_login",
            "summary": "Log in and receive access + refresh tokens",
            "tags": ["auth_api"],
            "security": [],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["username", "password"],
                            "properties": {
                                "username": {"type": "string"},
                                "password": {"type": "string", "format": "password"},
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Authenticated",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "access_token": {"type": "string"},
                                    "refresh_token": {"type": "string"},
                                    "user": {"type": "object"},
                                    "warning": {
                                        "type": "string",
                                        "description": "Present if must_change_password is set",
                                    },
                                },
                            }
                        }
                    },
                },
                "400": {"description": "username and password are required"},
                "401": {"description": "Invalid credentials"},
                "429": {"description": "Too many failed attempts — rate limited"},
            },
        }
    },
    "/api/auth/refresh": {
        "post": {
            "operationId": "auth_api_refresh",
            "summary": "Exchange a refresh token for a new access token",
            "tags": ["auth_api"],
            "security": [],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["refresh_token"],
                            "properties": {"refresh_token": {"type": "string"}},
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "New access token issued",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"access_token": {"type": "string"}},
                            }
                        }
                    },
                },
                "400": {"description": "refresh_token is required"},
                "401": {"description": "Invalid or expired refresh token"},
            },
        }
    },
    "/api/auth/logout": {
        "post": {
            "operationId": "auth_api_logout",
            "summary": "Log out — best-effort revoke of the supplied refresh token",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": []}],
            "requestBody": {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"refresh_token": {"type": "string"}},
                        }
                    }
                },
            },
            "responses": {
                "200": {"description": "Logged out"},
                "401": {"description": "Missing or invalid access token"},
            },
        }
    },
    "/api/auth/me": {
        "get": {
            "operationId": "auth_api_me",
            "summary": "Current user's decoded token payload",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "username": {"type": "string"},
                                    "role": {
                                        "type": "string",
                                        "enum": ["viewer", "operator", "admin"],
                                    },
                                    "must_change_password": {"type": "boolean"},
                                },
                            }
                        }
                    },
                },
                "401": {"description": "Missing or invalid access token"},
            },
        }
    },
    "/api/auth/change-password": {
        "post": {
            "operationId": "auth_api_change_password",
            "summary": "Change the current user's password",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": []}],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["old_password", "new_password"],
                            "properties": {
                                "old_password": {
                                    "type": "string",
                                    "format": "password",
                                },
                                "new_password": {
                                    "type": "string",
                                    "format": "password",
                                },
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {"description": "Password changed — re-login for new tokens"},
                "400": {"description": "Missing fields, or new_password fails policy"},
                "401": {"description": "old_password incorrect, or bad access token"},
            },
        }
    },
    "/api/auth/users": {
        "get": {
            "operationId": "auth_api_list_users",
            "summary": "List all users",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": ["admin"]}],
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "users": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    }
                                },
                            }
                        }
                    },
                },
                "401": {"description": "Missing or invalid access token"},
                "403": {"description": "Requires role: admin"},
            },
        },
        "post": {
            "operationId": "auth_api_create_user",
            "summary": "Create a new user",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": ["admin"]}],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["username", "password", "role"],
                            "properties": {
                                "username": {"type": "string"},
                                "password": {"type": "string", "format": "password"},
                                "role": {
                                    "type": "string",
                                    "enum": ["viewer", "operator", "admin"],
                                },
                            },
                        }
                    }
                },
            },
            "responses": {
                "201": {"description": "User created"},
                "400": {"description": "Missing field or invalid role"},
                "401": {"description": "Missing or invalid access token"},
                "403": {"description": "Requires role: admin"},
                "409": {"description": "Username already exists"},
            },
        },
    },
    "/api/auth/users/{username}": {
        "delete": {
            "operationId": "auth_api_delete_user",
            "summary": "Delete a user (cannot delete your own account)",
            "tags": ["auth_api"],
            "security": [{"bearerAuth": ["admin"]}],
            "parameters": [
                {
                    "name": "username",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {"description": "User deleted"},
                "400": {"description": "Cannot delete your own account"},
                "401": {"description": "Missing or invalid access token"},
                "403": {"description": "Requires role: admin"},
                "404": {"description": "User not found"},
            },
        }
    },
}


def build_spec():
    paths = {}
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r.rule)):
        if rule.endpoint in ("static", "serve_dashboard"):
            continue
        openapi_path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", str(rule.rule))
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        view_func = app.view_functions[rule.endpoint]
        role = _extract_role(view_func, rule.endpoint)

        detail = PATHS_DETAIL.get(openapi_path, {})
        paths.setdefault(openapi_path, {})
        path_params = _path_params(str(rule.rule))
        for method in methods:
            lower = method.lower()
            if lower in detail:
                paths[openapi_path][lower] = detail[lower]
            else:
                paths[openapi_path][lower] = _stub_operation(
                    method, rule.endpoint, role, summary=None, path_params=path_params
                )

    tags = sorted({op["tags"][0] for p in paths.values() for op in p.values()})

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Flemingo API",
            "version": "0.14.0",
            "description": (
                "Auto-generated from api/app.py's live url_map — do not "
                "hand-edit paths/security here, edit PATHS_DETAIL in "
                "scripts/generate_openapi_stub.py and regenerate instead."
            ),
        },
        "servers": [{"url": "https://{device-ip}", "description": "Device (TLS)"}],
        "tags": [{"name": t} for t in tags],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": (
                        "Role scopes used loosely here for documentation: "
                        "viewer < operator < admin. Flemingo does not do "
                        "real OAuth2 scope checking — this just documents "
                        "which @require_role the endpoint carries."
                    ),
                }
            }
        },
        "paths": paths,
    }
    return spec


if __name__ == "__main__":
    spec = build_spec()
    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "openapi.yaml")
    with open(out_path, "w") as f:
        yaml.dump(spec, f, sort_keys=False, width=100)
    n_ops = sum(len(v) for v in spec["paths"].values())
    print(f"Wrote {out_path}: {len(spec['paths'])} paths, {n_ops} operations.")
