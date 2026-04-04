from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import typer

from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.plugin_manager import PluginBuilder, PluginInstaller

app = typer.Typer(help="homelab_os command line interface")


@app.command("bootstrap-host")
def bootstrap_host(env_file: str = ".env") -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)

    typer.echo("Host bootstrap completed.")
    typer.echo("")
    typer.echo("Resolved settings:")
    typer.echo(f"  Hostname           : {settings.hostname}")
    typer.echo(f"  LAN IP             : {settings.lan_ip}")
    typer.echo(f"  Tailscale IP       : {settings.tailscale_ip}")
    typer.echo(f"  Tailscale FQDN     : {settings.tailscale_fqdn}")
    typer.echo(f"  NAS mount          : {settings.nas_mount}")
    typer.echo(f"  Docker root dir    : {settings.docker_root_dir}")
    typer.echo(f"  Build dir          : {settings.build_dir}")
    typer.echo(f"  Plugins dir        : {settings.plugins_dir}")
    typer.echo(f"  Runtime dir        : {settings.runtime_dir}")


@app.command("show-settings")
def show_settings(env_file: str = ".env") -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)

    for key, value in asdict(settings).items():
        typer.echo(f"{key}: {value}")


@app.command("build-all-plugins")
def build_all_plugins(env_file: str = ".env") -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)

    builder = PluginBuilder()
    count = 0

    if not settings.plugins_dir.exists():
        typer.echo(f"Plugins directory does not exist: {settings.plugins_dir}")
        raise typer.Exit(code=1)

    for source_dir in sorted(settings.plugins_dir.iterdir()):
        if source_dir.is_dir() and (source_dir / "plugin.json").exists():
            archive_path = settings.build_dir / f"{source_dir.name}.tgz"
            builder.build_plugin_archive(source_dir, archive_path)
            typer.echo(f"Built {archive_path}")
            count += 1

    typer.echo(f"Built {count} plugin archives")


@app.command("install-plugin")
def install_plugin(plugin_archive: Path, env_file: str = ".env") -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)

    installer = PluginInstaller(
        settings=settings,
        installed_plugins_dir=settings.runtime_installed_plugins_dir,
        registry_file=settings.manifests_dir / "installed_plugins.json",
    )
    result = installer.install_plugin(plugin_archive)

    typer.echo(f"Installed plugin: {result['name']} ({result['version']})")
    typer.echo(f"Installed dir: {result['installed_dir']}")
    if result.get("public_url"):
        typer.echo(f"Open URL: {result['public_url']}")


@app.command("run-control-shell")
def run_control_shell(env_file: str = ".env") -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    typer.echo(
        f"Control shell placeholder. Target bind: "
        f"{settings.control_center_bind}:{settings.control_center_port}"
    )


if __name__ == "__main__":
    app()
