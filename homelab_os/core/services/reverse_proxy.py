from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from homelab_os.core.config import Settings
from homelab_os.core.services.app_catalog import public_port_for_app, public_url_for_app


class ReverseProxyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)

    def _raise_with_context(self, action: str, result: subprocess.CompletedProcess) -> None:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(f"{action} failed: {details}")

    def read_caddyfile(self) -> str:
        result = self._run(["sudo", "cat", str(self.settings.caddyfile)])
        return result.stdout

    def read_snippet_file(self, filename: str) -> str | None:
        snippet_path = self.settings.caddy_apps_dir / filename
        result = self._run(["sudo", "cat", str(snippet_path)], check=False)
        if result.returncode != 0:
            return None
        return result.stdout

    def has_public_route(self, plugin_id: str) -> bool:
        return public_port_for_app(self.settings, plugin_id) is not None

    def public_port_for_plugin(self, plugin_id: str) -> int:
        port = public_port_for_app(self.settings, plugin_id)
        if port is None:
            raise KeyError(f"No public port mapping defined for plugin '{plugin_id}'")
        return port

    def public_url_for_plugin(self, plugin_id: str) -> str | None:
        return public_url_for_app(self.settings, plugin_id)

    def _snippet_tls_block(self) -> str:
        cert = self.settings.tailscale_cert_dir / f"{self.settings.tailscale_fqdn}.crt"
        key = self.settings.tailscale_cert_dir / f"{self.settings.tailscale_fqdn}.key"
        return f"    tls {cert} {key}\n"

    def generate_snippet(self, plugin_id: str, internal_port: int) -> str:
        public_port = self.public_port_for_plugin(plugin_id)
        return (
            f"https://{self.settings.tailscale_fqdn}:{public_port} {{\n"
            f"{self._snippet_tls_block()}"
            f"    reverse_proxy 127.0.0.1:{internal_port}\n"
            f"}}\n"
        )

    def generate_core_snippet(self) -> str:
        return (
            f"https://{self.settings.tailscale_fqdn}:{self.settings.control_center_public_port} {{\n"
            f"{self._snippet_tls_block()}"
            f"    reverse_proxy {self.settings.control_center_bind}:{self.settings.control_center_port}\n"
            f"}}\n"
        )

    def _fix_permissions(self, path: Path) -> None:
        result = self._run(["sudo", "chown", "caddy:caddy", str(path)], check=False)
        if result.returncode != 0:
            self._raise_with_context(f"chown {path.name}", result)
        result = self._run(["sudo", "chmod", "644", str(path)], check=False)
        if result.returncode != 0:
            self._raise_with_context(f"chmod {path.name}", result)

    def write_snippet_file(self, filename: str, content: str) -> tuple[Path, bool]:
        snippet_path = self.settings.caddy_apps_dir / filename
        current = self.read_snippet_file(filename)
        if current == content:
            self._fix_permissions(snippet_path)
            return snippet_path, False

        self._run(["sudo", "mkdir", "-p", str(self.settings.caddy_apps_dir)])
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            result = self._run(["sudo", "cp", str(tmp_path), str(snippet_path)], check=False)
            if result.returncode != 0:
                self._raise_with_context(f"copy snippet {filename}", result)
            self._fix_permissions(snippet_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return snippet_path, True

    def remove_snippet_file(self, filename: str) -> bool:
        snippet_path = self.settings.caddy_apps_dir / filename
        exists = self.read_snippet_file(filename) is not None
        if not exists:
            return False
        result = self._run(["sudo", "rm", "-f", str(snippet_path)], check=False)
        if result.returncode != 0:
            self._raise_with_context(f"remove snippet {filename}", result)
        return True

    def write_snippet(self, plugin_id: str, internal_port: int) -> tuple[Path, bool]:
        return self.write_snippet_file(f"{plugin_id}.caddy", self.generate_snippet(plugin_id, internal_port))

    def write_core_snippet(self) -> tuple[Path, bool]:
        return self.write_snippet_file("control-center.caddy", self.generate_core_snippet())

    def verify_main_caddyfile(self) -> None:
        try:
            content = self.read_caddyfile()
        except Exception as exc:
            print(f"[WARN] Could not read Caddyfile: {exc}")
            return
        required_import = f"import {self.settings.caddy_apps_dir}/*.caddy"
        if required_import not in content:
            print(f"[WARN] Missing import in Caddyfile: {required_import}")

    def validate_caddy(self) -> None:
        result = self._run(["sudo", "caddy", "validate", "--config", str(self.settings.caddyfile)], check=False)
        if result.returncode != 0:
            self._raise_with_context("caddy validate", result)

    def reload_caddy(self) -> None:
        result = self._run(["sudo", "systemctl", "reload", "caddy"], check=False)
        if result.returncode != 0:
            self._raise_with_context("caddy reload", result)

    def apply_plugin_route(self, plugin_id: str, internal_port: int) -> str | None:
        if not self.has_public_route(plugin_id):
            return None
        self.verify_main_caddyfile()
        _, changed = self.write_snippet(plugin_id, internal_port)
        if changed:
            self.validate_caddy()
            self.reload_caddy()
        return self.public_url_for_plugin(plugin_id)

    def remove_plugin_route(self, plugin_id: str) -> None:
        if not self.has_public_route(plugin_id):
            return
        changed = self.remove_snippet_file(f"{plugin_id}.caddy")
        if changed:
            self.validate_caddy()
            self.reload_caddy()

    def apply_core_route(self) -> str:
        self.verify_main_caddyfile()
        _, changed = self.write_core_snippet()
        if changed:
            self.validate_caddy()
            self.reload_caddy()
        return f"https://{self.settings.tailscale_fqdn}:{self.settings.control_center_public_port}"
