from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


# ── Config ─────────────────────────────────────────────────────────────────────
APP_TITLE   = os.getenv("PERSONAL_LIBRARY_TITLE", "Personal Library")
APP_NAME    = os.getenv("APP_NAME",    APP_TITLE)
APP_VERSION = os.getenv("APP_VERSION", "1.3.3")
HOST        = os.getenv("PERSONAL_LIBRARY_HOST", "0.0.0.0")
PORT        = int(os.getenv("PERSONAL_LIBRARY_PORT", "8132"))

DB_PATH = Path(os.getenv("PERSONAL_LIBRARY_DB_PATH", "/opt/personal-library/data/library.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

AMAZON_BASE            = os.getenv("PERSONAL_LIBRARY_AMAZON_BASE", "https://www.amazon.in/s?k=")
GOOGLE_BOOKS_URL       = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"

STATUS_OPTIONS = ["Not Bought", "Want to Read", "Reading", "Paused", "Read"]

DEFAULT_SCORE_SETTINGS = {
    "english_weight":              1.8,
    "wow_weight":                  1.4,
    "emotion_weight":              1.6,
    "sadness_weight":              0.7,
    "realism_weight":              1.3,
    "genre_bonus_weight":          1.2,
    "genre_bonus_value":           5.0,
    "genre_bonus_keywords":        "mystery, thriller, detective",
    "score_formula_label":         "english*1.8 + wow*1.4 + emotion*1.6 + sadness_balance*0.7 + realism*1.3 + genre_bonus",
    "recommendation_statuses":     "Want to Read, Paused",
    "recommendation_explain_label": "Eligible statuses for automatic next recommendation",
}

SEARCH_FIELD_MAP = {
    "title": "title", "author": "author", "genre": "genre",
    "subgenre": "subgenres", "subgenres": "subgenres", "notes": "notes",
    "mood": "mood", "language": "language", "complexity": "english_label",
    "languagecomplexity": "english_label", "complexityscore": "english_ease_score",
    "wow": "wow_score", "emotion": "emotional_score", "emotional": "emotional_score",
    "sadness": "sadness_score", "realism": "realism_score", "score": "personalized_score",
    "status": "status", "bookmark": "bookmark_note", "publisher": "publisher",
    "year": "published_year", "pages": "page_count", "buy": "buy_link",
}


# ── Database ───────────────────────────────────────────────────────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, author TEXT DEFAULT '', isbn TEXT DEFAULT '',
    genre TEXT DEFAULT '', subgenres TEXT DEFAULT '', description TEXT DEFAULT '',
    language TEXT DEFAULT '', published_year TEXT DEFAULT '', page_count INTEGER DEFAULT 0,
    publisher TEXT DEFAULT '', info_link TEXT DEFAULT '', cover_url TEXT DEFAULT '',
    buy_link TEXT DEFAULT '', mood TEXT DEFAULT '', english_label TEXT DEFAULT 'Moderate',
    english_ease_score INTEGER DEFAULT 3, india_set TEXT DEFAULT 'Unknown',
    wow_score INTEGER DEFAULT 3, emotional_score INTEGER DEFAULT 3,
    sadness_score INTEGER DEFAULT 2, realism_score INTEGER DEFAULT 3,
    personalized_score REAL DEFAULT 0, rating REAL DEFAULT 0,
    status TEXT DEFAULT 'Want to Read', notes TEXT DEFAULT '', source TEXT DEFAULT '',
    current_page INTEGER DEFAULT 0, bookmark_page INTEGER DEFAULT 0,
    bookmark_note TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_books_title_author ON books(title, author);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

MIGRATIONS = {
    "current_page":  "ALTER TABLE books ADD COLUMN current_page INTEGER DEFAULT 0",
    "bookmark_page": "ALTER TABLE books ADD COLUMN bookmark_page INTEGER DEFAULT 0",
    "bookmark_note": "ALTER TABLE books ADD COLUMN bookmark_note TEXT DEFAULT ''",
    "buy_link":      "ALTER TABLE books ADD COLUMN buy_link TEXT DEFAULT ''",
    "description":   "ALTER TABLE books ADD COLUMN description TEXT DEFAULT ''",
}


@contextmanager
def connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _get_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()}


def ensure_default_settings(conn):
    for key, value in DEFAULT_SCORE_SETTINGS.items():
        stored = json.dumps(value) if isinstance(value, (dict, list, int, float, bool)) else str(value)
        conn.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)", (key, stored))


def init_db():
    with connect() as conn:
        conn.executescript(CREATE_SQL)
        existing = _get_columns(conn)
        for col, sql in MIGRATIONS.items():
            if col not in existing:
                conn.execute(sql)
        ensure_default_settings(conn)


def get_settings(conn=None):
    owns = conn is None
    if owns:
        ctx = connect(); conn = ctx.__enter__()
    try:
        ensure_default_settings(conn)
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        data = dict(DEFAULT_SCORE_SETTINGS)
        for row in rows:
            try:   data[row["key"]] = json.loads(row["value"])
            except Exception: data[row["key"]] = row["value"]
        return data
    finally:
        if owns: ctx.__exit__(None, None, None)


