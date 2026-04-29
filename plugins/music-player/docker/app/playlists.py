from __future__ import annotations

from app.config import ARTIST_IMAGE_INDEX, ARTIST_IMAGES_DIR, PLAYLISTS_FILE
from app.utils import read_json, write_json


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
