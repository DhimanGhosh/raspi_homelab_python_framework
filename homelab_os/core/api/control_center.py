from __future__ import annotations

import json
import re
from pathlib import Path
from shutil import disk_usage

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse

from homelab_os import __version__
from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.plugin_manager import PluginInstaller
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.services.app_catalog import load_app_catalog
from homelab_os.core.services.jobs import JobStore
from homelab_os.core.services.logging_service import LoggingService
from homelab_os.core.services.reverse_proxy import ReverseProxyService

router = APIRouter()


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


def _version_key(version: str | None) -> tuple:
    if not version:
        return tuple()
    parts = re.split(r"[._-]", str(version).strip())
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _bundle_version_from_name(filename: str) -> str | None:
    match = re.search(r"\.v([^./]+(?:\.[^./]+)*)\.tgz$", filename)
    return match.group(1) if match else None


def _bundle_groups(settings: object) -> dict:
    build_dir = settings.build_dir
    grouped = {}
    if not build_dir.exists():
        return grouped

    for file in build_dir.iterdir():
        if not file.is_file() or file.suffix != ".tgz":
            continue

        stem = file.stem.replace("_", "-")
        app_id = stem.split(".v", 1)[0] if ".v" in stem else stem
        version = _bundle_version_from_name(file.name)
        grouped.setdefault(app_id, []).append(
            {
                "filename": file.name,
                "path": str(file),
                "version": version,
            }
        )

    for bundles in grouped.values():
        bundles.sort(key=lambda item: (_version_key(item.get("version")), item["filename"]), reverse=True)

    return grouped


def _load_state_payload(settings) -> dict:
    state_file = settings.manifests_dir / "plugin_state.json"
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8")).get("plugins", {})


def _app_name(app_id: str, installed_meta: dict | None, catalog_meta: dict | None) -> str:
    if catalog_meta and catalog_meta.get("name"):
        return catalog_meta["name"]
    if installed_meta and installed_meta.get("name"):
        return installed_meta["name"]
    return app_id.replace("-", " ").title()


def _app_port(app_id: str, settings, catalog_meta: dict | None):
    if app_id == "control-center":
        return settings.control_center_public_port
    if catalog_meta:
        return catalog_meta.get("public_port")
    return None


