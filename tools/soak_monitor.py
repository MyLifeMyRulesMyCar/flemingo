#!/usr/bin/env python3
# tools/soak_monitor.py
# 48-72hr soak test monitor for Flemingo on real Purple Pi OH2 hardware.
#
# Polls health endpoints + process metrics every 60s, writes CSV to
# stdout (redirect to a file). Designed to run alongside the deployed
# service during sustained hardware validation.
#
# Usage:
#   nohup python3 tools/soak_monitor.py > soak_$(date +%Y%m%d).csv 2>&1 &
#
# CSV columns:
#   timestamp, rss_mb, threads, health_ms, health_code,
#   wd_alive, can_ok, modbus_ok, unhealthy_components, notes

import csv
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil
import requests

BASE = os.getenv("SOAK_BASE_URL", "http://127.0.0.1:5000")
INTERVAL = int(os.getenv("SOAK_INTERVAL", "60"))
TOKEN = os.getenv("SOAK_TOKEN", "")
REFRESH_TOKEN = os.getenv("SOAK_REFRESH_TOKEN", "")
_last_token_refresh = time.time()

HEADER_CSV = [
    "timestamp",
    "rss_mb",
    "threads",
    "health_ms",
    "health_code",
    "wd_alive",
    "can_ok",
    "modbus_ok",
    "unhealthy_components",
    "notes",
]
writer = csv.writer(sys.stdout)
writer.writerow(HEADER_CSV)
sys.stdout.flush()


def find_flemingo_pid():
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"]) if proc.info["cmdline"] else ""
            if "api/app.py" in cmd or "api.app:app" in cmd:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def log(msg):
    ts = datetime.now().isoformat()
    sys.stderr.write(f"[{ts}] {msg}\n")
    sys.stderr.flush()


def maybe_refresh_token():
    global TOKEN, _last_token_refresh
    if not REFRESH_TOKEN:
        return
    elapsed = time.time() - _last_token_refresh
    if elapsed < 1500:  # 25 minutes
        return
    try:
        resp = requests.post(
            f"{BASE}/api/auth/refresh",
            json={"refresh_token": REFRESH_TOKEN},
            timeout=10,
        )
        if resp.status_code == 200:
            TOKEN = resp.json().get("access_token", "")
            _last_token_refresh = time.time()
            log("token refreshed")
        else:
            log(f"token refresh failed: {resp.status_code}")
    except requests.RequestException as e:
        log(f"token refresh error: {e}")


log("soak_monitor started")
log(f"  base={BASE}  interval={INTERVAL}s  token={'yes' if TOKEN else 'no'}")

while True:
    maybe_refresh_token()
    row = {
        "timestamp": datetime.now().isoformat(),
        "rss_mb": "",
        "threads": "",
        "health_ms": "",
        "health_code": "",
        "wd_alive": "",
        "can_ok": "",
        "modbus_ok": "",
        "unhealthy_components": "",
        "notes": "",
    }
    notes = []

    pid = find_flemingo_pid()
    if pid is None:
        notes.append("process not found")
    else:
        try:
            proc = psutil.Process(pid)
            row["rss_mb"] = round(proc.memory_info().rss / (1024 * 1024), 2)
            row["threads"] = proc.num_threads()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            notes.append(f"psutil error: {e}")

    t0 = time.time()
    try:
        resp = requests.get(f"{BASE}/api/health", timeout=10)
        row["health_ms"] = round((time.time() - t0) * 1000, 1)
        row["health_code"] = resp.status_code
    except requests.RequestException as e:
        row["health_ms"] = round((time.time() - t0) * 1000, 1)
        notes.append(f"health error: {e}")

    if TOKEN:
        try:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            resp = requests.get(
                f"{BASE}/api/health/detailed", headers=headers, timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                wd = data.get("watchdog", {})
                row["wd_alive"] = "yes" if wd.get("status") == "healthy" else "no"
                row["can_ok"] = "yes" if data.get("can", {}).get("connected") else "no"
                row["modbus_ok"] = (
                    "yes"
                    if data.get("modbus", {}).get("connected_count", 0) > 0
                    else "no"
                )
                unhealthy = []
                for name, comp in wd.get("components", {}).items():
                    if comp.get("status") != "healthy":
                        unhealthy.append(
                            f"{name}={comp.get('status')}"
                            f"(f{comp.get('failures', 0)})"
                        )
                row["unhealthy_components"] = "; ".join(unhealthy) if unhealthy else ""
            else:
                notes.append(f"detailed health returned {resp.status_code}")
        except requests.RequestException as e:
            notes.append(f"detailed health error: {e}")

    row["notes"] = "; ".join(notes)
    writer.writerow(list(row.values()))
    sys.stdout.flush()

    time.sleep(INTERVAL)
