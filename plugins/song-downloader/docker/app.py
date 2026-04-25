from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory

APP_NAME = os.getenv("APP_NAME", "Song Downloader")
PLUGIN_JSON = Path(__file__).resolve().parents[1] / "plugin.json"
try:
    APP_VERSION = json.loads(PLUGIN_JSON.read_text(encoding="utf-8")).get("version", os.getenv("APP_VERSION", "1.3.2"))
except Exception:
    APP_VERSION = os.getenv("APP_VERSION", "1.3.2")
PORT = int(os.getenv("PORT", "8145"))
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/song-downloader/data")).resolve()
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/mnt/nas/homelab/runtime/song-downloader/downloads")).resolve()
JOBS_FILE = APP_DATA_DIR / "jobs.json"
DEFAULT_COOKIES_FILE = APP_DATA_DIR / "cookies.txt"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
JOBS_LOCK = threading.Lock()


# -------------------------------
# Jobs helpers
# -------------------------------
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
        "abort_requested": False,
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




def request_abort(job_id: str) -> dict | None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                if job.get("status") in {"queued", "running"}:
                    job["abort_requested"] = True
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    save_jobs(jobs)
                return job
    return None


def is_abort_requested(job_id: str) -> bool:
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return bool(job.get("abort_requested"))
    return False


def startup_reconcile_jobs() -> None:
    with JOBS_LOCK:
        jobs = load_jobs()
        changed = False
        for job in jobs:
            if job.get("status") in {"queued", "running"}:
                job["status"] = "aborted"
                job["abort_requested"] = True
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                job.setdefault("logs", []).append("Recovered stale job on startup")
                job["error"] = job.get("error") or "Recovered stale job on startup"
                job["progress"] = max(int(job.get("progress") or 0), 100)
                changed = True
        if changed:
            save_jobs(jobs)

def request_abort_all() -> int:
    count = 0
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job.get("status") in {"queued", "running"} and not job.get("abort_requested"):
                job["abort_requested"] = True
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                count += 1
        save_jobs(jobs)
    return count


# -------------------------------
# Naming helpers
# -------------------------------
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


# -------------------------------
# Source helpers
# -------------------------------
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




def parse_filename_metadata(name: str) -> tuple[str, str, str]:
    base = Path(name).stem.strip()
    parts = [part.strip() for part in re.split(r"\s+-\s+", base) if part.strip()]
    if len(parts) >= 3:
        return parts[0], " - ".join(parts[1:-1]), parts[-1]
    if len(parts) == 2:
        return parts[0], "", parts[-1]
    return base, "", ""


def download_album_art(url: str, temp_dir: Path, logger) -> str:
    url = (url or '').strip()
    if not url:
        return ''
    try:
        suffix = Path(urlparse(url).path).suffix or '.jpg'
        out = temp_dir / f'cover{suffix}'
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        out.write_bytes(response.content)
        logger('Fetched album art from provided URL')
        return str(out)
    except Exception as exc:
        logger(f'Album art fetch skipped: {exc}')
        return ''



def normalize_download_payload(payload: dict) -> dict:
    payload = dict(payload or {})
    rename_to = (payload.get("rename_to") or "").strip()
    song_name = (payload.get("song_name") or "").strip()
    artist_names = (payload.get("artist_names") or "").strip()
    album_name = (payload.get("album_name") or "").strip()

    if rename_to and (not song_name or not artist_names or not album_name):
        parsed_title, parsed_album, parsed_artists = parse_filename_metadata(rename_to)
        song_name = song_name or parsed_title
        artist_names = artist_names or parsed_artists
        if not album_name and parsed_album:
            album_name = parsed_album

    payload["song_name"] = song_name
    payload["artist_names"] = artist_names
    payload["album_name"] = album_name
    payload["rename_to"] = rename_to
    return payload


def resolve_cookies_file(payload: dict | None = None) -> Path | None:
    payload = dict(payload or {})
    explicit = (payload.get("cookies_path") or "").strip()
    candidates: list[Path] = []
    if explicit:
        explicit_path = Path(explicit)
        if not explicit_path.is_absolute():
            explicit_path = (APP_DATA_DIR / explicit_path).resolve()
        candidates.append(explicit_path)
    candidates.append(DEFAULT_COOKIES_FILE)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def yt_dlp_base_cmd(payload: dict | None = None) -> list[str]:
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=android,web",
        "--sleep-requests",
        "1",
        "--sleep-interval",
        "2",
        "--max-sleep-interval",
        "5",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
    ]
    cookies_file = resolve_cookies_file(payload)
    if cookies_file:
        cmd.extend(["--cookies", str(cookies_file)])
    return cmd


