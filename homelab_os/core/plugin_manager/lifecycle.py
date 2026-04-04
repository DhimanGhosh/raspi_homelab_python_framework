from __future__ import annotations

from pathlib import Path


class PluginLifecycle:
    def install_marker(self, plugin_runtime_dir: Path) -> Path:
        marker = plugin_runtime_dir / ".installed"
        marker.write_text("installed\n", encoding="utf-8")
        return marker

    def enable_marker(self, plugin_runtime_dir: Path) -> Path:
        marker = plugin_runtime_dir / ".enabled"
        marker.write_text("enabled\n", encoding="utf-8")
        return marker

    def disable(self, plugin_runtime_dir: Path) -> None:
        marker = plugin_runtime_dir / ".enabled"
        if marker.exists():
            marker.unlink()

    def is_enabled(self, plugin_runtime_dir: Path) -> bool:
        return (plugin_runtime_dir / ".enabled").exists()
