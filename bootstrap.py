#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE_FILE = ROOT / ".env.example"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def ensure_venv() -> tuple[Path, Path]:
    if not VENV_DIR.exists():
        print("[bootstrap] creating virtual environment")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])

    py = VENV_DIR / "bin" / "python"
    pip = VENV_DIR / "bin" / "pip"
    return py, pip


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        return

    if ENV_EXAMPLE_FILE.exists():
        print("[bootstrap] creating .env from .env.example")
        ENV_FILE.write_text(ENV_EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        return

    print("[bootstrap] creating default .env")
    ENV_FILE.write_text(
        "\n".join(
            [
                "HOSTNAME=pi-nas",
                "LAN_IP=192.168.88.10",
                "TAILSCALE_IP=100.66.127.27",
                "TAILSCALE_FQDN=pi-nas.taild4713b.ts.net",
                "NAS_MOUNT=/mnt/nas",
                "HOMELAB_ROOT=/mnt/nas/homelab",
                "DOCKER_ROOT_DIR=/mnt/nas/homelab/docker",
                "CONTROL_CENTER_BIND=127.0.0.1",
                "CONTROL_CENTER_PORT=9000",
                "CONTROL_CENTER_PUBLIC_PORT=8444",
                "BUILD_DIR=build",
                "PLUGINS_DIR=plugins",
                "MANIFESTS_DIR=manifests",
                "RUNTIME_DIR=runtime",
                "LOGS_DIR=/mnt/nas/homelab/logs",
                "BACKUPS_DIR=/mnt/nas/homelab/backups",
                "CADDYFILE=/etc/caddy/Caddyfile",
                "CADDY_APPS_DIR=/etc/caddy/apps",
                "CADDY_DISABLED_DIR=/etc/caddy/apps.disabled",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def install_project(pip: Path, py: Path) -> None:
    print("[bootstrap] installing project into virtual environment")
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(pip), "install", "-e", str(ROOT)])


def run_host_bootstrap() -> None:
    homelabctl = VENV_DIR / "bin" / "homelabctl"
    print("[bootstrap] running host bootstrap")
    run([str(homelabctl), "bootstrap-host", "--env-file", ".env"])


def main() -> None:
    py, pip = ensure_venv()
    ensure_env_file()
    install_project(pip, py)
    run_host_bootstrap()

    print("\nBootstrap completed.\n")
    print("Recommended commands:")
    print("  source .venv/bin/activate")
    print("  homelabctl build-all-plugins --env-file .env")
    print("  homelabctl run-control-shell --env-file .env")


if __name__ == "__main__":
    main()
