from __future__ import annotations

import json
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
from app.workers import (
    run_download_job,
    run_retag_all_job,
    run_retag_from_json_job,
    run_retag_job,
    run_sequential_batch,
)

routes_bp = Blueprint("routes", __name__)

_DEFAULT_DELAY = 10   # seconds between songs in sequential / retag-from-json modes


# ── UI ─────────────────────────────────────────────────────────────────────────

@routes_bp.route("/")
def index():
    return send_from_directory("templates", "index.html")


@routes_bp.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory("static", filename)


# ── Health ─────────────────────────────────────────────────────────────────────

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
        "default_delay_seconds": _DEFAULT_DELAY,
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


# ── Jobs ───────────────────────────────────────────────────────────────────────

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


# ── Library ────────────────────────────────────────────────────────────────────

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


# ── Single download ────────────────────────────────────────────────────────────

@routes_bp.route("/api/download", methods=["POST"])
def download():
    payload = request.get_json(force=True)
    job     = create_job(payload)
    threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


# ── Batch download (sequential, anti-bot) ──────────────────────────────────────

def _queue_batch(songs_map: dict, delay_seconds: int) -> tuple[list[str], int]:
    """Create one queued job per song and return (job_ids, skipped_count)."""
    job_ids = []
    skipped = 0
    for song_name, item in songs_map.items():
        if not isinstance(item, dict):
            skipped += 1
            continue
        file_name     = (item.get("file_name") or "").strip()
        title, album, artists = parse_filename_metadata(file_name or song_name)
        job_payload   = normalize_download_payload({
            "song_name":     title or song_name,
            "artist_names":  artists,
            "album_name":    album,
            "youtube_url":   (item.get("ytb_link")    or "").strip(),
            "rename_to":     file_name or song_name,
            "auto_move":     True,
            "album_art_url": (item.get("album_art")   or "").strip(),
            "cookies_path":  (item.get("cookies_path") or "").strip(),
            "job_type":      "download-batch",
        })
        job = create_job(job_payload)
        job_ids.append(job["id"])
    return job_ids, skipped


@routes_bp.route("/api/download-batch", methods=["POST"])
def download_batch():
    """Accept JSON payload (pasted in UI).

    Supports two shapes:
      • Direct map:  {"Song A": {"ytb_link": "...", "file_name": "..."}, ...}
      • Wrapped:     {"songs": {...}, "delay_seconds": 10}
    """
    data = request.get_json(force=True) or {}
    if "songs" in data and isinstance(data["songs"], dict):
        songs_map     = data["songs"]
        delay_seconds = int(data.get("delay_seconds", _DEFAULT_DELAY))
    else:
        songs_map     = data
        delay_seconds = _DEFAULT_DELAY

    if not isinstance(songs_map, dict) or not songs_map:
        return jsonify({"ok": False, "error": "invalid or empty songs map"}), 400

    job_ids, skipped = _queue_batch(songs_map, delay_seconds)
    if not job_ids:
        return jsonify({"ok": False, "error": "no valid songs found in payload"}), 400

    threading.Thread(
        target=run_sequential_batch,
        args=(job_ids, delay_seconds),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "job_ids": job_ids, "skipped": skipped, "delay_seconds": delay_seconds})


@routes_bp.route("/api/download-batch-file", methods=["POST"])
def download_batch_file():
    """Accept a .json file upload for batch download."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400
    try:
        songs_map = json.loads(f.read().decode("utf-8"))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"invalid JSON: {exc}"}), 400

    delay_seconds = int(request.form.get("delay_seconds", _DEFAULT_DELAY))

    if not isinstance(songs_map, dict) or not songs_map:
        return jsonify({"ok": False, "error": "empty or invalid songs JSON"}), 400

    job_ids, skipped = _queue_batch(songs_map, delay_seconds)
    if not job_ids:
        return jsonify({"ok": False, "error": "no valid songs found"}), 400

    threading.Thread(
        target=run_sequential_batch,
        args=(job_ids, delay_seconds),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "job_ids": job_ids, "skipped": skipped, "delay_seconds": delay_seconds})


# ── Single retag ───────────────────────────────────────────────────────────────

@routes_bp.route("/api/retag", methods=["POST"])
def retag():
    payload = request.get_json(force=True)
    job     = create_job({**payload, "job_type": "retag"})
    threading.Thread(target=run_retag_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


# ── Retag from JSON (sequential, anti-bot) ─────────────────────────────────────

def _start_retag_from_json(songs_map: dict, delay_seconds: int):
    total = len(songs_map)
    job   = create_job({
        "job_type":      "retag-from-json",
        "song_name":     f"{total} songs from JSON",
        "artist_names":  "",
        "album_name":    "",
        "songs_map":     songs_map,
        "delay_seconds": delay_seconds,
    })
    threading.Thread(target=run_retag_from_json_job, args=(job["id"],), daemon=True).start()
    return job["id"]


@routes_bp.route("/api/retag-from-json", methods=["POST"])
def retag_from_json():
    """Accept JSON payload (pasted in UI).

    Supports two shapes:
      • Direct map:  {"Song A": {"ytb_link": "...", "file_name": "..."}, ...}
      • Wrapped:     {"songs": {...}, "delay_seconds": 10}
    """
    data = request.get_json(force=True) or {}
    if "songs" in data and isinstance(data["songs"], dict):
        songs_map     = data["songs"]
        delay_seconds = int(data.get("delay_seconds", _DEFAULT_DELAY))
    else:
        songs_map     = data
        delay_seconds = _DEFAULT_DELAY

    if not isinstance(songs_map, dict) or not songs_map:
        return jsonify({"ok": False, "error": "invalid or empty songs map"}), 400

    job_id = _start_retag_from_json(songs_map, delay_seconds)
    return jsonify({"ok": True, "job_id": job_id})


@routes_bp.route("/api/retag-from-json-file", methods=["POST"])
def retag_from_json_file():
    """Accept a .json file upload for retag-from-JSON."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400
    try:
        songs_map = json.loads(f.read().decode("utf-8"))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"invalid JSON: {exc}"}), 400

    delay_seconds = int(request.form.get("delay_seconds", _DEFAULT_DELAY))

    if not isinstance(songs_map, dict) or not songs_map:
        return jsonify({"ok": False, "error": "empty or invalid songs JSON"}), 400

    job_id = _start_retag_from_json(songs_map, delay_seconds)
    return jsonify({"ok": True, "job_id": job_id})


# ── Retag all ──────────────────────────────────────────────────────────────────

@routes_bp.route("/api/retag-all", methods=["POST"])
def retag_all():
    job = create_job({
        "job_type":     "retag-all",
        "song_name":    "All songs from filenames",
        "artist_names": "",
        "album_name":   "Unknown",
    })
    threading.Thread(target=run_retag_all_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})
