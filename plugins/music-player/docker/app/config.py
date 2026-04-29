from __future__ import annotations

import os
import re
from pathlib import Path

APP_NAME    = os.getenv("APP_NAME",    "Music Player")
APP_VERSION = os.getenv("APP_VERSION", "8.4.33")

MUSIC_ROOT       = Path(os.getenv("MUSIC_ROOT",    "/mnt/nas/media/music")).resolve()
APP_DATA_DIR     = Path(os.getenv("APP_DATA_DIR",  "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE   = APP_DATA_DIR / "playlists.json"
ARTIST_IMAGES_DIR = APP_DATA_DIR / "artist_images"
ART_CACHE_DIR    = APP_DATA_DIR / "art_cache"
ARTIST_IMAGE_INDEX = APP_DATA_DIR / "artist_images.json"

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
IMAGE_EXTENSIONS     = {".jpg", ".jpeg", ".png", ".webp"}

ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|，|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS  = {"chorus", "others", "other", "music"}
SAFE_NAME_RE    = re.compile(r"[^A-Za-z0-9._()\-\[\] ]+")

# ── Directory bootstrap ────────────────────────────────────────────────────────

for _directory in [APP_DATA_DIR, ARTIST_IMAGES_DIR, ART_CACHE_DIR]:
    _directory.mkdir(parents=True, exist_ok=True)

if not PLAYLISTS_FILE.exists():
    PLAYLISTS_FILE.write_text("{}", encoding="utf-8")
if not ARTIST_IMAGE_INDEX.exists():
    ARTIST_IMAGE_INDEX.write_text("{}", encoding="utf-8")
