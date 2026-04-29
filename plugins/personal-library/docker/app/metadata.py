from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote_plus

import requests

from app.config import (
    AMAZON_BASE,
    DEFAULT_SCORE_SETTINGS,
    GOOGLE_BOOKS_URL,
    OPEN_LIBRARY_SEARCH_URL,
)

# ── Text helpers ───────────────────────────────────────────────────────────────

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


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\sঀ-৿]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# ── Scoring ────────────────────────────────────────────────────────────────────

def _norm_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_SCORE_SETTINGS)
    if settings:
        merged.update(settings)
    return merged


def calculate_personalized_score(book: dict, settings: dict[str, Any] | None = None) -> float:
    cfg       = _norm_settings(settings)
    english   = int(book.get("english_ease_score", 3) or 3)
    wow       = int(book.get("wow_score",          3) or 3)
    emotional = int(book.get("emotional_score",    3) or 3)
    sadness   = int(book.get("sadness_score",       2) or 2)
    realism   = int(book.get("realism_score",       3) or 3)
    genre     = (book.get("genre") or "").lower()
    keywords  = [x.strip().lower() for x in str(cfg.get("genre_bonus_keywords", "")).split(",") if x.strip()]
    raw_bonus = float(cfg.get("genre_bonus_value", 5.0) or 0) if any(
        k in (genre + " " + str(book.get("subgenres", "")).lower()) for k in keywords
    ) else 0.0
    return round(
        english   * float(cfg.get("english_weight",     1.8) or 0)
        + wow     * float(cfg.get("wow_weight",          1.4) or 0)
        + emotional * float(cfg.get("emotion_weight",   1.6) or 0)
        + (6 - abs(3 - sadness)) * float(cfg.get("sadness_weight", 0.7) or 0)
        + realism * float(cfg.get("realism_weight",      1.3) or 0)
        + raw_bonus * float(cfg.get("genre_bonus_weight", 1.2) or 0),
        1,
    )


