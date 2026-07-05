#!/usr/bin/env python3
# core/backup_manager.py
# Phase 9 — configuration backup and restore.
#
# Backup: collects listed config files + a manifest into an in-memory zip.
# Restore: validates the zip end-to-end BEFORE writing a single byte to
# disk. Rejects path traversal, zip bombs, corrupt YAML/JSON, and missing
# manifests. Only whitelisted files are extracted.
#
# Usage (from routes):
#   buf = create_backup(version=VERSION, device_id="edge-01")
#   return send_file(buf, mimetype="application/zip", ...)
#
#   restored = restore_backup(raw_zip_bytes)
#   # → ["reliability.yaml", "mqtt.yaml", "users.json"]

import io
import json
import logging
import os
import zipfile
from datetime import datetime

from core.config import VERSION

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")

BACKUP_FILES = ["reliability.yaml", "mqtt.yaml", "users.json"]
EXCLUDED = {"jwt_secret.key"}

MAX_UNCOMPRESSED_MB = 10
_MAX_BYTES = MAX_UNCOMPRESSED_MB * 1024 * 1024


# ============================================================
# Backup
# ============================================================
def create_backup(version: str = VERSION, device_id: str = None) -> io.BytesIO:
    """
    Collect listed config files and a manifest into an in-memory zip.
    Missing config files are silently skipped.

    Returns a BytesIO positioned at offset 0, ready for Flask's send_file().
    """
    if device_id is None:
        import socket

        device_id = socket.gethostname()

    manifest = {
        "version": version,
        "created_at": datetime.now().isoformat(),
        "device_id": device_id,
        "files": [],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in BACKUP_FILES:
            full_path = os.path.join(CONFIG_DIR, filename)
            if os.path.isfile(full_path):
                zf.write(full_path, arcname=filename)
                manifest["files"].append(filename)

        zf.writestr("backup_manifest.json", json.dumps(manifest, indent=2))
        manifest["files"].append("backup_manifest.json")

    buf.seek(0)
    logger.info(
        f"Backup created: version={version}, device_id={device_id}, "
        f"files={manifest['files']}"
    )
    return buf


# ============================================================
# Restore
# ============================================================
def restore_backup(
    zip_bytes: bytes,
    target_dir: str = None,
    version: str = None,
    strict_version: bool = False,
) -> list:
    """
    Validate and restore configuration from a backup zip.

    Validation gates (in order — first failure aborts, nothing written):
      1. Must be a valid zip file.
      2. Total uncompressed size must be ≤ MAX_UNCOMPRESSED_MB.
      3. backup_manifest.json must be present and valid JSON.
      4. No file in the zip may contain path-traversal segments (.. or /).
      5. Only whitelisted files (BACKUP_FILES) are considered for restore.
      6. Each whitelisted file must parse cleanly (YAML or JSON).

    Returns the list of basenames that were successfully written to disk.
    """
    target_dir = target_dir or CONFIG_DIR
    restored = []

    # ── 1. Validate zip ──────────────────────────────────────────────
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except (zipfile.BadZipFile, Exception) as e:
        raise ValueError(f"Invalid or corrupt zip file: {e}") from e

    with zf:
        # ── 2. Zip bomb check ───────────────────────────────────────
        total_size = sum(info.file_size for info in zf.infolist())
        if total_size > _MAX_BYTES:
            raise ValueError(
                f"Backup zip uncompressed size ({total_size / 1024 / 1024:.1f} MB) "
                f"exceeds {MAX_UNCOMPRESSED_MB} MB limit"
            )

        # ── 3. Manifest check ───────────────────────────────────────
        try:
            manifest_raw = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except KeyError:
            raise ValueError("Backup zip is missing backup_manifest.json")
        except json.JSONDecodeError as e:
            raise ValueError(f"backup_manifest.json is not valid JSON: {e}") from e

        if not isinstance(manifest, dict) or "files" not in manifest:
            raise ValueError("backup_manifest.json must be a dict with a 'files' key")

        backup_version = manifest.get("version", "unknown")
        if strict_version and backup_version != version:
            raise ValueError(
                f"Backup version ({backup_version}) does not match "
                f"expected version ({version})"
            )
        logger.info(
            f"Restoring backup: version={backup_version}, "
            f"device_id={manifest.get('device_id', '?')}"
        )

        # ── 4. Path traversal check ─────────────────────────────────
        for info in zf.infolist():
            name = info.filename
            if os.path.isabs(name):
                raise ValueError(f"Rejecting absolute path in zip: {name!r}")
            segments = name.replace("\\", "/").split("/")
            if ".." in segments:
                raise ValueError(f"Rejecting path-traversal in zip: {name!r}")

        # ── 5/6. Validate each whitelisted file before writing ──────
        candidates = {}
        for filename in BACKUP_FILES:
            try:
                content = zf.read(filename)
            except KeyError:
                continue

            # Parse-validation before writing anything
            if filename.endswith(".yaml"):
                try:
                    import yaml  # noqa: F811

                    yaml.safe_load(content.decode("utf-8"))
                except Exception as e:
                    raise ValueError(
                        f"Config file '{filename}' is not valid YAML: {e}"
                    ) from e
            elif filename.endswith(".json"):
                try:
                    json.loads(content.decode("utf-8"))
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Config file '{filename}' is not valid JSON: {e}"
                    ) from e

            candidates[filename] = content

        # ── 7. Write to disk (only now, all checks passed) ──────────
        os.makedirs(target_dir, exist_ok=True)

        for filename in BACKUP_FILES:
            content = candidates.get(filename)
            if content is None:
                continue

            dest = os.path.join(target_dir, filename)
            with open(dest, "wb") as f:
                f.write(content)
            restored.append(filename)
            logger.info(f"Restored: {dest}")

    return restored
