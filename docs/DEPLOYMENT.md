# Deployment

Flemingo runs as a single systemd service on the Purple Pi OH2.
Flask, SocketIO, the daemon, and MQTT bridges all live in one process —
no separate worker containers or supervisor programs needed.

## Fresh install

```bash
sudo bash scripts/setup.sh
```

This installs system deps, creates a Python venv, installs pinned dependencies,
builds the dashboard, and enables the `flemingo` systemd service. Safe to re-run —
every step is idempotent.

After setup, the dashboard is available at `http://<device-ip>:5000`.
Optionally install the nginx TLS reverse proxy:

```bash
sudo bash scripts/install_nginx.sh
```

## Field update (v0)

```bash
sudo bash scripts/update.sh              # latest master
sudo bash scripts/update.sh v0.12.0      # specific tagged release
```

The update script has three guardrails:

- **Config backup.** `config/` is copied to `/var/backups/flemingo/<timestamp>/`
  before anything is touched.
- **Skip unnecessary work.** Python deps are only reinstalled if `requirements.txt`
  changed. The dashboard is only rebuilt if `package-lock.json` changed.
- **Health check.** After restart, `curl http://localhost:5000/api/health` verifies
  Flask is responding. On failure the script prints explicit rollback instructions
  and exits with a non-zero code.

### Rollback

If the health check fails, the script prints the exact commands to recover.
The general procedure is:

```bash
cd <repo-root>
git checkout <previous-ref>          # the ref from before the update
systemctl restart flemingo
```

Your config backup is at the path shown in the failed update output.

## Updating dashboard assets

Dashboard changes (JSX, CSS) don't require a Flask restart — only a rebuild:

```bash
cd dashboard
npm run build
```

Flask serves `dashboard/dist/` directly from disk on every request, so new
assets take effect immediately.

## Service management

```bash
systemctl status flemingo            # check the service
journalctl -u flemingo -f            # follow live logs
systemctl restart flemingo           # restart (e.g. after api/app.py changes)
```

The service runs as `Type=simple` — `api/app.py` stays in the foreground and
systemd treats it as the main process. On stop/restart the existing signal
handler performs a graceful shutdown (daemon stop, CAN/Modbus disconnect,
MQTT bridge shutdown).

## Production WSGI server (gunicorn + gthread)

The systemd service runs under gunicorn with a single gthread worker.
Gthread spawns one process with a thread pool — each connection gets its
own OS thread, so the broadcast thread's `socketio.emit()` calls work
correctly across thread boundaries (no greenthread queue conflicts).

The `FLEMINGO_EVENTLET=0` environment variable disables the eventlet
monkey-patch at the top of `api/app.py` since gthread doesn't need it.

Single worker required: module-level singletons (CAN, Modbus, GPIO, auth)
would conflict across multiple worker processes sharing the same hardware.

## Roadmap — v1 deployment story

The current v0 model uses in-place `git checkout` for updates. This works during
development but has two limitations once shipping to customers:

1. **Git required on the device.** Every unit needs `git` installed and network
   access to the upstream repository.
2. **No atomic rollback.** `git checkout <ref>` mutates the live working tree — if
   the checkout fails partway through, the device is left in an unpredictable state.

The natural next step is a **releases/ symlink layout**:

```
releases/
├── v1.0.0/
│   ├── api/
│   ├── core/
│   ├── dashboard/dist/
│   └── ...
├── v1.1.0/
└── current → v1.0.0
```

The systemd unit points at `releases/current/api/app.py`. An update extracts a
new version into its own directory, then atomically re-points the symlink.
Rollback is a single symlink change — no files touched, no half-state.

Further out, **signed release tarballs** eliminate the git-on-device requirement
entirely. A customer downloads `flemingo-v1.2.0.tar.gz` + `.sig` from a release
server, verifies the signature, and extracts it into `releases/` — no git clone,
no git fetch, no build tools on the device at all.

These are design notes for when they're actually needed, not implementation targets
for today.
