from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

from app.config import ART_CACHE_DIR, IMAGE_EXTENSIONS, MUSIC_ROOT
from app.utils import (
    first_value,
    mime_to_ext,
    normalize_spaces,
    parse_filename,
    safe_component,
    split_artists,
)


# ── Cover art ─────────────────────────────────────────────────────────────────

def ensure_cover_art(path: Path) -> str | None:
    key = hashlib.sha1(str(path).encode("utf-8")).hexdigest()
    for ext in IMAGE_EXTENSIONS:
        candidate = ART_CACHE_DIR / f"{key}{ext}"
        if candidate.exists():
            return f"/api/art-cache/{candidate.name}"
    try:
        audio = MutagenFile(path)
        if audio is None:
            return None
        tags = getattr(audio, "tags", None)
        if tags is None:
            return None
        data: bytes | None = None
        ext = ".jpg"
        if isinstance(tags, ID3):
            apic_frames = tags.getall("APIC")
            if apic_frames:
                data = apic_frames[0].data
                ext  = mime_to_ext(apic_frames[0].mime)
        elif hasattr(audio, "pictures") and getattr(audio, "pictures", None):
            picture = audio.pictures[0]
            data    = picture.data
            ext     = mime_to_ext(getattr(picture, "mime", None))
        elif isinstance(audio, MP4):
            covr = audio.tags.get("covr", []) if audio.tags else []
            if covr:
                cover = covr[0]
                data  = bytes(cover)
                fmt   = getattr(cover, "imageformat", None)
                ext   = ".png" if fmt == MP4Cover.FORMAT_PNG else ".jpg"
        if data:
            target = ART_CACHE_DIR / f"{key}{ext}"
            target.write_bytes(data)
            return f"/api/art-cache/{target.name}"
    except Exception:
        return None
    return None


# ── Track metadata ─────────────────────────────────────────────────────────────

def track_metadata(path: Path) -> dict[str, Any]:
    file_title, file_album, file_artists = parse_filename(path.name)
    title, album, artists, year, duration = file_title, file_album or "Unknown", file_artists[:], "", 0
    art_url = None
    try:
        audio = MutagenFile(path)
        if audio is not None:
            duration   = int(getattr(getattr(audio, "info", None), "length", 0) or 0)
            tags       = getattr(audio, "tags", None)
            tag_title  = normalize_spaces(first_value(tags, ["TIT2", "title", "TITLE", "©nam"]))
            tag_album  = normalize_spaces(first_value(tags, ["TALB", "album", "ALBUM", "©alb"]))
            tag_artist = normalize_spaces(first_value(tags, ["TPE1", "artist", "ARTIST", "©ART", "aART"]))
            tag_year   = normalize_spaces(first_value(tags, ["TDRC", "date", "DATE", "year", "YEAR", "©day"]))
            if tag_title:  title   = tag_title
            if tag_album:  album   = tag_album
            if tag_artist: artists = split_artists(tag_artist) or artists
            if tag_year:   year    = re.sub(r"[^0-9]", "", tag_year)[:4]
            art_url = ensure_cover_art(path)
    except Exception:
        pass
    if not artists:
        artists = ["Unknown Artist"]
    return {
        "title":    title,
        "album":    album or "Unknown",
        "artists":  artists,
        "artist":   ", ".join(artists),
        "year":     year,
        "duration": duration,
        "art_url":  art_url,
    }


# ── Remote fetch ───────────────────────────────────────────────────────────────

def fetch_remote_bytes(url: str) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:
        data         = resp.read()
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip().lower()
    return data, content_type


# ── Embedded art write ─────────────────────────────────────────────────────────

def write_embedded_art(path: Path, image_bytes: bytes, content_type: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        for key in list(tags.keys()):
            if key.startswith("APIC"):
                del tags[key]
        tags.add(APIC(encoding=3, mime=content_type, type=3, desc="Cover", data=image_bytes))
        tags.save(path)
        ensure_cover_art(path)
        return
    if suffix == ".flac":
        audio = FLAC(path)
        audio.clear_pictures()
        picture      = Picture()
        picture.type = 3
        picture.mime = content_type
        picture.data = image_bytes
        audio.add_picture(picture)
        audio.save()
        ensure_cover_art(path)
        return
    if suffix in {".m4a", ".mp4", ".aac"}:
        audio = MP4(path)
        fmt   = MP4Cover.FORMAT_PNG if content_type == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
        audio.save()
        ensure_cover_art(path)
        return
    raise ValueError(f"embedded art not supported for {suffix}")


# ── Rename helper ──────────────────────────────────────────────────────────────

def rename_track_for_metadata(path: Path, title: str, album: str, artist: str) -> Path:
    suffix = path.suffix
    current_title, current_album, current_artists = parse_filename(path.name)
    effective_title  = title  or current_title or path.stem
    effective_album  = album  or current_album
    effective_artist = artist or ", ".join(current_artists) or "Unknown Artist"
    parts = [safe_component(effective_title)]
    if effective_album and effective_album.lower() != "unknown":
        parts.append(safe_component(effective_album))
    if effective_artist:
        parts.append(safe_component(effective_artist.replace(", ", ",")))
    candidate = " - ".join(parts) + suffix
    if candidate != path.name:
        dest    = path.with_name(candidate)
        counter = 1
        while dest.exists() and dest != path:
            dest = path.with_name(f"{' - '.join(parts)} ({counter}){suffix}")
            counter += 1
        shutil.move(str(path), str(dest))
        return dest
    return path
