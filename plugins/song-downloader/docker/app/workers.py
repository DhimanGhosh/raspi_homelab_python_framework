from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import DOWNLOADS_DIR, MUSIC_ROOT
from app.jobs import append_log, is_abort_requested, update_job
from app.metadata import (
    enrich_file_metadata,
    metadata_matches_filename,
    safe_music_relative,
)
from app.utils import (
    build_target_filename,
    extract_progress_percent,
    find_downloaded_file,
    infer_album_from_rename,
    log_yt_dlp_runtime,
    normalize_download_payload,
    parse_filename_metadata,
    resolve_source,
    safe_destination,
    set_progress,
    yt_dlp_base_cmd,
)


# ── Download job ───────────────────────────────────────────────────────────────

def run_download_job(job_id: str) -> None:
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return

    payload      = normalize_download_payload(job["payload"])
    song_name    = payload.get("song_name",    "").strip()
    artist_names = payload.get("artist_names", "").strip()
    rename_to    = payload.get("rename_to",    "").strip()
    album_name   = infer_album_from_rename(
        rename_to=rename_to,
        song_name=song_name,
        artist_names=artist_names,
        album_name=payload.get("album_name", "").strip() or "Unknown",
    )
    auto_move = bool(payload.get("auto_move", True))

    try:
        append_log(job_id, "Preparing download job")
        log_yt_dlp_runtime(job_id, payload)
        source  = resolve_source(payload)
        marker  = f"job_{job_id.replace('-', '')}"
        output_template = str(DOWNLOADS_DIR / f"{marker}.%(ext)s")

        cmd = [
            *yt_dlp_base_cmd(payload),
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            "--embed-metadata", "--newline",
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
                    pct = extract_progress_percent(line)
                    if pct is not None:
                        last_progress = max(last_progress, pct)
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
        dest_dir   = MUSIC_ROOT if auto_move else DOWNLOADS_DIR
        final_path = safe_destination(dest_dir / target_name)

        shutil.move(str(downloaded), str(final_path))
        append_log(job_id, f"Saved file: {final_path}")
        append_log(job_id, "Applying metadata enrichment")
        enrich_file_metadata(
            final_path,
            payload={**payload, "song_name": song_name, "artist_names": artist_names, "album_name": album_name},
            source=source,
            logger=lambda line: append_log(job_id, line),
        )

        update_job(
            job_id,
            status="completed",
            output_file=str(downloaded),
            final_file=str(final_path),
            progress=100,
            payload={**payload, "song_name": song_name, "artist_names": artist_names, "album_name": album_name},
        )

    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), progress=100)
        append_log(job_id, f"ERROR: {exc}")


# ── Sequential batch runner ────────────────────────────────────────────────────

def run_sequential_batch(job_ids: list[str], delay_seconds: int) -> None:
    """Run a list of download jobs one by one with a cooldown between each.

    Each job shows a "waiting N s" message before it starts so the user can see
    the anti-bot delay in real time on the job card.
    """
    for i, job_id in enumerate(job_ids):
        if is_abort_requested(job_id):
            update_job(job_id, status="aborted", progress=100)
            continue
        if i > 0 and delay_seconds > 0:
            # Mark as running so the card shows activity while waiting
            update_job(job_id, status="running", progress=1)
            append_log(job_id, f"Anti-bot cooldown: waiting {delay_seconds}s before starting…")
            # Respect per-second abort checks during the wait
            for _ in range(delay_seconds):
                if is_abort_requested(job_id):
                    break
                time.sleep(1)
        run_download_job(job_id)


# ── Retag job ──────────────────────────────────────────────────────────────────

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


# ── Retag-from-JSON job ────────────────────────────────────────────────────────