def update_settings(payload, conn=None):
    owns = conn is None
    if owns:
        ctx = connect(); conn = ctx.__enter__()
    try:
        ensure_default_settings(conn)
        for key, value in payload.items():
            stored = json.dumps(value) if isinstance(value, (dict, list, int, float, bool)) else str(value)
            conn.execute("INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, stored))
        return get_settings(conn)
    finally:
        if owns: ctx.__exit__(None, None, None)


def backup_db(reason="manual"):
    if not DB_PATH.exists():
        return None
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"library_{reason}_{ts}.db"
    shutil.copy2(DB_PATH, target)
    return str(target)


def list_backups():
    backup_dir = DB_PATH.parent / "backups"
    if not backup_dir.exists():
        return []
    items = []
    for path in sorted(backup_dir.glob("library_*.db"), reverse=True):
        stat = path.stat()
        items.append({"name": path.name, "path": str(path), "size": stat.st_size,
                      "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return items


def restore_backup(name: str):
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / name
    if not target.exists():
        raise FileNotFoundError(name)
    pre = backup_db("pre_restore")
    shutil.copy2(target, DB_PATH)
    return {"restored_from": str(target), "pre_restore_backup": pre}


def delete_backup(name: str):
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / name
    if not target.exists():
        raise FileNotFoundError(name)
    target.unlink()
    return {"deleted": True, "name": name}


# ── Metadata ───────────────────────────────────────────────────────────────────
BENGALI_RANGE = re.compile(r"[ঀ-৿]")

INDIAN_PUBLISHERS = {
    "rupa", "rupa publications", "harpercollins publishers india", "westland",
    "penguin india", "penguin random house india", "ananda publishers",
    "anand publishers", "dey's publishing", "sahitya akademi", "rupa & co", "rupa and co",
}


def build_amazon_link(title: str, author: str = "") -> str:
    return f"{AMAZON_BASE}{quote_plus((title + ' ' + author).strip())}"


def force_https(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def curated_cover_url(title: str, author: str = "") -> str:
    t = normalize_text(title)
    a = normalize_text(author)
    if t in {"the sign of four", "the sign of the four"} and "arthur conan doyle" in a:
        return "https://covers.openlibrary.org/b/olid/OL31934902M-M.jpg"
    return ""


def fallback_cover_url(raw: dict[str, Any], title: str, author: str = "") -> str:
    image = force_https((raw.get("imageLinks") or {}).get("thumbnail", ""))
    if image:
        return image
    key = raw.get("infoLink") or ""
    if "openlibrary.org" in key and "/works/" in key:
        work = key.rstrip("/").split("/")[-1]
        if work:
            return f"https://covers.openlibrary.org/w/id/{work}-M.jpg"
    if "openlibrary.org/books/" in key:
        olid = key.rstrip("/").split("/")[-1]
        if olid:
            return f"https://covers.openlibrary.org/b/olid/{olid}-M.jpg"
    curated = curated_cover_url(title, author)
    if curated:
        return curated
    return ""


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\sঀ-৿]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def score_candidate(item: dict[str, Any], target_title: str, target_author: str = "", isbn: str = "") -> float:
    title_score  = similarity(item.get("title", ""), target_title)
    author_score = similarity(" ".join(item.get("authors", []) or []), target_author) if target_author else 0.5
    isbn_score   = 1.0 if isbn and isbn == first_identifier(item.get("industryIdentifiers") or [], "") else 0.0
    meta_bonus   = sum([
        0.10 if item.get("publishedDate") else 0,
        0.10 if item.get("pageCount")     else 0,
        0.10 if item.get("description")   else 0,
        0.05 if item.get("publisher")     else 0,
        0.05 if item.get("categories")    else 0,
    ])
    return (title_score * 0.55) + (author_score * 0.20) + (isbn_score * 0.20) + meta_bonus


def choose_best(candidates: list[dict[str, Any]], title: str, author: str = "", isbn: str = "") -> dict[str, Any] | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: score_candidate(item, title, author, isbn), reverse=True)[0]


def _norm_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_SCORE_SETTINGS)
    if settings:
        merged.update(settings)
    return merged


def calculate_personalized_score(book: dict, settings: dict[str, Any] | None = None) -> float:
    cfg      = _norm_settings(settings)
    english  = int(book.get("english_ease_score", 3) or 3)
    wow      = int(book.get("wow_score",          3) or 3)
    emotional = int(book.get("emotional_score",   3) or 3)
    sadness  = int(book.get("sadness_score",       2) or 2)
    realism  = int(book.get("realism_score",       3) or 3)
    genre    = (book.get("genre") or "").lower()
    keywords = [x.strip().lower() for x in str(cfg.get("genre_bonus_keywords", "")).split(",") if x.strip()]
    raw_bonus = float(cfg.get("genre_bonus_value", 5.0) or 0) if any(
        k in (genre + " " + str(book.get("subgenres", "")).lower()) for k in keywords
    ) else 0.0
    score = (
        english   * float(cfg.get("english_weight",     1.8) or 0)
        + wow     * float(cfg.get("wow_weight",          1.4) or 0)
        + emotional * float(cfg.get("emotion_weight",   1.6) or 0)
        + (6 - abs(3 - sadness)) * float(cfg.get("sadness_weight", 0.7) or 0)
        + realism * float(cfg.get("realism_weight",      1.3) or 0)
        + raw_bonus * float(cfg.get("genre_bonus_weight", 1.2) or 0)
    )
    return round(score, 1)


def score_breakdown(book: dict, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg      = _norm_settings(settings)
    genre    = (book.get("genre") or "").lower()
    keywords = [x.strip().lower() for x in str(cfg.get("genre_bonus_keywords", "")).split(",") if x.strip()]
    raw_bonus = float(cfg.get("genre_bonus_value", 5.0) or 0) if any(
        k in (genre + " " + str(book.get("subgenres", "")).lower()) for k in keywords
    ) else 0.0
    components = {
        "complexity_fit":  round(int(book.get("english_ease_score", 3) or 3) * float(cfg.get("english_weight",     1.8) or 0), 1),
        "wow":             round(int(book.get("wow_score",          3) or 3) * float(cfg.get("wow_weight",          1.4) or 0), 1),
        "emotional":       round(int(book.get("emotional_score",    3) or 3) * float(cfg.get("emotion_weight",     1.6) or 0), 1),
        "sadness_balance": round((6 - abs(3 - int(book.get("sadness_score", 2) or 2))) * float(cfg.get("sadness_weight", 0.7) or 0), 1),
        "realism":         round(int(book.get("realism_score",      3) or 3) * float(cfg.get("realism_weight",      1.3) or 0), 1),
        "genre_bonus":     round(raw_bonus * float(cfg.get("genre_bonus_weight", 1.2) or 0), 1),
    }
    return {
        "formula":    str(cfg.get("score_formula_label") or DEFAULT_SCORE_SETTINGS["score_formula_label"]),
        "components": components,
        "keywords":   keywords,
        "total":      round(sum(components.values()), 1),
    }


def merge_sources(primary: dict[str, Any], fallback: dict[str, Any] | None) -> dict[str, Any]:
    if not fallback:
        return primary
    merged = dict(primary)
    for key in ["description", "categories", "language", "publishedDate", "pageCount",
                "publisher", "industryIdentifiers", "infoLink", "imageLinks", "averageRating"]:
        if not merged.get(key) and fallback.get(key):
            merged[key] = fallback[key]
    if not merged.get("authors") and fallback.get("authors"):
        merged["authors"] = fallback["authors"]
    if not merged.get("title") and fallback.get("title"):
        merged["title"] = fallback["title"]
    sources = [x for x in [primary.get("_source"), fallback.get("_source")] if x]
    merged["_source"] = " + ".join(dict.fromkeys(sources))
    return merged


def enrich_book(title: str, author: str = "", isbn: str = "", settings: dict[str, Any] | None = None) -> dict:
    google  = search_google_books(title, author, isbn)
    openlib = search_open_library(title, author, isbn)
    raw     = merge_sources(google or openlib or default_raw(title, author), openlib)

    final_title  = raw.get("title") or title
    authors      = raw.get("authors") or ([author] if author else [])
    final_author = ", ".join([a for a in authors if a])
    description  = raw.get("description") or ""
    categories   = raw.get("categories") or []
    published    = str(raw.get("publishedDate") or "")
    m            = re.search(r"(19|20)\d{2}", published)
    year         = m.group(0) if m else published[:4]
    cover_url    = force_https((raw.get("imageLinks") or {}).get("thumbnail", "")) or fallback_cover_url(raw, final_title, final_author)
    language     = normalize_language(raw.get("language") or "")

    book = {
        "title": final_title, "author": final_author,
        "isbn": first_identifier(raw.get("industryIdentifiers") or [], isbn),
        "genre": derive_genre(categories, final_title, description, language),
        "subgenres": ", ".join(categories[:5]), "description": description,
        "language": language, "published_year": year,
        "page_count": int(raw.get("pageCount") or 0),
        "publisher": raw.get("publisher") or "",
        "info_link": force_https(raw.get("infoLink") or ""),
        "cover_url": cover_url, "buy_link": build_amazon_link(final_title, final_author),
        "rating": float(raw.get("averageRating") or 0),
        "source": raw.get("_source", "manual"),
    }
    label, ease             = derive_english_label(final_title, description, language)
    book["english_label"]   = label
    book["english_ease_score"] = ease
    book["mood"]            = derive_mood(book["genre"], description, final_title)
    book["india_set"]       = derive_india_set(final_title, final_author, description, categories, book["publisher"], language)
    wow, emotional, sadness, realism = derive_scores(book["genre"], description, final_title, categories, book["india_set"], language)
    book["wow_score"]       = wow
    book["emotional_score"] = emotional
    book["sadness_score"]   = sadness
    book["realism_score"]   = realism
    book["personalized_score"] = calculate_personalized_score(book, settings)
    return book


def default_raw(title: str, author: str = "") -> dict[str, Any]:
    return {"title": title, "authors": [author] if author else [], "description": "",
            "categories": [], "language": "", "publishedDate": "", "pageCount": 0,
            "publisher": "", "industryIdentifiers": [], "infoLink": "", "imageLinks": {},
            "averageRating": 0, "_source": "manual"}


def search_google_books(title: str, author: str = "", isbn: str = ""):
    parts = []
    if isbn:   parts.append(f"isbn:{isbn}")
    if title:  parts.append(f"intitle:{title}")
    if author: parts.append(f"inauthor:{author}")
    q = " ".join(parts).strip()
    if not q:
        return None
    try:
        r = requests.get(GOOGLE_BOOKS_URL, params={"q": q, "maxResults": 8}, timeout=20)
        r.raise_for_status()
        candidates = []
        for item in r.json().get("items", []):
            info = item.get("volumeInfo", {})
            candidates.append({"title": info.get("title", ""), "authors": info.get("authors", []),
                "description": info.get("description", ""), "categories": info.get("categories", []),
                "language": info.get("language", ""), "publishedDate": info.get("publishedDate", ""),
                "pageCount": info.get("pageCount", 0), "publisher": info.get("publisher", ""),
                "industryIdentifiers": info.get("industryIdentifiers", []),
                "infoLink": info.get("infoLink", ""), "imageLinks": info.get("imageLinks", {}),
                "averageRating": info.get("averageRating", 0), "_source": "google_books"})
        return choose_best(candidates, title, author, isbn)
    except Exception:
        return None


def search_open_library(title: str, author: str = "", isbn: str = ""):
    q = " ".join([x for x in [title, author, isbn] if x]).strip()
    if not q:
        return None
    try:
        r = requests.get(OPEN_LIBRARY_SEARCH_URL, params={"q": q, "limit": 8}, timeout=20)
        r.raise_for_status()
        candidates = []
        for d in r.json().get("docs", []):
            candidates.append({"title": d.get("title", ""), "authors": d.get("author_name", []),
                "description": "", "categories": (d.get("subject") or [])[:8],
                "language": ",".join((d.get("language") or [])[:2]),
                "publishedDate": str(d.get("first_publish_year", "")),
                "pageCount": d.get("number_of_pages_median", 0),
                "publisher": ", ".join((d.get("publisher") or [])[:2]),
                "industryIdentifiers": [{"identifier": x} for x in (d.get("isbn") or [])[:3]],
                "infoLink": f"https://openlibrary.org{d.get('key', '')}" if d.get("key") else "",
                "imageLinks": {"thumbnail": f"https://covers.openlibrary.org/b/id/{d.get('cover_i', '')}-M.jpg" if d.get("cover_i") else ""},
                "averageRating": 0, "_source": "open_library"})
        return choose_best(candidates, title, author, isbn)
    except Exception:
        return None


def first_identifier(items, fallback=""):
    for x in items:
        v = x.get("identifier", "")
        if v:
            return v
    return fallback


def normalize_language(raw_language: str) -> str:
    value = (raw_language or "").upper().replace(",", " / ")
    aliases = {"EN": "ENGLISH", "ENG": "ENGLISH", "BENG": "BENGALI",
               "BEN": "BENGALI", "BN": "BENGALI", "HIN": "HINDI", "HI": "HINDI"}
    return aliases.get(value, value)


def derive_genre(categories, title, description, language=""):
    corpus = " ".join((categories or []) + [title, description, language]).lower()
    checks = [
        ("Detective Mystery",       ["detective fiction", "detective", "sherlock holmes", "mystery", "crime fiction", "investigation", "feluda", "byomkesh"]),
        ("Psychological Thriller",  ["psychological thriller", "psychological suspense"]),
        ("Thriller",                ["thriller", "suspense", "serial killer"]),
        ("Romance",                 ["romance", "love story", "relationship fiction"]),
        ("Historical Fiction",      ["historical fiction", "historical novel", "historical"]),
        ("Mythological Retelling",  ["mythology", "retelling", "mahabharata", "ramayana"]),
        ("Campus Fiction",          ["college stories", "campus fiction", "college life", "iit", "iim"]),
        ("Science Fiction",         ["science fiction", "sci-fi", "space opera", "dystopia"]),
        ("Literary Fiction",        ["literary", "family life", "coming of age"]),
        ("Non Fiction",             ["biography", "memoir", "history", "self-help", "essay"]),
    ]
    for genre, words in checks:
        if any(w in corpus for w in words):
            return genre
    if categories:
        first = categories[0][:80]
        if "fiction" in first.lower() or "novel" in first.lower():
            return first
    return "General Fiction"


def derive_mood(genre, description, title):
    text = f"{genre} {description} {title}".lower()
    if any(w in text for w in ["murder", "thriller", "crime", "suspense", "killer"]):
        return "Dark / suspenseful"
    if any(w in text for w in ["love", "romance", "relationship"]):
        return "Emotional / romantic"
    if any(w in text for w in ["war", "partition", "tragic", "loss", "grief"]):
        return "Heavy / heartbreaking"
    if any(w in text for w in ["philosophy", "dream", "journey", "meaning"]):
        return "Reflective / inspirational"
    return "Thoughtful / engaging"


def derive_english_label(title, description, language=""):
    if "BENGALI" in (language or "").upper() or BENGALI_RANGE.search(f"{title} {description}"):
        return "Bengali", 3
    if "HINDI" in (language or "").upper():
        return "Hindi", 3
    text  = f"{title}. {description}".strip()
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return "Moderate", 3
    avg_len        = sum(len(w) for w in words) / len(words)
    sentence_count = max(1, len([x for x in re.split(r"[.!?]+", text) if x.strip()]))
    wps            = len(words) / sentence_count
    if avg_len < 4.8 and wps < 14: return "Very Simple", 5
    if avg_len < 5.3 and wps < 18: return "Simple", 4
    if avg_len < 6.0 and wps < 23: return "Moderate", 3
    if avg_len < 6.8 and wps < 30: return "Advanced", 2
    return "Complex", 1


def looks_indian_author(author: str) -> bool:
    corpus  = normalize_text(author)
    markers = ["bhagat","datta","bond","satyajit","tagore","mukherjee","chattopadhyay",
               "chakraborty","banerjee","ghosh","ray","basu","anand","narayan","tharoor",
               "amitav","rushdie","amish","tripathi"]
    return any(m in corpus for m in markers)


def derive_india_set(title, author, description, categories, publisher, language=""):
    cat_text    = " ".join(categories or []).lower()
    author_text = " ".join([title, author, publisher, language]).lower()
    if BENGALI_RANGE.search(title) or BENGALI_RANGE.search(author):
        return "Yes"
    if "BENGALI" in (language or "").upper() or "HINDI" in (language or "").upper():
        return "Yes"
    if any(pub in author_text for pub in INDIAN_PUBLISHERS):
        return "Yes"
    if looks_indian_author(author):
        return "Yes"
    if any(m in cat_text for m in ["indic fiction", "indian fiction", "indian literature", "bengali fiction", "bangla"]):
        return "Yes"
    if any(m in (cat_text + " " + (description or "").lower()) for m in ["partition", "south asia", "pakistan", "lahore", "bangladesh", "dhaka"]):
        return "Partly"
    return "No"


def derive_scores(genre, description, title, categories, india_set, language=""):
    text = f"{genre} {description} {title} {' '.join(categories)} {language}".lower()
    wow, emotional, sadness, realism = 3, 3, 2, 3
    if any(w in text for w in ["thriller","mystery","murder","crime","killer","suspense","feluda","byomkesh"]): wow = 5
    elif any(w in text for w in ["epic","mythology","science fiction","dystopia"]):                             wow = 4
    if any(w in text for w in ["love","family","friendship","loss","grief","heart"]):     emotional = 5
    elif any(w in text for w in ["journey","identity","coming of age"]):                  emotional = 4
    if any(w in text for w in ["tragic","death","war","partition","betrayal","grief","loss"]): sadness = 5
    elif any(w in text for w in ["melancholy","lonely","broken"]):                         sadness = 4
    elif any(w in text for w in ["romance","emotional"]):                                  sadness = 3
    if any(w in text for w in ["literary","family life","historical","contemporary","campus","realistic","bengali"]): realism = 5
    elif any(w in text for w in ["mythology","science fiction","fantasy"]):                realism = 2
    elif any(w in text for w in ["thriller","mystery"]):                                   realism = 4
    if india_set == "Yes":
        realism = min(5, realism + 1)
    return wow, emotional, sadness, realism


# ── FastAPI app ────────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).parent.resolve()

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

EXPORT_COLUMNS = [
    "id","title","author","isbn","genre","subgenres","description","language","published_year",
    "page_count","publisher","info_link","cover_url","image_path","buy_link","mood","english_label",
    "english_ease_score","india_set","wow_score","emotional_score","sadness_score","realism_score",
    "personalized_score","rating","status","notes","source","current_page","bookmark_page",
    "bookmark_note","created_at","updated_at",
]

SORT_FIELDS = {
    "title","author","genre","language","published_year","page_count","english_label",
    "wow_score","emotional_score","sadness_score","realism_score","personalized_score",
    "status","bookmark_page","current_page","rating","publisher","created_at","updated_at",
}


class AddBookRequest(BaseModel):
    title: str; author: str = ""; isbn: str = ""; notes: str = ""

class StatusRequest(BaseModel):
    status: str

class UpdateBookRequest(BaseModel):
    title: str | None = None; author: str | None = None; isbn: str | None = None
    genre: str | None = None; subgenres: str | None = None; description: str | None = None
    language: str | None = None; published_year: str | None = None
    page_count: int | None = None; publisher: str | None = None
    info_link: str | None = None; cover_url: str | None = None; buy_link: str | None = None
    mood: str | None = None; english_label: str | None = None
    english_ease_score: int | None = None; india_set: str | None = None
    wow_score: int | None = None; emotional_score: int | None = None
    sadness_score: int | None = None; realism_score: int | None = None
    rating: float | None = None; status: str | None = None; notes: str | None = None
    current_page: int | None = None; bookmark_page: int | None = None
    bookmark_note: str | None = None

class SettingsRequest(BaseModel):
    english_weight: float | None = None; wow_weight: float | None = None
    emotion_weight: float | None = None; sadness_weight: float | None = None
    realism_weight: float | None = None; genre_bonus_weight: float | None = None
    genre_bonus_value: float | None = None; genre_bonus_keywords: str | None = None
    score_formula_label: str | None = None; recommendation_statuses: str | None = None
    recommendation_explain_label: str | None = None

class BackupActionRequest(BaseModel):
    name: str


@app.on_event("startup")
def startup():
    init_db()


def ensure_db():
    init_db()


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": APP_NAME, "app_version": APP_VERSION,
    })


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    ensure_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION, "db_ok": True, "total_books": total}


