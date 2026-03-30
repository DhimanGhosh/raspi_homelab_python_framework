import json, os, re, shutil, subprocess, tarfile, threading, time, uuid
from pathlib import Path
from flask import Flask, jsonify, render_template, request, Response
from packaging.version import Version, InvalidVersion

BASE = Path(os.getenv("CONTROL_CENTER_BASE", "/mnt/nas/homelab/control-center"))
APP_DIR = BASE / "app"
DATA_DIR = BASE / "data"
LOG_DIR = BASE / "logs"
INSTALLERS_DIR = Path(os.getenv("INSTALLERS_DIR", "/mnt/nas/homelab/installers"))
APPS_DIR = Path(os.getenv("APPS_DIR", "/mnt/nas/homelab/apps"))
NAS_DIR = Path(os.getenv("NAS_MOUNT", "/mnt/nas"))
BACKUPS_DIR = Path(os.getenv("HOMELAB_BACKUPS_DIR", str(NAS_DIR / "homelab" / "backups")))
ENV_FILE = Path(os.environ.get("HOMELAB_ENV_FILE", ".env")).resolve()
REPO_ROOT = Path(os.environ.get("REPO_ROOT", ENV_FILE.parent)).resolve()
HOMELABCTL_BIN = Path(os.environ.get("HOMELABCTL_BIN", REPO_ROOT / ".venv" / "bin" / "homelabctl"))
VERSION = "1.7.1"

for p in [DATA_DIR, LOG_DIR, INSTALLERS_DIR, APPS_DIR, BACKUPS_DIR]:
    p.mkdir(parents=True, exist_ok=True)

KNOWN_APPS = {
    "pihole": {"name": "Pi-hole", "port": 8447, "open_path": "/admin/"},
    "navidrome": {"name": "Navidrome", "port": 8445, "open_path": "/"},
    "jellyfin": {"name": "Jellyfin", "port": 8446, "open_path": "/"},
    "nextcloud": {"name": "Nextcloud", "port": 8448, "open_path": "/"},
    "files": {"name": "Files", "port": 8449, "open_path": "/"},
    "home-assistant": {"name": "Home Assistant", "port": 8450, "open_path": "/"},
    "status": {"name": "Pi Status Board", "port": 8451, "open_path": "/"},
    "voice-ai": {"name": "Voice AI", "port": 8452, "open_path": "/"},
    "homarr": {"name": "Homarr", "port": 8453, "open_path": "/"},
    "personal-library": {"name": "Personal Library", "port": 8454, "open_path": "/"},
    "dictionary": {"name": "Dictionary", "port": 8455, "open_path": "/"},
    "api-gateway": {"name": "API Gateway", "port": 8456, "open_path": "/docs"},
    "control-center": {"name": "Control Center", "port": 8444, "open_path": "/"},
    "music-player": {"name": "Music Player", "port": 8459, "open_path": "/"},
}
APP_ID_ALIASES = {
    "home_assistant": "home-assistant",
    "voice_ai": "voice-ai",
    "api_gateway": "api-gateway",
    "control_center": "control-center",
}
BUNDLE_RE = re.compile(r"^(?P<id>[a-zA-Z0-9._-]+)\.app\.v(?P<ver>\d+\.\d+\.\d+)\.(?:tgz|tar\.gz|zip)$")
JOBS_LOCK = threading.Lock()
JOBS = {}
JOB_STATE_PATH = DATA_DIR / "jobs.json"
NOTIFICATIONS_PATH = DATA_DIR / "notifications.json"
BACKUP_ROOT = str(BACKUPS_DIR)

def _env_value(name: str, default: str = "") -> str:
    if name in os.environ:
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name:
                return v.strip()
    return default

FQDN = _env_value("TAILSCALE_FQDN", "pi-nas.taild4713b.ts.net")

