from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AppCatalog:
    path: Path
    apps: dict[str, dict[str, Any]]
    core_stack: list[str]

    def get_app(self, app_id: str) -> dict[str, Any] | None:
        return self.apps.get(app_id)

    def get_name(self, app_id: str, default: str | None = None) -> str | None:
        app = self.get_app(app_id)
        if app:
            return app.get("name", default)
        return default

    def get_public_port(self, app_id: str, default: int | None = None) -> int | None:
        app = self.get_app(app_id)
        if app:
            return app.get("public_port", default)
        return default


def _validate_catalog(payload: dict[str, Any], path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        raise ValueError(f"Invalid app catalog in {path}: 'apps' must be an object keyed by app id")

    validated_apps: dict[str, dict[str, Any]] = {}
    for app_id, meta in apps.items():
        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError(f"Invalid app catalog in {path}: app id must be a non-empty string")
        if not isinstance(meta, dict):
            raise ValueError(f"Invalid app catalog in {path}: app '{app_id}' metadata must be an object")
        validated_apps[app_id] = meta

    core_stack = payload.get("core_stack", [])
    if core_stack is None:
        core_stack = []
    if not isinstance(core_stack, list) or not all(isinstance(item, str) for item in core_stack):
        raise ValueError(f"Invalid app catalog in {path}: 'core_stack' must be a list of app ids")

    return validated_apps, core_stack


@lru_cache(maxsize=8)
def load_app_catalog(path_str: str) -> AppCatalog:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"App catalog not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    apps, core_stack = _validate_catalog(payload, path)
    return AppCatalog(path=path, apps=apps, core_stack=core_stack)
