from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME    = os.getenv("APP_NAME", "Song Downloader")
APP_VERSION = os.getenv("APP_VERSION", "1.3.2")
PORT        = int(os.getenv("PORT", "8145"))

MUSIC_ROOT    = Path(os.getenv("MUSIC_ROOT",    "/mnt/nas/media/music")).resolve()
APP_DATA_DIR  = Path(os.getenv("APP_DATA_DIR",  "/mnt/nas/homelab/runtime/song-downloader/data")).resolve()
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/mnt/nas/homelab/runtime/song-downloader/downloads")).resolve()

JOBS_FILE            = APP_DATA_DIR / "jobs.json"
DEFAULT_COOKIES_FILE = APP_DATA_DIR / "cookies.txt"

# ── Directory bootstrap ────────────────────────────────────────────────────────

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_ROOT.mkdir(parents=True, exist_ok=True)