def normalize_app_id(app_id):
    if not app_id:
        return ""
    app_id = str(app_id).strip()
    return APP_ID_ALIASES.get(app_id, app_id.replace("_", "-") if app_id.replace("_", "-") in KNOWN_APPS else app_id)

def parse_version(v: str):
    try:
        return Version(str(v))
    except InvalidVersion:
        return Version("0.0.0")

def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def notifications():
    items = _json(NOTIFICATIONS_PATH, [])
    return items if isinstance(items, list) else []

def save_notifications(items):
    _write_json(NOTIFICATIONS_PATH, items[:500])

def add_notification(message: str, app_id: str | None = None):
    items = notifications()
    items.insert(0, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message, "app_id": normalize_app_id(app_id) if app_id else None})
    save_notifications(items)

def notification_counts():
    counts = {}
    for item in notifications():
        app_id = normalize_app_id(item.get("app_id"))
        if app_id:
            counts[app_id] = counts.get(app_id, 0) + 1
    return counts

def docker_root_dir() -> str:
    try:
        out = subprocess.check_output(["docker", "info", "--format", "{{.DockerRootDir}}"], text=True, timeout=5).strip()
        return out or "/var/lib/docker"
    except Exception:
        return "/var/lib/docker"

def sdcard_warning() -> str | None:
    root = docker_root_dir()
    return None if root.startswith(str(NAS_DIR)) else f"Docker data root is currently on {root}. Recommended: {NAS_DIR / 'homelab' / 'docker'}"

def _disk(path: Path):
    try:
        u = shutil.disk_usage(path)
        return {"path": str(path), "total_gb": round(u.total / 1024**3, 2), "used_gb": round((u.total - u.free) / 1024**3, 2), "free_gb": round(u.free / 1024**3, 2), "used_pct": round(((u.total - u.free) / u.total) * 100, 2) if u.total else 0}
    except Exception:
        return {"path": str(path), "total_gb": 0, "used_gb": 0, "free_gb": 0, "used_pct": 0}

def read_bundle_metadata(bundle_path: Path) -> dict:
    try:
        with tarfile.open(bundle_path, "r:*") as tf:
            for m in tf.getmembers():
                if m.name.endswith("metadata.json"):
                    f = tf.extractfile(m)
                    if f:
                        return json.loads(f.read().decode("utf-8"))
    except Exception:
        return {}
    return {}

def scan_bundles():
    bundles = []
    bundles_by_id = {}
    latest_by_id = {}
    ota = []
    for base in [INSTALLERS_DIR, REPO_ROOT / "dist"]:
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            meta = read_bundle_metadata(p)
            app_id = normalize_app_id(meta.get("id") or "")
            version = meta.get("version")
            if not app_id:
                m = BUNDLE_RE.match(name)
                if m:
                    app_id = normalize_app_id(m.group("id"))
                    version = m.group("ver")
            if not app_id:
                continue
            item = {"filename": name, "app_id": app_id, "version": version or "0.0.0"}
            bundles.append(item)
            bundles_by_id.setdefault(app_id, []).append(item)
            prev = latest_by_id.get(app_id)
            if not prev or parse_version(item["version"]) > parse_version(prev["version"]):
                latest_by_id[app_id] = item
            if app_id == "control-center":
                ota.append(item)
    for _, v in bundles_by_id.items():
        v.sort(key=lambda x: parse_version(x["version"]), reverse=True)
    latest_ota = latest_by_id.get("control-center")
    return bundles, bundles_by_id, latest_by_id, latest_ota

def load_installed_app(app_id: str):
    app_id = normalize_app_id(app_id)
    d = APPS_DIR / app_id
    for name in ["install_state.json", "app_info.json", "metadata.json"]:
        obj = _json(d / name, None)
        if isinstance(obj, dict):
            obj["id"] = normalize_app_id(obj.get("id") or app_id)
            return obj
    return None

