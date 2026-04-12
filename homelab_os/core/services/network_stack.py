from __future__ import annotations

from pathlib import Path
import subprocess

from homelab_os.core.config import Settings
from homelab_os.core.services.app_catalog import core_stack
from homelab_os.core.services.reverse_proxy import ReverseProxyService


class NetworkStackService:

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.proxy = ReverseProxyService(settings)
        self.default_stack = core_stack(settings)

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)

    def plugin_archive_path(self, plugin_name: str) -> Path:
        return self.settings.build_dir / f"{plugin_name}.tgz"

    def installed_plugin_dir(self, plugin_id: str) -> Path:
        return self.settings.runtime_installed_plugins_dir / plugin_id

    def plugin_internal_port(self, plugin_id: str) -> int | None:
        runtime_json = self.installed_plugin_dir(plugin_id) / "runtime.json"
        if not runtime_json.exists():
            return None
        import json
        payload = json.loads(runtime_json.read_text(encoding="utf-8"))
        return payload.get("network", {}).get("internal_port")

    def ensure_core_route(self) -> str:
        return self.proxy.apply_core_route()

    def ensure_plugin_route(self, plugin_id: str) -> str | None:
        internal_port = self.plugin_internal_port(plugin_id)
        if not internal_port:
            return None
        return self.proxy.apply_plugin_route(plugin_id, internal_port)

    def tailscale_status(self) -> str:
        result = self._run(["tailscale", "status"], check=False)
        return (result.stdout or result.stderr).strip()

    def tailscale_ipv4(self) -> str:
        result = self._run(["tailscale", "ip", "-4"], check=False)
        return (result.stdout or "").strip()

    def reconcile_routes(self, plugin_ids: list[str]) -> dict:
        applied = {}
        for plugin_id in plugin_ids:
            url = self.ensure_plugin_route(plugin_id)
            if url:
                applied[plugin_id] = url
        return applied
