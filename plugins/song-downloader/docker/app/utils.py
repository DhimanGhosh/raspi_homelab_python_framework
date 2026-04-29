from __future__ import annotations

import re
from pathlib import Path

from app.config import APP_DATA_DIR, DEFAULT_COOKIES_FILE, DOWNLOADS_DIR
from app.jobs import append_log, update_job


# ── Filename helpers ───────────────────────────────────────────────────────────

def slugify_filename(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "downloaded-track"


def build_target_filename(song_name: str, artist_names: str, album_name: str) -> str:
    song_name    = slugify_filename(song_name    or "Unknown Song")
    artist_names = slugify_filename(artist_names or "Unknown Artist")
    album_name   = slugify_filename(album_name   or "Unknown")
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


def parse_filename_metadata(name: str) -> tuple[str, str, str]:
    base  = Path(name).stem.strip()
    parts = [part.strip() for part in re.split(r"\s+-\s+", base) if part.strip()]
    if len(parts) >= 3:
        return parts[0], " - ".join(parts[1:-1]), parts[-1]
    if len(parts) == 2:
        return parts[0], "", parts[-1]
    return base, "", ""


def infer_album_from_rename(rename_to: str, song_name: str, artist_names: str, album_name: str) -> str:
    rename_to = (rename_to or "").strip()
    if album_name and album_name.strip() and album_name.strip().lower() != "unknown":
        return album_name.strip()
    if not rename_to:
        return "Unknown"
    base  = rename_to[:-4] if rename_to.lower().endswith(".mp3") else rename_to
    parts = [part.strip() for part in base.split(" - ") if part.strip()]
    if len(parts) >= 3:
        return parts[1]
    return "Unknown"


def normalize_download_payload(payload: dict) -> dict:
    payload      = dict(payload or {})
    rename_to    = (payload.get("rename_to")    or "").strip()
    song_name    = (payload.get("song_name")    or "").strip()
    artist_names = (payload.get("artist_names") or "").strip()
    album_name   = (payload.get("album_name")   or "").strip()

    if rename_to and (not song_name or not artist_names or not album_name):
        parsed_title, parsed_album, parsed_artists = parse_filename_metadata(rename_to)
        song_name    = song_name    or parsed_title
        artist_names = artist_names or parsed_artists
        if not album_name and parsed_album:
            album_name = parsed_album

    payload["song_name"]    = song_name
    payload["artist_names"] = artist_names
    payload["album_name"]   = album_name
    payload["rename_to"]    = rename_to
    return payload


# ── yt-dlp helpers ─────────────────────────────────────────────────────────────

def yt_search_query(song_name: str, artist_names: str, album_name: str) -> str:
    query = " ".join(x for x in [song_name, artist_names, album_name, "official audio"] if x)
    return f"ytsearch1:{query.strip()}"


def resolve_source(payload: dict) -> str:
    youtube_url = (payload.get("youtube_url") or "").strip()
    if youtube_url:
        return youtube_url
    return yt_search_query(
        payload.get("song_name",    "").strip(),
        payload.get("artist_names", "").strip(),
        payload.get("album_name",   "").strip(),
    )


def resolve_cookies_file(payload: dict | None = None) -> Path | None:
    payload    = dict(payload or {})
    explicit   = (payload.get("cookies_path") or "").strip()
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
        "--extractor-args", "youtube:player_client=android,web",
        "--sleep-requests", "1",
        "--sleep-interval", "2",
        "--max-sleep-interval", "5",
        "--retries", "3",
        "--fragment-retries", "3",
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


def find_downloaded_file(download_dir: Path, marker: str) -> Path | None:
    for match in sorted(download_dir.glob(f"{marker}*")):
        if match.is_file() and match.suffix.lower() == ".mp3":
            return match
    return None


def set_progress(job_id: str, value: int) -> None:
    value = max(0, min(100, int(value)))
    update_job(job_id, progress=value)


def extract_progress_percent(line: str) -> int | None:
    match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
    if not match:
        return None
    return int(float(match.group(1)))
