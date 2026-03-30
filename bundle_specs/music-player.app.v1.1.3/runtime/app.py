from __future__ import annotations

import json
import mimetypes
import os
import re
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

APP_VERSION = "1.1.3"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
UNKNOWN_ARTIST_VALUES = {"", "unknown artist", "unknown"}
UNKNOWN_ALBUM_VALUES = {"", "unknown album", "unknown"}

app = FastAPI(title="Music Player", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def safe_rel_path(path: Path) -> str:
    return path.relative_to(MUSIC_ROOT).as_posix()


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_filename_title_artists(name: str) -> tuple[str, list[str]]:
    base = Path(name).stem
    base = re.sub(r"[_\.]+", " ", base)
    base = normalize_spaces(base)
    if " - " in base:
        title, artists_raw = base.split(" - ", 1)
        artists = [normalize_spaces(x) for x in artists_raw.split(",") if normalize_spaces(x)]
        if title:
            return title, artists
    return base or name, []


def file_id(path: Path) -> str:
    return sha1(safe_rel_path(path).encode("utf-8")).hexdigest()[:16]


def read_playlists() -> dict[str, list[str]]:
    if PLAYLISTS_FILE.exists():
        try:
            data = json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): [str(x) for x in (v or [])] for k, v in data.items()}
        except Exception:
            pass
    return {}


def write_playlists(payload: dict[str, list[str]]) -> None:
    ordered = {k: list(dict.fromkeys(v)) for k, v in sorted(payload.items())}
    PLAYLISTS_FILE.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_tags(path: Path) -> dict[str, Any]:
    parsed_title, parsed_artists = parse_filename_title_artists(path.name)
    result: dict[str, Any] = {
        "title": parsed_title,
        "artist": ", ".join(parsed_artists) if parsed_artists else "Unknown Artist",
        "artists": parsed_artists,
        "album": "Unknown Album",
        "duration": None,
    }
    if MutagenFile is None:
        return result
    try:
        audio = MutagenFile(str(path), easy=True)
        if not audio:
            return result
        title = normalize_spaces((audio.get("title") or [result["title"]])[0])
        artist = normalize_spaces((audio.get("artist") or [result["artist"]])[0])
        album = normalize_spaces((audio.get("album") or [result["album"]])[0])
        if title:
            result["title"] = title
        if artist and artist.lower() not in UNKNOWN_ARTIST_VALUES:
            result["artist"] = artist
            result["artists"] = [normalize_spaces(x) for x in artist.split(",") if normalize_spaces(x)]
        if album and album.lower() not in UNKNOWN_ALBUM_VALUES:
            result["album"] = album
        if getattr(audio, "info", None) and getattr(audio.info, "length", None):
            result["duration"] = round(float(audio.info.length), 2)
    except Exception:
        return result
    return result


def track_from_file(path: Path) -> dict[str, Any]:
    tags = extract_tags(path)
    rel = safe_rel_path(path)
    folder = Path(rel).parent.as_posix()
    if folder == ".":
        folder = ""
    mime = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    primary_artist = tags["artists"][0] if tags["artists"] else "Unknown Artist"
    return {
        "id": file_id(path),
        "path": rel,
        "title": tags["title"],
        "artist": tags["artist"],
        "artists": tags["artists"],
        "primary_artist": primary_artist,
        "album": tags["album"],
        "duration": tags["duration"],
        "folder": folder,
        "filename": path.name,
        "ext": path.suffix.lower().lstrip("."),
        "size": path.stat().st_size,
        "mime": mime,
        "stream_url": f"/api/stream/{quote(rel, safe='')}"
    }


def scan_tracks() -> list[dict[str, Any]]:
    if not MUSIC_ROOT.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            items.append(track_from_file(path))
    return items


def build_tree(paths: list[str]) -> list[dict[str, Any]]:
    tree: dict[str, Any] = {}
    for rel in paths:
        parts = [p for p in Path(rel).parent.as_posix().split("/") if p and p != "."]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})

    def convert(node: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
        out = []
        for name in sorted(node):
            current = f"{prefix}/{name}" if prefix else name
            out.append({"name": name, "path": current, "children": convert(node[name], current)})
        return out

    return convert(tree)


def resolve_music_path(rel_path: str) -> Path:
    target = (MUSIC_ROOT / rel_path).resolve()
    if MUSIC_ROOT not in target.parents and target != MUSIC_ROOT:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Track not found")
    return target


def playlist_objects(playlists: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [{"name": name, "tracks": items, "count": len(items)} for name, items in sorted(playlists.items())]


def auto_artist_playlists(tracks: list[dict[str, Any]]) -> dict[str, list[str]]:
    generated: dict[str, list[str]] = {}
    for track in tracks:
        for artist in track.get("artists") or []:
            if artist and artist.lower() not in UNKNOWN_ARTIST_VALUES:
                generated.setdefault(artist, []).append(track["id"])
    return {name: list(dict.fromkeys(ids)) for name, ids in generated.items()}


class PlaylistPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tracks: list[str] = Field(default_factory=list)


class PlaylistAppendPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tracks: list[str] = Field(default_factory=list)


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "Music Player",
        "version": APP_VERSION,
        "music_root": str(MUSIC_ROOT),
        "music_root_exists": MUSIC_ROOT.exists(),
    }


@app.get("/api/library")
def library() -> JSONResponse:
    tracks = scan_tracks()
    playlists = read_playlists()
    folders = build_tree([t["path"] for t in tracks])
    return JSONResponse({
        "tracks": tracks,
        "folders": folders,
        "playlists": playlist_objects(playlists),
        "stats": {
            "track_count": len(tracks),
            "folder_count": len({t["folder"] for t in tracks if t["folder"]}),
            "playlist_count": len(playlists),
        },
    })


@app.get("/api/playlists")
def get_playlists() -> JSONResponse:
    playlists = read_playlists()
    return JSONResponse({"playlists": playlist_objects(playlists)})


@app.post("/api/playlists")
def upsert_playlist(payload: PlaylistPayload) -> JSONResponse:
    playlists = read_playlists()
    playlists[payload.name.strip()] = list(dict.fromkeys(payload.tracks))
    write_playlists(playlists)
    return JSONResponse({"ok": True, "message": "Playlist saved", "playlists": playlist_objects(playlists)})


@app.post("/api/playlists/append")
def append_to_playlist(payload: PlaylistAppendPayload) -> JSONResponse:
    playlists = read_playlists()
    existing = playlists.get(payload.name.strip(), [])
    playlists[payload.name.strip()] = list(dict.fromkeys(existing + payload.tracks))
    write_playlists(playlists)
    return JSONResponse({"ok": True, "message": "Tracks added to playlist", "playlists": playlist_objects(playlists)})


@app.post("/api/playlists/auto-artists")
def create_artist_playlists() -> JSONResponse:
    playlists = read_playlists()
    generated = auto_artist_playlists(scan_tracks())
    for name, track_ids in generated.items():
        playlists[name] = list(dict.fromkeys(playlists.get(name, []) + track_ids))
    write_playlists(playlists)
    return JSONResponse({
        "ok": True,
        "message": f"Created or updated {len(generated)} artist playlists",
        "playlists": playlist_objects(playlists),
    })


@app.delete("/api/playlists/{name}")
def delete_playlist(name: str) -> JSONResponse:
    playlists = read_playlists()
    removed = playlists.pop(name, None)
    write_playlists(playlists)
    return JSONResponse({"ok": True, "removed": removed is not None, "playlists": playlist_objects(playlists)})


@app.get("/api/stream/{track_path:path}")
def stream_track(track_path: str):
    path = resolve_music_path(track_path)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)
