from __future__ import annotations

import json
from pathlib import Path


class PluginValidationError(Exception):
    pass


class PluginValidator:
    REQUIRED_TOP_LEVEL_KEYS = {"id", "name", "version"}

    def validate_plugin_dir(self, plugin_dir: Path) -> dict:
        if not plugin_dir.exists() or not plugin_dir.is_dir():
            raise PluginValidationError(f"Plugin directory does not exist: {plugin_dir}")

        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            raise PluginValidationError(f"plugin.json missing in {plugin_dir}")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginValidationError(f"Invalid plugin.json in {plugin_dir}: {exc}") from exc

        missing = self.REQUIRED_TOP_LEVEL_KEYS - set(manifest.keys())
        if missing:
            raise PluginValidationError(
                f"plugin.json missing required keys {sorted(missing)} in {plugin_dir}"
            )

        self._validate_structure(plugin_dir, manifest)
        return manifest

    def _validate_structure(self, plugin_dir: Path, manifest: dict) -> None:
        has_backend = (plugin_dir / "backend").exists()
        has_frontend = (plugin_dir / "frontend").exists()
        has_docker = (plugin_dir / "docker").exists()

        if not (has_backend or has_frontend or has_docker):
            raise PluginValidationError(
                f"Plugin must contain at least one of backend/, frontend/, or docker/: {plugin_dir}"
            )

        network = manifest.get("network", {})
        if network and "internal_port" in network:
            port = network["internal_port"]
            if not isinstance(port, int):
                raise PluginValidationError(
                    f"network.internal_port must be an integer in {plugin_dir}"
                )
