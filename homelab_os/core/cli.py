from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import typer

from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.plugin_manager import PluginBuilder, PluginInstaller
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.services.jobs import JobStore
from homelab_os.core.services.logging_service import LoggingService
from homelab_os.core.services.network_stack import NetworkStackService
from homelab_os.core.services.reverse_proxy import ReverseProxyService
from homelab_os.core.services.systemd_service import CoreServiceManager
from homelab_os.core.services.recovery import RecoveryService
from homelab_os.core.services.watchdog import WatchdogService
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.services.app_catalog import load_app_catalog

app = typer.Typer(help='homelab_os command line interface')


def _job_services(env_file: str):
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    return settings, JobStore(settings.manifests_dir / 'jobs.json'), LoggingService(settings.runtime_jobs_dir)


def _plugin_version(source_dir: Path) -> str:
    manifest = json.loads((source_dir / 'plugin.json').read_text(encoding='utf-8'))
    version = str(manifest.get('version', '')).strip()
    if not version:
        raise ValueError(f"plugin '{source_dir.name}' must define a non-empty version in plugin.json")
    return version


def _install_watchdog(settings, echo_fn=None) -> None:
    """Install or reinstall the watchdog service. Called automatically by
    bootstrap-host and self-heal so no manual steps are ever required."""
    echo = echo_fn or typer.echo
    try:
        watchdog = WatchdogService(settings)
        watchdog.install_and_enable()
        echo(f'Watchdog service installed and enabled ({watchdog.SERVICE_NAME})')
    except Exception as exc:  # noqa: BLE001
        echo(f'[watchdog] Warning: could not install watchdog service: {exc}')


@app.command('bootstrap-host')
def bootstrap_host(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)

    # 1 — Reconcile Caddy routes
    stack = NetworkStackService(settings)
    applied = stack.reconcile_routes(include_core=True)

    # 2 — Automatically install/update the watchdog service so CC + Pi-hole
    #     are always monitored and auto-restarted without any manual setup.
    _install_watchdog(settings)

    typer.echo('Host bootstrap completed.')
    typer.echo('')
    typer.echo('Resolved settings:')
    typer.echo(f'  Hostname           : {settings.hostname}')
    typer.echo(f'  LAN IP             : {settings.lan_ip}')
    typer.echo(f'  Tailscale IP       : {settings.tailscale_ip}')
    typer.echo(f'  Tailscale FQDN     : {settings.tailscale_fqdn}')
    typer.echo(f'  NAS mount          : {settings.nas_mount}')
    typer.echo(f'  Docker root dir    : {settings.docker_root_dir}')
    typer.echo(f'  Build dir          : {settings.build_dir}')
    typer.echo(f'  Plugins dir        : {settings.plugins_dir}')
    typer.echo(f'  Runtime dir        : {settings.runtime_dir}')
    typer.echo(f'  Pi-hole password   : {"(set)" if settings.pihole_password else "(not set)"}')
    typer.echo('')
    typer.echo(f'Rebound routes      : {len(applied)}')
    for plugin_id, public_url in applied.items():
        typer.echo(f'  {plugin_id:<18} -> {public_url}')


@app.command('install-watchdog')
def install_watchdog(env_file: str = '.env') -> None:
    """Install (or reinstall) the homelab-watchdog systemd service.

    This is called automatically by bootstrap-host and self-heal, so you only
    need to run this manually if you want to refresh the watchdog script after
    changing settings (e.g. LAN_IP, runtime path).
    """
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    watchdog = WatchdogService(settings)
    watchdog.install_and_enable()
    typer.echo(f'Watchdog installed and started: {watchdog.SERVICE_NAME}')
    typer.echo(f'  Script      : {watchdog.SCRIPT_PATH}')
    typer.echo(f'  Status      : {watchdog.status()}')
    typer.echo(f'  Logs        : {settings.logs_dir}/watchdog.log')


