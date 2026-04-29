from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import APP_NAME, APP_VERSION, ARTIST_IMAGES_DIR, MUSIC_ROOT, SUPPORTED_EXTENSIONS
from app.media import track_metadata
from app.playlists import artist_image_map, read_playlists


def resolve_track(relpath: str) -> Path:
    target = (MUSIC_ROOT / relpath).resolve()
    if MUSIC_ROOT not in target.parents and target != MUSIC_ROOT:
        raise ValueError("invalid path")
    return target


def scan_tracks() -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    if not MUSIC_ROOT.exists():
        return tracks
    for path in sorted(MUSIC_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel  = path.relative_to(MUSIC_ROOT).as_posix()
            meta = track_metadata(path)
            tracks.append({
                "id":         rel,
                "path":       rel,
                "title":      meta["title"],
                "album":      meta["album"],
                "artist":     meta["artist"],
                "artists":    meta["artists"],
                "year":       meta["year"],
                "duration":   meta["duration"],
                "folder":     "" if str(Path(rel).parent) == "." else str(Path(rel).parent),
                "filename":   path.name,
                "stream_url": "/api/stream/" + rel,
                "art_url":    meta["art_url"],
            })
    return tracks


def library_payload() -> dict[str, Any]:
    tracks    = scan_tracks()
    track_map = {t["id"]: t for t in tracks}

    artist_map_raw: dict[str, list[str]] = {}
    album_map_raw:  dict[str, list[str]] = {}
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
        stored    = artist_images.get(name)
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
        albums.append({
            "name":   name,
            "tracks": ids,
            "count":  len(ids),
            "art_url": art_url,
            "artist": ", ".join(artists_for_album[:3]),
        })

    folders = []
    for name, ids in sorted(folder_map_raw.items(), key=lambda x: x[0].lower()):
        art_url = track_map[ids[0]].get("art_url") if ids else None
        folders.append({"name": name, "tracks": ids, "count": len(ids), "art_url": art_url})

    playlists_raw = read_playlists()
    playlists     = []
    for name, ids in sorted(playlists_raw.items()):
        valid_ids = [tid for tid in ids if tid in track_map]
        art_url   = track_map[valid_ids[0]].get("art_url") if valid_ids else None
        playlists.append({"name": name, "tracks": valid_ids, "count": len(valid_ids), "art_url": art_url})

    return {
        "app":       {"name": APP_NAME, "version": APP_VERSION},
        "tracks":    tracks,
        "artists":   artists,
        "albums":    albums,
        "folders":   folders,
        "playlists": playlists,
    }
