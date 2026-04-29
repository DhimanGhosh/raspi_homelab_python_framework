from __future__ import annotations

import csv
import io
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.config import (
    EXPORT_COLUMNS, SEARCH_FIELD_MAP, SORT_FIELDS, STATUS_OPTIONS,
    APP_NAME, APP_VERSION,
)
from app.core import templates
from app.db import (
    backup_db, connect, delete_backup, get_settings, init_db,
    list_backups, restore_backup, update_settings,
)
from app.metadata import (
    calculate_personalized_score, safe_enrich_book, score_breakdown,
)

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

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


# ── Helpers ────────────────────────────────────────────────────────────────────

def ensure_db() -> None:
    init_db()


def safe_num(value: Any):
    try:    return float(value)
    except: return None


def should_use_enriched_as_primary(row: dict) -> bool:
    rich_fields = ["genre", "description", "cover_url", "published_year", "page_count", "language", "publisher"]
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


def remove_duplicate_rows(conn) -> list[dict]:
    rows    = conn.execute("SELECT id, title, author, created_at FROM books ORDER BY id ASC").fetchall()
    seen: dict  = {}
    removed = []
    for row in rows:
        key = normalized_title_author(row["title"], row["author"])
        if key in seen:
            conn.execute("DELETE FROM books WHERE id = ?", (row["id"],))
            removed.append({"removed_id": row["id"], "kept_id": seen[key], "title": row["title"], "author": row["author"]})
        else:
            seen[key] = row["id"]
    return removed


def insert_book(conn, book: dict) -> int:
    cols         = [c for c in EXPORT_COLUMNS if c not in {"id", "created_at", "updated_at", "image_path"}]
    placeholders = ",".join(["?"] * len(cols))
    values       = [book.get(c, "") for c in cols]
    cur          = conn.execute(f"INSERT INTO books ({','.join(cols)}) VALUES ({placeholders})", values)
    return cur.lastrowid


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
            return (value in {"1", "true", "yes", "y", "bookmarked", "on"} and (page > 0 or current > 0 or bool(note))) or value in note
        field = SEARCH_FIELD_MAP.get(key)
        if field:
            return value in str(book.get(field, "")).lower()
    hay = " | ".join([str(book.get(f, "")) for f in [
        "title", "author", "genre", "subgenres", "language", "publisher", "notes",
        "mood", "english_label", "wow_score", "emotional_score", "sadness_score",
        "realism_score", "status", "bookmark_note", "buy_link",
    ]]).lower()
    return low in hay


def sort_books(rows: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
    reverse = sort_dir == "desc"
    sort_by = sort_by if sort_by in SORT_FIELDS else "personalized_score"
    def keyfn(book: dict):
        v   = book.get(sort_by)
        num = safe_num(v)
        if num is not None: return (0, num)
        return (1, str(v or "").lower())
    rows.sort(key=keyfn, reverse=reverse)
    return rows


def recalculate_all_scores(settings: dict | None = None) -> None:
    ensure_db()
    settings = settings or get_settings()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
        for row in rows:
            score = calculate_personalized_score(row, settings)
            conn.execute(
                "UPDATE books SET personalized_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (score, row["id"]),
            )


def normalize_import_row(data: dict, settings: dict) -> dict:
    clean = {k: data.get(k, "") for k in EXPORT_COLUMNS if k not in {"id", "created_at", "updated_at", "image_path"}}
    if not str(clean.get("cover_url", "")).strip() and str(data.get("image_path", "")).strip():
        clean["cover_url"] = str(data.get("image_path", "")).strip()
    title    = str(clean.get("title", "")).strip()
    author   = str(clean.get("author", "")).strip()
    isbn     = str(clean.get("isbn", "")).strip()
    enriched = safe_enrich_book(title, author, isbn, settings=settings) if title else {}
    if should_use_enriched_as_primary(clean):
        base = dict(enriched)
        for field in ["status", "notes", "current_page", "bookmark_page", "bookmark_note", "buy_link", "info_link", "cover_url"]:
            if str(clean.get(field, "")).strip(): base[field] = clean[field]
        clean = {**clean, **base}
    else:
        for k, v in enriched.items():
            if not str(clean.get(k, "")).strip(): clean[k] = v
    if enriched.get("title"):  clean["title"]  = enriched["title"]
    if enriched.get("author"): clean["author"] = enriched["author"]
    for field in ["wow_score", "emotional_score", "sadness_score", "realism_score", "english_ease_score", "page_count", "current_page", "bookmark_page"]:
        try:
            if clean.get(field, "") != "": clean[field] = int(float(clean[field]))
        except Exception:
            clean[field] = 0 if field in {"page_count", "current_page", "bookmark_page"} else 3
    if clean.get("status") not in STATUS_OPTIONS: clean["status"] = "Want to Read"
    clean["personalized_score"] = calculate_personalized_score(clean, settings)
    return clean


def upsert_import_rows(rows: list[dict]) -> dict:
    settings = get_settings()
    backup_db("pre_import")
    summary = {"received": len(rows), "inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    with connect() as conn:
        for idx, row in enumerate(rows, start=1):
            try:
                clean = normalize_import_row(row, settings)
                if not str(clean.get("title", "")).strip():
                    summary["skipped"] += 1
                    summary["errors"].append({"row": idx, "reason": "Missing title"})
                    continue
                dup_id = find_duplicate_id(conn, clean.get("title", ""), clean.get("author", ""))
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


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": APP_NAME, "app_version": APP_VERSION,
    })


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@router.get("/api/health")
def health():
    ensure_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION, "db_ok": True, "total_books": total}


