from __future__ import annotations

from difflib import get_close_matches
from functools import lru_cache

import nltk
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from nltk.corpus import wordnet as wn

from app.config import APP_NAME, APP_VERSION
from app.core import templates
from app.nltk_setup import nltk_ready

router = APIRouter()


# ── Word index cache (built lazily after NLTK is ready) ───────────────────────

@lru_cache(maxsize=1)
def _lemma_index() -> tuple[str, ...]:
    words: set[str] = set()
    for syn in wn.all_synsets():
        for lemma in syn.lemma_names():
            words.add(lemma.replace("_", " ").lower())
    return tuple(sorted(words))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", include_in_schema=False)
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
    })


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@router.get("/api/health")
def health():
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION}


@router.get("/api/lookup")
def lookup(q: str = Query(..., min_length=1)):
    if not nltk_ready.is_set():
        raise HTTPException(
            status_code=503,
            detail="Dictionary data is still loading — please try again in a moment.",
        )
    word = q.strip().lower()
    try:
        synsets = wn.synsets(word)
    except LookupError:
        raise HTTPException(
            status_code=503,
            detail="Dictionary data unavailable — NLTK corpus may not have downloaded correctly.",
        )
    items = []
    synonyms: set[str] = set()
    antonyms: set[str] = set()
    for syn in synsets[:12]:
        lemmas = syn.lemmas()
        for lemma in lemmas:
            synonyms.add(lemma.name().replace("_", " "))
            for ant in lemma.antonyms():
                antonyms.add(ant.name().replace("_", " "))
        items.append({
            "part_of_speech": syn.pos(),
            "definition": syn.definition(),
            "examples": syn.examples(),
            "lemmas": sorted({lm.name().replace("_", " ") for lm in lemmas}),
        })
    found = bool(items)
    suggestions: list[str] = []
    if not found:
        suggestions = get_close_matches(word, _lemma_index(), n=8, cutoff=0.78)
    return {
        "query": word,
        "found": found,
        "meanings": items,
        "synonyms": sorted(synonyms)[:40],
        "antonyms": sorted(antonyms)[:20],
        "suggestions": suggestions,
    }
