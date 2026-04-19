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

try:
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3, APIC, error as ID3Error
    from mutagen.mp3 import MP3
except Exception:  # pragma: no cover
    EasyID3 = None
    ID3 = None
    APIC = None
    ID3Error = Exception
    MP3 = None

from flask import Flask, jsonify, request, send_from_directory

APP_NAME = os.getenv("APP_NAME", "Song Downloader")
APP_VERSION = os.getenv("APP_VERSION", "1.0.3")
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
        "progress": 0,
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


def apply_audio_metadata(file_path: Path, song_name: str, artist_names: str, album_name: str) -> None:
    if file_path.suffix.lower() != ".mp3" or EasyID3 is None:
        return
    album_value = (album_name or "Unknown").strip() or "Unknown"
    try:
        tags = EasyID3(str(file_path))
    except Exception:
        tags = EasyID3()
    tags["title"] = [song_name or file_path.stem]
    tags["artist"] = [artist_names or "Unknown Artist"]
    tags["album"] = [album_value]
    tags.save(str(file_path))


def detect_title_artist_album(file_path: Path) -> tuple[str, str, str]:
    base = file_path.stem
    parts = [part.strip() for part in base.split(" - ") if part.strip()]
    if len(parts) >= 3:
        return parts[0], parts[-1], parts[-2]
    if len(parts) == 2:
        return parts[0], parts[-1], "Unknown"
    return base, "Unknown Artist", "Unknown"


def set_progress(job_id: str, value: int) -> None:
    value = max(0, min(100, int(value)))
    update_job(job_id, progress=value)


def infer_album_from_rename(rename_to: str, song_name: str, artist_names: str, album_name: str) -> str:
    rename_to = (rename_to or "").strip()
    if album_name and album_name.strip() and album_name.strip().lower() != "unknown":
        return album_name.strip()
    if not rename_to:
        return "Unknown"
    base = rename_to[:-4] if rename_to.lower().endswith('.mp3') else rename_to
    parts = [part.strip() for part in base.split(' - ') if part.strip()]
    if len(parts) >= 3:
        return parts[1]
    return "Unknown"


def _extract_progress_percent(line: str) -> int | None:
    match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
    if not match:
        return None
    return int(float(match.group(1)))


def run_download_job(job_id: str) -> None:
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return

    payload = job["payload"]
    song_name = payload.get("song_name", "").strip()
    artist_names = payload.get("artist_names", "").strip()
    rename_to = payload.get("rename_to", "").strip()
    album_name = infer_album_from_rename(
        rename_to=rename_to,
        song_name=song_name,
        artist_names=artist_names,
        album_name=payload.get("album_name", "").strip() or "Unknown",
    )
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
            "--newline",
            "-o", output_template,
            source,
        ]

        append_log(job_id, "Running yt-dlp")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        last_progress = 1
        if proc.stdout is not None:
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if line:
                    append_log(job_id, line)
                    progress = _extract_progress_percent(line)
                    if progress is not None:
                        last_progress = max(last_progress, progress)
                        set_progress(job_id, last_progress)
        return_code = proc.wait()
        if return_code != 0:
            raise RuntimeError(f"yt-dlp failed with exit code {return_code}")

        set_progress(job_id, max(last_progress, 95))
        downloaded = find_downloaded_file(DOWNLOADS_DIR, marker)
        if not downloaded:
            raise RuntimeError("Downloaded file not found after yt-dlp run")

        target_name = rename_to or build_target_filename(song_name, artist_names, album_name)
        if not target_name.lower().endswith(".mp3"):
            target_name += ".mp3"
        final_path = safe_destination((MUSIC_ROOT if auto_move else DOWNLOADS_DIR) / target_name)

        shutil.move(str(downloaded), str(final_path))
        detected_song_name, detected_artist_names, detected_album_name = detect_title_artist_album(final_path)
        apply_audio_metadata(
            final_path,
            song_name or detected_song_name,
            artist_names or detected_artist_names,
            album_name or detected_album_name,
        )
        update_job(
            job_id,
            status="completed",
            output_file=str(downloaded),
            final_file=str(final_path),
            progress=100,
            payload={
                **payload,
                "song_name": song_name,
                "artist_names": artist_names,
                "album_name": album_name,
            },
        )
        append_log(job_id, f"Saved file: {final_path}")

    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), progress=100)
        append_log(job_id, f"ERROR: {exc}")


@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(app.static_folder, filename)


@app.route("/api/health")
def health():
    response = jsonify({
        "status": "ok",
        "name": APP_NAME,
        "version": APP_VERSION,
        "music_root": str(MUSIC_ROOT),
        "downloads_dir": str(DOWNLOADS_DIR),
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    return jsonify({"jobs": load_jobs()})


@app.route("/api/jobs/clear", methods=["POST"])
def clear_jobs():
    with JOBS_LOCK:
        jobs = load_jobs()
        remaining = [job for job in jobs if job.get("status") not in {"completed", "failed"}]
        cleared = len(jobs) - len(remaining)
        save_jobs(remaining)
    return jsonify({"ok": True, "cleared": cleared})


@app.route("/api/download", methods=["POST"])
def download():
    payload = request.get_json(force=True)
    job = create_job(payload)
    threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
