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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
        assert "backup_manifest.json" in names, "manifest in zip"
        assert "reliability.yaml" in names,     "reliability.yaml in zip"
        assert "mqtt.yaml" in names,             "mqtt.yaml in zip"
        assert "users.json" in names,            "users.json in zip"
        zf.close()


def test_backup_manifest_fields():
    import core.backup_manager as bm
    with tempfile.TemporaryDirectory() as td:
        cfg = make_config_dir(td, {"reliability.yaml": "x: 1"})
        bm.CONFIG_DIR = cfg

        buf = bm.create_backup(version="9.9.9", device_id="unit-99")
        zf = zipfile.ZipFile(buf)
        manifest = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
        zf.close()

        assert manifest["version"] == "9.9.9"
        assert manifest["device_id"] == "unit-99"
        assert "created_at" in manifest
        assert "files" in manifest


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

        assert "reliability.yaml" in names, "reliability.yaml included"
        assert "jwt_secret.key" not in names, "jwt_secret.key excluded"


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
        assert "reliability.yaml" in restored, "reliability.yaml restored"

        with open(os.path.join(target, "reliability.yaml"), "r") as f:
            restored_content = f.read()
        assert restored_content == content, "restored file content matches original"


def test_restore_rejects_path_traversal():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({
        "../../etc/passwd": "root:x:0:0:root:/root:/bin/bash",
    })
    with pytest.raises(ValueError):
        bm.restore_backup(zip_bytes, target_dir="/tmp")


def test_restore_rejects_missing_manifest():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({"reliability.yaml": "x: 1"}, with_manifest=False)
    with pytest.raises(ValueError):
        bm.restore_backup(zip_bytes, target_dir="/tmp")


def test_restore_rejects_zip_bomb():
    import core.backup_manager as bm

    original_max = bm._MAX_BYTES
    try:
        bm._MAX_BYTES = 50
        zip_bytes = _cram_zip({"reliability.yaml": "x: 1"}, with_manifest=True)
        with pytest.raises(ValueError):
            bm.restore_backup(zip_bytes, target_dir="/tmp")
    finally:
        bm._MAX_BYTES = original_max


def test_restore_rejects_bad_yaml():
    import core.backup_manager as bm
    zip_bytes = _cram_zip({
        "reliability.yaml": "} this is not: valid: yaml: [:",
    })
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "dst")
        os.makedirs(target)
        with pytest.raises(ValueError):
            bm.restore_backup(zip_bytes, target_dir=target)

        yaml_dest = os.path.join(target, "reliability.yaml")
        assert not os.path.exists(yaml_dest), "no file written for bad yaml"


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

        assert "reliability.yaml" in restored, "whitelisted file restored"
        assert "evil.sh" not in restored, "non-whitelisted file not restored"

        assert os.path.exists(os.path.join(target, "reliability.yaml")), \
            "whitelisted file written to disk"
        assert not os.path.exists(os.path.join(target, "evil.sh")), \
            "non-whitelisted file NOT written to disk"


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
        assert key in result, f"metrics dict has key '{key}'"

    if result.get("memory") is not None:
        mem = result["memory"]
        for k in ("total", "used", "available", "percent"):
            assert k in mem, f"memory dict has key '{k}'"

    if result.get("load_average") is not None:
        la = result["load_average"]
        for k in ("1min", "5min", "15min"):
            assert k in la, f"load_average dict has key '{k}'"

    assert result["mqtt"] is None, "mqtt is None when no manager provided"


def test_temperature_graceful():
    from core.system_metrics import get_temperature

    t = get_temperature()
    assert t is None or isinstance(t, (int, float)), \
        f"get_temperature() returned {type(t).__name__}, expected float or None"


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
    assert len(valid) == 15, "valid file returns bytes"

    with pytest.raises(ValidationError):
        validate_backup_file(FakeFileObj(b""))

    big_data = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError):
        validate_backup_file(FakeFileObj(big_data))
