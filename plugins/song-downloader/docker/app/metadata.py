from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.config import MUSIC_ROOT
from app.utils import yt_dlp_base_cmd


# ── Path safety ────────────────────────────────────────────────────────────────

def safe_music_relative(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(MUSIC_ROOT))
    except ValueError:
        raise ValueError("Selected file must be inside /mnt/nas/media/music")


# ── Lyrics helpers ─────────────────────────────────────────────────────────────

def parse_existing_lyrics(vtt_path: Path) -> str:
    try:
        text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    lines: list[str] = []
    seen:  set[str]  = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT":
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d+\s+-->\s+\d{2}:\d{2}:\d{2}\.\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines).strip()


# ── Tag helpers ────────────────────────────────────────────────────────────────

def read_current_tags(file_path: Path) -> dict:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format_tags=title,artist,album",
            "-of", "json", str(file_path),
        ], capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout or "{}")
        tags    = ((payload.get("format") or {}).get("tags") or {})
        return {
            "title":  (tags.get("title")  or "").strip(),
            "artist": (tags.get("artist") or "").strip(),
            "album":  (tags.get("album")  or "").strip(),
        }
    except Exception:
        return {"title": "", "artist": "", "album": ""}


def _norm_compare(text: str) -> str:
    text  = (text or "").replace("，", ",")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts:
        text = ", ".join(parts)
    return re.sub(r"\s+", " ", text).strip().lower()


def metadata_matches_filename(file_path: Path, title: str, album: str, artist: str) -> bool:
    current  = read_current_tags(file_path)
    title_ok = _norm_compare(current.get("title"))  == _norm_compare(title)
    artist_ok = _norm_compare(current.get("artist")) == _norm_compare(artist)
    expected_album = (album or "").strip()
    if not expected_album:
        return title_ok and artist_ok
    album_ok = _norm_compare(current.get("album")) == _norm_compare(expected_album)
    return title_ok and artist_ok and album_ok


# ── Art download ───────────────────────────────────────────────────────────────

def download_album_art(url: str, temp_dir: Path, logger) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        suffix = Path(urlparse(url).path).suffix or ".jpg"
        out    = temp_dir / f"cover{suffix}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        out.write_bytes(response.content)
        logger("Fetched album art from provided URL")
        return str(out)
    except Exception as exc:
        logger(f"Album art fetch skipped: {exc}")
        return ""


# ── Source info ────────────────────────────────────────────────────────────────

def fetch_source_info(source: str, temp_dir: Path, logger, payload: dict | None = None) -> dict:
    info_cmd = [*yt_dlp_base_cmd(payload), "-J", source]
    result   = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
    info     = json.loads(result.stdout or "{}")

    thumbnail_file = None
    thumbnail_url  = info.get("thumbnail")
    if thumbnail_url:
        try:
            suffix         = Path(urlparse(thumbnail_url).path).suffix or ".jpg"
            thumbnail_file = temp_dir / f"cover{suffix}"
            response       = requests.get(thumbnail_url, timeout=30)
            response.raise_for_status()
            thumbnail_file.write_bytes(response.content)
            logger("Fetched album art from YouTube thumbnail")
        except Exception as exc:
            logger(f"Album art fetch skipped: {exc}")
            thumbnail_file = None

    lyrics_text = ""
    subs_base   = temp_dir / "subs"
    subs_cmd    = [
        *yt_dlp_base_cmd(payload),
        "--skip-download", "--write-auto-sub", "--write-sub",
        "--sub-langs", "en.*,en", "--sub-format", "vtt/best",
        "-o", str(subs_base), source,
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
        "title":          (info.get("track") or info.get("title") or "").strip(),
        "artist":         (info.get("artist") or info.get("uploader") or "").strip(),
        "album":          (info.get("album") or "").strip(),
        "thumbnail_file": str(thumbnail_file) if thumbnail_file and thumbnail_file.exists() else "",
        "lyrics":         lyrics_text,
    }


# ── Main enrichment ────────────────────────────────────────────────────────────

def enrich_file_metadata(file_path: Path, payload: dict, source: str, logger) -> None:
    requested_title  = (payload.get("song_name")    or "").strip()
    requested_artist = (payload.get("artist_names") or "").strip()
    requested_album  = (payload.get("album_name")   or "").strip()

    with tempfile.TemporaryDirectory(prefix="songdown_meta_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        metadata = {
            "title":          requested_title,
            "artist":         requested_artist,
            "album":          requested_album if requested_album and requested_album.lower() != "unknown" else "",
            "lyrics":         "",
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

        if not metadata["title"]:   metadata["title"]  = (source_info.get("title")  or "").strip()
        if not metadata["artist"]:  metadata["artist"] = (source_info.get("artist") or "").strip()
        if not metadata["album"]:   metadata["album"]  = (source_info.get("album")  or "").strip()
        metadata["lyrics"] = (source_info.get("lyrics") or "").strip()
        if not metadata["thumbnail_file"]:
            metadata["thumbnail_file"] = (source_info.get("thumbnail_file") or "").strip()

        output_file = file_path.with_name(f"{file_path.stem}.retag{file_path.suffix}")
        ffmpeg_cmd  = ["ffmpeg", "-y", "-i", str(file_path)]
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
