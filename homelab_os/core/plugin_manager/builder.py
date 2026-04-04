from __future__ import annotations

import tarfile
from pathlib import Path

from homelab_os.core.plugin_manager.validator import PluginValidator


class PluginBuilder:
    def __init__(self) -> None:
        self.validator = PluginValidator()

    def build_plugin_archive(self, source_dir: Path, output_path: Path) -> Path:
        self.validator.validate_plugin_dir(source_dir)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tarfile.open(output_path, "w:gz") as tf:
            tf.add(source_dir, arcname=source_dir.name)

        return output_path