@app.get("/api/options")
def options():
    return {"status_options": STATUS_OPTIONS, "search_fields": SEARCH_FIELD_MAP}


@app.get("/api/settings")
def api_settings():
    ensure_db()
    return get_settings()


@app.patch("/api/settings")
def patch_settings(payload: SettingsRequest):
    ensure_db()
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No settings provided")
    settings = update_settings(data)
    recalculate_all_scores(settings)
    return settings


def safe_num(value: Any):
    try:    return float(value)
    except: return None


def should_use_enriched_as_primary(row: dict) -> bool:
    rich_fields = ["genre","description","cover_url","published_year","page_count","language","publisher"]
    return sum(1 for f in rich_fields if str(row.get(f, "")).strip()) <= 2


def normalized_title_author(title: str, author: str) -> tuple[str, str]:
    def norm(v: str) -> str:
        v = re.sub(r"[^\w\s]", " ", (v or "").strip().lower())
        return re.sub(r"\s+", " ", v).strip()
    return norm(title), norm(author)


def find_duplicate_id(conn, title: str, author: str):
    nt, na = normalized_title_author(title, author)
    if not nt:
        return None
    rows = conn.execute("SELECT id, title, author FROM books").fetchall()
    for row in rows:
        rt, ra = normalized_title_author(row["title"], row["author"])
        if rt == nt and ra == na:
            return row["id"]
    return None


