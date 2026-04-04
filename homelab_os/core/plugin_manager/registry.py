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

    def register(self, plugin_id: str, metadata: dict) -> None:
        data = self._read()
        data.setdefault("plugins", {})
        data["plugins"][plugin_id] = metadata
        self._write(data)

    def unregister(self, plugin_id: str) -> None:
        data = self._read()
        data.setdefault("plugins", {})
        data["plugins"].pop(plugin_id, None)
        self._write(data)

    def get(self, plugin_id: str) -> dict | None:
        data = self._read()
        return data.get("plugins", {}).get(plugin_id)

    def list_all(self) -> dict[str, dict]:
        data = self._read()
        return data.get("plugins", {})
