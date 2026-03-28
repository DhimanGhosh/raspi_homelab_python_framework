import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from waitress import serve

from homelab_platform.config import Settings
from homelab_platform.services.bundle_installer import BundleInstaller
from homelab_platform.services.health import health_snapshot
from homelab_platform.services.state import load_installed_apps

ENV_FILE = os.environ.get("HOMELAB_ENV_FILE", ".env")
settings = Settings.from_env_file(ENV_FILE)
app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)


def _read_bundle_metadata(bundle_path: Path) -> dict | None:
    try:
        if bundle_path.suffix == ".zip":
            with zipfile.ZipFile(bundle_path) as zf:
                for name in zf.namelist():
                    if name.endswith("metadata.json"):
                        return json.loads(zf.read(name).decode("utf-8"))
            return None
        with tarfile.open(bundle_path, "r:*") as tf:
            for member in tf.getmembers():
                if member.name.endswith("metadata.json"):
                    extracted = tf.extractfile(member)
                    if extracted:
                        return json.loads(extracted.read().decode("utf-8"))
    except Exception:
        return None
    return None


def discover_bundles():
    bundles_by_name = {}
    for base in [settings.dist_dir, settings.installers_dir]:
        if not base.exists():
            continue
        for p in sorted(base.glob("*.tgz")):
            meta = _read_bundle_metadata(p) or {}
            bundle = {
                "name": p.name,
                "filename": p.name,
                "location": str(base),
                "path": str(p),
                "id": meta.get("id") or p.name.split(".app.")[0].replace("_", "-"),
                "display_name": meta.get("name") or p.name,
                "version": meta.get("version"),
                "port": meta.get("port"),
                "open_path": meta.get("open_path", "/"),
            }
            bundles_by_name[p.name] = bundle
    return list(bundles_by_name.values())


@app.get("/")
def index():
    return render_template(
        "index.html",
        fqdn=settings.tailscale_fqdn,
        public_cc_port=settings.control_center_public_port,
        installed=load_installed_apps(settings.apps_dir, settings),
        bundles=discover_bundles(),
        health=health_snapshot(settings),
        expected_docker_root=str(settings.docker_root_dir),
    )


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "backend": settings.control_center_local, "health": health_snapshot(settings)})


@app.get("/api/bundles")
def bundles():
    installed = load_installed_apps(settings.apps_dir, settings)
    installed_map = {item["id"]: item for item in installed}
    bundle_rows = []
    for b in discover_bundles():
        installed_item = installed_map.get(b["id"])
        row = dict(b)
        row["installed"] = bool(installed_item and installed_item.get("is_installed"))
        row["installed_version"] = installed_item.get("version") if installed_item else None
        row["install_status"] = installed_item.get("install_status") if installed_item else None
        row["last_error"] = installed_item.get("last_error") if installed_item else None
        row["log_path"] = installed_item.get("log_path") if installed_item else None
        bundle_rows.append(row)
    return jsonify({"bundles": bundle_rows, "installed": installed})


@app.get("/api/logs/<app_id>")
def app_logs(app_id: str):
    installed = {item["id"]: item for item in load_installed_apps(settings.apps_dir, settings)}
    app_meta = installed.get(app_id)
    if not app_meta or not app_meta.get("log_path"):
        return jsonify({"ok": False, "message": f"No log found for {app_id}"}), 404
    path = Path(app_meta["log_path"])
    if not path.exists():
        return jsonify({"ok": False, "message": f"Log path does not exist: {path}"}), 404
    return jsonify({"ok": True, "app_id": app_id, "log_path": str(path), "content": path.read_text(encoding="utf-8", errors="replace")})


@app.post("/api/upload")
def upload():
    f = request.files["file"]
    settings.installers_dir.mkdir(parents=True, exist_ok=True)
    target = settings.installers_dir / f.filename
    f.save(target)
    return jsonify({"ok": True, "saved": str(target)})


@app.post("/api/install")
def install():
    data = request.get_json(force=True)
    name = data["bundle_filename"]
    candidate = settings.installers_dir / name
    if not candidate.exists():
        candidate = settings.dist_dir / name
    try:
        return jsonify(BundleInstaller(settings).install(candidate))
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.post("/api/remove")
def remove():
    data = request.get_json(force=True)
    try:
        return jsonify(BundleInstaller(settings).remove_app(data["app_id"]))
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


def main():
    serve(app, host=settings.control_center_bind, port=settings.control_center_port)


if __name__ == "__main__":
    main()
