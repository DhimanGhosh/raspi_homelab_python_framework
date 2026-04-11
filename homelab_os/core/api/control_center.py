from __future__ import annotations

from pathlib import Path
from shutil import disk_usage
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse

from homelab_os import __version__
from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.plugin_manager import PluginInstaller
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.services.jobs import JobStore
from homelab_os.core.services.logging_service import LoggingService
from homelab_os.core.services.reverse_proxy import ReverseProxyService

router = APIRouter()

APP_META = {
    "control-center": {"name": "Control Center", "port": 8444},
    "api-gateway": {"name": "API Gateway", "port": 8456},
    "dictionary": {"name": "Dictionary", "port": 8455},
    "files": {"name": "Files", "port": 8449},
    "homarr": {"name": "Homarr", "port": 8453},
    "home-assistant": {"name": "Home Assistant", "port": 8450},
    "jellyfin": {"name": "Jellyfin", "port": 8446},
    "link-downloader": {"name": "Media Downloader", "port": 8460},
    "music-player": {"name": "Music Player", "port": 8459},
    "navidrome": {"name": "Navidrome", "port": 8445},
    "nextcloud": {"name": "Nextcloud", "port": 8448},
    "personal-library": {"name": "Personal Library", "port": 8454},
    "pihole": {"name": "Pi-hole", "port": 8447},
    "status": {"name": "Pi Status Board", "port": 8451},
    "voice-ai": {"name": "Pi Voice AI", "port": 8452},
}

def _gb(value: int) -> float:
    return round(value / (1024 ** 3), 2)

def _usage(path: Path) -> dict:
    try:
        total, used, free = disk_usage(path)
        return {"total_gb": _gb(total), "used_gb": _gb(used), "free_gb": _gb(free)}
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0}

def _services():
    settings = load_settings(".env")
    ensure_runtime_dirs(settings)
    registry = PluginRegistry(settings.manifests_dir / "installed_plugins.json")
    jobs = JobStore(settings.manifests_dir / "jobs.json")
    logs = LoggingService(settings.runtime_jobs_dir)
    runtime = PluginRuntime(
        settings.runtime_installed_plugins_dir,
        settings.manifests_dir / "plugin_state.json",
        settings=settings,
    )
    installer = PluginInstaller(
        settings=settings,
        installed_plugins_dir=settings.runtime_installed_plugins_dir,
        registry_file=settings.manifests_dir / "installed_plugins.json",
        state_file=settings.manifests_dir / "plugin_state.json",
    )
    proxy = ReverseProxyService(settings)
    return settings, registry, jobs, logs, runtime, installer, proxy

def _bundle_groups(settings: object) -> dict:
    build_dir = settings.build_dir
    grouped = {}
    if not build_dir.exists():
        return grouped
    for file in build_dir.iterdir():
        if not file.is_file() or file.suffix != ".tgz":
            continue
        app_id = file.stem.replace("_", "-")
        grouped.setdefault(app_id, []).append({"filename": file.name, "path": str(file)})
    return grouped

def _catalog_with_runtime():
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    installed = registry.list_all()
    bundle_groups = _bundle_groups(settings)
    state_file = settings.manifests_dir / "plugin_state.json"
    state_payload = {}
    if state_file.exists():
        import json
        state_payload = json.loads(state_file.read_text(encoding="utf-8")).get("plugins", {})

    visible_ids = set(installed.keys()) | set(bundle_groups.keys()) | {"control-center"}
    catalog = []
    for app_id in sorted(visible_ids):
        meta = APP_META.get(app_id, {"name": app_id.replace("-", " ").title(), "port": None})
        installed_meta = installed.get(app_id)
        plugin_state = state_payload.get(app_id, {})
        public_url = None
        if app_id == "control-center":
            public_url = f"https://{settings.tailscale_fqdn}:{settings.control_center_public_port}"
        elif installed_meta:
            public_url = installed_meta.get("public_url")

        catalog.append({
            "id": app_id,
            "name": meta["name"],
            "latest_version": installed_meta.get("version") if installed_meta else (meta.get("version") or None),
            "installed_version": installed_meta.get("version") if installed_meta else None,
            "installed": installed_meta is not None or app_id == "control-center",
            "public_url": public_url,
            "port": meta["port"],
            "bundles": bundle_groups.get(app_id, []),
            "bundle_count": len(bundle_groups.get(app_id, [])),
            "status": plugin_state.get("status", "running" if app_id == "control-center" else ("stopped" if installed_meta else "not-installed")),
        })
    return settings, catalog, jobs

@router.get("/control-center", response_class=HTMLResponse)
def control_center_page() -> str:
    return (Path(__file__).resolve().parents[1] / "templates" / "control_center_full.html").read_text(encoding="utf-8")

@router.get("/control-center/summary")
def control_center_summary() -> dict:
    settings, catalog, jobs = _catalog_with_runtime()
    return {
        "current_version": __version__,
        "apps": catalog,
        "jobs": jobs.list_jobs(),
        "tailscale_fqdn": settings.tailscale_fqdn,
        "total_bundles": sum(item["bundle_count"] for item in catalog),
        "backup_root": str(settings.backups_dir),
        "docker_root": str(settings.docker_root_dir),
        "nas_usage": _usage(settings.nas_mount),
        "homelab_usage": _usage(settings.homelab_root),
        "root_usage": _usage(Path("/")),
        "notifications": [],
    }

