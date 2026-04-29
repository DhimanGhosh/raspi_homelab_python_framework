from __future__ import annotations

import base64
import hashlib
import re

from flask import Blueprint, jsonify, render_template, request, send_from_directory

from mutagen.easyid3 import EasyID3
from mutagen import File as MutagenFile
from mutagen.id3 import ID3NoHeaderError

from app.config import APP_NAME, APP_VERSION, ARTIST_IMAGES_DIR, ART_CACHE_DIR, MUSIC_ROOT
from app.library import library_payload, resolve_track
from app.media import (
    ensure_cover_art,
    fetch_remote_bytes,
    rename_track_for_metadata,
    track_metadata,
    write_embedded_art,
)
from app.playlists import artist_image_map, read_playlists, set_artist_image, write_playlists
from app.utils import mime_to_ext, normalize_spaces, safe_component

routes_bp = Blueprint("routes", __name__)


@routes_bp.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME, app_version=APP_VERSION)


@routes_bp.route("/api/library")
def api_library():
    return jsonify(library_payload())


@routes_bp.route("/api/playlists", methods=["POST"])
def api_playlists():
    data   = request.get_json(force=True, silent=True) or {}
    name   = normalize_spaces(data.get("name", ""))
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


@routes_bp.route("/api/playlists/add-tracks", methods=["POST"])
def api_playlist_add_tracks():
    data      = request.get_json(force=True, silent=True) or {}
    name      = normalize_spaces(data.get("name", ""))
    track_ids = [str(x) for x in data.get("track_ids", []) if str(x).strip()]
    force     = bool(data.get("force"))
    if not name or not track_ids:
        return jsonify({"ok": False, "error": "playlist name and track_ids required"}), 400
    payload    = read_playlists()
    payload.setdefault(name, [])
    duplicates = [tid for tid in track_ids if tid in payload[name]]
    if duplicates and not force:
        return jsonify({"ok": False, "duplicates": duplicates, "duplicate_count": len(duplicates), "message": "duplicate tracks found"}), 409
    for track_id in track_ids:
        if force or track_id not in payload[name]:
            payload[name].append(track_id)
    write_playlists(payload)
    return jsonify({"ok": True, "duplicates": duplicates, "added": len(track_ids) - len(duplicates)})


@routes_bp.route("/api/metadata/<path:relpath>", methods=["GET", "POST"])
def api_metadata(relpath: str):
    path = resolve_track(relpath)
    if request.method == "GET":
        meta = track_metadata(path)
        return jsonify({"ok": True, "track_id": relpath, **meta})

    data          = request.get_json(force=True, silent=True) or {}
    title         = normalize_spaces(data.get("title", ""))
    artists       = normalize_spaces(data.get("artist", data.get("artists", "")))
    album         = normalize_spaces(data.get("album", ""))
    year          = normalize_spaces(str(data.get("year", "")))
    art_link      = normalize_spaces(data.get("art_link", ""))
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

        if title:   tags["title"]  = [title]
        if album:   tags["album"]  = [album]
        if artists: tags["artist"] = [artists]
        if year:    tags["date"]   = [re.sub(r"[^0-9]", "", year)[:4]]
        tags.save()

        if art_link:
            image_bytes, content_type = fetch_remote_bytes(art_link)
            write_embedded_art(path, image_bytes, content_type)
        elif art_upload_data:
            if "," in art_upload_data:
                header, encoded = art_upload_data.split(",", 1)
                content_type = header.split(";")[0].split(":")[-1] or "image/jpeg"
            else:
                encoded      = art_upload_data
                content_type = "image/jpeg"
            image_bytes = base64.b64decode(encoded)
            write_embedded_art(path, image_bytes, content_type)

        new_path    = rename_track_for_metadata(path, title, album, artists)
        new_relpath = new_path.relative_to(MUSIC_ROOT).as_posix()
        meta        = track_metadata(new_path)
        return jsonify({"ok": True, "track_id": new_relpath, **meta})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@routes_bp.route("/api/artist-image/<path:artist>", methods=["POST"])
def api_artist_image(artist: str):
    name = normalize_spaces(artist)
    if not name:
        return jsonify({"ok": False, "error": "artist required"}), 400
    data        = request.get_json(force=True, silent=True) or {}
    image_link  = normalize_spaces(data.get("image_link", ""))
    upload_data = data.get("upload_data", "")
    try:
        content_type  = "image/jpeg"
        image_bytes: bytes | None = None
        if image_link:
            image_bytes, content_type = fetch_remote_bytes(image_link)
        elif upload_data:
            if "," in upload_data:
                header, encoded = upload_data.split(",", 1)
                content_type    = header.split(";")[0].split(":")[-1] or "image/jpeg"
            else:
                encoded = upload_data
            image_bytes = base64.b64decode(encoded)
        else:
            return jsonify({"ok": False, "error": "image input required"}), 400
        ext      = mime_to_ext(content_type)
        filename = f"{safe_component(name, 'artist').replace(' ', '_')}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}{ext}"
        target   = ARTIST_IMAGES_DIR / filename
        target.write_bytes(image_bytes)
        set_artist_image(name, filename)
        return jsonify({"ok": True, "image_url": f"/api/artist-images/{filename}"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@routes_bp.route("/api/artist-images/<path:filename>")
def api_artist_images(filename: str):
    return send_from_directory(str(ARTIST_IMAGES_DIR), filename)


@routes_bp.route("/api/art-cache/<path:filename>")
def api_art_cache(filename: str):
    return send_from_directory(str(ART_CACHE_DIR), filename)


@routes_bp.route("/api/stream/<path:relpath>")
def api_stream(relpath: str):
    return send_from_directory(str(MUSIC_ROOT), relpath)
