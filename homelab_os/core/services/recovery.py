from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


class RecoveryService:
    def __init__(self, settings, app_catalog, caddy_service, plugin_runtime, plugin_registry):
        self.settings = settings
        self.app_catalog = app_catalog
        self.caddy_service = caddy_service
        self.plugin_runtime = plugin_runtime
        self.plugin_registry = plugin_registry

    def self_heal(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "docker_root": str(self.settings.docker_root_dir),
            "docker_root_changed": False,
            "rebound_routes": [],
            "started_plugins": [],
            "warnings": [],
            "pihole": None,
        }

        summary["docker_root_changed"] = self._ensure_docker_root()
        summary["rebound_routes"] = self._rebind_routes()

        for plugin_id in self._installed_plugin_ids():
            try:
                result = self.plugin_runtime.start_plugin(plugin_id)
                summary["started_plugins"].append(
                    {
                        "plugin_id": plugin_id,
                        "public_url": result.get("public_url"),
                    }
                )
            except subprocess.CalledProcessError as exc:
                handled = self._try_auto_recover_plugin(plugin_id, exc)
                if handled:
                    try:
                        result = self.plugin_runtime.start_plugin(plugin_id)
                        summary["started_plugins"].append(
                            {
                                "plugin_id": plugin_id,
                                "public_url": result.get("public_url"),
                            }
                        )
                        continue
                    except subprocess.CalledProcessError as exc2:
                        summary["warnings"].append(f"{plugin_id}: {exc2}")
                else:
                    summary["warnings"].append(f"{plugin_id}: {exc}")

        summary["pihole"] = self._check_and_fix_pihole()
        return summary

    def _ensure_docker_root(self) -> bool:
        wanted = str(self.settings.docker_root_dir)
        generated = Path("/mnt/nas/homelab/generated/docker-daemon.generated.json")
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(json.dumps({"data-root": wanted}, indent=2), encoding="utf-8")

        daemon_file = Path("/etc/docker/daemon.json")
        current = {}
        if daemon_file.exists():
            try:
                current = json.loads(daemon_file.read_text(encoding="utf-8"))
            except Exception:
                current = {}

        if current.get("data-root") == wanted:
            return False

        current["data-root"] = wanted
        daemon_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
        subprocess.run(["systemctl", "restart", "docker"], check=True)
        return True

    def _rebind_routes(self) -> list[dict[str, str]]:
        rebound: list[dict[str, str]] = []

        self.caddy_service.write_base_config()
        self.caddy_service.write_control_center_route()

        for plugin_id in self._installed_plugin_ids():
            plugin = self.plugin_registry.get(plugin_id)
            if not plugin:
                continue
            public_url = plugin.get("public_url", "")
            if not public_url:
                continue
            self.caddy_service.write_plugin_route(plugin_id, plugin)
            rebound.append({"plugin_id": plugin_id, "public_url": public_url})

        self.caddy_service.reload()
        return rebound

    def _installed_plugin_ids(self) -> list[str]:
        installed = self.plugin_registry.list_installed()
        return sorted(installed.keys())

    def _try_auto_recover_plugin(self, plugin_id: str, exc: subprocess.CalledProcessError) -> bool:
        message = ""
        if getattr(exc, "stderr", None):
            message += str(exc.stderr)
        if getattr(exc, "stdout", None):
            message += "\n" + str(exc.stdout)
        message = message.lower()

        if "layer does not exist" in message or "unable to get image" in message:
            subprocess.run(["docker", "system", "prune", "-af"], check=False)
            image = self._plugin_image_name(plugin_id)
            if image:
                subprocess.run(["docker", "pull", image], check=False)
            return True

        return False

    def _plugin_image_name(self, plugin_id: str) -> str | None:
        if plugin_id == "pihole":
            return "pihole/pihole:latest"
        return None

    def _check_and_fix_pihole(self) -> dict[str, Any]:
        plugin = self.plugin_registry.get("pihole")
        if not plugin:
            return {"ok": False, "error": "pihole not installed"}

        # Validate locally instead of using the public Tailscale FQDN from the Pi itself
        local_admin_url = "http://127.0.0.1:8080/admin/"
        result: dict[str, Any] = {"ok": False, "status_code": None, "url": local_admin_url}

        # Keep the admin password in sync with the env file / settings
        password = getattr(self.settings, "pihole_password", None) or os.getenv("PIHOLE_PASSWORD")
        if password:
            subprocess.run(
                ["docker", "exec", "pihole", "pihole", "setpassword", password],
                check=False,
                capture_output=True,
                text=True,
            )

        try:
            req = urllib.request.Request(local_admin_url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                result["ok"] = resp.status in (200, 301, 302)
                result["status_code"] = resp.status
        except Exception as exc:
            result["error"] = str(exc)

        if result["ok"]:
            result["public_url"] = plugin.get("public_url", "")
        return result