@router.get("/api/options")
def options():
    return {"status_options": STATUS_OPTIONS, "search_fields": SEARCH_FIELD_MAP}


@router.get("/api/settings")
def api_settings():
    ensure_db()
    return get_settings()


@router.patch("/api/settings")
def patch_settings(payload: SettingsRequest):
    ensure_db()
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No settings provided")
    settings = update_settings(data)
    recalculate_all_scores(settings)
    return settings


@router.get("/api/books")
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


@router.get("/api/genres")
def genres():
    ensure_db()
    with connect() as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT DISTINCT genre FROM books WHERE genre != '' ORDER BY genre"
        ).fetchall()]
    return rows


@router.post("/api/books")
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


@router.patch("/api/books/{book_id}/status")
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


@router.patch("/api/books/{book_id}")
def update_book(book_id: int, payload: UpdateBookRequest):
    ensure_db()
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields provided")
    if "status" in data and data["status"] not in STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid status")
    for field in ["wow_score", "emotional_score", "sadness_score", "realism_score", "english_ease_score"]:
        if field in data and data[field] is not None:
            data[field] = max(1, min(5, int(data[field])))
    for field in ["current_page", "bookmark_page", "page_count"]:
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


@router.get("/api/books/lookup")
def book_lookup(author: str = "", title: str = "", q: str = Query(default="")):
    query = q or author or title
    rows  = list_books(q=query)
    return {"query": query, "count": len(rows), "items": rows[:25]}


@router.get("/api/books/{book_id}")
def get_book(book_id: int):
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return dict(row)


@router.get("/api/books/{book_id}/score-breakdown")
def get_score_breakdown(book_id: int):
    ensure_db()
    with connect() as conn:
        row      = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        settings = get_settings(conn)
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return score_breakdown(dict(row), settings)


@router.post("/api/books/{book_id}/refresh")
def refresh_book(book_id: int):
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Book not found")
        current  = dict(row)
        settings = get_settings(conn)
        enriched = safe_enrich_book(current["title"], current.get("author", ""), current.get("isbn", ""), settings=settings)
        keep     = {k: current.get(k, v) for k, v in {"status": "Want to Read", "notes": "", "current_page": 0, "bookmark_page": 0, "bookmark_note": ""}.items()}
        merged   = {**current, **enriched, **keep}
        merged["personalized_score"] = calculate_personalized_score(merged, settings)
        refresh_fields  = ["author", "isbn", "genre", "subgenres", "description", "language", "published_year", "page_count", "publisher", "info_link", "cover_url", "buy_link", "mood", "english_label", "english_ease_score", "india_set", "wow_score", "emotional_score", "sadness_score", "realism_score", "personalized_score", "rating", "source", "status", "notes", "current_page", "bookmark_page", "bookmark_note"]
        assignments     = ", ".join([f"{f} = ?" for f in refresh_fields]) + ", updated_at = CURRENT_TIMESTAMP"
        values          = [merged.get(f, "") for f in refresh_fields] + [book_id]
        conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", values)
        updated = dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())
    return updated


