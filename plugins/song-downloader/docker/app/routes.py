from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request, send_from_directory

from app.config import APP_NAME, APP_VERSION, DEFAULT_COOKIES_FILE, DOWNLOADS_DIR, MUSIC_ROOT
from app.jobs import (
    create_job,
    load_jobs,
    request_abort,
    request_abort_all,
    save_jobs,
    JOBS_LOCK,
)
from app.metadata import safe_music_relative
from app.utils import normalize_download_payload, parse_filename_metadata
from app.workers import run_download_job, run_retag_all_job, run_retag_job

routes_bp = Blueprint("routes", __name__)


@routes_bp.route("/")
def index():
    return send_from_directory("templates", "index.html")


@routes_bp.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory("static", filename)


@routes_bp.route("/api/health")
def health():
    response = jsonify({
        "status":               "ok",
        "name":                 APP_NAME,
        "version":              APP_VERSION,
        "music_root":           str(MUSIC_ROOT),
        "downloads_dir":        str(DOWNLOADS_DIR),
        "auto_cookies_path":    str(DEFAULT_COOKIES_FILE),
        "auto_cookies_present": DEFAULT_COOKIES_FILE.exists(),
        "yt_dlp_mode":          "stable-no-deno",
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@routes_bp.route("/api/jobs", methods=["GET"])
def get_jobs():
    return jsonify({"jobs": load_jobs()})


@routes_bp.route("/api/jobs/clear", methods=["POST"])
def clear_jobs():
    with JOBS_LOCK:
        jobs      = load_jobs()
        remaining = [job for job in jobs if job.get("status") not in {"completed", "failed"}]
        save_jobs(remaining)
    return jsonify({"ok": True})


@routes_bp.route("/api/library-songs", methods=["GET"])
def library_songs():
    songs = []
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".mp3":
            try:
                rel = safe_music_relative(path)
                songs.append({"path": rel, "name": path.name})
            except Exception:
                continue
    return jsonify({"songs": songs})


@routes_bp.route("/api/download", methods=["POST"])
def download():
    payload = request.get_json(force=True)
    job     = create_job(payload)
    threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@routes_bp.route("/api/retag", methods=["POST"])
def retag():
    payload = request.get_json(force=True)
    job     = create_job({**payload, "job_type": "retag"})
    threading.Thread(target=run_retag_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@routes_bp.route("/api/download-batch", methods=["POST"])
def download_batch():
    payload = request.get_json(force=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid payload"}), 400
    job_ids = []
    for song_name, item in payload.items():
        if not isinstance(item, dict):
            continue
        file_name     = (item.get("file_name") or "").strip()
        title, album, artists = parse_filename_metadata(file_name or song_name)
        job_payload   = normalize_download_payload({
            "song_name":    title or song_name,
            "artist_names": artists,
            "album_name":   album,
            "youtube_url":  (item.get("ytb_link")    or "").strip(),
            "rename_to":    file_name or song_name,
            "auto_move":    True,
            "album_art_url": (item.get("album_art")   or "").strip(),
            "cookies_path": (item.get("cookies_path") or "").strip(),
            "job_type":     "download-batch",
        })
        job = create_job(job_payload)
        threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
        job_ids.append(job["id"])
    return jsonify({"ok": True, "job_ids": job_ids})


@routes_bp.route("/api/retag-all", methods=["POST"])
def retag_all():
    job = create_job({"job_type": "retag-all", "song_name": "All songs from filenames", "artist_names": "", "album_name": "Unknown"})
    threading.Thread(target=run_retag_all_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@routes_bp.route("/api/jobs/<job_id>/abort", methods=["POST"])
def abort_job(job_id: str):
    job = request_abort(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, "job": job})


@routes_bp.route("/api/jobs/abort-all", methods=["POST"])
def abort_all_jobs():
    count = request_abort_all()
    return jsonify({"ok": True, "count": count})
