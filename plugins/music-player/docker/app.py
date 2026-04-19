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

from mutagen import File as MutagenFile
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, USLT

APP_VERSION = os.getenv("APP_VERSION", "7.2.1")
APP_NAME = os.getenv("APP_NAME", "Music Player")
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|，|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS = {"chorus", "others", "other", "music"}

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("，", ",")).strip()


def split_artists(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        raw = ", ".join(str(x) for x in value if x)
    else:
        raw = str(value or "")
    artists: list[str] = []
    for chunk in ARTIST_SPLIT_RE.split(raw):
        item = normalize_spaces(chunk)
        if item and item.lower() not in IGNORE_ARTISTS and item not in artists:
            artists.append(item)
    return artists


def stable_track_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


def parse_filename(name: str) -> tuple[str, str, list[str]]:
    base = normalize_spaces(re.sub(r"[_\.]+", " ", Path(name).stem))
    parts = [normalize_spaces(p) for p in base.split(" - ") if normalize_spaces(p)]
    if len(parts) >= 3:
        title = " - ".join(parts[:-2])
        album = parts[-2]
        artists = split_artists(parts[-1])
        return title or base, album or "Unknown", artists
    if len(parts) == 2:
        return parts[0], "Unknown", split_artists(parts[1])
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


def extract_embedded_lyrics(path: Path) -> str:
    try:
        tags = ID3(path)
        for key in tags.keys():
            if key.startswith("USLT"):
                text = normalize_spaces(getattr(tags[key], "text", ""))
                if text:
                    return text
    except Exception:
        pass
    lrc = path.with_suffix(".lrc")
    if lrc.exists():
        try:
            content = lrc.read_text(encoding="utf-8", errors="ignore")
            content = re.sub(r"\[[^\]]+\]", "", content)
            return normalize_spaces(content)
        except Exception:
            return ""
    return ""


def extract_album_art_data_uri(path: Path) -> str:
    try:
        tags = ID3(path)
        for key in tags.keys():
            if key.startswith("APIC"):
                frame = tags[key]
                mime = getattr(frame, "mime", "image/jpeg") or "image/jpeg"
                data = base64.b64encode(frame.data).decode("ascii")
                return f"data:{mime};base64,{data}"
    except Exception:
        pass
    return ""


def read_track_metadata(path: Path) -> dict:
    file_title, file_album, file_artists = parse_filename(path.name)
    title = file_title
    album = file_album or "Unknown"
    artists = file_artists[:]
    duration = 0
    lyrics = ""
    album_art = ""
    try:
        audio = MutagenFile(path)
        if audio is not None:
            duration = int(getattr(getattr(audio, "info", None), "length", 0) or 0)
            tags = getattr(audio, "tags", None)
            if tags:
                def first(keys: list[str]) -> str:
                    for key in keys:
                        if key in tags:
                            value = tags.get(key)
                            if isinstance(value, list):
                                if value:
                                    return str(value[0])
                            text = getattr(value, "text", None)
                            if isinstance(text, list) and text:
                                return str(text[0])
                            if text:
                                return str(text)
                            if value:
                                return str(value)
                    return ""

                tag_title = normalize_spaces(first(["TIT2", "title", "TITLE"]))
                tag_album = normalize_spaces(first(["TALB", "album", "ALBUM"]))
                tag_artist = normalize_spaces(first(["TPE1", "artist", "ARTIST"]))
                if tag_title:
                    title = tag_title
                if tag_album:
                    album = tag_album
                if tag_artist:
                    artists = split_artists(tag_artist) or artists
    except Exception:
        pass

    lyrics = extract_embedded_lyrics(path)
    album_art = extract_album_art_data_uri(path)
    if not artists:
        artists = ["Unknown Artist"]
    return {
        "title": title,
        "album": album or "Unknown",
        "artists": artists,
        "artist": ", ".join(artists),
        "duration": duration,
        "lyrics": lyrics,
        "album_art": album_art,
    }


def scan_tracks() -> list[dict]:
    tracks: list[dict] = []
    if not MUSIC_ROOT.exists():
        return tracks

    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = path.relative_to(MUSIC_ROOT).as_posix()
            meta = read_track_metadata(path)
            tracks.append(
                {
                    "id": stable_track_id(rel),
                    "path": rel,
                    "title": meta["title"],
                    "album": meta["album"],
                    "artist": meta["artist"],
                    "artists": meta["artists"],
                    "duration": meta["duration"],
                    "lyrics": meta["lyrics"],
                    "album_art": meta["album_art"],
                    "folder": "" if str(Path(rel).parent) == "." else str(Path(rel).parent),
                    "filename": path.name,
                    "stream_url": "/api/stream/" + rel,
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
        playlists.append({"name": name, "tracks": resolved_ids, "count": len(resolved_ids), "stored_count": len(raw_ids), "unresolved_count": unresolved_count})

    artist_map: dict[str, list[str]] = {}
    folder_map: dict[str, list[str]] = {}
    album_map: dict[str, list[str]] = {}
    for track in tracks:
        artists = track.get("artists") or ["Unknown Artist"]
        for artist in artists:
            artist_map.setdefault(artist.strip(), []).append(track["id"])
        folder_name = track["folder"] or "Root"
        folder_map.setdefault(folder_name, []).append(track["id"])
        album_name = track.get("album") or "Unknown"
        album_map.setdefault(album_name, []).append(track["id"])

    artists = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(artist_map.items())]
    folders = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(folder_map.items())]
    albums = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(album_map.items())]

    return {
        "tracks": tracks,
        "playlists": playlists,
        "artists": artists,
        "folders": folders,
        "albums": albums,
        "name": APP_NAME,
        "version": APP_VERSION,
        "playlist_note": (
            "Some older playlists may have unresolved legacy entries from a previous broken ID format." if legacy_unresolved else ""
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

    def do_POST(self) -> None:
        self._handle_post()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

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
        if path.startswith("/api/stream/"):
            rel = unquote(path[len("/api/stream/"):])
            target = resolve_target(rel)
            if target is None:
                return self._json({"error": "not found"}, 404, include_body=not head_only)
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return
        return self._json({"error": "not found"}, 404, include_body=not head_only)

    def _handle_post(self) -> None:
        path = urlparse(self.path).path
        payload = self._read_json()
        playlists = read_playlists()

        if path == "/api/playlists":
            name = normalize_spaces(payload.get("name", ""))
            ids = [str(x) for x in (payload.get("track_ids") or [])]
            if not name or not ids:
                return self._json({"error": "invalid request"}, 400)
            playlists.setdefault(name, [])
            for track_id in ids:
                if track_id not in playlists[name]:
                    playlists[name].append(track_id)
            write_playlists(playlists)
            return self._json({"ok": True})

        if path == "/api/playlists/rename":
            old_name = normalize_spaces(payload.get("old_name", ""))
            new_name = normalize_spaces(payload.get("new_name", ""))
            if not old_name or not new_name or old_name not in playlists:
                return self._json({"error": "invalid request"}, 400)
            playlists[new_name] = playlists.pop(old_name)
            write_playlists(playlists)
            return self._json({"ok": True})

        if path == "/api/playlists/delete":
            name = normalize_spaces(payload.get("name", ""))
            if not name or name not in playlists:
                return self._json({"error": "invalid request"}, 400)
            playlists.pop(name, None)
            write_playlists(playlists)
            return self._json({"ok": True})

        if path == "/api/folders/create":
            name = normalize_spaces(payload.get("name", "")).strip("/\\")
            if not name:
                return self._json({"error": "invalid request"}, 400)
            folder = (MUSIC_ROOT / name).resolve()
            folder.mkdir(parents=True, exist_ok=True)
            return self._json({"ok": True})

        if path == "/api/folders/add":
            name = normalize_spaces(payload.get("name", ""))
            ids = [str(x) for x in (payload.get("track_ids") or [])]
            if not name or not ids:
                return self._json({"error": "invalid request"}, 400)
            moved = move_tracks_to_folder(ids, name)
            return self._json({"ok": True, "moved": moved})

        return self._json({"error": "not found"}, 404)


def main() -> None:
    port = int(os.getenv("PORT", "8140"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"{APP_NAME} {APP_VERSION} listening on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
