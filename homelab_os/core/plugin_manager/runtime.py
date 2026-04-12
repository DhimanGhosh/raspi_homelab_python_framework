from __future__ import annotations

import json
from pathlib import Path

from homelab_os.core.services.process_runner import ProcessRunner
from homelab_os.core.services.state_store import StateStore
from homelab_os.core.services.health import HealthService


class PluginRuntime:
    def __init__(self, runtime_root: Path, state_file: Path, settings=None) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.runner = ProcessRunner()
        self.health = HealthService()
        self.state_store = StateStore(state_file)
        self.settings = settings

    def plugin_runtime_dir(self, plugin_id: str) -> Path:
        return self.runtime_root / plugin_id

    def manifest_path(self, plugin_id: str) -> Path:
        return self.plugin_runtime_dir(plugin_id) / "plugin.json"

    def read_manifest(self, plugin_id: str) -> dict:
        path = self.manifest_path(plugin_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

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

    def detect_runtime_type(self, plugin_dir: Path) -> str:
        plugin_json = plugin_dir / "plugin.json"
        if plugin_json.exists():
            manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
            backend = manifest.get("backend", {})
            if backend.get("module"):
                return "python_module"
            if backend.get("script"):
                return "python_script"
        if (plugin_dir / "docker" / "docker-compose.yml").exists():
            return "docker"
        if (plugin_dir / "backend" / "app.py").exists():
            return "python_script"
        return "unknown"

    def _maybe_apply_public_route(self, plugin_id: str) -> str | None:
        if not self.settings:
            return None
        from homelab_os.core.services.reverse_proxy import ReverseProxyService
        metadata = self.read_runtime_metadata(plugin_id) or {}
        internal_port = metadata.get("network", {}).get("internal_port")
        if not internal_port:
            return None
        proxy = ReverseProxyService(self.settings)
        url = proxy.apply_plugin_route(plugin_id, internal_port)
        if url:
            metadata["public_url"] = url
            self.write_runtime_metadata(plugin_id, metadata)
        return url

    def start_plugin(self, plugin_id: str) -> dict:
        plugin_dir = self.plugin_runtime_dir(plugin_id)
        if not plugin_dir.exists():
            raise FileNotFoundError(f"Installed plugin not found: {plugin_dir}")

        runtime_type = self.detect_runtime_type(plugin_dir)
        manifest = self.read_manifest(plugin_id)
        metadata = self.read_runtime_metadata(plugin_id) or {}
        internal_port = str(metadata.get("network", {}).get("internal_port", ""))

        if runtime_type == "docker":
            compose_dir = plugin_dir / "docker"
            result = self.runner.run(["docker", "compose", "-p", plugin_id, "up", "-d", "--remove-orphans"], cwd=compose_dir)
            public_url = self._maybe_apply_public_route(plugin_id)
            self.state_store.update_plugin_state(plugin_id, {
                "status": "running",
                "runtime_type": "docker",
                "last_action": "start",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "public_url": public_url,
            })
            return {"plugin_id": plugin_id, "runtime_type": "docker", "status": "running", "public_url": public_url}

        if runtime_type == "python_module":
            backend_dir = plugin_dir / "backend"
            log_dir = self.runtime_root / "_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_file = open(log_dir / f"{plugin_id}.out.log", "a", encoding="utf-8")
            stderr_file = open(log_dir / f"{plugin_id}.err.log", "a", encoding="utf-8")
            backend = manifest.get("backend", {})
            module = backend["module"]
            host = backend.get("host", "127.0.0.1")
            cmd = ["python3", "-m", "uvicorn", module, "--host", host, "--port", internal_port]
            process = self.runner.popen(cmd, cwd=backend_dir, stdout=stdout_file, stderr=stderr_file)
            public_url = self._maybe_apply_public_route(plugin_id)
            self.state_store.update_plugin_state(plugin_id, {
                "status": "running",
                "runtime_type": "python_module",
                "last_action": "start",
                "pid": process.pid,
                "stdout_log": str(log_dir / f"{plugin_id}.out.log"),
                "stderr_log": str(log_dir / f"{plugin_id}.err.log"),
                "public_url": public_url,
            })
            return {"plugin_id": plugin_id, "runtime_type": "python_module", "status": "running", "pid": process.pid, "public_url": public_url}

        if runtime_type == "python_script":
            backend_dir = plugin_dir / "backend"
            log_dir = self.runtime_root / "_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_file = open(log_dir / f"{plugin_id}.out.log", "a", encoding="utf-8")
            stderr_file = open(log_dir / f"{plugin_id}.err.log", "a", encoding="utf-8")
            backend = manifest.get("backend", {})
            script = backend.get("script", "app.py")
            process = self.runner.popen(["python3", script], cwd=backend_dir, stdout=stdout_file, stderr=stderr_file)
            public_url = self._maybe_apply_public_route(plugin_id)
            self.state_store.update_plugin_state(plugin_id, {
                "status": "running",
                "runtime_type": "python_script",
                "last_action": "start",
                "pid": process.pid,
                "stdout_log": str(log_dir / f"{plugin_id}.out.log"),
                "stderr_log": str(log_dir / f"{plugin_id}.err.log"),
                "public_url": public_url,
            })
            return {"plugin_id": plugin_id, "runtime_type": "python_script", "status": "running", "pid": process.pid, "public_url": public_url}

        raise RuntimeError(f"Unsupported runtime type for plugin '{plugin_id}'")

    def stop_plugin(self, plugin_id: str) -> dict:
        plugin_dir = self.plugin_runtime_dir(plugin_id)
        if not plugin_dir.exists():
            raise FileNotFoundError(f"Installed plugin not found: {plugin_dir}")
        runtime_type = self.detect_runtime_type(plugin_dir)
        plugin_state = self.state_store.get_plugin_state(plugin_id) or {}

        if runtime_type == "docker":
            compose_dir = plugin_dir / "docker"
            result = self.runner.run(["docker", "compose", "-p", plugin_id, "down", "--remove-orphans"], cwd=compose_dir)
            self.state_store.update_plugin_state(plugin_id, {
                "status": "stopped",
                "last_action": "stop",
                "stdout": result.stdout,
                "stderr": result.stderr,
            })
            return {"plugin_id": plugin_id, "runtime_type": "docker", "status": "stopped"}

        if runtime_type in {"python_module", "python_script"}:
            pid = plugin_state.get("pid")
            if pid:
                self.runner.run(["kill", str(pid)], check=False)
            self.state_store.update_plugin_state(plugin_id, {"status": "stopped", "last_action": "stop"})
            return {"plugin_id": plugin_id, "runtime_type": runtime_type, "status": "stopped"}

        raise RuntimeError(f"Unsupported runtime type for plugin '{plugin_id}'")

    def restart_plugin(self, plugin_id: str) -> dict:
        self.stop_plugin(plugin_id)
        return self.start_plugin(plugin_id)

    def healthcheck_plugin(self, plugin_id: str) -> dict:
        metadata = self.read_runtime_metadata(plugin_id)
        if not metadata:
            raise FileNotFoundError(f"runtime.json missing for plugin '{plugin_id}'")
        public_url = metadata.get("public_url")
        plugin_state = self.state_store.get_plugin_state(plugin_id) or {}
        internal_port = metadata.get("network", {}).get("internal_port")

        if public_url:
            health = self.health.check_http(public_url)
            self.state_store.update_plugin_state(plugin_id, {"last_healthcheck": health})
            return health

        if internal_port:
            health = self.health.check_http(f"http://127.0.0.1:{internal_port}/")
            self.state_store.update_plugin_state(plugin_id, {"last_healthcheck": health})
            return health

        return {"ok": plugin_state.get("status") == "running", "status_code": None, "url": None}