def _install_job(job_id: str, archive_path: str) -> None:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    try:
        jobs.update_job(job_id, status="running", progress=10)
        logs.append_job_log(job_id, f"Installing plugin from {archive_path}")
        result = installer.install_plugin(Path(archive_path))
        logs.append_job_log(job_id, f"Installed plugin: {result['name']} ({result['version']})")
        if result.get("public_url"):
            logs.append_job_log(job_id, f"Open URL: {result['public_url']}")
        jobs.update_job(job_id, status="completed", progress=100, result=result)
    except Exception as exc:
        logs.append_job_log(job_id, f"Install failed: {exc}")
        jobs.update_job(job_id, status="failed", progress=100, error=str(exc))

def _runtime_job(job_id: str, action: str, plugin_id: str) -> None:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    try:
        jobs.update_job(job_id, status="running", progress=25)
        logs.append_job_log(job_id, f"{action} plugin {plugin_id}")
        if action == "start":
            result = runtime.start_plugin(plugin_id)
        elif action == "stop":
            result = runtime.stop_plugin(plugin_id)
        elif action == "restart":
            result = runtime.restart_plugin(plugin_id)
        elif action == "healthcheck":
            result = runtime.healthcheck_plugin(plugin_id)
        elif action == "uninstall":
            result = installer.uninstall_plugin(plugin_id)
        else:
            raise RuntimeError(f"Unsupported action: {action}")
        logs.append_job_log(job_id, str(result))
        jobs.update_job(job_id, status="completed", progress=100, result=result)
    except Exception as exc:
        logs.append_job_log(job_id, f"{action} failed: {exc}")
        jobs.update_job(job_id, status="failed", progress=100, error=str(exc))

@router.post("/control-center/install")
def control_center_install(archive_path: str, background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    archive = Path(archive_path)
    if not archive.exists():
        raise HTTPException(status_code=404, detail=f"Archive not found: {archive}")
    job = jobs.create_job("install_plugin", archive_path, {"archive": archive_path})
    logs.append_job_log(job["job_id"], "Queued install job")
    background_tasks.add_task(_install_job, job["job_id"], archive_path)
    return {"job_id": job["job_id"]}

@router.post("/control-center/apps/{app_id}/install-bundle/{filename}")
def install_specific_bundle(app_id: str, filename: str, background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    archive = settings.build_dir / filename
    if not archive.exists():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")
    job = jobs.create_job("install_plugin", str(archive), {"archive": str(archive), "app_id": app_id})
    logs.append_job_log(job["job_id"], f"Queued install for {filename}")
    background_tasks.add_task(_install_job, job["job_id"], str(archive))
    return {"job_id": job["job_id"]}

@router.post("/control-center/bundles/{filename}/delete")
def delete_bundle(filename: str) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    archive = settings.build_dir / filename
    if archive.exists():
        archive.unlink()
    return {"ok": True, "filename": filename}

@router.post("/control-center/jobs/clear-completed")
def clear_completed_jobs() -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    removed = jobs.clear_completed()
    return {"ok": True, "removed": removed}

@router.post("/control-center/jobs/clear-all")
def clear_all_jobs() -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    removed = jobs.clear_all()
    return {"ok": True, "removed": removed}

@router.post("/control-center/marketplace/rescan")
def rescan_marketplace() -> dict:
    return {"ok": True}

@router.post("/control-center/install-all")
def install_all(background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    count = 0
    for app_id, bundles in _bundle_groups(settings).items():
        if app_id == "control-center":
            continue
        for bundle in bundles:
            job = jobs.create_job("install_plugin", bundle["path"], {"archive": bundle["path"], "app_id": app_id})
            logs.append_job_log(job["job_id"], f"Queued install for {bundle['filename']}")
            background_tasks.add_task(_install_job, job["job_id"], bundle["path"])
            count += 1
            break
    return {"ok": True, "queued": count}

@router.post("/control-center/update-all")
def update_all(background_tasks: BackgroundTasks) -> dict:
    settings, catalog, jobs = _catalog_with_runtime()
    count = 0
    logs = LoggingService(settings.runtime_jobs_dir)
    for app in catalog:
        if app["installed"] and app["id"] != "control-center":
            job = jobs.create_job("restart_plugin", app["id"], {"plugin_id": app["id"]})
            logs.append_job_log(job["job_id"], f"Queued restart for {app['id']}")
            background_tasks.add_task(_runtime_job, job["job_id"], "restart", app["id"])
            count += 1
    return {"ok": True, "queued": count}

@router.post("/control-center/plugins/{plugin_id}/{action}")
def control_center_plugin_action(plugin_id: str, action: str, background_tasks: BackgroundTasks) -> dict:
    if action not in {"start", "stop", "restart", "healthcheck", "uninstall"}:
        raise HTTPException(status_code=400, detail="Unsupported action")
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    job = jobs.create_job(f"{action}_plugin", plugin_id, {"plugin_id": plugin_id})
    logs.append_job_log(job["job_id"], f"Queued {action} for {plugin_id}")
    background_tasks.add_task(_runtime_job, job["job_id"], action, plugin_id)
    return {"job_id": job["job_id"]}
