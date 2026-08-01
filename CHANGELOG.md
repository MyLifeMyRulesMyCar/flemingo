# Changelog

All notable changes to Flemingo are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.14.0] - 2026-07-31
### Added
- Modbus TCP server (`core/modbus_tcp_server.py`) — live-reading server
  exposing DI/DO/CAN state to SCADA/HMI over Modbus TCP with manual MBAP
  framing
- Configurable register map (`core/modbus_tcp_register_map.py`) — map
  function code + address pairs to local source keys or RTU device/register
  pairs with `source_type` ("local"/"modbus_rtu"), `rtu_device_id`,
  `rtu_address`, and per-entry `writable` flag
- FC 6/FC 16 RTU holding register writes to RS485 devices via
  `asyncio.run_in_executor()` — blocking I/O never stalls the event loop
- CAN send channels (`core/can_send_channel.py`) — 6-register FC 16 stage
  block + single FC 5 coil trigger, `asyncio.Lock`-serialized to prevent
  concurrent stage/trigger races, stage cleared after successful send
- Test-write endpoint `POST /api/modbus-tcp/register-map/test-write`
  (operator role) for one-off write checks before marking an entry writable
- CAN send channel CRUD `GET/POST /api/modbus-tcp/can-send-channels`
  with overlap validation against register map entries
- Network config isolation (`core/network_config.py`) — eth1 static IP
  via `netplan`, `RevertScheduler` auto-reverts bad IP changes after 60s
- `scripts/flemingo-net-apply` — privilege boundary that re-derives
  interface name rather than trusting caller-supplied connection name,
  validates octet ranges not just digit-count regex
- Cable-disconnect detection via `/sys/class/net/eth1/carrier`
- Hardware config externalized to `config/hardware.yaml` (GPIO chip/line
  map, CAN crystal/bitrate) with `HARDWARE_DEFAULTS` fallback in
  `core/config.py`; malformed hardware config blocks startup
- CAN RX filter with subscriber gating (`core/can_manager.set_id_filter()`)
- Dashboard Modbus TCP page: ServerCard, NetworkCard, RegisterMapCard
  (with RTU fields and Test Write), CANSendChannelsCard
- `.opencode/conventions.md` — project codebase conventions
- 95 new tests (Modbus TCP server/framing, register map validation,
  FC 6/16 RTU write, CAN send stage+trigger, network config, hardware
  config, CAN filter routes)

### Changed
- `RegisterMapEntry` extended with `source_type` (default "local"),
  `rtu_device_id`, `rtu_address`, `writable` fields — existing persisted
  entries stay read-only on upgrade
- `core/io_manager.py` — GPIO pins loaded from `core/config.py`'s
  `HARDWARE_DEFAULTS` instead of hardcoded module-level constants
- `core/can_manager.py` — health-check reliability improvements,
  RX filter plumbing
- `core/config.py` — `HARDWARE_DEFAULTS` dict + `_deep_merge()` pattern
  for `config/hardware.yaml` overrides
- `daemon/daemon.py` — watchdog registration for Modbus TCP health
  watches carrier state on eth1
- Dashboard: CAN RX filter picker, Modbus TCP multi-card layout
- `api/validators.py` extended for Modbus TCP port, register map entries,
  network IPs, gateway-in-subnet checks

### Fixed
- `_resolve_bind_host` no longer 500s when network command fails —
  falls back to `0.0.0.0` with a warning
- `_check_modbus_tcp_health` now checks `/sys/class/net/eth1/carrier`
  instead of unconditionally returning `True`
- `tests/test_do_individual.py` and `tests/test_di_monitor.py` moved to
  `tools/` — they imported removed constants and caused pytest collection
  to abort before any test ran (271 tests now collect clean)

## [0.13.2] - 2026-07-12
### Changed
- Added MQTT/CAN bridge refinements for local broker traffic and runtime stability.
- Updated CAN and MQTT-related configuration handling for improved publish behavior.
- Included a hardware-oriented CAN concurrency test utility for validation.

## [0.13.1] - 2026-07-11
### Changed
- Refined deployment and service startup behavior for production environments.
- Improved runtime reliability around IO bridging, Modbus handling, and watchdog monitoring.
- Updated deployment documentation and monitoring tooling for ongoing validation.

## [0.13.0] - 2026-07-06
### Added
- Watchdog exit-on-timeout: `os._exit(1)` on hung loop so systemd `Restart=on-failure` recovers
- Gunicorn + eventlet production WSGI server (`thread=False` preserves CAN/GPIO threads)
- `StartLimitIntervalSec`/`StartLimitBurst` to prevent infinite restart loops on broken builds
### Changed
- `ExecStart` in systemd unit: `python3 api/app.py` → `gunicorn --worker-class eventlet --workers 1`
- `SocketIO(async_mode="threading")` → `async_mode="eventlet"`
- `_start_runtime()` extracted from `__main__` for gunicorn import-time daemon startup

## [0.12.0] - 2026-07-05
### Added
- CI pipeline (lint, pytest, dashboard build) via GitHub Actions
- Pinned all Python and Node dependencies to exact versions
- Black formatting + flake8 linting (49 files reformatted)
- One-shot `scripts/setup.sh` installer for fresh Purple Pi OH2
- Field-update script with config backup, hash-diff guardrails, health check
- systemd `flemingo.service` template
- `docs/DEPLOYMENT.md` with v0 update procedure and v1 roadmap
- `README.md` with quick-start guide
- `CHANGELOG.md` with backfilled history from 0.5.0

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