@app.command('show-settings')
def show_settings(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    for key, value in asdict(settings).items():
        typer.echo(f'{key}: {value}')


@app.command('reconcile-routes')
def reconcile_routes(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    stack = NetworkStackService(settings)
    applied = stack.reconcile_routes(include_core=True)
    typer.echo(f'Rebound {len(applied)} routes')
    for plugin_id, public_url in applied.items():
        typer.echo(f'{plugin_id}: {public_url}')


@app.command('build-all-plugins')
def build_all_plugins(env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    builder = PluginBuilder()
    job = job_store.create_job('build_all_plugins', 'all', {'plugins_dir': str(settings.plugins_dir)})
    logger.append_job_log(job['job_id'], 'Starting build-all-plugins')
    try:
        plugin_dirs = [d for d in sorted(settings.plugins_dir.iterdir()) if d.is_dir() and (d / 'plugin.json').exists()]
        total = max(len(plugin_dirs), 1)
        count = 0
        for index, source_dir in enumerate(plugin_dirs, start=1):
            version = _plugin_version(source_dir)
            archive_path = settings.build_dir / f'{source_dir.name}.v{version}.tgz'
            logger.append_job_log(job['job_id'], f'Building {source_dir.name} v{version} -> {archive_path}')
            builder.build_plugin_archive(source_dir, archive_path)
            typer.echo(f'Built {archive_path}')
            count += 1
            job_store.update_job(job['job_id'], status='running', progress=int((index / total) * 100))
        logger.append_job_log(job['job_id'], f'Built {count} plugin archives')
        job_store.update_job(job['job_id'], status='completed', progress=100, result={'count': count})
        typer.echo(f'Built {count} plugin archives')
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Build failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('install-plugin')
def install_plugin(plugin_archive: Path, env_file: str = '.env') -> None:
    """Install a plugin archive AND start it immediately.

    Identical behaviour to clicking Install in the Control Center GUI —
    no separate start-plugin call required.
    """
    settings, job_store, logger = _job_services(env_file)
    installer = PluginInstaller(
        settings=settings,
        installed_plugins_dir=settings.runtime_installed_plugins_dir,
        registry_file=settings.manifests_dir / 'installed_plugins.json',
        state_file=settings.manifests_dir / 'plugin_state.json',
    )
    runtime = PluginRuntime(
        settings.runtime_installed_plugins_dir,
        settings.manifests_dir / 'plugin_state.json',
        settings=settings,
    )
    job = job_store.create_job('install_plugin', str(plugin_archive), {'archive': str(plugin_archive), 'auto_start': True})
    logger.append_job_log(job['job_id'], f'Starting install for {plugin_archive}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=10)
        result = installer.install_plugin(plugin_archive)
        plugin_id = result['id']
        logger.append_job_log(job['job_id'], f"Installed plugin: {result['name']} ({result['version']})")
        logger.append_job_log(job['job_id'], f"Installed dir: {result['installed_dir']}")

        # Auto-start — same as CC GUI install flow
        job_store.update_job(job['job_id'], status='running', progress=70)
        logger.append_job_log(job['job_id'], f'Auto-starting {plugin_id}')
        start_result = runtime.start_plugin(plugin_id)
        logger.append_job_log(job['job_id'], f'Started: {start_result}')
        result['start_result'] = start_result

        if result.get('public_url'):
            logger.append_job_log(job['job_id'], f"Open URL: {result['public_url']}")

        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(f"Installed: {result['name']} ({result['version']})")
        typer.echo(f"Started:   {plugin_id}")
        if result.get('public_url'):
            typer.echo(f"Open URL:  {result['public_url']}")
        typer.echo(f"Job ID:    {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Install/start failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('start-plugin')
def start_plugin(plugin_id: str, env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
    job = job_store.create_job('start_plugin', plugin_id, {'plugin_id': plugin_id})
    logger.append_job_log(job['job_id'], f'Starting plugin {plugin_id}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=25)
        result = runtime.start_plugin(plugin_id)
        logger.append_job_log(job['job_id'], str(result))
        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(str(result))
        typer.echo(f"Job ID: {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Start failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('stop-plugin')
def stop_plugin(plugin_id: str, env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
    job = job_store.create_job('stop_plugin', plugin_id, {'plugin_id': plugin_id})
    logger.append_job_log(job['job_id'], f'Stopping plugin {plugin_id}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=25)
        result = runtime.stop_plugin(plugin_id)
        logger.append_job_log(job['job_id'], str(result))
        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(str(result))
        typer.echo(f"Job ID: {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Stop failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('restart-plugin')
def restart_plugin(plugin_id: str, env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
    job = job_store.create_job('restart_plugin', plugin_id, {'plugin_id': plugin_id})
    logger.append_job_log(job['job_id'], f'Restarting plugin {plugin_id}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=25)
        result = runtime.restart_plugin(plugin_id)
        logger.append_job_log(job['job_id'], str(result))
        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(str(result))
        typer.echo(f"Job ID: {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Restart failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('healthcheck-plugin')
def healthcheck_plugin(plugin_id: str, env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
    job = job_store.create_job('healthcheck_plugin', plugin_id, {'plugin_id': plugin_id})
    logger.append_job_log(job['job_id'], f'Healthchecking plugin {plugin_id}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=25)
        result = runtime.healthcheck_plugin(plugin_id)
        logger.append_job_log(job['job_id'], str(result))
        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(str(result))
        typer.echo(f"Job ID: {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Healthcheck failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('uninstall-plugin')
def uninstall_plugin(plugin_id: str, env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    installer = PluginInstaller(
        settings=settings,
        installed_plugins_dir=settings.runtime_installed_plugins_dir,
        registry_file=settings.manifests_dir / 'installed_plugins.json',
        state_file=settings.manifests_dir / 'plugin_state.json',
    )
    job = job_store.create_job('uninstall_plugin', plugin_id, {'plugin_id': plugin_id})
    logger.append_job_log(job['job_id'], f'Uninstalling plugin {plugin_id}')
    try:
        job_store.update_job(job['job_id'], status='running', progress=25)
        result = installer.uninstall_plugin(plugin_id)
        logger.append_job_log(job['job_id'], str(result))
        job_store.update_job(job['job_id'], status='completed', progress=100, result=result)
        typer.echo(str(result))
        typer.echo(f"Job ID: {job['job_id']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Uninstall failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


@app.command('install-core-route')
def install_core_route(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    url = ReverseProxyService(settings).apply_core_route()
    typer.echo(f'Installed core Caddy route: {url}')


@app.command('core-service-status')
def core_service_status(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    typer.echo(CoreServiceManager(settings).status())


@app.command('run-control-shell')
def run_control_shell(env_file: str = '.env') -> None:
    settings = load_settings(env_file)
    ensure_runtime_dirs(settings)
    typer.echo(f'Control shell placeholder. Target bind: {settings.control_center_bind}:{settings.control_center_port}')


@app.command('self-heal')
def self_heal(env_file: str = '.env') -> None:
    settings, job_store, logger = _job_services(env_file)
    runtime = PluginRuntime(settings.runtime_installed_plugins_dir, settings.manifests_dir / 'plugin_state.json', settings=settings)
    registry = PluginRegistry(settings.manifests_dir / 'installed_plugins.json')
    proxy = ReverseProxyService(settings)
    catalog = load_app_catalog(str(settings.app_catalog_file))

    job = job_store.create_job('self_heal', 'host', {'env_file': env_file})

    def log(message: str) -> None:
        typer.echo(message)
        logger.append_job_log(job['job_id'], message)

    def progress(value: int, message: str) -> None:
        job_store.update_job(job['job_id'], status='running', progress=value, message=message)
        log(message)

    service = RecoveryService(
        settings=settings,
        app_catalog=catalog,
        caddy_service=proxy,
        plugin_runtime=runtime,
        plugin_registry=registry,
        log_fn=log,
        progress_fn=progress,
    )

    try:
        progress(1, 'Queued self-heal job')
        summary = service.self_heal()

        # Always ensure the watchdog is installed/running after a self-heal
        # so that CC and Pi-hole stay up even after future failures.
        _install_watchdog(settings, echo_fn=log)

        job_store.update_job(job['job_id'], status='completed', progress=100, result=summary)
        typer.echo(f"Job ID: {job['job_id']}")
        typer.echo(f"Docker root: {summary['docker_root']}")
        typer.echo(f"Docker root changed: {summary['docker_root_changed']}")
        typer.echo(f"Docker repaired: {summary.get('docker_repaired', False)}")
        typer.echo(f"Rebound routes: {len(summary['rebound_routes'])}")
        for item in summary['rebound_routes']:
            typer.echo(f"  {item.get('plugin_id')}: {item.get('public_url')}")
        typer.echo(f"Started plugins: {len(summary['started_plugins'])}")
        for item in summary['started_plugins']:
            typer.echo(f"  {item.get('plugin_id')}: {item.get('public_url')}")
        if summary.get('timed_out_plugins'):
            typer.echo('Timed out plugins:')
            for plugin_id in summary['timed_out_plugins']:
                typer.echo(f"  - {plugin_id}")
        if summary.get('warnings'):
            typer.echo('Warnings:')
            for warning in summary['warnings']:
                typer.echo(f"  - {warning}")
        if summary.get('pihole'):
            typer.echo(f"Pi-hole: {summary['pihole']}")
    except Exception as exc:
        logger.append_job_log(job['job_id'], f'Self-heal failed: {exc}')
        job_store.update_job(job['job_id'], status='failed', progress=100, error=str(exc))
        raise


if __name__ == '__main__':
    app()