def remove_duplicate_rows(conn):
    rows    = conn.execute("SELECT id, title, author, created_at FROM books ORDER BY id ASC").fetchall()
    seen    = {}
    removed = []
    for row in rows:
        key = normalized_title_author(row["title"], row["author"])
        if key in seen:
            conn.execute("DELETE FROM books WHERE id = ?", (row["id"],))
            removed.append({"removed_id": row["id"], "kept_id": seen[key], "title": row["title"], "author": row["author"]})
        else:
            seen[key] = row["id"]
    return removed


def safe_enrich_book(title: str, author: str = "", isbn: str = "", settings: dict | None = None) -> dict:
    try:
        return enrich_book(title, author, isbn, settings=settings)
    except Exception:
        fallback = {"title": title.strip(), "author": author.strip(), "isbn": isbn.strip(),
                    "genre": "General Fiction", "subgenres": "", "description": "", "language": "",
                    "published_year": "", "page_count": 0, "publisher": "", "info_link": "",
                    "cover_url": "", "buy_link": "", "mood": "Thoughtful / engaging",
                    "english_label": "Moderate", "english_ease_score": 3, "india_set": "Unknown",
                    "wow_score": 3, "emotional_score": 3, "sadness_score": 2, "realism_score": 3,
                    "rating": 0.0, "source": "manual-fallback"}
        fallback["personalized_score"] = calculate_personalized_score(fallback, settings)
        return fallback