def score_breakdown(book: dict, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg      = _norm_settings(settings)
    genre    = (book.get("genre") or "").lower()
    keywords = [x.strip().lower() for x in str(cfg.get("genre_bonus_keywords", "")).split(",") if x.strip()]
    raw_bonus = float(cfg.get("genre_bonus_value", 5.0) or 0) if any(
        k in (genre + " " + str(book.get("subgenres", "")).lower()) for k in keywords
    ) else 0.0
    components = {
        "complexity_fit":  round(int(book.get("english_ease_score", 3) or 3) * float(cfg.get("english_weight",  1.8) or 0), 1),
        "wow":             round(int(book.get("wow_score",          3) or 3) * float(cfg.get("wow_weight",       1.4) or 0), 1),
        "emotional":       round(int(book.get("emotional_score",    3) or 3) * float(cfg.get("emotion_weight",  1.6) or 0), 1),
        "sadness_balance": round((6 - abs(3 - int(book.get("sadness_score", 2) or 2))) * float(cfg.get("sadness_weight", 0.7) or 0), 1),
        "realism":         round(int(book.get("realism_score",      3) or 3) * float(cfg.get("realism_weight",  1.3) or 0), 1),
        "genre_bonus":     round(raw_bonus * float(cfg.get("genre_bonus_weight", 1.2) or 0), 1),
    }
    return {
        "formula":    str(cfg.get("score_formula_label") or DEFAULT_SCORE_SETTINGS["score_formula_label"]),
        "components": components,
        "keywords":   keywords,
        "total":      round(sum(components.values()), 1),
    }


# ── Metadata enrichment ────────────────────────────────────────────────────────

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
    return curated if curated else ""


def first_identifier(items: list, fallback: str = "") -> str:
    for x in items:
        v = x.get("identifier", "")
        if v:
            return v
    return fallback


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


def default_raw(title: str, author: str = "") -> dict[str, Any]:
    return {
        "title": title, "authors": [author] if author else [], "description": "",
        "categories": [], "language": "", "publishedDate": "", "pageCount": 0,
        "publisher": "", "industryIdentifiers": [], "infoLink": "", "imageLinks": {},
        "averageRating": 0, "_source": "manual",
    }


def search_google_books(title: str, author: str = "", isbn: str = "") -> dict[str, Any] | None:
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
            candidates.append({
                "title": info.get("title", ""), "authors": info.get("authors", []),
                "description": info.get("description", ""), "categories": info.get("categories", []),
                "language": info.get("language", ""), "publishedDate": info.get("publishedDate", ""),
                "pageCount": info.get("pageCount", 0), "publisher": info.get("publisher", ""),
                "industryIdentifiers": info.get("industryIdentifiers", []),
                "infoLink": info.get("infoLink", ""), "imageLinks": info.get("imageLinks", {}),
                "averageRating": info.get("averageRating", 0), "_source": "google_books",
            })
        return choose_best(candidates, title, author, isbn)
    except Exception:
        return None


def search_open_library(title: str, author: str = "", isbn: str = "") -> dict[str, Any] | None:
    q = " ".join([x for x in [title, author, isbn] if x]).strip()
    if not q:
        return None
    try:
        r = requests.get(OPEN_LIBRARY_SEARCH_URL, params={"q": q, "limit": 8}, timeout=20)
        r.raise_for_status()
        candidates = []
        for d in r.json().get("docs", []):
            candidates.append({
                "title": d.get("title", ""), "authors": d.get("author_name", []),
                "description": "", "categories": (d.get("subject") or [])[:8],
                "language": ",".join((d.get("language") or [])[:2]),
                "publishedDate": str(d.get("first_publish_year", "")),
                "pageCount": d.get("number_of_pages_median", 0),
                "publisher": ", ".join((d.get("publisher") or [])[:2]),
                "industryIdentifiers": [{"identifier": x} for x in (d.get("isbn") or [])[:3]],
                "infoLink": f"https://openlibrary.org{d.get('key', '')}" if d.get("key") else "",
                "imageLinks": {"thumbnail": f"https://covers.openlibrary.org/b/id/{d.get('cover_i', '')}-M.jpg" if d.get("cover_i") else ""},
                "averageRating": 0, "_source": "open_library",
            })
        return choose_best(candidates, title, author, isbn)
    except Exception:
        return None


def normalize_language(raw_language: str) -> str:
    value = (raw_language or "").upper().replace(",", " / ")
    aliases = {"EN": "ENGLISH", "ENG": "ENGLISH", "BENG": "BENGALI",
               "BEN": "BENGALI", "BN": "BENGALI", "HIN": "HINDI", "HI": "HINDI"}
    return aliases.get(value, value)


def derive_genre(categories: list, title: str, description: str, language: str = "") -> str:
    corpus = " ".join((categories or []) + [title, description, language]).lower()
    checks = [
        ("Detective Mystery",      ["detective fiction", "detective", "sherlock holmes", "mystery", "crime fiction", "investigation", "feluda", "byomkesh"]),
        ("Psychological Thriller", ["psychological thriller", "psychological suspense"]),
        ("Thriller",               ["thriller", "suspense", "serial killer"]),
        ("Romance",                ["romance", "love story", "relationship fiction"]),
        ("Historical Fiction",     ["historical fiction", "historical novel", "historical"]),
        ("Mythological Retelling", ["mythology", "retelling", "mahabharata", "ramayana"]),
        ("Campus Fiction",         ["college stories", "campus fiction", "college life", "iit", "iim"]),
        ("Science Fiction",        ["science fiction", "sci-fi", "space opera", "dystopia"]),
        ("Literary Fiction",       ["literary", "family life", "coming of age"]),
        ("Non Fiction",            ["biography", "memoir", "history", "self-help", "essay"]),
    ]
    for genre, words in checks:
        if any(w in corpus for w in words):
            return genre
    if categories:
        first = categories[0][:80]
        if "fiction" in first.lower() or "novel" in first.lower():
            return first
    return "General Fiction"


def derive_mood(genre: str, description: str, title: str) -> str:
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


def derive_english_label(title: str, description: str, language: str = "") -> tuple[str, int]:
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
    markers = ["bhagat", "datta", "bond", "satyajit", "tagore", "mukherjee", "chattopadhyay",
               "chakraborty", "banerjee", "ghosh", "ray", "basu", "anand", "narayan", "tharoor",
               "amitav", "rushdie", "amish", "tripathi"]
    return any(m in corpus for m in markers)


def derive_india_set(title: str, author: str, description: str, categories: list, publisher: str, language: str = "") -> str:
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


def derive_scores(genre: str, description: str, title: str, categories: list, india_set: str, language: str = "") -> tuple[int, int, int, int]:
    text = f"{genre} {description} {title} {' '.join(categories)} {language}".lower()
    wow, emotional, sadness, realism = 3, 3, 2, 3
    if any(w in text for w in ["thriller", "mystery", "murder", "crime", "killer", "suspense", "feluda", "byomkesh"]): wow = 5
    elif any(w in text for w in ["epic", "mythology", "science fiction", "dystopia"]):                                  wow = 4
    if any(w in text for w in ["love", "family", "friendship", "loss", "grief", "heart"]):     emotional = 5
    elif any(w in text for w in ["journey", "identity", "coming of age"]):                      emotional = 4
    if any(w in text for w in ["tragic", "death", "war", "partition", "betrayal", "grief", "loss"]): sadness = 5
    elif any(w in text for w in ["melancholy", "lonely", "broken"]):                                  sadness = 4
    elif any(w in text for w in ["romance", "emotional"]):                                            sadness = 3
    if any(w in text for w in ["literary", "family life", "historical", "contemporary", "campus", "realistic", "bengali"]): realism = 5
    elif any(w in text for w in ["mythology", "science fiction", "fantasy"]):                                                realism = 2
    elif any(w in text for w in ["thriller", "mystery"]):                                                                    realism = 4
    if india_set == "Yes":
        realism = min(5, realism + 1)
    return wow, emotional, sadness, realism


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


def safe_enrich_book(title: str, author: str = "", isbn: str = "", settings: dict | None = None) -> dict:
    try:
        return enrich_book(title, author, isbn, settings=settings)
    except Exception:
        fallback = {
            "title": title.strip(), "author": author.strip(), "isbn": isbn.strip(),
            "genre": "General Fiction", "subgenres": "", "description": "", "language": "",
            "published_year": "", "page_count": 0, "publisher": "", "info_link": "",
            "cover_url": "", "buy_link": "", "mood": "Thoughtful / engaging",
            "english_label": "Moderate", "english_ease_score": 3, "india_set": "Unknown",
            "wow_score": 3, "emotional_score": 3, "sadness_score": 2, "realism_score": 3,
            "rating": 0.0, "source": "manual-fallback",
        }
        fallback["personalized_score"] = calculate_personalized_score(fallback, settings)
        return fallback
