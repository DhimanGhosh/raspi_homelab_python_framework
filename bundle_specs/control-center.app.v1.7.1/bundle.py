import json
import subprocess
from datetime import datetime
from pathlib import Path

import requests


def _run(cmd, check=True):
    proc = subprocess.run(list(map(str, cmd)), text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def _sudo_write(path: Path, content: str):
    proc = subprocess.run(['sudo', 'tee', str(path)], input=content, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed writing {path}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")


def _log(log_path: Path | None, message: str):
    if not log_path:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as fh:
        fh.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")


def _wait_health(url: str, timeout: int = 90, log_path: Path | None = None) -> bool:
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.get(url, timeout=3)
            _log(log_path, f"HEALTH {url} -> {response.status_code}")
            if response.status_code < 500:
                return True
        except Exception as exc:
            _log(log_path, f"HEALTH {url} -> exception: {exc}")
        time.sleep(1)
    return False


def _record_state(settings, app_id: str, meta: dict, log_path: str | None):
    app_dir = settings.apps_dir / app_id
    _run(['sudo', 'mkdir', '-p', app_dir])
    _sudo_write(app_dir / 'metadata.json', json.dumps(meta, indent=2))
    state = {
        'app_id': app_id,
        'version': meta.get('version'),
        'status': 'installed',
        'runtime_dir': str(settings.homelab_root / 'control-center' / 'current'),
        'log_path': log_path,
        'last_error': None,
    }
    _sudo_write(app_dir / 'install_state.json', json.dumps(state, indent=2))


def _clear_state(settings, app_id: str):
    _run(['sudo', 'rm', '-rf', settings.apps_dir / app_id], check=False)


def install(settings, extracted, meta):
    log_path = Path(meta.get('_log_path')) if meta.get('_log_path') else None
    payload = extracted / 'payload'
    app_dir = payload / 'app'
    cc_pkg = app_dir / 'control_center_app' / 'web.py'
    service_template = payload / 'service' / 'control-center.service.template'
    if not cc_pkg.exists():
        raise RuntimeError(f'Missing Control Center app payload: {cc_pkg}')
    if not service_template.exists():
        raise RuntimeError(f'Missing service template: {service_template}')

    repo_root = settings.env_file.parent.resolve()
    repo_homelabctl = repo_root / '.venv' / 'bin' / 'homelabctl'
    if not repo_homelabctl.exists():
        raise RuntimeError(f'homelabctl not found at {repo_homelabctl}')

    app_id = meta['id']
    user = _run(['bash', '-lc', 'whoami']).stdout.strip() or 'pi'
    cc_root = settings.homelab_root / 'control-center'
    current_dir = cc_root / 'current'
    venv_dir = cc_root / 'venv'
    venv_python = venv_dir / 'bin' / 'python'
    backup_dir = settings.backups_dir / 'control-center.prev'
    service_name = getattr(settings, 'control_center_service_name', 'raspi-homelab-python-framework.service')
    legacy_name = getattr(settings, 'legacy_cc_service', 'pi-control-center.service')

    _log(log_path, f'Starting control-center install version={meta.get("version")}')
    _run(['sudo', 'mkdir', '-p', settings.homelab_root, settings.backups_dir, settings.logs_dir, settings.apps_dir, settings.installers_dir, cc_root])
    _run(['sudo', 'chown', '-R', f'{user}:{user}', settings.homelab_root])

    if backup_dir.exists():
        _run(['sudo', 'rm', '-rf', backup_dir], check=False)
    if current_dir.exists():
        _run(['sudo', 'mv', current_dir, backup_dir], check=False)
        _log(log_path, f'Moved previous deployment to {backup_dir}')

    _run(['sudo', 'mkdir', '-p', current_dir])
    _run(['sudo', 'rsync', '-a', f'{payload}/', f'{current_dir}/'])
    _run(['sudo', 'chown', '-R', f'{user}:{user}', cc_root])

    if venv_dir.exists():
        _run(['sudo', 'rm', '-rf', venv_dir], check=False)
    _run(['sudo', 'python3', '-m', 'venv', venv_dir])
    _run(['sudo', venv_python, '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel'])
    _run(['sudo', venv_python, '-m', 'pip', 'install', '-r', current_dir / 'app' / 'requirements.txt'])

    service_content = service_template.read_text(encoding='utf-8').format(
        app_dir=current_dir / 'app',
        env_file=settings.env_file.resolve(),
        python=venv_python,
        user=user,
        repo_homelabctl=repo_homelabctl,
        repo_root=repo_root,
    )
    _sudo_write(Path('/etc/systemd/system') / service_name, service_content)

    cc_caddy = f"""https://{settings.tailscale_fqdn}:{meta['port']} {{
    tls {settings.tailscale_cert_dir / (settings.tailscale_fqdn + '.crt')} {settings.tailscale_cert_dir / (settings.tailscale_fqdn + '.key')}
    reverse_proxy {meta['local_upstream']}
}}
"""
    _sudo_write(settings.caddy_apps_dir / 'control-center.caddy', cc_caddy)

    _run(['sudo', 'systemctl', 'stop', legacy_name], check=False)
    _run(['sudo', 'systemctl', 'disable', legacy_name], check=False)
    _run(['sudo', 'systemctl', 'daemon-reload'])
    _run(['sudo', 'systemctl', 'enable', service_name], check=False)
    _run(['sudo', 'systemctl', 'restart', service_name], check=False)
    _run(['sudo', 'caddy', 'validate', '--config', settings.caddyfile])
    _run(['sudo', 'systemctl', 'restart', 'caddy'])

    if not _wait_health(meta['health_url'], int(meta.get('health_timeout', 90)), log_path):
        status = _run(['sudo', 'systemctl', 'status', service_name, '--no-pager'], check=False)
        journal = _run(['sudo', 'journalctl', '-u', service_name, '-n', '200', '--no-pager'], check=False)
        _log(log_path, 'SYSTEMCTL STATUS\n' + (status.stdout or '') + (status.stderr or ''))
        _log(log_path, 'JOURNAL\n' + (journal.stdout or '') + (journal.stderr or ''))
        raise RuntimeError(f"Health check failed for control-center at {meta['health_url']}")

    clean_meta = {k: v for k, v in meta.items() if not str(k).startswith('_')}
    _record_state(settings, app_id, clean_meta, str(log_path) if log_path else None)
    return {'ok': True, 'message': f"Installed {meta['name']} -> https://{settings.tailscale_fqdn}:{meta['port']}/", 'log_path': str(log_path) if log_path else None}


def uninstall(settings, extracted, meta):
    log_path = Path(meta.get('_log_path')) if meta.get('_log_path') else None
    service_name = getattr(settings, 'control_center_service_name', 'raspi-homelab-python-framework.service')
    cc_root = settings.homelab_root / 'control-center'
    _log(log_path, 'Removing Control Center')
    _run(['sudo', 'systemctl', 'stop', service_name], check=False)
    _run(['sudo', 'systemctl', 'disable', service_name], check=False)
    _run(['sudo', 'rm', '-f', Path('/etc/systemd/system') / service_name], check=False)
    _run(['sudo', 'systemctl', 'daemon-reload'], check=False)
    _run(['sudo', 'rm', '-rf', cc_root], check=False)
    _run(['sudo', 'rm', '-f', settings.caddy_apps_dir / 'control-center.caddy'], check=False)
    _run(['sudo', 'caddy', 'validate', '--config', settings.caddyfile], check=False)
    _run(['sudo', 'systemctl', 'restart', 'caddy'], check=False)
    _clear_state(settings, meta['id'])
    return {'ok': True, 'message': f"Removed {meta['name']}", 'log_path': str(log_path) if log_path else None}
