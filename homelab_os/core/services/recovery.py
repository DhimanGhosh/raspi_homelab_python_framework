
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from homelab_os.core.config import Settings, ensure_runtime_dirs
from homelab_os.core.plugin_manager.installer import PluginInstaller
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.services.network_stack import NetworkStackService
from homelab_os.core.services.reverse_proxy import ReverseProxyService
from homelab_os.core.services.systemd_service import CoreServiceManager


class RecoveryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = PluginRegistry(settings.manifests_dir / 'installed_plugins.json')
        self.runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
        self.stack = NetworkStackService(settings)
        self.proxy = ReverseProxyService(settings)
        self.installer = PluginInstaller(
            settings=settings,
            installed_plugins_dir=settings.runtime_installed_plugins_dir,
            registry_file=settings.manifests_dir / 'installed_plugins.json',
            state_file=settings.manifests_dir / 'plugin_state.json',
        )
        self.core = CoreServiceManager(settings)

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)

    def ensure_docker_root(self) -> bool:
        self.settings.docker_root_dir.mkdir(parents=True, exist_ok=True)
        daemon_json = Path('/etc/docker/daemon.json')
        desired = {'data-root': str(self.settings.docker_root_dir)}
        current: dict = {}
        result = self._run(['sudo', 'cat', str(daemon_json)], check=False)
        if result.returncode == 0 and result.stdout.strip():
            try:
                current = json.loads(result.stdout)
            except json.JSONDecodeError:
                current = {}
        if current.get('data-root') == desired['data-root']:
            return False
        current.update(desired)
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8') as tmp:
            json.dump(current, tmp, indent=2)
            tmp.write('\n')
            tmp_path = Path(tmp.name)
        try:
            self._run(['sudo', 'mkdir', '-p', str(daemon_json.parent)])
            self._run(['sudo', 'cp', str(tmp_path), str(daemon_json)])
            self._run(['sudo', 'systemctl', 'restart', 'docker'])
        finally:
            tmp_path.unlink(missing_ok=True)
        return True

    def ensure_core_services(self) -> None:
        self.core.install_service()
        self.core.enable_and_start()
        self.proxy.ensure_main_caddyfile()
        self.proxy.apply_core_route()

    def ensure_installed_plugins_running(self) -> dict[str, str]:
        started: dict[str, str] = {}
        installed = self.registry.list_all()
        for plugin_id in sorted(installed.keys()):
            if plugin_id == 'control-center':
                continue
            try:
                self.runtime.start_plugin(plugin_id)
                url = self.stack.ensure_plugin_route(plugin_id)
                if url:
                    started[plugin_id] = url
            except Exception:
                continue
        return started

    def ensure_plugin_installed_and_started(self, plugin_id: str) -> dict:
        plugin = self.registry.get_plugin(plugin_id)
        archive_candidates = sorted(self.settings.build_dir.glob(f'{plugin_id}.v*.tgz'))
        archive_path = archive_candidates[-1] if archive_candidates else None
        if not plugin and archive_path:
            self.installer.install_plugin(archive_path)
        result = self.runtime.start_plugin(plugin_id)
        return result

    def ensure_pihole(self) -> dict:
        plugin_dir = self.settings.plugins_dir / 'pihole'
        data_dir = self.settings.homelab_root / 'runtime' / 'pihole' / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        archive_candidates = sorted(self.settings.build_dir.glob('pihole.v*.tgz'))
        if not self.registry.get_plugin('pihole') and archive_candidates:
            self.installer.install_plugin(archive_candidates[-1])
        if not self.registry.get_plugin('pihole') and plugin_dir.exists():
            raise RuntimeError('Pi-hole is not installed and no built archive was found')
        result = self.runtime.start_plugin('pihole')
        return result

    def run_self_heal(self, include_pihole: bool = True) -> dict:
        ensure_runtime_dirs(self.settings)
        docker_root_changed = self.ensure_docker_root()
        self.ensure_core_services()
        rebound = self.stack.reconcile_routes(include_core=True)
        started = self.ensure_installed_plugins_running()
        pihole_result = None
        if include_pihole:
            try:
                pihole_result = self.ensure_pihole()
                rebound = self.stack.reconcile_routes(include_core=True)
            except Exception as exc:
                pihole_result = {'error': str(exc)}
        return {
            'docker_root': str(self.settings.docker_root_dir),
            'docker_root_changed': docker_root_changed,
            'rebound_routes': rebound,
            'started_plugins': started,
            'pihole': pihole_result,
        }