def run_retag_from_json_job(job_id: str) -> None:
    """Match existing library files against a songs JSON map and retag them
    sequentially using the YouTube links provided in the JSON (avoids bot-check
    failures that occur when every search query hits YouTube at once).
    """
    if is_abort_requested(job_id):
        update_job(job_id, status="aborted", progress=100)
        return
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return

    payload       = job["payload"]
    songs_map     = payload.get("songs_map", {})
    delay_seconds = int(payload.get("delay_seconds", 8))

    # Build a lowercase-stem → Path index of every mp3 in the library
    library_index: dict[str, Path] = {}
    for path in MUSIC_ROOT.rglob("*.mp3"):
        library_index[path.stem.strip().lower()] = path

    matched:   list[tuple[Path, str, dict]] = []
    not_found: list[str]                    = []

    for song_key, item in songs_map.items():
        file_name = (item.get("file_name") or song_key).strip()
        # Strip .mp3 extension if present to get the stem
        stem = Path(file_name).stem if file_name.lower().endswith(".mp3") else file_name
        if stem.strip().lower() in library_index:
            matched.append((library_index[stem.strip().lower()], song_key, item))
        else:
            not_found.append(file_name)

    append_log(job_id, f"Library scan complete — matched: {len(matched)}, not found: {len(not_found)}")
    for nf in not_found:
        append_log(job_id, f"  NOT FOUND: {nf}")

    total = len(matched)
    if total == 0:
        update_job(job_id, status="completed", progress=100)
        append_log(job_id, "Nothing to retag.")
        return

    for i, (file_path, song_key, item) in enumerate(matched):
        if is_abort_requested(job_id):
            update_job(job_id, status="aborted", progress=100)
            append_log(job_id, "Retag from JSON aborted.")
            return

        if i > 0 and delay_seconds > 0:
            append_log(job_id, f"Anti-bot cooldown: waiting {delay_seconds}s…")
            for _ in range(delay_seconds):
                if is_abort_requested(job_id):
                    break
                time.sleep(1)

        file_name     = (item.get("file_name") or song_key).strip()
        title, album, artist = parse_filename_metadata(file_name)
        ytb_link      = (item.get("ytb_link")   or "").strip()
        album_art_url = (item.get("album_art")  or "").strip()

        retag_payload = {
            "song_name":    title  or song_key,
            "artist_names": artist,
            "album_name":   album,
            "youtube_url":  ytb_link,
            "album_art_url": album_art_url,
        }

        try:
            append_log(job_id, f"({i + 1}/{total}) Retagging: {file_path.name}")
            log_yt_dlp_runtime(job_id, retag_payload)
            source = resolve_source(retag_payload)
            enrich_file_metadata(file_path, retag_payload, source, lambda line: append_log(job_id, line))
        except Exception as exc:
            append_log(job_id, f"ERROR retagging {file_path.name}: {exc}")

        set_progress(job_id, min(99, int(((i + 1) / total) * 100)))

    final_status = "aborted" if is_abort_requested(job_id) else "completed"
    update_job(job_id, status=final_status, progress=100)
    append_log(job_id, f"Retag from JSON done — matched: {len(matched)}, not found: {len(not_found)}")


# ── Retag-all job ──────────────────────────────────────────────────────────────

def run_retag_all_job(job_id: str) -> None:
    if is_abort_requested(job_id):
        update_job(job_id, status="aborted", progress=100)
        return
    job = update_job(job_id, status="running", progress=1)
    if not job:
        return

    all_files: list[str] = []
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
    completed      = 0
    completed_lock = threading.Lock()

    def one(relative_path: str):
        nonlocal completed
        if is_abort_requested(job_id):
            return "aborted"
        path  = (MUSIC_ROOT / relative_path).resolve()
        safe_music_relative(path)
        title, album, artist = parse_filename_metadata(path.name)
        job_payload = {
            "selected_file": relative_path,
            "song_name":     title,
            "artist_names":  artist,
            "album_name":    album,
            "job_type":      "retag-all",
        }
        if metadata_matches_filename(path, title, album, artist):
            append_log(job_id, f"Skipping {path.name}: metadata already matches filename")
        else:
            append_log(job_id, f"Retagging {path.name}")
            log_yt_dlp_runtime(job_id, job_payload)
            source = resolve_source(job_payload)
            enrich_file_metadata(path, job_payload, source, lambda line: append_log(job_id, line))
        with completed_lock:
            completed += 1
            set_progress(job_id, min(99, int((completed / total) * 100)))
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
