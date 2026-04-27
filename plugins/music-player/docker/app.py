from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from flask import Flask, jsonify, render_template, send_file, send_from_directory, request
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
APP_NAME = os.getenv("APP_NAME", "Music Player")
APP_VERSION = os.getenv("APP_VERSION", "8.4.33")
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
ARTIST_IMAGES_DIR = APP_DATA_DIR / "artist_images"
ART_CACHE_DIR = APP_DATA_DIR / "art_cache"
ARTIST_IMAGE_INDEX = APP_DATA_DIR / "artist_images.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|，|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS = {"chorus", "others", "other", "music"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._()\-\[\] ]+")

for directory in [APP_DATA_DIR, ARTIST_IMAGES_DIR, ART_CACHE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
if not PLAYLISTS_FILE.exists():
    PLAYLISTS_FILE.write_text("{}", encoding="utf-8")
if not ARTIST_IMAGE_INDEX.exists():
    ARTIST_IMAGE_INDEX.write_text("{}", encoding="utf-8")

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("，", ",")).strip()


def safe_component(text: str, fallback: str = "Unknown") -> str:
    clean = SAFE_NAME_RE.sub("", normalize_spaces(text)).strip(" .")
    return clean or fallback


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


def parse_filename(name: str) -> tuple[str, str, list[str]]:
    base = normalize_spaces(re.sub(r"[_]+", " ", Path(name).stem))
    parts = [normalize_spaces(p) for p in base.split(" - ") if normalize_spaces(p)]
    if len(parts) >= 3:
        return parts[0], parts[1], split_artists(" - ".join(parts[2:]))
    if len(parts) == 2:
        return parts[0], "Unknown", split_artists(parts[1])
    return base, "Unknown", []


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_playlists() -> dict[str, list[str]]:
    payload = read_json(PLAYLISTS_FILE, {})
    if isinstance(payload, dict):
        return {str(k): [str(x) for x in (v or [])] for k, v in payload.items()}
    return {}


def write_playlists(data: dict[str, list[str]]) -> None:
    write_json(PLAYLISTS_FILE, data)


def artist_image_map() -> dict[str, str]:
    payload = read_json(ARTIST_IMAGE_INDEX, {})
    if isinstance(payload, dict):
        return {str(k): str(v) for k, v in payload.items() if v}
    return {}


def set_artist_image(artist: str, filename: str) -> None:
    payload = artist_image_map()
    payload[artist] = filename
    write_json(ARTIST_IMAGE_INDEX, payload)


def first_value(tags: Any, keys: list[str]) -> str:
    for key in keys:
        if not tags or key not in tags:
            continue
        value = tags.get(key)
        if isinstance(value, list):
            return str(value[0]) if value else ""
        text = getattr(value, "text", None)
        if isinstance(text, list) and text:
            return str(text[0])
        if text:
            return str(text)
        if value:
            return str(value)
    return ""


def mime_to_ext(mime: str | None) -> str:
    mime = (mime or "").lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    return mapping.get(mime, ".jpg")


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
                ext = mime_to_ext(apic_frames[0].mime)
        elif hasattr(audio, "pictures") and getattr(audio, "pictures", None):
            picture = audio.pictures[0]
            data = picture.data
            ext = mime_to_ext(getattr(picture, "mime", None))
        elif isinstance(audio, MP4):
            covr = audio.tags.get("covr", []) if audio.tags else []
            if covr:
                cover = covr[0]
                data = bytes(cover)
                fmt = getattr(cover, "imageformat", None)
                ext = ".png" if fmt == MP4Cover.FORMAT_PNG else ".jpg"
        if data:
            target = ART_CACHE_DIR / f"{key}{ext}"
            target.write_bytes(data)
            return f"/api/art-cache/{target.name}"
    except Exception:
        return None
    return None


