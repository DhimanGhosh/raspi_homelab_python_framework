from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pathlib import Path

from homelab_os.core.config import load_settings
from homelab_os.core.plugin_manager.registry import PluginRegistry


router = APIRouter()


@router.get("/plugins")
def list_plugins() -> dict:
    settings = load_settings(".env")
    registry = PluginRegistry(settings.manifests_dir / "installed_plugins.json")
    return {"plugins": registry.list_all()}


@router.get("/plugins/{plugin_id}")
def get_plugin(plugin_id: str) -> dict:
    settings = load_settings(".env")
    registry = PluginRegistry(settings.manifests_dir / "installed_plugins.json")
    plugin = registry.get(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.get("/plugins/{plugin_id}/open")
def open_plugin(plugin_id: str) -> dict:
    settings = load_settings(".env")
    registry = PluginRegistry(settings.manifests_dir / "installed_plugins.json")
    plugin = registry.get(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    public_url = plugin.get("public_url")
    if not public_url:
        raise HTTPException(status_code=404, detail="Plugin has no public URL")

    return {"url": public_url}
