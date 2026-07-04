#!/usr/bin/env python3
# tests/test_system.py
# Phase 9 — pure-logic tests for backup/restore and system metrics.
# Same pattern as test_validators.py:
#   - no Flask app context needed
#   - no hardware needed
#   - runs with `python3 tests/test_system.py`
#
# Uses temp directories so config/ is never touched.

import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ok(msg):
    print(f"✅ {msg}")


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def must_raise(exc_type, fn, *args, msg="", **kwargs):
    """Assert fn(*args, **kwargs) raises exc_type."""
    try:
        fn(*args, **kwargs)
        fail(f"Expected {exc_type.__name__} but none raised — {msg or fn.__name__}{args}")
    except exc_type:
        pass
    except Exception as e:
        if isinstance(e, exc_type):
            return
        fail(f"Expected {exc_type.__name__}, got {type(e).__name__}: {e} — {msg}")


def must_equal(result, expected, msg=""):
    if result != expected:
        fail(f"Expected {expected!r}, got {result!r} — {msg}")


def must_contains(haystack, needle, msg=""):
    if needle not in haystack:
        fail(f"Expected {haystack!r} to contain {needle!r} — {msg}")


def must_not_contain(haystack, needle, msg=""):
    if needle in haystack:
        fail(f"Expected {haystack!r} to NOT contain {needle!r} — {msg}")


def make_config_dir(base, files=None):
    """
    Create a temp config dir with the given files.
    Returns the dir path.
    """
    cfg = os.path.join(base, "config")
    os.makedirs(cfg, exist_ok=True)
    if files:
        for name, content in files.items():
            with open(os.path.join(cfg, name), "w") as f:
                f.write(content)
    return cfg