def track_metadata(path: Path) -> dict[str, Any]:
    file_title, file_album, file_artists = parse_filename(path.name)
    title, album, artists, year, duration = file_title, file_album or "Unknown", file_artists[:], "", 0
    art_url = None
    try:
        audio = MutagenFile(path)
        if audio is not None:
            duration = int(getattr(getattr(audio, "info", None), "length", 0) or 0)
            tags = getattr(audio, "tags", None)
            tag_title = normalize_spaces(first_value(tags, ["TIT2", "title", "TITLE", "©nam"]))
            tag_album = normalize_spaces(first_value(tags, ["TALB", "album", "ALBUM", "©alb"]))
            tag_artist = normalize_spaces(first_value(tags, ["TPE1", "artist", "ARTIST", "©ART", "aART"]))
            tag_year = normalize_spaces(first_value(tags, ["TDRC", "date", "DATE", "year", "YEAR", "©day"]))
            if tag_title:
                title = tag_title
            if tag_album:
                album = tag_album
            if tag_artist:
                artists = split_artists(tag_artist) or artists
            if tag_year:
                year = re.sub(r"[^0-9]", "", tag_year)[:4]
            art_url = ensure_cover_art(path)
    except Exception:
        pass
    if not artists:
        artists = ["Unknown Artist"]
    return {
        "title": title,
        "album": album or "Unknown",
        "artists": artists,
        "artist": ", ".join(artists),
        "year": year,
        "duration": duration,
        "art_url": art_url,
    }


def scan_tracks() -> list[dict[str, Any]]:
    tracks = []
    if not MUSIC_ROOT.exists():
        return tracks
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = path.relative_to(MUSIC_ROOT).as_posix()
            meta = track_metadata(path)
            tracks.append({
                "id": rel,
                "path": rel,
                "title": meta["title"],
                "album": meta["album"],
                "artist": meta["artist"],
                "artists": meta["artists"],
                "year": meta["year"],
                "duration": meta["duration"],
                "folder": "" if str(Path(rel).parent) == "." else str(Path(rel).parent),
                "filename": path.name,
                "stream_url": "/api/stream/" + rel,
                "art_url": meta["art_url"],
            })
    return tracks


def library_payload() -> dict[str, Any]:
    tracks = scan_tracks()
    track_map = {track["id"]: track for track in tracks}
    artist_map_raw: dict[str, list[str]] = {}
    album_map_raw: dict[str, list[str]] = {}
    folder_map_raw: dict[str, list[str]] = {}
    for track in tracks:
        for artist in track.get("artists") or [track.get("artist") or "Unknown Artist"]:
            artist_map_raw.setdefault(artist.strip(), []).append(track["id"])
        album_map_raw.setdefault(track.get("album") or "Unknown", []).append(track["id"])
        folder_map_raw.setdefault(track.get("folder") or "Root", []).append(track["id"])
    artist_images = artist_image_map()
    artists = []
    for name, ids in sorted(artist_map_raw.items(), key=lambda x: x[0].lower()):
        image_url = None
        stored = artist_images.get(name)
        if stored and (ARTIST_IMAGES_DIR / stored).exists():
            image_url = f"/api/artist-images/{stored}"
        elif ids:
            image_url = track_map[ids[0]].get("art_url")
        artists.append({"name": name, "tracks": ids, "count": len(ids), "image_url": image_url})
    albums = []
    for name, ids in sorted(album_map_raw.items(), key=lambda x: x[0].lower()):
        art_url = track_map[ids[0]].get("art_url") if ids else None
        artists_for_album: list[str] = []
        for track_id in ids[:8]:
            for artist in track_map[track_id].get("artists", []):
                if artist not in artists_for_album:
                    artists_for_album.append(artist)
        albums.append({"name": name, "tracks": ids, "count": len(ids), "art_url": art_url, "artist": ", ".join(artists_for_album[:3])})
    folders = []
    for name, ids in sorted(folder_map_raw.items(), key=lambda x: x[0].lower()):
        art_url = track_map[ids[0]].get("art_url") if ids else None
        folders.append({"name": name, "tracks": ids, "count": len(ids), "art_url": art_url})
    playlists_raw = read_playlists()
    playlists = []
    for name, ids in sorted(playlists_raw.items()):
        valid_ids = [tid for tid in ids if tid in track_map]
        art_url = track_map[valid_ids[0]].get("art_url") if valid_ids else None
        playlists.append({"name": name, "tracks": valid_ids, "count": len(valid_ids), "art_url": art_url})
    return {"app": {"name": APP_NAME, "version": APP_VERSION}, "tracks": tracks, "artists": artists, "albums": albums, "folders": folders, "playlists": playlists}


