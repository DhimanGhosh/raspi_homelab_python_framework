from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover
    MutagenFile = None

APP_VERSION = os.getenv("APP_VERSION", "7.2.0")
APP_NAME = os.getenv("APP_NAME", "Music Player")
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS = {"chorus", "others", "other", "music"}

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def stable_track_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


def split_artists(artists_raw: str) -> list[str]:
    artists: list[str] = []
    for chunk in ARTIST_SPLIT_RE.split(artists_raw or ""):
        item = normalize_spaces(chunk)
        if item and item.lower() not in IGNORE_ARTISTS and item not in artists:
            artists.append(item)
    return artists


def parse_filename(name: str) -> tuple[str, str, list[str]]:
    base = Path(name).stem
    base = re.sub(r"[_\.]+", " ", base)
    base = normalize_spaces(base)
    parts = [normalize_spaces(part) for part in base.split(" - ") if normalize_spaces(part)]
    if len(parts) >= 3:
        title = parts[0]
        album = parts[-2]
        artists = split_artists(parts[-1])
        return title, album or "Unknown", artists
    if len(parts) == 2:
        title = parts[0]
        artists = split_artists(parts[-1])
        return title, "Unknown", artists
    return base, "Unknown", []


def read_playlists() -> dict[str, list[str]]:
    if PLAYLISTS_FILE.exists():
        try:
            data = json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): [str(x) for x in (v or [])] for k, v in data.items()}
        except Exception:
            pass
    return {}


def write_playlists(data: dict[str, list[str]]) -> None:
    PLAYLISTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_settings() -> dict:
    defaults = {"crossfade_enabled": False, "crossfade_seconds": 4}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = defaults | data
                merged["crossfade_seconds"] = max(0, min(12, int(merged.get("crossfade_seconds", 4) or 0)))
                merged["crossfade_enabled"] = bool(merged.get("crossfade_enabled", False))
                return merged
        except Exception:
            pass
    return defaults


def write_settings(data: dict) -> dict:
    settings = {
        "crossfade_enabled": bool(data.get("crossfade_enabled", False)),
        "crossfade_seconds": max(0, min(12, int(data.get("crossfade_seconds", 4) or 0))),
    }
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    return settings


def _first_value(tags: dict, keys: list[str]) -> str:
    for key in keys:
        value = tags.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        text = normalize_spaces(value)
        if text:
            return text
    return ""


def _extract_cover_data_url(audio_file) -> str:
    if audio_file is None:
        return ""
    tags = getattr(audio_file, "tags", None)
    if not tags:
        return ""
    try:
        if isinstance(tags, dict):
            for key in ["APIC:", "APIC", "covr", "metadata_block_picture"]:
                if key not in tags:
                    continue
                value = tags[key]
                if isinstance(value, list):
                    value = value[0] if value else None
                if value is None:
                    continue
                if hasattr(value, "data"):
                    mime = getattr(value, "mime", "image/jpeg") or "image/jpeg"
                    data = value.data
                elif hasattr(value, "value"):
                    mime = "image/jpeg"
                    data = value.value
                elif isinstance(value, bytes):
                    mime = "image/jpeg"
                    data = value
                else:
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""
    return ""


def _extract_lyrics(path: Path, audio_file) -> str:
    lrc_path = path.with_suffix(".lrc")
    if lrc_path.exists():
        try:
            return lrc_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if audio_file is None:
        return ""
    tags = getattr(audio_file, "tags", None)
    if not tags:
        return ""
    try:
        if isinstance(tags, dict):
            for key in ["USLT::eng", "USLT", "lyrics", "LYRICS", "©lyr"]:
                value = tags.get(key)
                if value is None:
                    continue
                if isinstance(value, list):
                    value = value[0] if value else None
                if hasattr(value, "text"):
                    text = value.text
                elif isinstance(value, bytes):
                    text = value.decode("utf-8", errors="ignore")
                else:
                    text = str(value)
                text = normalize_spaces(text)
                if text:
                    return text
    except Exception:
        return ""
    return ""


