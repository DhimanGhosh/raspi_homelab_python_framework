import json
import tarfile
import tempfile
import types
import zipfile
from datetime import datetime
from pathlib import Path

from homelab_platform.services.bundle_runtime import generic_docker_uninstall
from homelab_platform.services.health import docker_is_healthy
from homelab_platform.services.recovery import recover_stack
from homelab_platform.services.state import clear_app_state, load_installed_apps, mark_install_attempt, mark_install_failure


class BundleInstaller:
    def __init__(self, settings):
        self.settings = settings

    def _make_log_path(self, app_id: str | None, prefix: str = "install") -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_root = self.settings.logs_dir / "installs"
        logs_root.mkdir(parents=True, exist_ok=True)
        safe = app_id or "unknown"
        return logs_root / f"{safe}_{prefix}_{stamp}.log"

    def extract_bundle(self, bundle_path: Path) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="ccbundle-"))
        if bundle_path.suffix == ".zip":
            with zipfile.ZipFile(bundle_path) as zf:
                zf.extractall(temp_dir)
        else:
            with tarfile.open(bundle_path, "r:*") as tf:
                tf.extractall(temp_dir)
        children = list(temp_dir.iterdir())
        return children[0] if len(children) == 1 and children[0].is_dir() else temp_dir

    def load_metadata(self, extracted: Path) -> dict:
        path = extracted / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"metadata.json missing in {extracted}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _normalize_bundle_source(self, bundle_path: Path) -> str:
        source = bundle_path.read_text(encoding="utf-8")
        if "\n" in source and "" not in source.strip():
            source = source.encode("utf-8").decode("unicode_escape")
        source = source.replace("homelab_py.services.", "homelab_platform.services.")
        source = source.replace("from homelab_py.", "from homelab_platform.")
        source = source.replace("import homelab_py.", "import homelab_platform.")
        bundle_path.write_text(source, encoding="utf-8")
        return source

    def _load_bundle_module(self, extracted: Path):
        bundle_path = extracted / "bundle.py"
        if not bundle_path.exists():
            raise FileNotFoundError(f"bundle.py missing in {extracted}")
        source = self._normalize_bundle_source(bundle_path)
        module = types.ModuleType("bundle_module")
        module.__file__ = str(bundle_path)
        code = compile(source, str(bundle_path), "exec")
        exec(code, module.__dict__)
        return module, bundle_path

    def _run_python_bundle(self, extracted: Path, meta: dict, func_name: str):
        module, bundle_path = self._load_bundle_module(extracted)
        func = getattr(module, func_name, None)
        if func is None:
            raise AttributeError(f"{func_name} not found in {bundle_path}")
        return func(self.settings, extracted, meta)

    def install(self, bundle_path: Path):
        if not docker_is_healthy():
            recover_stack(self.settings)
            raise RuntimeError("Docker unstable — recovery triggered. Retry install.")
        extracted = self.extract_bundle(bundle_path)
        meta = self.load_metadata(extracted)
        log_path = self._make_log_path(meta.get("id"), prefix="install")
        meta["_log_path"] = str(log_path)
        mark_install_attempt(self.settings.apps_dir, meta["id"], meta, str(log_path))
        try:
            result = self._run_python_bundle(extracted, meta, "install")
            if isinstance(result, dict):
                result.setdefault("log_path", str(log_path))
            return result
        except Exception as exc:
            mark_install_failure(self.settings.apps_dir, meta["id"], meta, str(exc), str(log_path))
            raise RuntimeError(f"Install failed for {meta['id']}: {exc}\nDetailed log: {log_path}") from None

    def remove_app(self, app_id: str):
        app_root = self.settings.apps_dir / app_id
        bundle_dir = app_root / "bundle"
        meta_path = app_root / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Installed app metadata not found for {app_id}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        log_path = self._make_log_path(app_id, prefix="remove")
        meta["_log_path"] = str(log_path)
        try:
            if bundle_dir.exists():
                try:
                    result = self._run_python_bundle(bundle_dir, meta, "uninstall")
                except AttributeError:
                    result = generic_docker_uninstall(self.settings, bundle_dir, meta)
            else:
                result = generic_docker_uninstall(self.settings, app_root, meta)
            clear_app_state(self.settings.apps_dir, app_id)
            if isinstance(result, dict):
                result.setdefault("log_path", str(log_path))
            return result
        except Exception as exc:
            raise RuntimeError(f"Remove failed for {app_id}: {exc}\nDetailed log: {log_path}") from None

    def list_installed(self):
        return load_installed_apps(self.settings.apps_dir, self.settings)