def book_matches_query(book: dict, q: str) -> bool:
    if not q:
        return True
    q   = q.strip()
    low = q.lower()
    if "=" in q:
        key, value = q.split("=", 1)
        key = key.strip().lower(); value = value.strip().lower()
        if key in {"bookmark", "bookmarked"}:
            page    = int(book.get("bookmark_page") or 0)
            note    = str(book.get("bookmark_note") or "").strip().lower()
            current = int(book.get("current_page") or 0)
            return (value in {"1","true","yes","y","bookmarked","on"} and (page > 0 or current > 0 or bool(note))) or value in note
        field = SEARCH_FIELD_MAP.get(key)
        if field:
            return value in str(book.get(field, "")).lower()
    hay = " | ".join([str(book.get(f, "")) for f in [
        "title","author","genre","subgenres","language","publisher","notes",
        "mood","english_label","wow_score","emotional_score","sadness_score",
        "realism_score","status","bookmark_note","buy_link",
    ]]).lower()
    return low in hay


def sort_books(rows: list[dict], sort_by: str, sort_dir: str):
    reverse = sort_dir == "desc"
    sort_by = sort_by if sort_by in SORT_FIELDS else "personalized_score"
    def keyfn(book: dict):
        v   = book.get(sort_by)
        num = safe_num(v)
        if num is not None: return (0, num)
        return (1, str(v or "").lower())
    rows.sort(key=keyfn, reverse=reverse)
    return rows