@router.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return {"deleted": True, "id": book_id}


@router.get("/api/recommendation")
def recommendation():
    ensure_db()
    rows     = list_books(sort_by="personalized_score", sort_dir="desc")
    settings = get_settings()
    current  = next((b for b in rows if b.get("status") == "Reading"), {})
    current_id       = current.get("id")
    raw_statuses     = str(settings.get("recommendation_statuses", "Want to Read, Paused") or "")
    allowed_statuses = {part.strip() for part in raw_statuses.split(",") if part.strip()} or {"Want to Read", "Paused"}
    candidates = [b for b in rows if (b.get("status") or "").strip() in allowed_statuses and b.get("id") != current_id]
    next_book  = candidates[0] if candidates else {}
    return {
        "current": current, "next": next_book,
        "allowed_statuses": sorted(allowed_statuses),
        "rule_label": settings.get("recommendation_explain_label") or "Eligible statuses for automatic next recommendation",
    }


@router.get("/api/stats")
def stats():
    ensure_db()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
    counts = {status: sum(1 for r in rows if r.get("status") == status) for status in STATUS_OPTIONS}
    top_genres: dict[str, int] = {}
    for row in rows:
        genre = (row.get("genre") or "").strip()
        if genre:
            top_genres[genre] = top_genres.get(genre, 0) + 1
    return {
        "total": len(rows), "statuses": counts,
        "bookmarked": sum(1 for r in rows if int(r.get("bookmark_page") or 0) > 0 or int(r.get("current_page") or 0) > 0),
        "top_genres": [{"genre": k, "cnt": v} for k, v in sorted(top_genres.items(), key=lambda x: (-x[1], x[0]))[:8]],
    }


@router.get("/api/export.json")
def export_json():
    return JSONResponse(list_books(sort_by="personalized_score", sort_dir="desc"))


@router.get("/api/export.csv")
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


@router.get("/api/import/sample.json")
def sample_json():
    return JSONResponse([{
        "title": "Sample Book", "author": "Sample Author", "genre": "Mystery",
        "description": "Short description", "language": "ENGLISH", "published_year": "2024",
        "page_count": 240, "buy_link": "https://example.com", "wow_score": 4, "emotional_score": 3,
        "sadness_score": 2, "realism_score": 4, "status": "Want to Read", "notes": "Optional notes",
        "bookmark_page": 0, "bookmark_note": "",
    }])


@router.get("/api/import/sample.csv")
def sample_csv():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerow({
        "title": "Sample Book", "author": "Sample Author", "genre": "Mystery",
        "description": "Short description", "language": "ENGLISH", "published_year": "2024",
        "page_count": 240, "buy_link": "https://example.com", "wow_score": 4, "emotional_score": 3,
        "sadness_score": 2, "realism_score": 4, "status": "Want to Read", "notes": "Optional notes",
        "bookmark_page": 0, "bookmark_note": "",
    })
    mem     = io.BytesIO(output.getvalue().encode("utf-8"))
    headers = {"Content-Disposition": "attachment; filename=personal_library_sample.csv"}
    return StreamingResponse(mem, media_type="text/csv", headers=headers)


@router.post("/api/import/json")
async def import_json(file: UploadFile = File(...)):
    import json
    payload = json.loads((await file.read()).decode("utf-8"))
    return upsert_import_rows(payload if isinstance(payload, list) else [payload])


@router.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8-sig")
    return upsert_import_rows(list(csv.DictReader(io.StringIO(text))))


@router.post("/api/backup")
def create_backup():
    ensure_db()
    return {"backup_path": backup_db("manual")}


@router.get("/api/backups")
def api_backups():
    ensure_db()
    return {"items": list_backups()}


@router.post("/api/backups/restore")
def api_restore_backup(payload: BackupActionRequest):
    ensure_db()
    try:
        result = restore_backup(payload.name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    init_db()
    return result


@router.delete("/api/backups/{name}")
def api_delete_backup(name: str):
    ensure_db()
    try:
        return delete_backup(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")


@router.post("/api/books/deduplicate")
def deduplicate_books():
    ensure_db()
    with connect() as conn:
        removed = remove_duplicate_rows(conn)
    return {"removed_count": len(removed), "removed": removed}
