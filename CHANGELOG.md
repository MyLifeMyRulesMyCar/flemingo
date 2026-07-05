# Changelog

All notable changes to Flemingo are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- CI pipeline (lint, pytest, dashboard build) via GitHub Actions
- Pinned all Python and Node dependencies to exact versions
- Black formatting + flake8 linting (49 files reformatted)
- One-shot `scripts/setup.sh` installer for fresh Purple Pi OH2
- Field-update script with config backup, hash-diff guardrails, health check
- systemd `flemingo.service` template
- `docs/DEPLOYMENT.md` with v0 update procedure and v1 roadmap
- `README.md` with quick-start guide

## [0.11.0] - 2026-07-05

### Added
- pytest suite: 151 tests, zero hardware needed, 25s runtime
- Real SocketIO handler tests via `flask_socketio.test_client()` (connect, set_do, role gates)
- `api/socket_handlers.py` extracted from `api/app.py` for testability
- Shared `conftest.py` with session-scoped auth manager
- Route-level tests for `/api/io`, `/api/can`, `/api/modbus`
- `test_io_manager.py` in forced simulation mode

### Fixed
- DI debounce now commits debounced value, not raw reading
- WebSocket connect handler missing `request` import (NameError crash on connect)
- Tautological socket-auth rejection tests replaced with `assert is_connected()` assertions
- Debounce comment fixed: `~30ms` → `~300ms` (3 reads × 0.1s)

## [0.10.0] - 2026-07-04

### Added
- React/Vite dashboard: 6 pages (Overview, I/O, CAN, Modbus, MQTT, System)
- JWT-authenticated SocketIO with `request_io` push on page mount
- `system_metrics` SocketIO emission every 10s in broadcast loop
- Reusable components: StatusLed, MetricCard, ConfirmModal, Toast
- Role-gated UI (viewer/operator/admin button visibility)
- IO bridge poll interval and publish-on-change controls
- CAN bridge topic inputs and QoS selector

### Fixed
- WebSocket auth/role bypass (no JWT check on connect or set_do)
- Session not reconnecting socket after page refresh (AuthContext `useEffect`)
- MQTT broker username/password fields missing from UI
- IO bridge debounce removed (was filtering out short DI pulses)
- CAN log auto-scroll direction (was scrolling to bottom, not top)
- Sidebar collapse now shows a logout icon on narrow screens
- JetBrains Mono CDN removed — falls back to system monospace

## [0.9.0] - 2026-07-04

### Added
- `core/system_metrics.py` — CPU, RAM, disk, temperature, network, process metrics
- `core/backup_manager.py` — config backup zip with manifest, restore with 7 validation gates
- `/api/system/info`, `/api/system/metrics`, `/api/system/backup`, `/api/system/restore`
- Config backup ZIP with `backup_manifest.json` (version, device_id, file list)
- Restore validation: path-traversal rejection, zip-bomb check, YAML/JSON parse check
- `VERSION = "0.5.0"` constant in `core/config.py`
- User table with created-at column, restore confirmation modal

## [0.8.0] - 2026-07-03

### Added
- `core/mqtt_manager.py` — single paho-mqtt client with `connect_async`
- `core/bridges/can_bridge.py`, `modbus_bridge.py`, `io_bridge.py`
- `/api/mqtt/connect`, `/api/mqtt/disconnect`, `/api/mqtt/bridges/*` routes
- MQTT topic wildcard routing (+ and # matching per MQTT 3.1.1 §4.7)
- MQTT bridge stats in system metrics response

## [0.7.0] - 2026-07-02

### Added
- `api/validators.py` — centralised input validation for CAN, Modbus, MQTT
- `nginx/flemingo.conf` — TLS 1.2+ reverse proxy with WebSocket upgrade
- `scripts/gen_selfsigned_cert.sh`, `scripts/install_nginx.sh`
- CORS origins from `reliability.yaml` with `PURPLEIO_CORS_ORIGINS` env override

## [0.6.0] - 2026-07-01

### Added
- `core/auth_manager.py` — scrypt password hashing, JWT access/refresh tokens
- `api/auth_decorators.py` — `@require_auth`, `@require_role` decorators
- `api/auth_routes.py` — login, logout, refresh, user CRUD, change-password
- First-boot admin bootstrap with one-time password
- viewer < operator < admin role hierarchy

## [0.5.0] - 2026-07-01

### Added
- `core/resilience.py` — circuit breakers (CAN, per-device Modbus), retry with backoff
- `core/watchdog.py` — software watchdog with per-component health checks
- `core/logging_config.py` — structured JSON + console logging with rotation
- `config/reliability.yaml` — tunable thresholds, no code changes needed
