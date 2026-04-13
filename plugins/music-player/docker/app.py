from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

APP_VERSION = os.getenv("APP_VERSION", "7.1.6")
APP_NAME = os.getenv("APP_NAME", "Music Player")
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"

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
    return re.sub(r"\s+", " ", text).strip()


def stable_track_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


def parse_filename(name: str) -> tuple[str, list[str]]:
    base = Path(name).stem
    base = re.sub(r"[_\.]+", " ", base)
    base = normalize_spaces(base)
    if " - " in base:
        title, artists_raw = base.split(" - ", 1)
        artists: list[str] = []
        for chunk in ARTIST_SPLIT_RE.split(artists_raw):
            item = normalize_spaces(chunk)
            if item and item.lower() not in IGNORE_ARTISTS:
                artists.append(item)
        if artists:
            return title.strip(), artists
    return base, []


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


def scan_tracks() -> list[dict]:
    tracks: list[dict] = []
    if not MUSIC_ROOT.exists():
        return tracks

    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = path.relative_to(MUSIC_ROOT).as_posix()
            title, artists = parse_filename(path.name)
            tracks.append(
                {
                    "id": stable_track_id(rel),
                    "path": rel,
                    "title": title,
                    "artist": ", ".join(artists) if artists else "Unknown Artist",
                    "artists": artists,
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
        playlists.append(
            {
                "name": name,
                "tracks": resolved_ids,
                "count": len(resolved_ids),
                "stored_count": len(raw_ids),
                "unresolved_count": unresolved_count,
            }
        )

    artist_map: dict[str, list[str]] = {}
    folder_map: dict[str, list[str]] = {}
    for track in tracks:
        artists = track.get("artists") or []
        if artists:
            for artist in artists:
                artist_map.setdefault(artist.strip(), []).append(track["id"])
        else:
            artist_map.setdefault("Unknown Artist", []).append(track["id"])

        folder_name = track["folder"] or "Root"
        folder_map.setdefault(folder_name, []).append(track["id"])

    artists = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(artist_map.items())]
    folders = [{"name": k, "tracks": v, "count": len(v)} for k, v in sorted(folder_map.items())]

    return {
        "tracks": tracks,
        "playlists": playlists,
        "artists": artists,
        "folders": folders,
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

            size = target.stat().st_size
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            range_header = self.headers.get("Range")
            start, end = 0, size - 1
            status = 200

            if range_header and range_header.startswith("bytes="):
                spec = range_header.split("=", 1)[1]
                first, _, last = spec.partition("-")
                start = int(first) if first else 0
                end = int(last) if last else size - 1
                status = 206

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            if head_only:
                return

            try:
                with target.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = handle.read(min(262144, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        return self._json({"error": "not found"}, 404, include_body=not head_only)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8"))

        if parsed.path == "/api/playlists":
            name = normalize_spaces(str(payload.get("name", "")))
            track_ids = [str(x) for x in payload.get("track_ids", [])]
            if not name:
                return self._json({"error": "playlist name required"}, 400)
            data = read_playlists()
            existing = data.get(name, [])
            data[name] = list(dict.fromkeys(existing + track_ids))
            write_playlists(data)
            return self._json({"ok": True, "name": name, "count": len(data[name])})

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

        return self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8140"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"{APP_NAME} listening on {port}", flush=True)
    server.serve_forever()
