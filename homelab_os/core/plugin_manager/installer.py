from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from homelab_os.core.config import Settings
from homelab_os.core.plugin_manager.lifecycle import PluginLifecycle
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.plugin_manager.validator import PluginValidator
from homelab_os.core.services.reverse_proxy import ReverseProxyService


class PluginInstaller:
    def __init__(
        self,
        settings: Settings,
        installed_plugins_dir: Path,
        registry_file: Path,
        state_file: Path,
    ) -> None:
        self.settings = settings
        self.installed_plugins_dir = installed_plugins_dir
        self.installed_plugins_dir.mkdir(parents=True, exist_ok=True)

        self.registry = PluginRegistry(registry_file)
        self.runtime = PluginRuntime(installed_plugins_dir, state_file, settings=settings)
        self.lifecycle = PluginLifecycle()
        self.validator = PluginValidator()
        self.reverse_proxy = ReverseProxyService(settings)
        self.state_file = state_file

    def install_plugin(self, archive_path: Path) -> dict:
        if not archive_path.exists():
            raise FileNotFoundError(f"Plugin archive not found: {archive_path}")

        extract_tmp_dir = self.installed_plugins_dir / "__extract_tmp__"
        if extract_tmp_dir.exists():
            shutil.rmtree(extract_tmp_dir)
        extract_tmp_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(extract_tmp_dir)

        extracted_dirs = [p for p in extract_tmp_dir.iterdir() if p.is_dir()]
        if len(extracted_dirs) != 1:
            raise RuntimeError(
                f"Expected exactly one plugin root in archive {archive_path}, found {len(extracted_dirs)}"
            )

        plugin_source_dir = extracted_dirs[0]
        manifest = self.validator.validate_plugin_dir(plugin_source_dir)

        plugin_id = manifest["id"]
        final_dir = self.installed_plugins_dir / plugin_id

        if final_dir.exists():
            shutil.rmtree(final_dir)

        shutil.move(str(plugin_source_dir), str(final_dir))
        shutil.rmtree(extract_tmp_dir, ignore_errors=True)

        network = manifest.get("network", {})
        runtime_metadata = {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "installed_dir": str(final_dir),
            "network": network,
            "entrypoint": manifest.get("entrypoint", {}),
            "public_url": None,
        }

        self.runtime.write_runtime_metadata(plugin_id, runtime_metadata)
        self.lifecycle.install_marker(final_dir)
        self.lifecycle.enable_marker(final_dir)

        internal_port = network.get("internal_port")
        if internal_port and self.reverse_proxy.has_public_route(plugin_id):
            runtime_metadata["public_url"] = self.reverse_proxy.apply_plugin_route(plugin_id, internal_port)
            self.runtime.write_runtime_metadata(plugin_id, runtime_metadata)

        self.registry.register(plugin_id, runtime_metadata)
        return runtime_metadata

    def uninstall_plugin(self, plugin_id: str) -> dict:
        plugin_dir = self.installed_plugins_dir / plugin_id
        if not plugin_dir.exists():
            raise FileNotFoundError(f"Installed plugin not found: {plugin_dir}")

        try:
            self.runtime.stop_plugin(plugin_id)
        except Exception:
            pass

        try:
            self.reverse_proxy.remove_plugin_route(plugin_id)
        except Exception:
            pass

        self.registry.unregister(plugin_id)

        runtime_file = self.installed_plugins_dir / plugin_id / "runtime.json"
        if runtime_file.exists():
            try:
                runtime_file.unlink()
            except Exception:
                pass

        shutil.rmtree(plugin_dir, ignore_errors=True)
        return {"plugin_id": plugin_id, "status": "uninstalled"}
