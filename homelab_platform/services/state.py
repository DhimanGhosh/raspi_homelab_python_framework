import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clean_meta(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if not str(k).startswith("_")}


def write_install_state(apps_dir: Path, app_id: str, payload: dict):
    dst = apps_dir / app_id
    dst.mkdir(parents=True, exist_ok=True)
    current = read_json(dst / "install_state.json", default={}) or {}
    current.update(payload)
    write_json(dst / "install_state.json", current)


def mark_install_attempt(apps_dir: Path, app_id: str, meta: dict, log_path: str | None = None):
    dst = apps_dir / app_id
    dst.mkdir(parents=True, exist_ok=True)
    write_json(dst / "metadata.json", _clean_meta(meta))
    write_install_state(apps_dir, app_id, {
        "app_id": app_id,
        "version": meta.get("version"),
        "status": "installing",
        "last_attempt_at": utc_now(),
        "last_error": None,
        "log_path": log_path,
    })


def mark_install_failure(apps_dir: Path, app_id: str, meta: dict, error: str, log_path: str | None = None):
    dst = apps_dir / app_id
    dst.mkdir(parents=True, exist_ok=True)
    write_json(dst / "metadata.json", _clean_meta(meta))
    write_install_state(apps_dir, app_id, {
        "app_id": app_id,
        "version": meta.get("version"),
        "status": "failed",
        "last_attempt_at": utc_now(),
        "last_error": error,
        "log_path": log_path,
    })


def clear_app_state(apps_dir: Path, app_id: str):
    dst = apps_dir / app_id
    if dst.exists():
        shutil.rmtree(dst)


def record_install_state(apps_dir: Path, app_id: str, meta: dict, extracted: Path, runtime_dir: Path | None = None, log_path: str | None = None):
    dst = apps_dir / app_id
    dst.mkdir(parents=True, exist_ok=True)
    write_json(dst / "metadata.json", _clean_meta(meta))
    write_json(dst / "install_state.json", {
        "app_id": app_id,
        "version": meta.get("version"),
        "status": "installed",
        "installed_at": utc_now(),
        "runtime_dir": str(runtime_dir) if runtime_dir else None,
        "last_error": None,
        "log_path": log_path,
    })
    bundle_copy = dst / "bundle"
    if bundle_copy.exists():
        shutil.rmtree(bundle_copy)
    shutil.copytree(extracted, bundle_copy)


def _runtime_dir_for(settings, app_id: str, state: dict) -> Path:
    runtime_value = state.get("runtime_dir")
    return Path(runtime_value) if runtime_value else (settings.runtime_dir / app_id)


def _is_effectively_installed(settings, app_id: str, state: dict) -> bool:
    runtime_dir = _runtime_dir_for(settings, app_id, state)
    compose = runtime_dir / "docker-compose.yml"
    snippet = settings.caddy_apps_dir / f"{app_id}.caddy"
    return compose.exists() and snippet.exists()


def _is_stale_install(settings, app_id: str, state: dict) -> bool:
    return state.get("status") == "installed" and not _is_effectively_installed(settings, app_id, state)


def load_installed_apps(apps_dir: Path, settings=None) -> list[dict]:
    out = []
    if not apps_dir.exists():
        return out
    for p in sorted(apps_dir.iterdir()):
        meta = read_json(p / "metadata.json", default=None)
        if not meta:
            continue
        state = read_json(p / "install_state.json", default={}) or {}
        app_id = meta.get("id", p.name)
        if settings is not None and _is_stale_install(settings, app_id, state):
            continue
        combined = dict(meta)
        combined["install_status"] = state.get("status", "unknown")
        combined["last_error"] = state.get("last_error")
        combined["log_path"] = state.get("log_path")
        combined["runtime_dir"] = state.get("runtime_dir")
        combined["is_installed"] = _is_effectively_installed(settings, app_id, state) if settings is not None else state.get("status") == "installed"
        out.append(combined)
    return out
