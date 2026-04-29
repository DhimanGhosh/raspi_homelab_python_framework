from __future__ import annotations

import os
from pathlib import Path

APP_NAME    = os.getenv("APP_NAME",    "Offline Dictionary")
APP_VERSION = os.getenv("APP_VERSION", "1.4.5")
PORT        = int(os.getenv("PORT",    "8133"))

# NAS-persisted NLTK data — downloaded once on first boot, reused on restarts
NLTK_DATA_DIR = Path("/opt/offline-dictionary/data/nltk_data")
