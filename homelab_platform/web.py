import os
from pathlib import Path
from flask import Flask, jsonify, render_template, request
from waitress import serve
from homelab_platform.config import Settings
from homelab_platform.services.bundle_installer import BundleInstaller
from homelab_platform.services.state import load_installed_apps
from homelab_platform.services.health import health_snapshot

ENV_FILE = os.environ.get("HOMELAB_ENV_FILE", ".env")
settings = Settings.from_env_file(ENV_FILE)
app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / "templates"), static_folder=str(Path(__file__).resolve().parent / "static"))


def discover_bundles():
    bundles = []
    for base in [settings.dist_dir, settings.installers_dir]:
        if base.exists():
            for p in sorted(base.glob("*.tgz")):
                bundles.append({"name": p.name, "location": str(base), "path": str(p)})
    seen = set()
    out = []
    for b in bundles:
        if b["name"] not in seen:
            out.append(b)
            seen.add(b["name"])
    return out

@app.get("/")
def index():
    return render_template("index.html", fqdn=settings.tailscale_fqdn, public_cc_port=settings.control_center_public_port, installed=load_installed_apps(settings.apps_dir, settings), bundles=discover_bundles(), health=health_snapshot(settings), expected_docker_root=str(settings.docker_root_dir))

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "backend": settings.control_center_local, "health": health_snapshot(settings)})

@app.get("/api/bundles")
def bundles():
    return jsonify({"bundles": discover_bundles(), "installed": load_installed_apps(settings.apps_dir, settings)})

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
        return jsonify({"ok": False, "message": str(exc)}) , 500

@app.post("/api/remove")
def remove():
    data = request.get_json(force=True)
    try:
        return jsonify(BundleInstaller(settings).remove_app(data["app_id"]))
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}) , 500


def main():
    serve(app, host=settings.control_center_bind, port=settings.control_center_port)

if __name__ == "__main__":
    main()