def _cram_zip(add_files=None, with_manifest=True):
    """
    Build a zip in memory. add_files is a dict: {arcname: (bytes, zipinfo_size)}.
    If zipinfo_size is None, uses actual data length.
    If with_manifest is True, adds a valid manifest.
    Returns bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if add_files:
            for arcname, payload in add_files.items():
                if isinstance(payload, tuple):
                    data, declared_size = payload
                else:
                    data, declared_size = payload, None
                info = zipfile.ZipInfo(arcname)
                if declared_size is not None:
                    info.file_size = declared_size
                zf.writestr(info, data if isinstance(data, bytes) else data.encode("utf-8"))
        if with_manifest:
            manifest = json.dumps({"version": "0.5.0", "files": [], "device_id": "test", "created_at": "2026-01-01T00:00:00"})
            zf.writestr("backup_manifest.json", manifest)
    buf.seek(0)
    return buf.read()


# ═══════════════════════════════════════════════════════════════════
# Section 1: Backup creation
# ═══════════════════════════════════════════════════════════════════

def test_backup_creates_zip():
    import core.backup_manager as bm
    with tempfile.TemporaryDirectory() as td:
        cfg = make_config_dir(td, {
            "reliability.yaml": "circuit_breaker: {can: {failure_threshold: 3}}",
            "mqtt.yaml":         "broker: {host: 127.0.0.1}",
            "users.json":        '{"admin": {"role": "admin"}}',
        })
        bm.CONFIG_DIR = cfg

        buf = bm.create_backup(version="0.5.0", device_id="test-pi")
        assert zipfile.is_zipfile(buf), "create_backup should return a valid zip"

        zf = zipfile.ZipFile(buf)
        names = zf.namelist()
        must_contains(names, "backup_manifest.json", "manifest in zip")
        must_contains(names, "reliability.yaml",     "reliability.yaml in zip")
        must_contains(names, "mqtt.yaml",             "mqtt.yaml in zip")
        must_contains(names, "users.json",            "users.json in zip")
        zf.close()
        ok("test_backup_creates_zip")


def test_backup_manifest_fields():
    import core.backup_manager as bm
    with tempfile.TemporaryDirectory() as td:
        cfg = make_config_dir(td, {"reliability.yaml": "x: 1"})
        bm.CONFIG_DIR = cfg

        buf = bm.create_backup(version="9.9.9", device_id="unit-99")
        zf = zipfile.ZipFile(buf)
        manifest = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
        zf.close()

        must_equal(manifest["version"],   "9.9.9")
        must_equal(manifest["device_id"], "unit-99")
        must_contains(manifest, "created_at")
        must_contains(manifest, "files")
        ok("test_backup_manifest_fields")


def test_backup_excludes_secrets():
    import core.backup_manager as bm
    with tempfile.TemporaryDirectory() as td:
        cfg = make_config_dir(td, {
            "reliability.yaml": "x: 1",
            "jwt_secret.key":   "SHOULD-NOT-BE-HERE",
        })
        bm.CONFIG_DIR = cfg

        buf = bm.create_backup()
        zf = zipfile.ZipFile(buf)
        names = zf.namelist()
        zf.close()

        must_contains(names, "reliability.yaml", "reliability.yaml included")
        must_not_contain(names, "jwt_secret.key", "jwt_secret.key excluded")
        ok("test_backup_excludes_secrets")


# ═══════════════════════════════════════════════════════════════════
# Section 2: Restore
# ═══════════════════════════════════════════════════════════════════

def test_restore_round_trip():
    import core.backup_manager as bm
    content = "circuit_breaker:\n  can:\n    failure_threshold: 5\n"
    with tempfile.TemporaryDirectory() as td:
        cfg_src = make_config_dir(td, {"reliability.yaml": content})
        bm.CONFIG_DIR = cfg_src

        buf = bm.create_backup()
        zip_bytes = buf.read()

    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "restored")
        os.makedirs(target)

        restored = bm.restore_backup(zip_bytes, target_dir=target)
        must_contains(restored, "reliability.yaml", "reliability.yaml restored")

        with open(os.path.join(target, "reliability.yaml"), "r") as f:
            restored_content = f.read()
        must_equal(restored_content, content, "restored file content matches original")

        ok("test_restore_round_trip")


def test_restore_rejects_path_traversal():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({
        "../../etc/passwd": "root:x:0:0:root:/root:/bin/bash",
    })
    must_raise(ValueError, bm.restore_backup, zip_bytes, target_dir="/tmp",
               msg="path traversal should raise ValueError")
    ok("test_restore_rejects_path_traversal")


def test_restore_rejects_missing_manifest():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({"reliability.yaml": "x: 1"}, with_manifest=False)
    must_raise(ValueError, bm.restore_backup, zip_bytes, target_dir="/tmp",
               msg="missing manifest should raise ValueError")
    ok("test_restore_rejects_missing_manifest")


def test_restore_rejects_zip_bomb():
    import core.backup_manager as bm

    original_max = bm._MAX_BYTES
    try:
        bm._MAX_BYTES = 50
        zip_bytes = _cram_zip({"reliability.yaml": "x: 1"}, with_manifest=True)
        must_raise(ValueError, bm.restore_backup, zip_bytes, target_dir="/tmp",
                   msg="zip exceeding threshold should raise ValueError")
    finally:
        bm._MAX_BYTES = original_max
    ok("test_restore_rejects_zip_bomb")


def test_restore_rejects_bad_yaml():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({
        "reliability.yaml": "} this is not: valid: yaml: [:",
    })
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "dst")
        os.makedirs(target)
        must_raise(ValueError, bm.restore_backup, zip_bytes, target_dir=target,
                   msg="bad yaml should raise ValueError")

        yaml_dest = os.path.join(target, "reliability.yaml")
        must_equal(os.path.exists(yaml_dest), False,
                   "no file written for bad yaml")
        ok("test_restore_rejects_bad_yaml")


def test_restore_whitelist():
    import core.backup_manager as bm
    yaml_content = "circuit_breaker: {can: {failure_threshold: 1}}"
    zip_bytes = _cram_zip({
        "reliability.yaml": yaml_content,
        "evil.sh":          "#!/bin/bash\nrm -rf /\n",
    })
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "dst")
        os.makedirs(target)
        restored = bm.restore_backup(zip_bytes, target_dir=target)

        must_contains(restored, "reliability.yaml", "whitelisted file restored")
        must_not_contain(restored, "evil.sh", "non-whitelisted file not restored")

        must_equal(os.path.exists(os.path.join(target, "reliability.yaml")), True,
                   "whitelisted file written to disk")
        must_equal(os.path.exists(os.path.join(target, "evil.sh")), False,
                   "non-whitelisted file NOT written to disk")
        ok("test_restore_whitelist")


# ═══════════════════════════════════════════════════════════════════
# Section 3: System metrics
# ═══════════════════════════════════════════════════════════════════

def test_metrics_shape():
    from core.system_metrics import collect_metrics

    result = collect_metrics(mqtt_manager=None)

    required_keys = [
        "cpu_percent", "load_average", "memory", "disk",
        "temperature_c", "network", "process", "uptime_seconds", "mqtt",
    ]
    for key in required_keys:
        must_contains(result, key, f"metrics dict has key '{key}'")

    if result.get("memory") is not None:
        mem = result["memory"]
        for k in ("total", "used", "available", "percent"):
            must_contains(mem, k, f"memory dict has key '{k}'")

    if result.get("load_average") is not None:
        la = result["load_average"]
        for k in ("1min", "5min", "15min"):
            must_contains(la, k, f"load_average dict has key '{k}'")

    must_equal(result["mqtt"], None, "mqtt is None when no manager provided")
    ok("test_metrics_shape")


def test_temperature_graceful():
    from core.system_metrics import get_temperature

    t = get_temperature()
    if t is None:
        pass
    elif isinstance(t, (int, float)):
        pass
    else:
        fail(f"get_temperature() returned {type(t).__name__}, expected float or None")

    ok("test_temperature_graceful")


# ═══════════════════════════════════════════════════════════════════
# Section 4: Validators
# ═══════════════════════════════════════════════════════════════════

class FakeFileObj:
    def __init__(self, data):
        self._data = data
        self.pos = 0

    def read(self, size=-1):
        if self.pos >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self.pos
        chunk = self._data[self.pos:self.pos + size]
        self.pos += size
        return chunk


def test_validate_backup_file():
    from api.validators import ValidationError, validate_backup_file

    valid = validate_backup_file(FakeFileObj(b"PK\x03\x04hello world"))
    must_equal(len(valid), 15, "valid file returns bytes")

    must_raise(ValidationError, validate_backup_file, FakeFileObj(b""),
               msg="empty file rejected")

    big_data = b"x" * (10 * 1024 * 1024 + 1)
    must_raise(ValidationError, validate_backup_file, FakeFileObj(big_data),
               msg="oversized file rejected")
    ok("test_validate_backup_file")


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    test_backup_creates_zip()
    test_backup_manifest_fields()
    test_backup_excludes_secrets()

    test_restore_round_trip()
    test_restore_rejects_path_traversal()
    test_restore_rejects_missing_manifest()
    test_restore_rejects_zip_bomb()
    test_restore_rejects_bad_yaml()
    test_restore_whitelist()

    test_metrics_shape()
    test_temperature_graceful()

    test_validate_backup_file()

    print("\nAll system tests passed.")
