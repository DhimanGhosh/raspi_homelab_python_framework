from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
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

APP_VERSION = "4.0.1"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
UNKNOWN_ARTIST_VALUES = {"", "unknown artist", "unknown"}
UNKNOWN_ALBUM_VALUES = {"", "unknown album", "unknown"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|/|&| feat\.? | ft\.? | featuring )\s*", re.I)

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
        artists = [normalize_spaces(x) for x in ARTIST_SPLIT_RE.split(artists_raw) if normalize_spaces(x)]
        if title:
            return title, artists
    return base or name, []


def split_artists(raw: str | None) -> list[str]:
    if not raw:
        return []
    items = [normalize_spaces(x) for x in ARTIST_SPLIT_RE.split(raw) if normalize_spaces(x)]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key in UNKNOWN_ARTIST_VALUES or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


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


def ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=6,
        ).strip()
        return round(float(out), 2) if out else None
    except Exception:
        return None


def extract_tags(path: Path) -> dict[str, Any]:
    parsed_title, parsed_artists = parse_filename_title_artists(path.name)
    result: dict[str, Any] = {
        "title": parsed_title,
        "artist": ", ".join(parsed_artists) if parsed_artists else "Unknown Artist",
        "artists": parsed_artists,
        "album": "Unknown Album",
        "duration": None,
    }
    if MutagenFile is not None:
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio:
                title = normalize_spaces((audio.get("title") or [result["title"]])[0])
                artist = normalize_spaces((audio.get("artist") or [result["artist"]])[0])
                album = normalize_spaces((audio.get("album") or [result["album"]])[0])
                if title:
                    result["title"] = title
                if artist and artist.lower() not in UNKNOWN_ARTIST_VALUES:
                    result["artist"] = artist
                    result["artists"] = split_artists(artist) or parsed_artists
                if album and album.lower() not in UNKNOWN_ALBUM_VALUES:
                    result["album"] = album
                if getattr(audio, "info", None) and getattr(audio.info, "length", None):
                    result["duration"] = round(float(audio.info.length), 2)
        except Exception:
            pass
    if not result["artists"]:
        result["artists"] = parsed_artists
        if parsed_artists:
            result["artist"] = ", ".join(parsed_artists)
    if result["duration"] is None:
        result["duration"] = ffprobe_duration(path)
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
        track_artists = track.get("artists") or split_artists(track.get("artist"))
        if not track_artists:
            _, parsed = parse_filename_title_artists(track.get("filename") or track.get("title") or "")
            track_artists = parsed
        for artist in track_artists:
            key = normalize_spaces(artist)
            if key and key.lower() not in UNKNOWN_ARTIST_VALUES:
                generated.setdefault(key, []).append(track["id"])
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
    artists = auto_artist_playlists(tracks)
    return JSONResponse({
        "tracks": tracks,
        "folders": folders,
        "playlists": playlist_objects(playlists),
        "artist_playlists": [{"name": k, "count": len(v), "tracks": v} for k, v in sorted(artists.items())],
        "stats": {
            "track_count": len(tracks),
            "folder_count": len({t["folder"] for t in tracks if t["folder"]}),
            "playlist_count": len(playlists),
            "artist_playlist_count": len(artists),
        },
    })


@app.get("/api/playlists")
def get_playlists() -> JSONResponse:
    playlists = read_playlists()
    return JSONResponse({"playlists": playlist_objects(playlists)})


@app.post("/api/playlists")
def create_playlist(payload: PlaylistPayload) -> JSONResponse:
    playlists = read_playlists()
    playlists[payload.name] = list(dict.fromkeys(payload.tracks))
    write_playlists(playlists)
    return JSONResponse({"ok": True, "message": f"Playlist '{payload.name}' saved.", "playlists": playlist_objects(playlists)})


@app.post("/api/playlists/append")
def append_playlist(payload: PlaylistAppendPayload) -> JSONResponse:
    playlists = read_playlists()
    playlists.setdefault(payload.name, [])
    playlists[payload.name].extend(payload.tracks)
    playlists[payload.name] = list(dict.fromkeys(playlists[payload.name]))
    write_playlists(playlists)
    return JSONResponse({"ok": True, "message": f"Added {len(payload.tracks)} track(s) to '{payload.name}'.", "playlists": playlist_objects(playlists)})


@app.post("/api/playlists/generate-artists")
def generate_artist_playlists() -> JSONResponse:
    tracks = scan_tracks()
    generated = auto_artist_playlists(tracks)
    playlists = read_playlists()
    count_new = 0
    for name, ids in generated.items():
        if playlists.get(name) != ids:
            if name not in playlists:
                count_new += 1
            playlists[name] = ids
    write_playlists(playlists)
    return JSONResponse({
        "ok": True,
        "message": f"Generated {len(generated)} artist playlists.",
        "new_count": count_new,
        "playlists": playlist_objects(playlists),
    })


@app.get("/api/stream/{rel_path:path}")
def stream(rel_path: str) -> FileResponse:
    target = resolve_music_path(rel_path)
    return FileResponse(str(target), media_type=mimetypes.guess_type(target.name)[0] or "audio/mpeg", filename=target.name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8139)
