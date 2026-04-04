from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from homelab_os.core.plugin_manager.lifecycle import PluginLifecycle
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.plugin_manager.validator import PluginValidator


class PluginInstaller:
    def __init__(
        self,
        installed_plugins_dir: Path,
        registry_file: Path,
    ) -> None:
        self.installed_plugins_dir = installed_plugins_dir
        self.installed_plugins_dir.mkdir(parents=True, exist_ok=True)

        self.registry = PluginRegistry(registry_file)
        self.runtime = PluginRuntime(installed_plugins_dir)
        self.lifecycle = PluginLifecycle()
        self.validator = PluginValidator()

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

        runtime_metadata = {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "installed_dir": str(final_dir),
            "network": manifest.get("network", {}),
            "entrypoint": manifest.get("entrypoint", {}),
        }

        self.runtime.write_runtime_metadata(plugin_id, runtime_metadata)
        self.lifecycle.install_marker(final_dir)
        self.lifecycle.enable_marker(final_dir)

        self.registry.register(plugin_id, runtime_metadata)

        return runtime_metadata