@app.get("/api/books")
def list_books(q: str = "", genre: str = "", status: str = "",
               sort_by: str = "personalized_score", sort_dir: str = "desc", bookmarked: bool = False):
    ensure_db()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
    if genre and genre != "All":
        rows = [r for r in rows if genre.lower() in str(r.get("genre", "")).lower()]
    if status and status != "All":
        rows = [r for r in rows if r.get("status") == status]
    if bookmarked:
        rows = [r for r in rows if int(r.get("bookmark_page") or 0) > 0
                or int(r.get("current_page") or 0) > 0
                or str(r.get("bookmark_note") or "").strip()]
    if q:
        rows = [r for r in rows if book_matches_query(r, q)]
    return sort_books(rows, sort_by, sort_dir)


@app.get("/api/genres")
def genres():
    ensure_db()
    with connect() as conn:
        rows = [r[0] for r in conn.execute("SELECT DISTINCT genre FROM books WHERE genre != '' ORDER BY genre").fetchall()]
    return rows


def insert_book(conn, book: dict):
    cols         = [c for c in EXPORT_COLUMNS if c not in {"id","created_at","updated_at","image_path"}]
    placeholders = ",".join(["?"] * len(cols))
    values       = [book.get(c, "") for c in cols]
    cur          = conn.execute(f"INSERT INTO books ({','.join(cols)}) VALUES ({placeholders})", values)
    return cur.lastrowid


@app.post("/api/books")
def add_book(payload: AddBookRequest):
    ensure_db()
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    settings = get_settings()
    book = safe_enrich_book(payload.title.strip(), payload.author.strip(), payload.isbn.strip(), settings=settings)
    book.update({"notes": payload.notes.strip(), "status": "Want to Read",
                 "current_page": 0, "bookmark_page": 0, "bookmark_note": ""})
    with connect() as conn:
        duplicate_id = find_duplicate_id(conn, book.get("title", payload.title), book.get("author", payload.author))
        if duplicate_id:
            row = dict(conn.execute("SELECT * FROM books WHERE id = ?", (duplicate_id,)).fetchone())
            row["_duplicate_skipped"] = True
            row["_message"] = "Duplicate book already exists. Existing entry kept, new one skipped."
            return JSONResponse(row)
        book_id = insert_book(conn, book)
        row     = dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())
    return row