def _catalog_with_runtime():
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    installed = registry.list_all()
    bundle_groups = _bundle_groups(settings)
    state_payload = _load_state_payload(settings)
    app_catalog = load_app_catalog(str(settings.app_catalog_file))

    visible_ids = (set(installed.keys()) | set(bundle_groups.keys())) - {"control-center"}

    catalog = []
    for app_id in sorted(visible_ids):
        installed_meta = installed.get(app_id)
        catalog_meta = app_catalog.get_app(app_id)
        plugin_state = state_payload.get(app_id, {})
        bundles = bundle_groups.get(app_id, [])
        latest_bundle = bundles[0] if bundles else None
        installed_version = installed_meta.get("version") if installed_meta else None
        latest_version = latest_bundle.get("version") if latest_bundle else installed_version
        update_available = bool(
            installed_meta and latest_bundle and _version_key(latest_version) > _version_key(installed_version)
        )

        catalog.append({
            "id": app_id,
            "name": _app_name(app_id, installed_meta, catalog_meta),
            "latest_version": latest_version,
            "installed_version": installed_version,
            "installed": installed_meta is not None,
            "public_url": installed_meta.get("public_url") if installed_meta else None,
            "port": _app_port(app_id, settings, catalog_meta),
            "bundles": bundles,
            "bundle_count": len(bundles),
            "status": plugin_state.get("status", "stopped" if installed_meta else "not-installed"),
            "latest_bundle_filename": latest_bundle.get("filename") if latest_bundle else None,
            "update_available": update_available,
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


def _install_job(job_id: str, archive_path: str, auto_start: bool = False) -> None:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    try:
        jobs.update_job(job_id, status="running", progress=10)
        logs.append_job_log(job_id, f"Installing plugin from {archive_path}")
        result = installer.install_plugin(Path(archive_path))
        logs.append_job_log(job_id, f"Installed plugin: {result['name']} ({result['version']})")
        if result.get("public_url"):
            logs.append_job_log(job_id, f"Open URL: {result['public_url']}")
        if auto_start:
            plugin_id = result["id"]
            jobs.update_job(job_id, progress=70)
            logs.append_job_log(job_id, f"Auto-starting plugin {plugin_id}")
            start_result = runtime.start_plugin(plugin_id)
            logs.append_job_log(job_id, f"Start result: {start_result}")
            result["start_result"] = start_result
        jobs.update_job(job_id, status="completed", progress=100, result=result)
    except Exception as exc:
        logs.append_job_log(job_id, f"Install failed: {exc}")
        try:
            if 'result' in locals() and auto_start and result.get("id"):
                logs.append_job_log(job_id, f"Rolling back failed install for {result['id']}")
                installer.uninstall_plugin(result["id"])
        except Exception as rollback_exc:
            logs.append_job_log(job_id, f"Rollback failed: {rollback_exc}")
        jobs.update_job(job_id, status="failed", progress=100, error=str(exc))


@router.post("/control-center/install")
def control_center_install(archive_path: str, background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    archive = Path(archive_path)
    if not archive.exists():
        raise HTTPException(status_code=404, detail=f"Archive not found: {archive}")
    job = jobs.create_job("install_plugin", archive_path, {"archive": archive_path, "auto_start": True})
    logs.append_job_log(job["job_id"], "Queued install job")
    background_tasks.add_task(_install_job, job["job_id"], archive_path, True)
    return {"job_id": job["job_id"]}


@router.post("/control-center/apps/{app_id}/install-bundle/{filename}")
def install_specific_bundle(app_id: str, filename: str, background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    archive = settings.build_dir / filename
    if not archive.exists():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")
    job = jobs.create_job("install_plugin", str(archive), {"archive": str(archive), "app_id": app_id, "auto_start": True})
    logs.append_job_log(job["job_id"], f"Queued install for {filename}")
    background_tasks.add_task(_install_job, job["job_id"], str(archive), True)
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
        if app_id == "control-center" or not bundles:
            continue
        bundle = bundles[0]
        job = jobs.create_job("install_plugin", bundle["path"], {"archive": bundle["path"], "app_id": app_id, "auto_start": True})
        logs.append_job_log(job["job_id"], f"Queued install for {bundle['filename']}")
        background_tasks.add_task(_install_job, job["job_id"], bundle["path"], True)
        count += 1
    return {"ok": True, "queued": count}


@router.post("/control-center/update-all")
def update_all(background_tasks: BackgroundTasks) -> dict:
    settings, catalog, jobs = _catalog_with_runtime()
    logs = LoggingService(settings.runtime_jobs_dir)
    count = 0
    for app in catalog:
        if app.get("installed") and app.get("update_available") and app.get("latest_bundle_filename"):
            archive = settings.build_dir / app["latest_bundle_filename"]
            job = jobs.create_job("install_plugin", str(archive), {"archive": str(archive), "app_id": app["id"], "auto_start": True})
            logs.append_job_log(job["job_id"], f"Queued update for {app['id']} -> {app['latest_bundle_filename']}")
            background_tasks.add_task(_install_job, job["job_id"], str(archive), True)
            count += 1
    return {"ok": True, "queued": count}


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


@router.post("/control-center/plugins/{plugin_id}/{action}")
def control_center_plugin_action(plugin_id: str, action: str, background_tasks: BackgroundTasks) -> dict:
    if action not in {"start", "stop", "restart", "healthcheck", "uninstall"}:
        raise HTTPException(status_code=400, detail="Unsupported action")
    settings, registry, jobs, logs, runtime, installer, proxy = _services()
    job = jobs.create_job(f"{action}_plugin", plugin_id, {"plugin_id": plugin_id})
    logs.append_job_log(job["job_id"], f"Queued {action} for {plugin_id}")
    background_tasks.add_task(_runtime_job, job["job_id"], action, plugin_id)
    return {"job_id": job["job_id"]}