def extract_metadata(path: Path) -> dict:
    file_title, file_album, file_artists = parse_filename(path.name)
    metadata = {
        "title": file_title,
        "album": file_album or "Unknown",
        "artists": file_artists,
        "artist": ", ".join(file_artists) if file_artists else "Unknown Artist",
        "duration": 0,
        "duration_text": "0:00",
        "year": "",
        "cover_data_url": "",
        "lyrics": "",
    }
    if MutagenFile is None:
        return metadata
    try:
        audio_file = MutagenFile(path)
    except Exception:
        audio_file = None
    if audio_file is None:
        return metadata

    tags = getattr(audio_file, "tags", {}) or {}
    title = _first_value(tags, ["title", "TIT2", "©nam"])
    album = _first_value(tags, ["album", "TALB", "©alb"])
    artist_value = _first_value(tags, ["artist", "albumartist", "TPE1", "TPE2", "©ART"])
    year = _first_value(tags, ["date", "year", "TDRC", "TYER", "©day"])

    if title:
        metadata["title"] = title
    if album:
        metadata["album"] = album
    if artist_value:
        artists = split_artists(artist_value)
        metadata["artists"] = artists or file_artists
        metadata["artist"] = ", ".join(metadata["artists"]) if metadata["artists"] else artist_value
    if year:
        metadata["year"] = re.findall(r"\d{4}", year)[0] if re.findall(r"\d{4}", year) else year

    duration = float(getattr(getattr(audio_file, "info", None), "length", 0) or 0)
    metadata["duration"] = round(duration, 2)
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    metadata["duration_text"] = f"{minutes}:{seconds:02d}"
    metadata["cover_data_url"] = _extract_cover_data_url(audio_file)
    metadata["lyrics"] = _extract_lyrics(path, audio_file)
    if not metadata["artists"]:
        metadata["artists"] = file_artists
        metadata["artist"] = ", ".join(file_artists) if file_artists else "Unknown Artist"
    if not metadata["album"]:
        metadata["album"] = file_album or "Unknown"
    return metadata


def scan_tracks() -> list[dict]:
    tracks: list[dict] = []
    if not MUSIC_ROOT.exists():
        return tracks
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = path.relative_to(MUSIC_ROOT).as_posix()
            meta = extract_metadata(path)
            tracks.append(
                {
                    "id": stable_track_id(rel),
                    "path": rel,
                    "title": meta["title"],
                    "artist": meta["artist"],
                    "artists": meta["artists"],
                    "album": meta["album"] or "Unknown",
                    "folder": "" if str(Path(rel).parent) == "." else str(Path(rel).parent),
                    "filename": path.name,
                    "stream_url": "/api/stream/" + rel,
                    "duration": meta["duration"],
                    "duration_text": meta["duration_text"],
                    "year": meta["year"],
                    "lyrics": meta["lyrics"],
                    "cover_data_url": meta["cover_data_url"],
                }
            )
    tracks.sort(key=lambda x: (x["title"].lower(), x["artist"].lower(), x["path"].lower()))
    return tracks


def library_payload() -> dict:
    tracks = scan_tracks()
    track_map = {track["id"]: track for track in tracks}
    playlists = []
    legacy_unresolved = False
    for name, raw_ids in sorted(read_playlists().items()):
        resolved_ids = [track_id for track_id in raw_ids if track_id in track_map]
        unresolved_count = len(raw_ids) - len(resolved_ids)
        if unresolved_count:
            legacy_unresolved = True
        playlists.append({
            "name": name,
            "tracks": resolved_ids,
            "count": len(resolved_ids),
            "stored_count": len(raw_ids),
            "unresolved_count": unresolved_count,
        })

    artist_map: dict[str, list[str]] = {}
    album_map: dict[str, list[str]] = {}
    folder_map: dict[str, list[str]] = {}
    for track in tracks:
        artists = track.get("artists") or []
        if artists:
            for artist in artists:
                artist_map.setdefault(artist.strip(), []).append(track["id"])
        else:
            artist_map.setdefault("Unknown Artist", []).append(track["id"])
        album_map.setdefault(track.get("album") or "Unknown", []).append(track["id"])
        folder_name = track["folder"] or "Root"
        folder_map.setdefault(folder_name, []).append(track["id"])

    artists = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(artist_map.items())]
    albums = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(album_map.items())]
    folders = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(folder_map.items())]

    return {
        "tracks": tracks,
        "playlists": playlists,
        "artists": artists,
        "albums": albums,
        "folders": folders,
        "settings": read_settings(),
        "name": APP_NAME,
        "version": APP_VERSION,
        "playlist_note": (
            "Some older playlists may have unresolved legacy entries from a previous broken ID format."
            if legacy_unresolved else ""
        ),
    }


def resolve_target(rel: str) -> Path | None:
    target = (MUSIC_ROOT / rel).resolve()
    if not target.exists() or not target.is_file():
        return None
    if MUSIC_ROOT != target and MUSIC_ROOT not in target.parents:
        return None
    return target