def resolve_track(relpath: str) -> Path:
    target = (MUSIC_ROOT / relpath).resolve()
    if MUSIC_ROOT not in target.parents and target != MUSIC_ROOT:
        raise ValueError("invalid path")
    return target


def fetch_remote_bytes(url: str) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip().lower()
    return data, content_type


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
        picture = Picture()
        picture.type = 3
        picture.mime = content_type
        picture.data = image_bytes
        audio.add_picture(picture)
        audio.save()
        ensure_cover_art(path)
        return
    if suffix in {".m4a", ".mp4", ".aac"}:
        audio = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if content_type == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
        audio.save()
        ensure_cover_art(path)
        return
    raise ValueError(f"embedded art not supported for {suffix}")


def rename_track_for_metadata(path: Path, title: str, album: str, artist: str) -> Path:
    suffix = path.suffix
    current_title, current_album, current_artists = parse_filename(path.name)
    effective_title = title or current_title or path.stem
    effective_album = album or current_album
    effective_artist = artist or ", ".join(current_artists) or "Unknown Artist"
    new_name = None
    parts = [safe_component(effective_title)]
    if effective_album and effective_album.lower() != "unknown":
        parts.append(safe_component(effective_album))
    if effective_artist:
        parts.append(safe_component(effective_artist.replace(", ", ",")))
    candidate = " - ".join(parts) + suffix
    if candidate != path.name:
        dest = path.with_name(candidate)
        counter = 1
        while dest.exists() and dest != path:
            dest = path.with_name(f"{' - '.join(parts)} ({counter}){suffix}")
            counter += 1
        shutil.move(str(path), str(dest))
        return dest
    return path


@app.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME, app_version=APP_VERSION)


@app.route("/api/library")
def api_library():
    return jsonify(library_payload())


@app.route("/api/playlists", methods=["POST"])
def api_playlists():
    data = request.get_json(force=True, silent=True) or {}
    name = normalize_spaces(data.get("name", ""))
    tracks = [str(x) for x in data.get("tracks", [])]
    if not name:
        return jsonify({"ok": False, "error": "playlist name required"}), 400
    payload = read_playlists()
    payload.setdefault(name, [])
    for track_id in tracks:
        if track_id not in payload[name]:
            payload[name].append(track_id)
    write_playlists(payload)
    return jsonify({"ok": True, "created": name})


@app.route("/api/playlists/add-tracks", methods=["POST"])
def api_playlist_add_tracks():
    data = request.get_json(force=True, silent=True) or {}
    name = normalize_spaces(data.get("name", ""))
    track_ids = [str(x) for x in data.get("track_ids", []) if str(x).strip()]
    force = bool(data.get("force"))
    if not name or not track_ids:
        return jsonify({"ok": False, "error": "playlist name and track_ids required"}), 400
    payload = read_playlists()
    payload.setdefault(name, [])
    duplicates = [track_id for track_id in track_ids if track_id in payload[name]]
    if duplicates and not force:
        return jsonify({"ok": False, "duplicates": duplicates, "duplicate_count": len(duplicates), "message": "duplicate tracks found"}), 409
    for track_id in track_ids:
        if force or track_id not in payload[name]:
            payload[name].append(track_id)
    write_playlists(payload)
    return jsonify({"ok": True, "duplicates": duplicates, "added": len(track_ids) - len(duplicates)})


