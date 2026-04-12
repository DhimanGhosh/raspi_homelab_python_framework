from __future__ import annotations

import json
from pathlib import Path

from homelab_os.core.config import Settings


_DEFAULT_APPS = {
    "control-center": {"name": "Control Center", "public_port": 8444, "entrypoint_path": "/", "_comment": "Special handling to ensure public port is always set to settings.control_center_public_port"},
    "api-gateway": {"name": "API Gateway", "public_port": 8456, "entrypoint_path": "/docs", "_comment": "All plugins API endpoints should be proxied through the API Gateway, and plugins should not be exposed directly to avoid port sprawl and to provide a single point for auth and TLS termination"},
    "dictionary": {"name": "Dictionary", "public_port": 8455, "entrypoint_path": "/", "_comment": "Personal dictionary app powered by a local LLM, not to be confused with the built-in dictionary feature in control center which is more of a quick reference tool"},
    "files": {"name": "Files", "public_port": 8449, "entrypoint_path": "/", "_comment": "NAS file browser and manager, not to be confused with Nextcloud which is more of a full featured cloud storage solution"},
    "homarr": {"name": "Homarr", "public_port": 8453, "entrypoint_path": "/", "_comment": "Dashboard app, can be used to create a custom dashboard for your homelab with links to your other apps and various widgets"},
    "home-assistant": {"name": "Home Assistant", "public_port": 8450, "entrypoint_path": "/", "_comment": "Deprecated, use voice-ai instead"},
    "jellyfin": {"name": "Jellyfin", "public_port": 8446, "entrypoint_path": "/", "_comment": "Deprecated, use media-player instead"},
    "link-downloader": {"name": "Media Downloader", "public_port": 8460, "entrypoint_path": "/", "_comment": "App for downloading media from various sources, can be used with RSS feeds for automatic downloads"},
    "music-player": {"name": "Music Player", "public_port": 8459, "entrypoint_path": "/", "_comment": "App for playing music, can be used with Link Downloader for a complete music management solution"},
    "navidrome": {"name": "Navidrome", "public_port": 8445, "entrypoint_path": "/", "_comment": "Deprecated, use music-player instead"},
    "nextcloud": {"name": "Nextcloud", "public_port": 8448, "entrypoint_path": "/", "_comment": "Deprecated, use files instead"},
    "personal-library": {"name": "Personal Library", "public_port": 8454, "entrypoint_path": "/", "_comment": "App for managing ebooks and other documents, can be used with Link Downloader for a complete ebook management solution"},
    "pihole": {"name": "Pi-hole", "public_port": 8447, "entrypoint_path": "/admin/", "_comment": "Network-wide ad blocker, make sure to set the upstream DNS server in Pi-hole to the IP of your homelab for it to work properly"},
    "status": {"name": "Pi Status Board", "public_port": 8451, "entrypoint_path": "/", "_comment": "Dashboard for monitoring the status of your Raspberry Pi and other devices in your homelab, can be used to display system metrics, network status, and more"},
    "voice-ai": {"name": "Pi Voice AI", "public_port": 8452, "entrypoint_path": "/", "_comment": "Voice assistant app powered by a local LLM, can be used for controlling other apps in your homelab with voice commands, setting reminders, and more"},
}

_DEFAULT_CORE_STACK = [
    "pihole",
    "files",
    "status",
    "voice-ai",
    "homarr",
    "personal-library",
    "dictionary",
    "api-gateway",
    "music-player",
    "link-downloader",
]


def _plugin_metadata(settings: Settings) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    for plugin_file in settings.plugins_dir.glob('*/plugin.json'):
        try:
            payload = json.loads(plugin_file.read_text(encoding='utf-8'))
        except Exception:
            continue
        plugin_id = payload.get('id')
        if not plugin_id:
            continue
        metadata[plugin_id] = {
            'name': payload.get('name') or plugin_id.replace('-', ' ').title(),
            'entrypoint_path': payload.get('entrypoint', {}).get('path') or '/',
            'internal_port': payload.get('network', {}).get('internal_port'),
            'version': payload.get('version'),
        }
    return metadata


def _default_config(settings: Settings) -> dict:
    plugin_meta = _plugin_metadata(settings)
    apps: dict[str, dict] = {}
    for app_id, defaults in _DEFAULT_APPS.items():
        merged = dict(defaults)
        merged.update({k: v for k, v in plugin_meta.get(app_id, {}).items() if v is not None})
        apps[app_id] = merged

    for app_id, meta in plugin_meta.items():
        apps.setdefault(app_id, meta)

    apps['control-center'] = {
        **apps.get('control-center', {}),
        'name': apps.get('control-center', {}).get('name', 'Control Center'),
        'public_port': settings.control_center_public_port,
        'entrypoint_path': '/',
    }
    return {'apps': apps, 'core_stack': list(_DEFAULT_CORE_STACK)}


def load_app_catalog(settings: Settings) -> dict:
    config = _default_config(settings)
    config_file = settings.app_catalog_file
    if config_file.exists():
        try:
            payload = json.loads(config_file.read_text(encoding='utf-8'))
            if isinstance(payload.get('apps'), dict):
                for app_id, app_meta in payload['apps'].items():
                    if isinstance(app_meta, dict):
                        merged = dict(config['apps'].get(app_id, {}))
                        merged.update(app_meta)
                        config['apps'][app_id] = merged
            if isinstance(payload.get('core_stack'), list):
                config['core_stack'] = [str(item) for item in payload['core_stack']]
        except Exception:
            pass

    config['apps']['control-center']['public_port'] = settings.control_center_public_port
    return config


def app_meta_map(settings: Settings) -> dict[str, dict]:
    return load_app_catalog(settings).get('apps', {})


def app_meta(settings: Settings, app_id: str) -> dict:
    metadata = app_meta_map(settings)
    return metadata.get(app_id, {'name': app_id.replace('-', ' ').title(), 'entrypoint_path': '/'})


def public_port_for_app(settings: Settings, app_id: str) -> int | None:
    value = app_meta(settings, app_id).get('public_port')
    return int(value) if value is not None else None


def public_url_for_app(settings: Settings, app_id: str) -> str | None:
    port = public_port_for_app(settings, app_id)
    if port is None:
        return None
    path = str(app_meta(settings, app_id).get('entrypoint_path') or '/').strip()
    if not path.startswith('/'):
        path = f'/{path}'
    return f'https://{settings.tailscale_fqdn}:{port}{path}'


def core_stack(settings: Settings) -> list[str]:
    payload = load_app_catalog(settings)
    return [str(item) for item in payload.get('core_stack', [])]