def safe_filename(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    idx = 1
    while True:
        option = target_dir / f"{stem} ({idx}){suffix}"
        if not option.exists():
            return option
        idx += 1


def move_tracks_to_folder(track_ids: list[str], folder_name: str) -> int:
    folder_name = normalize_spaces(folder_name).strip("/\\")
    if not folder_name:
        raise ValueError("folder name required")
    target_dir = (MUSIC_ROOT / folder_name).resolve()
    if MUSIC_ROOT != target_dir and MUSIC_ROOT not in target_dir.parents:
        raise ValueError("invalid folder target")
    target_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for track in scan_tracks():
        if track["id"] not in track_ids:
            continue
        source = resolve_target(track["path"])
        if source is None:
            continue
        dest = safe_filename(target_dir, source.name)
        shutil.move(str(source), str(dest))
        moved += 1
    return moved


class Handler(BaseHTTPRequestHandler):
    server_version = "MusicPlayer/" + APP_VERSION

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _json(self, payload, code: int = 200, include_body: bool = True) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def _text(self, text: str, code: int = 200, ctype: str = "text/plain; charset=utf-8", include_body: bool = True) -> None:
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_HEAD(self) -> None:
        self._handle_request(head_only=True)

    def do_GET(self) -> None:
        self._handle_request(head_only=False)

    def _serve_static(self, rel_path: str, head_only: bool) -> None:
        rel = rel_path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not target.exists() or not target.is_file() or STATIC_DIR not in target.parents:
            return self._json({"error": "not found"}, 404, include_body=not head_only)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _stream_audio(self, target: Path, head_only: bool) -> None:
        size = target.stat().st_size
        range_header = self.headers.get("Range")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if not head_only:
                with target.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile)
            return
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            return self._json({"error": "invalid range"}, 416, include_body=not head_only)
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return self._json({"error": "invalid range"}, 416, include_body=not head_only)
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if not head_only:
            with target.open("rb") as handle:
                handle.seek(start)
                self.wfile.write(handle.read(length))

    def _handle_request(self, head_only: bool) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ["/", "/index.html"]:
            return self._text(read_text(TEMPLATES_DIR / "index.html"), ctype="text/html; charset=utf-8", include_body=not head_only)
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):], head_only=head_only)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/health":
            return self._json({"status": "ok", "version": APP_VERSION, "name": APP_NAME}, include_body=not head_only)
        if path == "/api/library":
            return self._json(library_payload(), include_body=not head_only)
        if path == "/api/settings":
            return self._json(read_settings(), include_body=not head_only)
        if path.startswith("/api/stream/"):
            rel = unquote(path[len("/api/stream/"):])
            target = resolve_target(rel)
            if target is None:
                return self._json({"error": "not found"}, 404, include_body=not head_only)
            return self._stream_audio(target, head_only)
        return self._json({"error": "not found"}, 404, include_body=not head_only)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8"))

        if parsed.path == "/api/playlists":
            name = normalize_spaces(str(payload.get("name", "")))
            track_ids = [str(x) for x in payload.get("track_ids", [])]
            add_anyway = bool(payload.get("add_anyway", False))
            if not name:
                return self._json({"error": "playlist name required"}, 400)
            data = read_playlists()
            existing = data.get(name, [])
            duplicates = [track_id for track_id in track_ids if track_id in existing]
            if add_anyway:
                data[name] = existing + track_ids
            else:
                data[name] = existing + [track_id for track_id in track_ids if track_id not in existing]
            write_playlists(data)
            return self._json({"ok": True, "name": name, "count": len(data[name]), "duplicates": duplicates})

        if parsed.path == "/api/playlists/rename":
            old_name = normalize_spaces(str(payload.get("old_name", "")))
            new_name = normalize_spaces(str(payload.get("new_name", "")))
            if not old_name or not new_name:
                return self._json({"error": "old and new playlist names required"}, 400)
            data = read_playlists()
            if old_name not in data:
                return self._json({"error": "playlist not found"}, 404)
            tracks = data.pop(old_name)
            existing = data.get(new_name, [])
            data[new_name] = existing + tracks
            write_playlists(data)
            return self._json({"ok": True, "name": new_name})

        if parsed.path == "/api/playlists/delete":
            name = normalize_spaces(str(payload.get("name", "")))
            if not name:
                return self._json({"error": "playlist name required"}, 400)
            data = read_playlists()
            data.pop(name, None)
            write_playlists(data)
            return self._json({"ok": True})

        if parsed.path == "/api/folders/create":
            name = normalize_spaces(str(payload.get("name", ""))).strip("/\\")
            if not name:
                return self._json({"error": "folder name required"}, 400)
            target = (MUSIC_ROOT / name).resolve()
            if MUSIC_ROOT != target and MUSIC_ROOT not in target.parents:
                return self._json({"error": "invalid folder target"}, 400)
            target.mkdir(parents=True, exist_ok=True)
            return self._json({"ok": True, "name": name})

        if parsed.path == "/api/folders/add":
            name = normalize_spaces(str(payload.get("name", "")))
            track_ids = [str(x) for x in payload.get("track_ids", [])]
            if not name:
                return self._json({"error": "folder name required"}, 400)
            try:
                moved = move_tracks_to_folder(track_ids, name)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"ok": True, "name": name, "moved": moved})

        if parsed.path == "/api/settings":
            settings = write_settings(payload)
            return self._json({"ok": True, "settings": settings})

        return self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8140"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"{APP_NAME} listening on {port}", flush=True)
    server.serve_forever()