@app.route("/api/metadata/<path:relpath>", methods=["GET", "POST"])
def api_metadata(relpath: str):
    path = resolve_track(relpath)
    if request.method == "GET":
        meta = track_metadata(path)
        return jsonify({"ok": True, "track_id": relpath, **meta})
    data = request.get_json(force=True, silent=True) or {}
    title = normalize_spaces(data.get("title", ""))
    artists = normalize_spaces(data.get("artist", data.get("artists", "")))
    album = normalize_spaces(data.get("album", ""))
    year = normalize_spaces(str(data.get("year", "")))
    art_link = normalize_spaces(data.get("art_link", ""))
    art_upload_data = data.get("art_upload_data", "")
    try:
        try:
            tags = EasyID3(path)
        except ID3NoHeaderError:
            audio = MutagenFile(path)
            if audio is None:
                raise ValueError("unsupported file")
            if getattr(audio, "tags", None) is None:
                audio.add_tags()
                audio.save()
            tags = EasyID3(path)
        if title:
            tags["title"] = [title]
        if album:
            tags["album"] = [album]
        if artists:
            tags["artist"] = [artists]
        if year:
            tags["date"] = [re.sub(r"[^0-9]", "", year)[:4]]
        tags.save()

        if art_link:
            image_bytes, content_type = fetch_remote_bytes(art_link)
            write_embedded_art(path, image_bytes, content_type)
        elif art_upload_data:
            if "," in art_upload_data:
                header, encoded = art_upload_data.split(",", 1)
                content_type = header.split(";")[0].split(":")[-1] or "image/jpeg"
            else:
                encoded = art_upload_data
                content_type = "image/jpeg"
            image_bytes = base64.b64decode(encoded)
            write_embedded_art(path, image_bytes, content_type)

        new_path = rename_track_for_metadata(path, title, album, artists)
        new_relpath = new_path.relative_to(MUSIC_ROOT).as_posix()
        meta = track_metadata(new_path)
        return jsonify({"ok": True, "track_id": new_relpath, **meta})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/artist-image/<path:artist>", methods=["POST"])
def api_artist_image(artist: str):
    name = normalize_spaces(artist)
    if not name:
        return jsonify({"ok": False, "error": "artist required"}), 400
    data = request.get_json(force=True, silent=True) or {}
    image_link = normalize_spaces(data.get("image_link", ""))
    upload_data = data.get("upload_data", "")
    try:
        content_type = "image/jpeg"
        image_bytes: bytes | None = None
        if image_link:
            image_bytes, content_type = fetch_remote_bytes(image_link)
        elif upload_data:
            if "," in upload_data:
                header, encoded = upload_data.split(",", 1)
                content_type = header.split(";")[0].split(":")[-1] or "image/jpeg"
            else:
                encoded = upload_data
            image_bytes = base64.b64decode(encoded)
        else:
            return jsonify({"ok": False, "error": "image input required"}), 400
        ext = mime_to_ext(content_type)
        filename = f"{safe_component(name, 'artist').replace(' ', '_')}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}{ext}"
        target = ARTIST_IMAGES_DIR / filename
        target.write_bytes(image_bytes)
        set_artist_image(name, filename)
        return jsonify({"ok": True, "image_url": f"/api/artist-images/{filename}"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/artist-images/<path:filename>")
def api_artist_images(filename: str):
    return send_from_directory(ARTIST_IMAGES_DIR, filename)


@app.route("/api/art-cache/<path:filename>")
def api_art_cache(filename: str):
    return send_from_directory(ART_CACHE_DIR, filename)


@app.route("/api/stream/<path:relpath>")
def api_stream(relpath: str):
    return send_from_directory(MUSIC_ROOT, relpath)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8140)
