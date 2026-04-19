from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.services.process_runner import ProcessRunner
from homelab_os.core.services.reverse_proxy import ReverseProxyService
from homelab_os.core.services.state_store import StateStore


class PluginInstaller:
    def __init__(
        self,
        settings,
        installed_plugins_dir: Path,
        registry_file: Path,
        state_file: Path,
    ) -> None:
        self.settings = settings
        self.installed_plugins_dir = installed_plugins_dir
        self.registry = PluginRegistry(registry_file)
        self.state_store = StateStore(state_file)
        self.runner = ProcessRunner()
        self.proxy = ReverseProxyService(settings)
        self.installed_plugins_dir.mkdir(parents=True, exist_ok=True)

    def _read_manifest(self, plugin_dir: Path) -> dict:
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"plugin.json not found in {plugin_dir}")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _docker_compose_cmd(self, plugin_id: str, *args: str) -> list[str]:
        return ["docker", "compose", "-p", plugin_id, *args]

    def _prepare_public_url(self, plugin_id: str, manifest: dict) -> str | None:
        internal_port = manifest.get("network", {}).get("internal_port")
        if not internal_port:
            return None
        return self.proxy.apply_plugin_route(plugin_id, int(internal_port))

    def _cleanup_existing_install(self, plugin_id: str) -> None:
        existing = self.registry.get_plugin(plugin_id)
        if existing:
            self.uninstall_plugin(plugin_id)
        else:
            plugin_dir = self.installed_plugins_dir / plugin_id
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir, ignore_errors=True)
            self.state_store.remove_plugin_state(plugin_id)
            try:
                self.proxy.remove_plugin_route(plugin_id)
            except Exception:
                pass

    def install_plugin(self, archive_path: Path) -> dict:
        if not archive_path.exists():
            raise FileNotFoundError(f"Plugin archive not found: {archive_path}")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(temp_dir)

            extracted_roots = [p for p in temp_dir.iterdir() if p.is_dir()]
            if len(extracted_roots) != 1:
                raise RuntimeError(f"Expected exactly one root directory in archive {archive_path}")

            source_dir = extracted_roots[0]
            manifest = self._read_manifest(source_dir)
            plugin_id = manifest["id"]
            target_dir = self.installed_plugins_dir / plugin_id

            self._cleanup_existing_install(plugin_id)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

        public_url = self._prepare_public_url(plugin_id, manifest)
        entry = {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "installed_dir": str(target_dir),
            "network": manifest.get("network", {}),
            "entrypoint": manifest.get("entrypoint", {}),
            "public_url": public_url,
        }
        self.registry.upsert_plugin(entry)
        runtime_metadata = {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "installed_dir": str(target_dir),
            "network": manifest.get("network", {}),
            "entrypoint": manifest.get("entrypoint", {}),
            "public_url": public_url,
        }
        (target_dir / "runtime.json").write_text(json.dumps(runtime_metadata, indent=2), encoding="utf-8")
        return entry

    def uninstall_plugin(self, plugin_id: str) -> dict:
        plugin_entry = self.registry.get_plugin(plugin_id)
        plugin_dir = self.installed_plugins_dir / plugin_id

        compose_dir = plugin_dir / "docker"
        if compose_dir.exists() and (compose_dir / "docker-compose.yml").exists():
            self.runner.run(
                self._docker_compose_cmd(plugin_id, "down", "--remove-orphans", "-v"),
                cwd=compose_dir,
                check=False,
            )

        self.runner.run(["docker", "rm", "-f", plugin_id], check=False)
        self.proxy.remove_plugin_route(plugin_id)

        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)

        self.registry.remove_plugin(plugin_id)
        self.state_store.remove_plugin_state(plugin_id)

        if not plugin_entry:
            return {"ok": True, "plugin_id": plugin_id, "message": "Plugin already absent"}
        return {"ok": True, "plugin_id": plugin_id}
