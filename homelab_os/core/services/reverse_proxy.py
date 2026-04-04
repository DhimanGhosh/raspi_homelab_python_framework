from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from homelab_os.core.config import Settings


PLUGIN_PORT_MAP = {
    "control-center": 8444,
    "pihole": 8447,
    "files": 8449,
    "status": 8451,
    "voice-ai": 8452,
    "homarr": 8453,
    "personal-library": 8454,
    "dictionary": 8455,
    "api-gateway": 8456,
    "music-player": 8459,
    "link-downloader": 8460,
}

PLUGIN_PATH_SUFFIX = {
    "control-center": "",
    "pihole": "/admin/",
    "files": "",
    "status": "",
    "voice-ai": "/",
    "homarr": "/",
    "personal-library": "/",
    "dictionary": "/",
    "api-gateway": "/docs",
    "music-player": "/",
    "link-downloader": "/",
}


class ReverseProxyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def public_port_for_plugin(self, plugin_id: str) -> int:
        if plugin_id not in PLUGIN_PORT_MAP:
            raise KeyError(f"No public port mapping defined for plugin '{plugin_id}'")
        return PLUGIN_PORT_MAP[plugin_id]

    def public_url_for_plugin(self, plugin_id: str) -> str:
        port = self.public_port_for_plugin(plugin_id)
        suffix = PLUGIN_PATH_SUFFIX.get(plugin_id, "")
        return f"https://{self.settings.tailscale_fqdn}:{port}{suffix}"

    def _run(self, cmd: list[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=check, text=True, capture_output=capture_output)

    def _sudo_read_text(self, path: Path) -> str:
        result = self._run(["sudo", "cat", str(path)], capture_output=True)
        return result.stdout

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

    def write_snippet(self, plugin_id: str, internal_port: int) -> Path:
        snippet_path = self.settings.caddy_apps_dir / f"{plugin_id}.caddy"
        snippet_content = self.generate_snippet(plugin_id, internal_port)
        self._run(["sudo", "mkdir", "-p", str(self.settings.caddy_apps_dir)])
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(snippet_content)
            temp_path = Path(tmp.name)
        try:
            self._run(["sudo", "cp", str(temp_path), str(snippet_path)])
            self._run(["sudo", "chmod", "644", str(snippet_path)])
        finally:
            temp_path.unlink(missing_ok=True)
        return snippet_path

    def verify_main_caddyfile(self) -> None:
        try:
            if not self.settings.caddyfile.exists():
                print(f"[WARN] Caddyfile not found: {self.settings.caddyfile}")
                return
            content = self._sudo_read_text(self.settings.caddyfile)
            required_import = f"import {self.settings.caddy_apps_dir}/*.caddy"
            if required_import not in content:
                print(f"[WARN] Caddyfile missing required import line: {required_import}")
        except Exception as exc:
            print(f"[WARN] Could not verify Caddyfile: {exc}")

    def validate_caddy(self) -> None:
        self._run(["sudo", "caddy", "validate", "--config", str(self.settings.caddyfile)])

    def reload_caddy(self) -> None:
        self._run(["sudo", "systemctl", "reload", "caddy"])

    def apply_plugin_route(self, plugin_id: str, internal_port: int) -> str:
        self.verify_main_caddyfile()
        self.write_snippet(plugin_id, internal_port)
        self.validate_caddy()
        self.reload_caddy()
        return self.public_url_for_plugin(plugin_id)
