from __future__ import annotations

import os

APP_NAME    = os.getenv("APP_NAME",    "API Gateway")
APP_VERSION = os.getenv("APP_VERSION", "1.4.0")
PORT        = int(os.getenv("PORT",    "8134"))

MUSIC_PLAYER_API = os.getenv("MUSIC_PLAYER_API", "http://127.0.0.1:8140")
FILES_API        = os.getenv("FILES_API",         "http://127.0.0.1:8088")
PIHOLE_API       = os.getenv("PIHOLE_API",        "http://127.0.0.1:8080")
