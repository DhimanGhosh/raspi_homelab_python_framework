from __future__ import annotations

import threading

import nltk

from app.config import NLTK_DATA_DIR

# Set once the corpora are available; lookup routes check this before querying WordNet
nltk_ready = threading.Event()


def _ensure_nltk() -> None:
    """Download WordNet corpora in the background so server startup is non-blocking.

    Data lands on the NAS-backed volume, so subsequent container restarts skip
    the download entirely.
    """
    try:
        NLTK_DATA_DIR.mkdir(parents=True, exist_ok=True)
        data_path = str(NLTK_DATA_DIR)
        if data_path not in nltk.data.path:
            nltk.data.path.insert(0, data_path)
        for pkg in ["wordnet", "omw-1.4"]:
            try:
                nltk.data.find(f"corpora/{pkg}")
                print(f"[dict] {pkg}: already present")
            except LookupError:
                print(f"[dict] Downloading {pkg} …")
                nltk.download(pkg, download_dir=data_path, quiet=False)
                print(f"[dict] {pkg} ready")
        nltk_ready.set()
        print("[dict] NLTK ready")
    except Exception as exc:
        print(f"[dict] NLTK setup error: {exc}")
        nltk_ready.set()  # still set so lookup returns a 503 instead of hanging


def startup_handler() -> None:
    """FastAPI startup event: register NLTK path and kick off background download."""
    data_path = str(NLTK_DATA_DIR)
    if data_path not in nltk.data.path:
        nltk.data.path.insert(0, data_path)
    threading.Thread(target=_ensure_nltk, daemon=True).start()