@app.patch("/api/books/{book_id}/status")
def update_status(book_id: int, payload: StatusRequest):
    ensure_db()
    if payload.status not in STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid status")
    with connect() as conn:
        conn.execute("UPDATE books SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.status, book_id))
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return dict(row)


@app.patch("/api/books/{book_id}")
def update_book(book_id: int, payload: UpdateBookRequest):
    ensure_db()
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields provided")
    if "status" in data and data["status"] not in STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid status")
    for field in ["wow_score","emotional_score","sadness_score","realism_score","english_ease_score"]:
        if field in data and data[field] is not None:
            data[field] = max(1, min(5, int(data[field])))
    for field in ["current_page","bookmark_page","page_count"]:
        if field in data and data[field] is not None:
            data[field] = max(0, int(data[field]))
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Book not found")
        current  = dict(row)
        merged   = {**current, **data}
        settings = get_settings(conn)
        merged["personalized_score"] = calculate_personalized_score(merged, settings)
        fields      = list(data.keys()) + ["personalized_score"]
        assignments = ", ".join([f"{f} = ?" for f in fields]) + ", updated_at = CURRENT_TIMESTAMP"
        values      = [merged[f] for f in fields] + [book_id]
        conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", values)
        updated = dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())
    return updated


@app.get("/api/books/{book_id}")
def get_book(book_id: int):
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return dict(row)


@app.get("/api/books/{book_id}/score-breakdown")
def get_score_breakdown(book_id: int):
    ensure_db()
    with connect() as conn:
        row      = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        settings = get_settings(conn)
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return score_breakdown(dict(row), settings)


@app.post("/api/books/{book_id}/refresh")
def refresh_book(book_id: int):
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Book not found")
        current  = dict(row)
        settings = get_settings(conn)
        enriched = safe_enrich_book(current["title"], current.get("author",""), current.get("isbn",""), settings=settings)
        keep     = {k: current.get(k, v) for k, v in {"status":"Want to Read","notes":"","current_page":0,"bookmark_page":0,"bookmark_note":""}.items()}
        merged   = {**current, **enriched, **keep}
        merged["personalized_score"] = calculate_personalized_score(merged, settings)
        refresh_fields  = ["author","isbn","genre","subgenres","description","language","published_year","page_count","publisher","info_link","cover_url","buy_link","mood","english_label","english_ease_score","india_set","wow_score","emotional_score","sadness_score","realism_score","personalized_score","rating","source","status","notes","current_page","bookmark_page","bookmark_note"]
        assignments     = ", ".join([f"{f} = ?" for f in refresh_fields]) + ", updated_at = CURRENT_TIMESTAMP"
        values          = [merged.get(f, "") for f in refresh_fields] + [book_id]
        conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", values)
        updated = dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())
    return updated


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return {"deleted": True, "id": book_id}


@app.get("/api/recommendation")
def recommendation():
    ensure_db()
    rows     = list_books(sort_by="personalized_score", sort_dir="desc")
    settings = get_settings()
    current  = next((b for b in rows if b.get("status") == "Reading"), {})
    current_id       = current.get("id")
    raw_statuses     = str(settings.get("recommendation_statuses", "Want to Read, Paused") or "")
    allowed_statuses = {part.strip() for part in raw_statuses.split(",") if part.strip()} or {"Want to Read","Paused"}
    candidates = [b for b in rows if (b.get("status") or "").strip() in allowed_statuses and b.get("id") != current_id]
    next_book  = candidates[0] if candidates else {}
    return {"current": current, "next": next_book,
            "allowed_statuses": sorted(allowed_statuses),
            "rule_label": settings.get("recommendation_explain_label") or "Eligible statuses for automatic next recommendation"}


@app.get("/api/stats")
def stats():
    ensure_db()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
    counts     = {status: sum(1 for r in rows if r.get("status") == status) for status in STATUS_OPTIONS}
    top_genres: dict[str, int] = {}
    for row in rows:
        genre = (row.get("genre") or "").strip()
        if genre:
            top_genres[genre] = top_genres.get(genre, 0) + 1
    return {"total": len(rows), "statuses": counts,
            "bookmarked": sum(1 for r in rows if int(r.get("bookmark_page") or 0) > 0 or int(r.get("current_page") or 0) > 0),
            "top_genres": [{"genre": k, "cnt": v} for k, v in sorted(top_genres.items(), key=lambda x: (-x[1], x[0]))[:8]]}


@app.get("/api/export.json")
def export_json():
    return JSONResponse(list_books(sort_by="personalized_score", sort_dir="desc"))


@app.get("/api/export.csv")
def export_csv():
    rows   = list_books(sort_by="personalized_score", sort_dir="desc")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        out = {k: row.get(k, "") for k in EXPORT_COLUMNS}
        out["image_path"] = row.get("cover_url", "")
        writer.writerow(out)
    mem     = io.BytesIO(output.getvalue().encode("utf-8"))
    headers = {"Content-Disposition": "attachment; filename=personal_library.csv"}
    return StreamingResponse(mem, media_type="text/csv", headers=headers)


