from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import ARTIST_SPLIT_RE, IGNORE_ARTISTS, SAFE_NAME_RE


# ── Text helpers ───────────────────────────────────────────────────────────────

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
    base  = normalize_spaces(re.sub(r"[_]+", " ", Path(name).stem))
    parts = [normalize_spaces(p) for p in base.split(" - ") if normalize_spaces(p)]
    if len(parts) >= 3:
        return parts[0], parts[1], split_artists(" - ".join(parts[2:]))
    if len(parts) == 2:
        return parts[0], "Unknown", split_artists(parts[1])
    return base, "Unknown", []


# ── JSON helpers ───────────────────────────────────────────────────────────────

def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Tag helpers ────────────────────────────────────────────────────────────────

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
        "image/jpg":  ".jpg",
        "image/png":  ".png",
        "image/webp": ".webp",
    }
    return mapping.get(mime, ".jpg")