def log_yt_dlp_runtime(job_id: str, payload: dict) -> None:
    cookies_file = resolve_cookies_file(payload)
    if cookies_file:
        append_log(job_id, f"Using cookies file: {cookies_file}")
    else:
        append_log(job_id, f"No cookies file found. Auto path checked: {DEFAULT_COOKIES_FILE}")
    append_log(job_id, "Using stable yt-dlp mode (no deno runtime)")

def _extract_progress_percent(line: str) -> int | None:
    match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
    if not match:
        return None
    return int(float(match.group(1)))


# -------------------------------
# Metadata enrichment
# -------------------------------
def safe_music_relative(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(MUSIC_ROOT))
    except ValueError:
        raise ValueError("Selected file must be inside /mnt/nas/media/music")


def parse_existing_lyrics(vtt_path: Path) -> str:
    try:
        text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT":
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d+\s+-->\s+\d{2}:\d{2}:\d{2}\.\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines).strip()


def read_current_tags(file_path: Path) -> dict:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist,album",
            "-of", "json", str(file_path)
        ], capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout or "{}")
        tags = ((payload.get("format") or {}).get("tags") or {})
        return {
            "title": (tags.get("title") or "").strip(),
            "artist": (tags.get("artist") or "").strip(),
            "album": (tags.get("album") or "").strip(),
        }
    except Exception:
        return {"title": "", "artist": "", "album": ""}


def _norm_compare(text: str) -> str:
    text = (text or "").replace("，", ",")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts:
        text = ", ".join(parts)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def metadata_matches_filename(file_path: Path, title: str, album: str, artist: str) -> bool:
    current = read_current_tags(file_path)
    title_ok = _norm_compare(current.get("title")) == _norm_compare(title)
    artist_ok = _norm_compare(current.get("artist")) == _norm_compare(artist)
    expected_album = (album or "").strip()
    if not expected_album:
        return title_ok and artist_ok
    album_ok = _norm_compare(current.get("album")) == _norm_compare(expected_album)
    return title_ok and artist_ok and album_ok


def fetch_source_info(source: str, temp_dir: Path, logger, payload: dict | None = None) -> dict:
    info_cmd = [*yt_dlp_base_cmd(payload), "-J", source]
    result = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout or "{}")

    thumbnail_file = None
    thumbnail_url = info.get("thumbnail")
    if thumbnail_url:
        try:
            suffix = Path(urlparse(thumbnail_url).path).suffix or ".jpg"
            thumbnail_file = temp_dir / f"cover{suffix}"
            response = requests.get(thumbnail_url, timeout=30)
            response.raise_for_status()
            thumbnail_file.write_bytes(response.content)
            logger("Fetched album art from YouTube thumbnail")
        except Exception as exc:
            logger(f"Album art fetch skipped: {exc}")
            thumbnail_file = None

    lyrics_text = ""
    subs_base = temp_dir / "subs"
    subs_cmd = [
        *yt_dlp_base_cmd(payload),
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-langs",
        "en.*,en",
        "--sub-format",
        "vtt/best",
        "-o",
        str(subs_base),
        source,
    ]
    subs_proc = subprocess.run(subs_cmd, capture_output=True, text=True)
    if subs_proc.returncode == 0:
        for candidate in sorted(temp_dir.glob("subs*.vtt")):
            lyrics_text = parse_existing_lyrics(candidate)
            if lyrics_text:
                logger("Fetched lyrics from subtitles/auto-captions")
                break
    else:
        logger("Lyrics fetch skipped: subtitles not available")

    return {
        "title": (info.get("track") or info.get("title") or "").strip(),
        "artist": (info.get("artist") or info.get("uploader") or "").strip(),
        "album": (info.get("album") or "").strip(),
        "thumbnail_file": str(thumbnail_file) if thumbnail_file and thumbnail_file.exists() else "",
        "lyrics": lyrics_text,
    }


