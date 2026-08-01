# Flemingo — Codebase Conventions

Read this before making changes. It captures patterns established
across many review rounds — following them avoids re-deriving things
that are already decided, and avoids reintroducing bugs that were
already found and fixed once.

## Architecture at a glance

- `core/` — hardware managers and business logic (`io_manager.py`,
  `can_manager.py`, `modbus_manager.py`, `modbus_tcp_server.py`,
  `network_config.py`, `watchdog.py`, `resilience.py`, `config.py`).
  No Flask imports here — these are usable standalone.
- `api/` — Flask blueprints, one file per resource
  (`*_routes.py`), plus `auth_decorators.py`, `validators.py`.
- `daemon/daemon.py` — the long-running process: owns the manager
  instances, the poll loop, watchdog registration.
- `dashboard/src/` — React/Vite. `pages/` = one file per nav tab,
  `components/` = shared widgets (`ConfirmModal.jsx`, `Toast.jsx`,
  `StatusLed.jsx`, `MetricCard.jsx`) — check here before writing a new
  one, most UI needs are already covered.
- `config/*.yaml` / `config/*.json` — externalized, per-deployment
  settings. `.yaml` files are hand-edited (`reliability.yaml`,
  `hardware.yaml`); `.json` files are UI-managed and gitignored
  (`modbus_tcp_map.json`, `network_backup.json`,
  `can_send_channels.json`).

## Patterns to reuse, not reinvent

**New Flask blueprint** — copy the shape of an existing `*_routes.py`,
not from scratch:
```python
some_api = Blueprint("some_api", __name__)
_some_manager = None
def set_some_manager(mgr):
    global _some_manager
    _some_manager = mgr

@some_api.route("/api/some/thing", methods=["GET"])
@require_role("viewer")
def get_thing(): ...
```
Full path baked into the decorator (`/api/some/thing`, not a
blueprint-level `url_prefix`). Wire into `api/app.py`: import, call
`set_some_manager(instance)`, `app.register_blueprint(some_api)` —
three lines, same order as every other manager.

**Role tiers**: `viewer` (read), `operator` (start/stop, write
values), `admin` (config changes, device/channel CRUD, destructive
ops). When in doubt, check what a structurally similar existing route
uses rather than guessing.

**Persisted, user-editable config** (register maps, channels, network
backup) — atomic write, always:
```python
tmp_path = path + ".tmp"
with open(tmp_path, "w") as f:
    json.dump(data, f, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp_path, path)
```
Reference implementation: `core/modbus_tcp_register_map.py`'s
`save_register_map()`.

**Hand-edited YAML config** — load/merge/cache pattern in
`core/config.py`, `_deep_merge()` + a `DEFAULTS` dict + a module-level
cache. **Two different failure philosophies depending on stakes:**
tuning values (`reliability.yaml`) → missing or malformed file falls
back to defaults *silently*, never blocks startup. Physical/wiring
values (`hardware.yaml`) → missing file falls back silently (same
board, nothing changes), but a *malformed* file must raise and block
startup — a wrong GPIO line mapping is not a "use the default and
move on" situation. Don't default new config types to the low-stakes
behavior without checking which category they're actually in.

**Blocking I/O inside the async Modbus TCP server** — never call a
blocking library (RS485 via `minimalmodbus`/`pyserial`, SPI via
`gpiod`, `can_manager.send_message()`) directly from inside
`core/modbus_tcp_server.py`'s `handle()` coroutine. It's a single
event loop; a blocking call there stalls every connected client, not
just the one that triggered it. Always
`await loop.run_in_executor(None, blocking_fn, ...)`.

**Modbus exception codes** — `0x02` (Illegal Data Address) means "not
mapped, or mapped but not writable" — a configuration problem, known
at request time without touching hardware. `0x0A` (Gateway Target
Device Failed to Respond) means "mapped and permitted, but the real
RTU/CAN hardware didn't respond." Keep these distinct — collapsing
them makes a customer's own misconfiguration look like a device fault.
A multi-register write (FC16) spanning one valid and one invalid
address must reject the *whole* request — Modbus has no partial-success
signaling within one PDU.

**Watchdog registration** — `watchdog.register_component(name,
health_check)` where `health_check` takes no args, returns bool,
exceptions inside it count as unhealthy automatically. Don't write a
health check that unconditionally returns `True` — that was a real gap
in this codebase for a while (`_check_modbus_tcp_health`) and provided
zero actual monitoring until it checked something real (carrier
state). If a component's registered but there's nothing meaningful to
check yet, that's a signal to either find something meaningful or hold
off registering it.

