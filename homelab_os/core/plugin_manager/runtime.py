from __future__ import annotations

import json
from pathlib import Path


class PluginRuntime:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def plugin_runtime_dir(self, plugin_id: str) -> Path:
        return self.runtime_root / plugin_id

    def write_runtime_metadata(self, plugin_id: str, metadata: dict) -> Path:
        runtime_dir = self.plugin_runtime_dir(plugin_id)
        runtime_dir.mkdir(parents=True, exist_ok=True)

        runtime_file = runtime_dir / "runtime.json"
        runtime_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return runtime_file

    def read_runtime_metadata(self, plugin_id: str) -> dict | None:
        runtime_file = self.plugin_runtime_dir(plugin_id) / "runtime.json"
        if not runtime_file.exists():
            return None
        return json.loads(runtime_file.read_text(encoding="utf-8"))