@app.get("/api/import/sample.json")
def sample_json():
    return JSONResponse([{"title":"Sample Book","author":"Sample Author","genre":"Mystery",
        "description":"Short description","language":"ENGLISH","published_year":"2024",
        "page_count":240,"buy_link":"https://example.com","wow_score":4,"emotional_score":3,
        "sadness_score":2,"realism_score":4,"status":"Want to Read","notes":"Optional notes",
        "bookmark_page":0,"bookmark_note":""}])


@app.get("/api/import/sample.csv")
def sample_csv():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerow({"title":"Sample Book","author":"Sample Author","genre":"Mystery",
        "description":"Short description","language":"ENGLISH","published_year":"2024",
        "page_count":240,"buy_link":"https://example.com","wow_score":4,"emotional_score":3,
        "sadness_score":2,"realism_score":4,"status":"Want to Read","notes":"Optional notes",
        "bookmark_page":0,"bookmark_note":""})
    mem     = io.BytesIO(output.getvalue().encode("utf-8"))
    headers = {"Content-Disposition": "attachment; filename=personal_library_sample.csv"}
    return StreamingResponse(mem, media_type="text/csv", headers=headers)


def normalize_import_row(data: dict, settings: dict) -> dict:
    clean = {k: data.get(k, "") for k in EXPORT_COLUMNS if k not in {"id","created_at","updated_at","image_path"}}
    if not str(clean.get("cover_url","")).strip() and str(data.get("image_path","")).strip():
        clean["cover_url"] = str(data.get("image_path","")).strip()
    title    = str(clean.get("title","")).strip()
    author   = str(clean.get("author","")).strip()
    isbn     = str(clean.get("isbn","")).strip()
    enriched = safe_enrich_book(title, author, isbn, settings=settings) if title else {}
    if should_use_enriched_as_primary(clean):
        base = dict(enriched)
        for field in ["status","notes","current_page","bookmark_page","bookmark_note","buy_link","info_link","cover_url"]:
            if str(clean.get(field,"")).strip(): base[field] = clean[field]
        clean = {**clean, **base}
    else:
        for k, v in enriched.items():
            if not str(clean.get(k,"")).strip(): clean[k] = v
    if enriched.get("title"):  clean["title"]  = enriched["title"]
    if enriched.get("author"): clean["author"] = enriched["author"]
    for field in ["wow_score","emotional_score","sadness_score","realism_score","english_ease_score","page_count","current_page","bookmark_page"]:
        try:
            if clean.get(field,"") != "": clean[field] = int(float(clean[field]))
        except Exception:
            clean[field] = 0 if field in {"page_count","current_page","bookmark_page"} else 3
    if clean.get("status") not in STATUS_OPTIONS: clean["status"] = "Want to Read"
    clean["personalized_score"] = calculate_personalized_score(clean, settings)
    return clean


def upsert_import_rows(rows: list[dict]):
    settings = get_settings()
    backup_db("pre_import")
    summary = {"received": len(rows), "inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    with connect() as conn:
        for idx, row in enumerate(rows, start=1):
            try:
                clean = normalize_import_row(row, settings)
                if not str(clean.get("title","")).strip():
                    summary["skipped"] += 1
                    summary["errors"].append({"row": idx, "reason": "Missing title"})
                    continue
                dup_id = find_duplicate_id(conn, clean.get("title",""), clean.get("author",""))
                if dup_id:
                    fields      = list(clean.keys())
                    assignments = ", ".join([f"{f} = ?" for f in fields]) + ", updated_at = CURRENT_TIMESTAMP"
                    conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", [clean[f] for f in fields] + [dup_id])
                    summary["updated"] += 1
                else:
                    insert_book(conn, clean)
                    summary["inserted"] += 1
            except Exception as e:
                summary["skipped"] += 1
                summary["errors"].append({"row": idx, "reason": str(e)[:200]})
    return summary


@app.post("/api/import/json")
async def import_json(file: UploadFile = File(...)):
    payload = json.loads((await file.read()).decode("utf-8"))
    return upsert_import_rows(payload if isinstance(payload, list) else [payload])


@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8-sig")
    return upsert_import_rows(list(csv.DictReader(io.StringIO(text))))


@app.post("/api/backup")
def create_backup():
    ensure_db()
    return {"backup_path": backup_db("manual")}


@app.get("/api/backups")
def api_backups():
    ensure_db()
    return {"items": list_backups()}


@app.post("/api/backups/restore")
def api_restore_backup(payload: BackupActionRequest):
    ensure_db()
    try:    result = restore_backup(payload.name)
    except FileNotFoundError: raise HTTPException(status_code=404, detail="Backup not found")
    init_db()
    return result


@app.delete("/api/backups/{name}")
def api_delete_backup(name: str):
    ensure_db()
    try:    return delete_backup(name)
    except FileNotFoundError: raise HTTPException(status_code=404, detail="Backup not found")


def recalculate_all_scores(settings: dict | None = None):
    ensure_db()
    settings = settings or get_settings()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
        for row in rows:
            score = calculate_personalized_score(row, settings)
            conn.execute("UPDATE books SET personalized_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (score, row["id"]))


@app.get("/api/books/lookup")
def book_lookup(author: str = "", title: str = "", q: str = Query(default="")):
    query = q or author or title
    rows  = list_books(q=query)
    return {"query": query, "count": len(rows), "items": rows[:25]}


@app.post("/api/books/deduplicate")
def deduplicate_books():
    ensure_db()
    with connect() as conn:
        removed = remove_duplicate_rows(conn)
    return {"removed_count": len(removed), "removed": removed}


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
