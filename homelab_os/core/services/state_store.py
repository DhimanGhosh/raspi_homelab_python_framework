from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self._write({"plugins": {}})

    def _read(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_all_plugin_states(self) -> dict:
        return self._read().get("plugins", {})

    def get_plugin_state(self, plugin_id: str) -> dict | None:
        return self._read().get("plugins", {}).get(plugin_id)

    def update_plugin_state(self, plugin_id: str, updates: dict) -> dict:
        data = self._read()
        data.setdefault("plugins", {})
        plugin_state = data["plugins"].get(plugin_id, {})
        plugin_state.update(updates)
        data["plugins"][plugin_id] = plugin_state
        self._write(data)
        return plugin_state

    def remove_plugin_state(self, plugin_id: str) -> None:
        data = self._read()
        plugins = data.setdefault("plugins", {})
        if plugin_id in plugins:
            del plugins[plugin_id]
            self._write(data)
