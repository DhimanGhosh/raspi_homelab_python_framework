from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    hostname: str
    lan_ip: str
    tailscale_ip: str
    tailscale_fqdn: str
    nas_mount: Path
    homelab_root: Path
    docker_root_dir: Path

    build_dir: Path
    plugins_dir: Path
    manifests_dir: Path
    runtime_dir: Path

    logs_dir: Path
    backups_dir: Path

    control_center_bind: str
    control_center_port: int
    control_center_public_port: int

    caddyfile: Path
    caddy_apps_dir: Path
    caddy_disabled_dir: Path

    @property
    def runtime_installed_plugins_dir(self) -> Path:
        return self.runtime_dir / "installed_plugins"

    @property
    def runtime_marketplace_cache_dir(self) -> Path:
        return self.runtime_dir / "marketplace_cache"

    @property
    def runtime_jobs_dir(self) -> Path:
        return self.runtime_dir / "jobs"

    @property
    def runtime_logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def runtime_backups_dir(self) -> Path:
        return self.runtime_dir / "backups"


def _load_env_file(env_file: str | Path | None) -> None:
    if not env_file:
        return

    path = Path(env_file)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_settings(env_file: str | Path | None = None) -> Settings:
    _load_env_file(env_file)

    root = Path.cwd()

    return Settings(
        hostname=os.getenv("HOSTNAME", "pi-nas"),
        lan_ip=os.getenv("LAN_IP", "192.168.88.10"),
        tailscale_ip=os.getenv("TAILSCALE_IP", "100.66.127.27"),
        tailscale_fqdn=os.getenv("TAILSCALE_FQDN", "pi-nas.taild4713b.ts.net"),
        nas_mount=Path(os.getenv("NAS_MOUNT", "/mnt/nas")),
        homelab_root=Path(os.getenv("HOMELAB_ROOT", "/mnt/nas/homelab")),
        docker_root_dir=Path(os.getenv("DOCKER_ROOT_DIR", "/mnt/nas/homelab/docker")),
        build_dir=root / os.getenv("BUILD_DIR", "build"),
        plugins_dir=root / os.getenv("PLUGINS_DIR", "plugins"),
        manifests_dir=root / os.getenv("MANIFESTS_DIR", "manifests"),
        runtime_dir=root / os.getenv("RUNTIME_DIR", "runtime"),
        logs_dir=Path(os.getenv("LOGS_DIR", "/mnt/nas/homelab/logs")),
        backups_dir=Path(os.getenv("BACKUPS_DIR", "/mnt/nas/homelab/backups")),
        control_center_bind=os.getenv("CONTROL_CENTER_BIND", "127.0.0.1"),
        control_center_port=int(os.getenv("CONTROL_CENTER_PORT", "9000")),
        control_center_public_port=int(os.getenv("CONTROL_CENTER_PUBLIC_PORT", "8444")),
        caddyfile=Path(os.getenv("CADDYFILE", "/etc/caddy/Caddyfile")),
        caddy_apps_dir=Path(os.getenv("CADDY_APPS_DIR", "/etc/caddy/apps")),
        caddy_disabled_dir=Path(os.getenv("CADDY_DISABLED_DIR", "/etc/caddy/apps.disabled")),
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    dirs = [
        settings.build_dir,
        settings.plugins_dir,
        settings.manifests_dir,
        settings.runtime_dir,
        settings.runtime_installed_plugins_dir,
        settings.runtime_marketplace_cache_dir,
        settings.runtime_jobs_dir,
        settings.runtime_logs_dir,
        settings.runtime_backups_dir,
        settings.logs_dir,
        settings.backups_dir,
    ]

    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
