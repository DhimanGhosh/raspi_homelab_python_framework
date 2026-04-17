from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def prepare_install_target(installed_plugins_root: str | Path, plugin_id: str, archive_version: str | None = None) -> dict[str, Any]:
    """
    Ensures plugin install target is in a clean state before extraction/copy.
    If an old directory exists from a failed install, it is moved aside first.
    """
    root = Path(installed_plugins_root)
    target = root / plugin_id
    backup = None

    if target.exists():
        backup = root / f"_{plugin_id}_stale"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        target.rename(backup)

    return {
        "ok": True,
        "target": str(target),
        "backup": str(backup) if backup else None,
        "plugin_id": plugin_id,
        "archive_version": archive_version,
    }


def cleanup_stale_backup(installed_plugins_root: str | Path, plugin_id: str) -> dict[str, Any]:
    root = Path(installed_plugins_root)
    backup = root / f"_{plugin_id}_stale"
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
        return {"ok": True, "removed": str(backup)}
    return {"ok": True, "removed": None}