**Frontend pages** — one card per logical concern, not one monolithic
page. `MQTT.jsx` is the reference: separate cards for
connection-level config vs. each functional bridge, each with its own
status LED, Start/Stop, config fields (disabled while running), stats
footer. `ModbusTCP.jsx` follows the same shape now
(`ServerCard`/`NetworkCard`/`RegisterMapCard`/`CANSendChannelsCard`).
New pages should match this rhythm, not introduce a different one.

**Before adding a new UI picker/selector** — check whether a similar
one already exists to copy. The RTU device dropdown and CAN ID chip
picker in `ModbusTCP.jsx` were built by lifting `MQTT.jsx`'s existing
device-picker pattern nearly verbatim, not designed fresh.

## Testing standard

**A regression test must call the real code path, not reimplement the
expected output.** This bit once already: an early "FC15 regression
test" hand-built its own expected bytes and never actually invoked the
buggy function — reintroducing the exact original bug still passed
that test. Standard now: for any bug fix, before considering it done,
temporarily reintroduce the bug and confirm the new test *fails*. If it
doesn't fail, the test isn't testing the fix.

**Extract inline logic before testing it.** Complex logic living
inside a closure (e.g. deep in an `async def handle()`) can't be unit
tested directly. Pull it into a standalone function first
(`_parse_fc15_write`, `_resolve_bind_host`, `_check_rtu_devices_exist`
are the precedents), then test the function, not the whole request
cycle.

**Hardware-adjacent code gets mocked at the boundary, not skipped.**
`subprocess.run`, `gpiod`, `minimalmodbus`/`pyserial` calls get
patched in tests — the surrounding logic (parsing, validation,
locking, error translation) is real Python and should be exercised for
real. Tests requiring actual physical hardware get the `hardware`
pytest marker so `-m "not hardware"` excludes them in CI — but marker
exclusion only works if the module *imports* cleanly; a broken import
aborts collection regardless of markers (this happened once — see
CHANGELOG around the hardware-config externalization).

**`_run()`-style OS command wrappers must translate every failure mode
into one exception type**, not just check the common case. A missing
binary is `FileNotFoundError`, a hang is `subprocess.TimeoutExpired`,
neither is the `NetworkConfigError` every caller actually catches —
this was a real bug (`_resolve_bind_host` 500ing instead of falling
back). Fix failure translation once, at the wrapper, not per caller.

## Security boundaries actually enforced, don't weaken these

- `core/network_config.py`'s `_assert_not_management_iface` — this
  module must never be reachable against `eth0` (management network),
  only `eth1`.
- `scripts/flemingo-net-apply` is the actual privilege boundary for
  network changes, not the Python code that calls it — it re-derives
  "the connection currently on eth1" itself rather than trusting a
  caller-supplied connection name, and validates its own inputs
  (octet ranges, not just digit-count regex) rather than trusting the
  Python layer already did.
- First-boot admin credentials are randomly generated
  (`core/auth_manager.py`'s `_bootstrap_default_admin`), logged once,
  unrecoverable — `must_change_password` blocks every route except
  `/me` and `/change-password` until cleared. Don't add a path that
  bypasses this check.

## Where things actually are (avoid re-discovering)

- GPIO chip/line map: `core/config.py`'s `HARDWARE_DEFAULTS`, override
  via `config/hardware.yaml` (see `.example` for the template).
- CAN bitrate/crystal: same file, same mechanism.
- Modbus TCP register map source types: `"local"` (DI/DO/CAN status,
  string `source_key`), `"modbus_rtu"` (structured `rtu_device_id`/
  `rtu_address`/`writable` fields) — don't add a third stringly-typed
  scheme for a new source type, follow the structured-fields pattern.
- CAN send channels are a distinct concept from register-map entries
  — a named group of 6 registers + 1 coil (`core/can_send_channel.py`),
  not individual mappable rows. The two modules cross-validate against
  each other for address overlap; if you add a third "occupies
  addresses" concept, it needs to check against both existing ones,
  not just be checked against.

## Still open, check current status before assuming either way

TLS-enabled-by-default, EMC pre-compliance lab scheduling, OpenAPI/
Postman reference, hardware-in-the-loop bench rig. Don't assume these
are done or not done — confirm.