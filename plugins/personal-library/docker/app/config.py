from __future__ import annotations

import json
import os
from pathlib import Path

APP_TITLE   = os.getenv("PERSONAL_LIBRARY_TITLE", "Personal Library")
APP_NAME    = os.getenv("APP_NAME",    APP_TITLE)
APP_VERSION = os.getenv("APP_VERSION", "1.3.3")
HOST        = os.getenv("PERSONAL_LIBRARY_HOST", "0.0.0.0")
PORT        = int(os.getenv("PERSONAL_LIBRARY_PORT", "8132"))

DB_PATH = Path(os.getenv("PERSONAL_LIBRARY_DB_PATH", "/opt/personal-library/data/library.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

AMAZON_BASE             = os.getenv("PERSONAL_LIBRARY_AMAZON_BASE", "https://www.amazon.in/s?k=")
GOOGLE_BOOKS_URL        = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"

STATUS_OPTIONS = ["Not Bought", "Want to Read", "Reading", "Paused", "Read"]

DEFAULT_SCORE_SETTINGS: dict = {
    "english_weight":               1.8,
    "wow_weight":                   1.4,
    "emotion_weight":               1.6,
    "sadness_weight":               0.7,
    "realism_weight":               1.3,
    "genre_bonus_weight":           1.2,
    "genre_bonus_value":            5.0,
    "genre_bonus_keywords":         "mystery, thriller, detective",
    "score_formula_label":          "english*1.8 + wow*1.4 + emotion*1.6 + sadness_balance*0.7 + realism*1.3 + genre_bonus",
    "recommendation_statuses":      "Want to Read, Paused",
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

EXPORT_COLUMNS = [
    "id", "title", "author", "isbn", "genre", "subgenres", "description", "language",
    "published_year", "page_count", "publisher", "info_link", "cover_url", "image_path",
    "buy_link", "mood", "english_label", "english_ease_score", "india_set", "wow_score",
    "emotional_score", "sadness_score", "realism_score", "personalized_score", "rating",
    "status", "notes", "source", "current_page", "bookmark_page", "bookmark_note",
    "created_at", "updated_at",
]

SORT_FIELDS = {
    "title", "author", "genre", "language", "published_year", "page_count", "english_label",
    "wow_score", "emotional_score", "sadness_score", "realism_score", "personalized_score",
    "status", "bookmark_page", "current_page", "rating", "publisher", "created_at", "updated_at",
}
