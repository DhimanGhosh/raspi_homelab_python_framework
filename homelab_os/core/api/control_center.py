from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse

from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.plugin_manager import PluginInstaller
from homelab_os.core.plugin_manager.registry import PluginRegistry
from homelab_os.core.plugin_manager.runtime import PluginRuntime
from homelab_os.core.services.jobs import JobStore
from homelab_os.core.services.logging_service import LoggingService

router = APIRouter()

def _services():
    settings = load_settings(".env")
    ensure_runtime_dirs(settings)
    registry = PluginRegistry(settings.manifests_dir / "installed_plugins.json")
    jobs = JobStore(settings.manifests_dir / "jobs.json")
    logs = LoggingService(settings.runtime_jobs_dir)
    runtime = PluginRuntime(
        settings.runtime_installed_plugins_dir,
        settings.manifests_dir / "plugin_state.json",
    )
    installer = PluginInstaller(
        settings=settings,
        installed_plugins_dir=settings.runtime_installed_plugins_dir,
        registry_file=settings.manifests_dir / "installed_plugins.json",
        state_file=settings.manifests_dir / "plugin_state.json",
    )
    return settings, registry, jobs, logs, runtime, installer

@router.get("/control-center", response_class=HTMLResponse)
def control_center_page() -> str:
    return (Path(__file__).resolve().parents[1] / "templates" / "control_center.html").read_text(encoding="utf-8")

@router.get("/control-center/summary")
def control_center_summary() -> dict:
    settings, registry, jobs, logs, runtime, installer = _services()
    return {
        "installed_plugins": registry.list_all(),
        "jobs": jobs.list_jobs(),
        "tailscale_fqdn": settings.tailscale_fqdn,
        "control_center_port": settings.control_center_public_port,
    }

def _install_job(job_id: str, archive_path: str) -> None:
    settings, registry, jobs, logs, runtime, installer = _services()
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

@router.post("/control-center/install")
def control_center_install(archive_path: str, background_tasks: BackgroundTasks) -> dict:
    settings, registry, jobs, logs, runtime, installer = _services()
    archive = Path(archive_path)
    if not archive.exists():
        raise HTTPException(status_code=404, detail=f"Archive not found: {archive}")

    job = jobs.create_job("install_plugin", archive_path, {"archive": archive_path})
    logs.append_job_log(job["job_id"], "Queued install job")
    background_tasks.add_task(_install_job, job["job_id"], archive_path)
    return {"job_id": job["job_id"]}

def _runtime_job(job_id: str, action: str, plugin_id: str) -> None:
    settings, registry, jobs, logs, runtime, installer = _services()
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
        else:
            raise RuntimeError(f"Unsupported action: {action}")
        logs.append_job_log(job_id, str(result))
        jobs.update_job(job_id, status="completed", progress=100, result=result)
    except Exception as exc:
        logs.append_job_log(job_id, f"{action} failed: {exc}")
        jobs.update_job(job_id, status="failed", progress=100, error=str(exc))

@router.post("/control-center/plugins/{plugin_id}/{action}")
def control_center_plugin_action(plugin_id: str, action: str, background_tasks: BackgroundTasks) -> dict:
    if action not in {"start", "stop", "restart", "healthcheck"}:
        raise HTTPException(status_code=400, detail="Unsupported action")
    settings, registry, jobs, logs, runtime, installer = _services()
    job = jobs.create_job(f"{action}_plugin", plugin_id, {"plugin_id": plugin_id})
    logs.append_job_log(job["job_id"], f"Queued {action} for {plugin_id}")
    background_tasks.add_task(_runtime_job, job["job_id"], action, plugin_id)
    return {"job_id": job["job_id"]}
