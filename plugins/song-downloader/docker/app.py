\
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

APP_NAME = os.getenv("APP_NAME", "Song Downloader")
APP_VERSION = os.getenv("APP_VERSION", "1.0.2")
PORT = int(os.getenv("PORT", "8145"))
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/song-downloader/data")).resolve()
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/mnt/nas/homelab/runtime/song-downloader/downloads")).resolve()
JOBS_FILE = APP_DATA_DIR / "jobs.json"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
JOBS_LOCK = threading.Lock()


def load_jobs() -> list[dict]:
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_jobs(jobs: list[dict]) -> None:
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def update_job(job_id: str, **updates) -> dict | None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(updates)
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_jobs(jobs)
                return job
    return None


def create_job(payload: dict) -> dict:
    job = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
        "logs": [],
        "output_file": "",
        "final_file": "",
        "error": "",
    }
    with JOBS_LOCK:
        jobs = load_jobs()
        jobs.insert(0, job)
        save_jobs(jobs)
    return job


def append_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.setdefault("logs", []).append(line)
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_jobs(jobs)
                return


def slugify_filename(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "downloaded-track"


def build_target_filename(song_name: str, artist_names: str, album_name: str) -> str:
    song_name = slugify_filename(song_name or "Unknown Song")
    artist_names = slugify_filename(artist_names or "Unknown Artist")
    album_name = slugify_filename(album_name or "Unknown")
    if album_name and album_name.lower() != "unknown":
        return f"{song_name} - {album_name} - {artist_names}.mp3"
    return f"{song_name} - {artist_names}.mp3"


def safe_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    idx = 1
    while True:
        option = path.with_name(f"{stem} ({idx}){suffix}")
        if not option.exists():
            return option
        idx += 1


def yt_search_query(song_name: str, artist_names: str, album_name: str) -> str:
    query = " ".join(x for x in [song_name, artist_names, album_name, "official audio"] if x)
    return f"ytsearch1:{query.strip()}"


def resolve_source(payload: dict) -> str:
    youtube_url = (payload.get("youtube_url") or "").strip()
    if youtube_url:
        return youtube_url
    return yt_search_query(
        payload.get("song_name", "").strip(),
        payload.get("artist_names", "").strip(),
        payload.get("album_name", "").strip(),
    )


def find_downloaded_file(download_dir: Path, marker: str) -> Path | None:
    matches = sorted(download_dir.glob(f"{marker}*"))
    for match in matches:
        if match.is_file() and match.suffix.lower() == ".mp3":
            return match
    return None


def run_download_job(job_id: str) -> None:
    job = update_job(job_id, status="running")
    if not job:
        return

    payload = job["payload"]
    song_name = payload.get("song_name", "").strip()
    artist_names = payload.get("artist_names", "").strip()
    album_name = payload.get("album_name", "").strip() or "Unknown"
    rename_to = payload.get("rename_to", "").strip()
    auto_move = bool(payload.get("auto_move", True))

    try:
        append_log(job_id, "Preparing download job")
        source = resolve_source(payload)
        marker = f"job_{job_id.replace('-', '')}"
        output_template = str(DOWNLOADS_DIR / f"{marker}.%(ext)s")

        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-metadata",
            "--no-playlist",
            "-o", output_template,
            source,
        ]

        append_log(job_id, "Running yt-dlp")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            for line in proc.stdout.splitlines():
                append_log(job_id, line)
        if proc.stderr:
            for line in proc.stderr.splitlines():
                append_log(job_id, line)
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp failed with exit code {proc.returncode}")

        downloaded = find_downloaded_file(DOWNLOADS_DIR, marker)
        if not downloaded:
            raise RuntimeError("Downloaded file not found after yt-dlp run")

        target_name = rename_to or build_target_filename(song_name, artist_names, album_name)
        final_path = safe_destination((MUSIC_ROOT if auto_move else DOWNLOADS_DIR) / target_name)

        shutil.move(str(downloaded), str(final_path))
        update_job(job_id, status="completed", output_file=str(downloaded), final_file=str(final_path))
        append_log(job_id, f"Saved file: {final_path}")

    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))
        append_log(job_id, f"ERROR: {exc}")


@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(app.static_folder, filename)


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "name": APP_NAME,
        "version": APP_VERSION,
        "music_root": str(MUSIC_ROOT),
        "downloads_dir": str(DOWNLOADS_DIR),
    })


@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    return jsonify({"jobs": load_jobs()})


@app.route("/api/jobs/clear", methods=["POST"])
def clear_jobs():
    with JOBS_LOCK:
        save_jobs([])
    return jsonify({"ok": True})


@app.route("/api/download", methods=["POST"])
def download():
    payload = request.get_json(force=True)
    job = create_job(payload)
    threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
