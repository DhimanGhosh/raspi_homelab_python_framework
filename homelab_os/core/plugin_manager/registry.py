from __future__ import annotations

import json
from pathlib import Path


class PluginRegistry:
    def __init__(self, registry_file: Path) -> None:
        self.registry_file = registry_file
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.registry_file.exists():
            self._write({"plugins": {}})

    def _read(self) -> dict:
        return json.loads(self.registry_file.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_all(self) -> dict:
        return self._read().get("plugins", {})

    def get_plugin(self, plugin_id: str) -> dict | None:
        return self._read().get("plugins", {}).get(plugin_id)

    def upsert_plugin(self, plugin_data: dict) -> dict:
        data = self._read()
        plugin_id = plugin_data["id"]
        data.setdefault("plugins", {})
        data["plugins"][plugin_id] = plugin_data
        self._write(data)
        return plugin_data

    def remove_plugin(self, plugin_id: str) -> None:
        data = self._read()
        if plugin_id in data.get("plugins", {}):
            del data["plugins"][plugin_id]
            self._write(data)
