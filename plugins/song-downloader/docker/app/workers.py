from __future__ import annotations

import shutil
import subprocess
import threading
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
        dest_dir  = MUSIC_ROOT if auto_move else DOWNLOADS_DIR
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

    import os
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
