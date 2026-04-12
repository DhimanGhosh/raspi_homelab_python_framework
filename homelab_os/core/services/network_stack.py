from __future__ import annotations

from pathlib import Path
import json
import subprocess

from homelab_os.core.config import Settings
from homelab_os.core.services.app_catalog import load_app_catalog
from homelab_os.core.services.reverse_proxy import ReverseProxyService
from homelab_os.core.plugin_manager.registry import PluginRegistry


class NetworkStackService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = load_app_catalog(str(settings.app_catalog_file))
        self.proxy = ReverseProxyService(settings)
        self.registry = PluginRegistry(settings.manifests_dir / 'installed_plugins.json')

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)

    def core_stack(self) -> list[str]:
        return list(self.catalog.core_stack)

    def plugin_archive_path(self, plugin_name: str) -> Path:
        matches = sorted(self.settings.build_dir.glob(f"{plugin_name}.v*.tgz"))
        if matches:
            return matches[-1]
        return self.settings.build_dir / f"{plugin_name}.tgz"

    def installed_plugin_dir(self, plugin_id: str) -> Path:
        return self.settings.runtime_installed_plugins_dir / plugin_id

    def plugin_internal_port(self, plugin_id: str) -> int | None:
        runtime_json = self.installed_plugin_dir(plugin_id) / 'runtime.json'
        if not runtime_json.exists():
            return None
        payload = json.loads(runtime_json.read_text(encoding='utf-8'))
        return payload.get('network', {}).get('internal_port')

    def ensure_core_route(self) -> str:
        return self.proxy.apply_core_route()

    def ensure_plugin_route(self, plugin_id: str) -> str | None:
        internal_port = self.plugin_internal_port(plugin_id)
        if not internal_port:
            return None
        return self.proxy.apply_plugin_route(plugin_id, internal_port)

    def tailscale_status(self) -> str:
        result = self._run(['tailscale', 'status'], check=False)
        return (result.stdout or result.stderr).strip()

    def tailscale_ipv4(self) -> str:
        result = self._run(['tailscale', 'ip', '-4'], check=False)
        return (result.stdout or '').strip()

    def reconcile_routes(self, plugin_ids: list[str] | None = None, include_core: bool = False) -> dict[str, str]:
        applied: dict[str, str] = {}
        if include_core:
            applied['control-center'] = self.ensure_core_route()
        installed_ids = sorted(self.registry.list_all().keys())
        ids = plugin_ids if plugin_ids is not None else installed_ids
        for plugin_id in ids:
            url = self.ensure_plugin_route(plugin_id)
            if url:
                applied[plugin_id] = url
        return applied