def enrich_file_metadata(file_path: Path, payload: dict, source: str, logger) -> None:
    requested_title = (payload.get("song_name") or "").strip()
    requested_artist = (payload.get("artist_names") or "").strip()
    requested_album = (payload.get("album_name") or "").strip()

    with tempfile.TemporaryDirectory(prefix="songdown_meta_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        metadata = {
            "title": requested_title,
            "artist": requested_artist,
            "album": requested_album if requested_album and requested_album.lower() != "unknown" else "",
            "lyrics": "",
            "thumbnail_file": "",
        }
        provided_art = download_album_art((payload.get("album_art_url") or "").strip(), temp_dir, logger)
        if provided_art:
            metadata["thumbnail_file"] = provided_art

        try:
            source_info = fetch_source_info(source, temp_dir, logger, payload)
        except Exception as exc:
            logger(f"Metadata lookup skipped: {exc}")
            source_info = {}

        if not metadata["title"]:
            metadata["title"] = (source_info.get("title") or "").strip()
        if not metadata["artist"]:
            metadata["artist"] = (source_info.get("artist") or "").strip()
        if not metadata["album"]:
            metadata["album"] = (source_info.get("album") or "").strip()
        metadata["lyrics"] = (source_info.get("lyrics") or "").strip()
        if not metadata["thumbnail_file"]:
            metadata["thumbnail_file"] = (source_info.get("thumbnail_file") or "").strip()

        output_file = file_path.with_name(f"{file_path.stem}.retag{file_path.suffix}")
        ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(file_path)]
        if metadata["thumbnail_file"]:
            ffmpeg_cmd.extend(["-i", metadata["thumbnail_file"]])

        ffmpeg_cmd.extend(["-map", "0:a"])
        if metadata["thumbnail_file"]:
            ffmpeg_cmd.extend(["-map", "1", "-c:v", "mjpeg"])

        ffmpeg_cmd.extend([
            "-c:a", "copy",
            "-id3v2_version", "3",
            "-metadata", f"title={metadata['title']}",
            "-metadata", f"artist={metadata['artist']}",
            "-metadata", f"album={metadata['album']}",
        ])
        if metadata["lyrics"]:
            ffmpeg_cmd.extend(["-metadata", f"lyrics={metadata['lyrics']}"])
        if metadata["thumbnail_file"]:
            ffmpeg_cmd.extend([
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
            ])
        ffmpeg_cmd.append(str(output_file))

        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg metadata update failed")

        output_file.replace(file_path)
        logger(
            "Metadata applied: "
            f"title={metadata['title'] or '—'}, "
            f"artist={metadata['artist'] or '—'}, "
            f"album={metadata['album'] or '—'}, "
            f"lyrics={'yes' if metadata['lyrics'] else 'no'}, "
            f"album_art={'yes' if metadata['thumbnail_file'] else 'no'}"
        )


# -------------------------------
# Download / retag workers
# -------------------------------
def run_download_job(job_id: str) -> None:
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return

    payload = normalize_download_payload(job["payload"])
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
        log_yt_dlp_runtime(job_id, payload)
        source = resolve_source(payload)
        marker = f"job_{job_id.replace('-', '')}"
        output_template = str(DOWNLOADS_DIR / f"{marker}.%(ext)s")

        cmd = [
            *yt_dlp_base_cmd(payload),
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-metadata",
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
        append_log(job_id, f"Saved file: {final_path}")
        append_log(job_id, "Applying metadata enrichment")
        enrich_file_metadata(
            final_path,
            payload={
                **payload,
                "song_name": song_name,
                "artist_names": artist_names,
                "album_name": album_name,
            },
            source=source,
            logger=lambda line: append_log(job_id, line),
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

    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), progress=100)
        append_log(job_id, f"ERROR: {exc}")


def run_retag_job(job_id: str) -> None:
    if is_abort_requested(job_id):
        update_job(job_id, status="aborted", progress=100)
        return
    job = update_job(job_id, status="running", progress=5)
    if not job:
        return

    payload = normalize_download_payload(job["payload"])
    try:
        if is_abort_requested(job_id):
            update_job(job_id, status="aborted", progress=100)
            return
        relative_path = payload.get("selected_file", "")
        if not relative_path:
            raise ValueError("No song selected for retagging")
        target = (MUSIC_ROOT / relative_path).resolve()
        safe_music_relative(target)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("Selected song file was not found")

        append_log(job_id, f"Retagging file: {target}")
        log_yt_dlp_runtime(job_id, payload)
        source = resolve_source(payload)
        enrich_file_metadata(target, payload, source, lambda line: append_log(job_id, line))
        if is_abort_requested(job_id):
            update_job(job_id, status="aborted", final_file=str(target), progress=100)
            return
        update_job(job_id, status="completed", final_file=str(target), progress=100)
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), progress=100)
        append_log(job_id, f"ERROR: {exc}")