def load_jobs():
    arr = _json(JOB_STATE_PATH, [])
    if not isinstance(arr, list):
        return
    with JOBS_LOCK:
        for item in arr:
            if item.get("status") in ("queued", "running"):
                item["status"] = "failed"
                item["message"] = "Recovered after service restart"
                item["progress"] = 100
            JOBS[item["id"]] = item

def persist_jobs():
    with JOBS_LOCK:
        _write_json(JOB_STATE_PATH, [dict(v) for v in JOBS.values()])

def current_jobs():
    with JOBS_LOCK:
        return [dict(v) for v in JOBS.values()]

def get_running_job_for_app(app_id: str):
    app_id = normalize_app_id(app_id)
    with JOBS_LOCK:
        for job in JOBS.values():
            if job["app_id"] == app_id and job["status"] in ("queued", "running"):
                return dict(job)
    return None

def tail_log(log_path, lines=40):
    p = Path(log_path)
    if not p.exists():
        return "No log available."
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(txt[-lines:]) if txt else "(empty log)"
    except Exception as e:
        return f"Failed to read log: {e}"

def scan_backups():
    items = []
    for p in sorted(BACKUPS_DIR.glob("homelab_snapshot_*.tar.gz"), reverse=True):
        items.append({"filename": p.name, "size_mb": round(p.stat().st_size / 1024**2, 2), "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)), "label": p.stem.replace("homelab_snapshot_", "").replace("_", " "), "path": str(p), "includes": ["/mnt/nas/homelab"]})
    return items

def scan_apps():
    installed = {}
    if APPS_DIR.exists():
        for d in sorted(APPS_DIR.iterdir()):
            if d.is_dir():
                info = load_installed_app(d.name)
                if info:
                    installed[normalize_app_id(d.name)] = info
    _, bundles_by_id, latest_by_id, _ = scan_bundles()
    ids = set(KNOWN_APPS) | set(installed) | set(latest_by_id)
    running_by_app = {}
    for j in current_jobs():
        if j["status"] in ("queued", "running"):
            running_by_app[j["app_id"]] = j
    note_counts = notification_counts()
    cards = []
    for app_id in ids:
        m = {"id": app_id}
        m.update(KNOWN_APPS.get(app_id, {}))
        m.update(installed.get(app_id, {}))
        latest = latest_by_id.get(app_id)
        if latest and (not m.get("name") or not m.get("port") or not m.get("open_path")):
            latest_meta = read_bundle_metadata((INSTALLERS_DIR / latest["filename"])) if (INSTALLERS_DIR / latest["filename"]).exists() else read_bundle_metadata((REPO_ROOT / "dist" / latest["filename"]))
            if isinstance(latest_meta, dict):
                for key in ("name", "port", "open_path"):
                    if latest_meta.get(key) and not m.get(key):
                        m[key] = latest_meta.get(key)
        installed_ver = m.get("installed_version") or m.get("version")
        m["installed_version"] = installed_ver
        m["latest_version"] = latest["version"] if latest else installed_ver
        m["bundle_filename"] = latest["filename"] if latest else None
        m["bundles"] = bundles_by_id.get(app_id, [])
        m["installed"] = app_id in installed
        port = m.get("port")
        path = m.get("open_path", "/")
        if path and not path.startswith("/"):
            path = "/" + path
        m["open_url"] = f"https://{FQDN}:{port}{path}" if port and (m["installed"] or app_id == "control-center") else None
        running = running_by_app.get(app_id)
        m["job"] = running
        m["notification_count"] = note_counts.get(app_id, 0)
        m["has_notifications"] = m["notification_count"] > 0
        if app_id == "control-center":
            m["installed"] = True
            m["installed_version"] = VERSION
            m["latest_version"] = latest["version"] if latest else VERSION
            m["bundle_filename"] = latest["filename"] if latest else None
            m["action"] = "update" if latest and parse_version(latest["version"]) > parse_version(VERSION) else "installed"
        elif running:
            m["action"] = "running"
        elif not m["installed"] and latest:
            m["action"] = "install"
        elif m["installed"] and latest and parse_version(m["latest_version"] or "0.0.0") > parse_version(installed_ver or "0.0.0"):
            m["action"] = "update"
        elif m["installed"]:
            m["action"] = "reinstall"
        else:
            m["action"] = "none"
        cards.append(m)
    cards.sort(key=lambda x: (x["id"] != "control-center", x.get("name", x["id"]).lower()))
    return cards

def _run_and_stream(job_id: str, cmd, log_path: Path):
    proc = subprocess.Popen(list(map(str, cmd)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["pid"] = proc.pid
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["message"] = "Running command..."
            JOBS[job_id]["progress"] = 15
            JOBS[job_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    persist_jobs()
    lines = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        for line in proc.stdout:
            lf.write(line)
            lines.append(line.rstrip())
            lines = lines[-60:]
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["log_tail"] = "\n".join(lines[-20:])
                    JOBS[job_id]["message"] = lines[-1][:180] if lines else JOBS[job_id].get("message")
                    JOBS[job_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            persist_jobs()
    return proc.wait()

def run_job_thread(job_id: str, app_id: str, action: str, bundle_filename: str | None = None):
    log_name = f"{app_id}-{action}-{int(time.time())}.log"
    log_path = LOG_DIR / log_name
    try:
        bundle_path = None
        if action in ("install", "update", "reinstall"):
            if bundle_filename:
                for base in [INSTALLERS_DIR, REPO_ROOT / "dist"]:
                    candidate = base / bundle_filename
                    if candidate.exists():
                        bundle_path = candidate
                        break
            if not bundle_path:
                _, bundles_by_id, _, _ = scan_bundles()
                app_bundles = bundles_by_id.get(app_id, [])
                bundle_path = next((INSTALLERS_DIR / b["filename"] for b in app_bundles if (INSTALLERS_DIR / b["filename"]).exists()), None)
                if not bundle_path:
                    bundle_path = next(((REPO_ROOT / "dist") / b["filename"] for b in app_bundles if ((REPO_ROOT / "dist") / b["filename"]).exists()), None)
            if not bundle_path:
                raise RuntimeError(f"No bundle available for {app_id}")
            cmd = [HOMELABCTL_BIN, "install-bundle", "--bundle", bundle_path, "--env-file", ENV_FILE]
        else:
            cmd = [HOMELABCTL_BIN, "remove-app", "--app-id", app_id, "--env-file", ENV_FILE]
        rc = _run_and_stream(job_id, cmd, log_path)
        with JOBS_LOCK:
            job = JOBS.get(job_id, {})
            job["log_name"] = log_name
            job["log_tail"] = tail_log(log_path)
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if rc == 0:
                job["status"] = "success"
                job["progress"] = 100
                job["message"] = f"{action.title()} completed"
                add_notification(f"{action.title()} completed for {app_id}", app_id)
            else:
                job["status"] = "failed"
                job["progress"] = 100
                job["message"] = f"{action.title()} failed"
                add_notification(f"{action.title()} failed for {app_id}", app_id)
            JOBS[job_id] = job
        persist_jobs()
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS.get(job_id, {})
            job["status"] = "failed"
            job["progress"] = 100
            job["message"] = str(exc)
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["log_name"] = log_name
            JOBS[job_id] = job
        persist_jobs()
        log_path.write_text(str(exc), encoding="utf-8")

def create_job(app_id: str, action: str, bundle_filename: str | None = None):
    app_id = normalize_app_id(app_id)
    running = get_running_job_for_app(app_id)
    if running:
        return running, False
    app_name = KNOWN_APPS.get(app_id, {}).get("name", app_id)
    job = {"id": str(uuid.uuid4()), "app_id": app_id, "app_name": app_name, "action": action, "bundle_filename": bundle_filename, "status": "queued", "progress": 5, "message": "Queued", "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "log_tail": "", "log_name": None}
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    persist_jobs()
    threading.Thread(target=run_job_thread, args=(job["id"], app_id, action, bundle_filename), daemon=True).start()
    return job, True

def create_backup_snapshot():
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = BACKUPS_DIR / f"homelab_snapshot_{ts}.tar.gz"
    subprocess.run(["tar", "-czf", str(out), "-C", str(NAS_DIR), "homelab"], check=False)
    add_notification(f"Created homelab snapshot: {out.name}", "control-center")
    return out

def create_app():
    load_jobs()
    app = Flask(__name__, template_folder="templates", static_folder="static")

    def state_payload():
        apps = scan_apps()
        note = notifications()
        jobs = current_jobs()
        bundles, _, _, ota = scan_bundles()
        return {"apps": apps, "notifications": note, "jobs": jobs, "current_version": VERSION, "total_bundles": len(bundles), "ota": ota, "backups": scan_backups(), "notification_total": len(note), "backup_root": BACKUP_ROOT, "docker_root": docker_root_dir(), "sdcard_warning": sdcard_warning(), "nas_usage": _disk(NAS_DIR), "homelab_usage": _disk(NAS_DIR / "homelab"), "root_usage": _disk(Path("/"))}

    @app.get("/")
    def index():
        st = state_payload()
        return render_template("index.html", **st)

    @app.get("/api/state")
    def api_state():
        return jsonify(state_payload())

    @app.post("/api/marketplace/rescan")
    def marketplace_rescan():
        add_notification("Marketplace rescanned")
        return jsonify({"ok": True, "message": "Marketplace rescanned."})

    @app.post("/api/notifications/clear")
    def clear_notifications():
        save_notifications([])
        return jsonify({"ok": True, "message": "Notifications cleared."})

    @app.post("/api/bundles/upload")
    def upload_bundle():
        files = request.files.getlist("files") or request.files.getlist("file")
        saved = []
        for f in files:
            name = Path(f.filename or "").name
            if not name:
                continue
            target = INSTALLERS_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            f.save(target)
            saved.append(name)
            add_notification(f"Uploaded bundle: {name}")
        if not saved:
            return jsonify({"ok": False, "message": "No files uploaded."}), 400
        return jsonify({"ok": True, "message": f"Uploaded {len(saved)} bundle(s)."})

    @app.delete("/api/bundles/<filename>")
    def delete_bundle(filename):
        p = INSTALLERS_DIR / Path(filename).name
        if p.exists():
            p.unlink()
            add_notification(f"Deleted bundle: {filename}")
        return jsonify({"ok": True, "message": f"Deleted {filename}"})

    @app.get("/api/backups")
    def list_backups():
        return jsonify({"ok": True, "items": scan_backups()})

    @app.post("/api/backups/create")
    def create_backup():
        snap = create_backup_snapshot()
        return jsonify({"ok": True, "message": f"Created {snap.name}"})

    @app.post("/api/backups/<filename>/restore")
    def restore_backup(filename):
        return jsonify({"ok": False, "message": "Restore from UI is not enabled in this build."}), 501

    @app.delete("/api/backups/<filename>")
    def delete_backup(filename):
        snapshot = BACKUPS_DIR / Path(filename).name
        if snapshot.exists():
            snapshot.unlink()
            add_notification(f"Deleted homelab snapshot: {snapshot.name}")
        return jsonify({"ok": True, "message": f"Deleted {snapshot.name}"})

    @app.get("/api/logs/<log_name>")
    def get_log(log_name):
        path = LOG_DIR / Path(log_name).name
        if not path.exists():
            return Response("Log not found", status=404, mimetype="text/plain")
        return Response(path.read_text(encoding="utf-8", errors="ignore"), mimetype="text/plain")

    @app.post("/api/apps/<app_id>/install")
    def install_app(app_id):
        app_id = normalize_app_id(app_id)
        _, bundles_by_id, _, _ = scan_bundles()
        item = (bundles_by_id.get(app_id) or [None])[0]
        job, created = create_job(app_id, "install", bundle_filename=item["filename"] if item else None)
        return jsonify({"ok": True, "message": "Install queued." if created else "Install already running.", "created": created, "job_id": job["id"]})

    @app.post("/api/apps/<app_id>/install-bundle/<filename>")
    def install_app_bundle(app_id, filename):
        app_id = normalize_app_id(app_id)
        job, created = create_job(app_id, "install", bundle_filename=Path(filename).name)
        return jsonify({"ok": True, "message": f"Install queued for {filename}." if created else "Install already running.", "created": created, "job_id": job["id"]})

    @app.post("/api/apps/<app_id>/uninstall")
    def uninstall_app(app_id):
        app_id = normalize_app_id(app_id)
        job, created = create_job(app_id, "uninstall")
        return jsonify({"ok": True, "message": "Uninstall queued." if created else "Job already running for this app.", "created": created, "job_id": job["id"]})

    @app.post("/api/install-all")
    def install_all():
        queued = 0
        for app in scan_apps():
            if app["id"] == "control-center":
                continue
            if not app.get("installed") and app.get("bundles"):
                create_job(app["id"], "install", bundle_filename=app["bundles"][0]["filename"])
                queued += 1
        return jsonify({"ok": True, "message": f"Queued {queued} install job(s)."})

    @app.post("/api/update-all")
    def update_all():
        queued = 0
        for app in scan_apps():
            if app["id"] == "control-center":
                continue
            if app.get("installed") and app.get("bundles") and parse_version(app.get("latest_version") or "0.0.0") > parse_version(app.get("installed_version") or "0.0.0"):
                create_job(app["id"], "install", bundle_filename=app["bundles"][0]["filename"])
                queued += 1
        return jsonify({"ok": True, "message": f"Queued {queued} update job(s)."})

    @app.post("/api/ota/apply")
    def ota_apply():
        return jsonify({"ok": False, "message": "Control Center self-update from UI is disabled in this build. Use CLI upload/install."}), 501

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return jsonify({"ok": False, "message": "Job not found"}), 404
            pid = job.get("pid")
            if pid:
                try:
                    os.killpg(int(pid), 15)
                except Exception:
                    pass
            job["status"] = "canceled"
            job["message"] = "Canceled"
            job["progress"] = 100
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        persist_jobs()
        return jsonify({"ok": True, "message": "Cancel requested."})

    @app.post("/api/jobs/<job_id>/dismiss")
    def dismiss_job(job_id):
        with JOBS_LOCK:
            if job_id not in JOBS:
                return jsonify({"ok": False, "message": "Job not found"}), 404
            if JOBS[job_id].get("status") in ("queued", "running"):
                return jsonify({"ok": False, "message": "Running jobs cannot be dismissed."}), 400
            del JOBS[job_id]
        persist_jobs()
        return jsonify({"ok": True, "message": "Job dismissed."})

    @app.post("/api/jobs/clear-all")
    def clear_all_jobs():
        with JOBS_LOCK:
            count = len(JOBS)
            JOBS.clear()
        persist_jobs()
        return jsonify({"ok": True, "message": f"Cleared {count} job(s)."})

    @app.post("/api/jobs/clear-completed")
    def clear_completed_jobs():
        removed = 0
        with JOBS_LOCK:
            for jid in list(JOBS.keys()):
                if JOBS[jid].get("status") in ("success", "failed", "canceled"):
                    del JOBS[jid]
                    removed += 1
        persist_jobs()
        return jsonify({"ok": True, "message": f"Cleared {removed} completed job(s)."})

    @app.get("/api/health")
    def control_center_health():
        return jsonify({"ok": True, "service": "Pi Control Center", "version": VERSION})

    return app