def run_retag_all_job(job_id: str) -> None:
    if is_abort_requested(job_id):
        update_job(job_id, status="aborted", progress=100)
        return
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return
    all_files = []
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".mp3":
            try:
                all_files.append(safe_music_relative(path))
            except Exception:
                continue
    total = len(all_files)
    if total == 0:
        append_log(job_id, "No mp3 files found for retag-all")
        update_job(job_id, status="completed", progress=100)
        return
    workers = min(4, max(1, (os.cpu_count() or 2)))
    append_log(job_id, f"Retag-all started with {total} songs using {workers} workers")
    completed = 0
    completed_lock = threading.Lock()

    def one(relative_path: str):
        nonlocal completed
        if is_abort_requested(job_id):
            return "aborted"
        path = (MUSIC_ROOT / relative_path).resolve()
        safe_music_relative(path)
        title, album, artist = parse_filename_metadata(path.name)
        payload = {
            "selected_file": relative_path,
            "song_name": title,
            "artist_names": artist,
            "album_name": album,
            "job_type": "retag-all",
        }
        if metadata_matches_filename(path, title, album, artist):
            append_log(job_id, f"Skipping {path.name}: metadata already matches filename")
        else:
            append_log(job_id, f"Retagging {path.name}")
            log_yt_dlp_runtime(job_id, payload)
            source = resolve_source(payload)
            enrich_file_metadata(path, payload, source, lambda line: append_log(job_id, line))
        with completed_lock:
            completed += 1
            pct = min(99, int((completed / total) * 100))
            set_progress(job_id, pct)
        return "ok"

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(one, rel) for rel in all_files]
            for fut in as_completed(futures):
                if is_abort_requested(job_id):
                    for f in futures:
                        f.cancel()
                    update_job(job_id, status="aborted", progress=100)
                    append_log(job_id, "Retag-all aborted")
                    return
                try:
                    fut.result()
                except Exception as exc:
                    append_log(job_id, f"ERROR: {exc}")
        final_status = "aborted" if is_abort_requested(job_id) else "completed"
        update_job(job_id, status=final_status, progress=100)
        append_log(job_id, f"Retag-all finished with status: {final_status}")
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), progress=100)
        append_log(job_id, f"ERROR: {exc}")




# -------------------------------
# Routes
# -------------------------------
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
        "auto_cookies_path": str(DEFAULT_COOKIES_FILE),
        "auto_cookies_present": DEFAULT_COOKIES_FILE.exists(),
        "yt_dlp_mode": "stable-no-deno",
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
        save_jobs(remaining)
    return jsonify({"ok": True})


@app.route("/api/library-songs", methods=["GET"])
def library_songs():
    songs = []
    for path in sorted(MUSIC_ROOT.rglob('*')):
        if path.is_file() and path.suffix.lower() == '.mp3':
            try:
                rel = safe_music_relative(path)
                songs.append({"path": rel, "name": path.name})
            except Exception:
                continue
    return jsonify({"songs": songs})


@app.route("/api/download", methods=["POST"])
def download():
    payload = request.get_json(force=True)
    job = create_job(payload)
    threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@app.route("/api/retag", methods=["POST"])
def retag():
    payload = request.get_json(force=True)
    job = create_job({**payload, "job_type": "retag"})
    threading.Thread(target=run_retag_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@app.route("/api/download-batch", methods=["POST"])
def download_batch():
    payload = request.get_json(force=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid payload"}), 400
    job_ids = []
    for song_name, item in payload.items():
        if not isinstance(item, dict):
            continue
        file_name = (item.get("file_name") or "").strip()
        title, album, artists = parse_filename_metadata(file_name or song_name)
        job_payload = normalize_download_payload({
            "song_name": title or song_name,
            "artist_names": artists,
            "album_name": album,
            "youtube_url": (item.get("ytb_link") or "").strip(),
            "rename_to": file_name or song_name,
            "auto_move": True,
            "album_art_url": (item.get("album_art") or "").strip(),
            "cookies_path": (item.get("cookies_path") or "").strip(),
            "job_type": "download-batch",
        })
        job = create_job(job_payload)
        threading.Thread(target=run_download_job, args=(job["id"],), daemon=True).start()
        job_ids.append(job["id"])
    return jsonify({"ok": True, "job_ids": job_ids})

@app.route("/api/retag-all", methods=["POST"])
def retag_all():
    job = create_job({"job_type": "retag-all", "song_name": "All songs from filenames", "artist_names": "", "album_name": "Unknown"})
    threading.Thread(target=run_retag_all_job, args=(job["id"],), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"]})


@app.route("/api/jobs/<job_id>/abort", methods=["POST"])
def abort_job(job_id: str):
    job = request_abort(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, "job": job})


@app.route("/api/jobs/abort-all", methods=["POST"])
def abort_all_jobs():
    count = request_abort_all()
    return jsonify({"ok": True, "count": count})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)


startup_reconcile_jobs()
