from __future__ import annotations

import os
import threading

from functools import lru_cache
from pathlib import Path
from difflib import get_close_matches

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import nltk
from nltk.corpus import wordnet as wn
import uvicorn

APP_NAME    = os.getenv("APP_NAME",    "Offline Dictionary")
APP_VERSION = os.getenv("APP_VERSION", "1.4.5")

# NAS-persisted path — survives restarts; downloaded on first boot only
NLTK_DATA_DIR = Path('/opt/offline-dictionary/data/nltk_data')

_BASE_DIR    = Path(__file__).parent.resolve()
_nltk_ready  = threading.Event()   # set once wordnet is available

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount('/static', StaticFiles(directory=str(_BASE_DIR / 'static')), name='static')
templates = Jinja2Templates(directory=str(_BASE_DIR / 'templates'))


def _ensure_nltk() -> None:
    """Download NLTK corpora in the background so startup is non-blocking."""
    try:
        NLTK_DATA_DIR.mkdir(parents=True, exist_ok=True)
        data_path = str(NLTK_DATA_DIR)
        if data_path not in nltk.data.path:
            nltk.data.path.insert(0, data_path)
        for pkg in ['wordnet', 'omw-1.4']:
            try:
                nltk.data.find(f'corpora/{pkg}')
                print(f'[dict] {pkg}: already present')
            except LookupError:
                print(f'[dict] Downloading {pkg} …')
                nltk.download(pkg, download_dir=data_path, quiet=False)
                print(f'[dict] {pkg} ready')
        _nltk_ready.set()
        print('[dict] NLTK ready')
    except Exception as exc:
        print(f'[dict] NLTK setup error: {exc}')
        _nltk_ready.set()   # still set so lookups return an error instead of hanging


@app.on_event('startup')
def startup():
    data_path = str(NLTK_DATA_DIR)
    if data_path not in nltk.data.path:
        nltk.data.path.insert(0, data_path)
    threading.Thread(target=_ensure_nltk, daemon=True).start()

@lru_cache(maxsize=1)
def lemma_index() -> tuple[str, ...]:
    words = set()
    for syn in wn.all_synsets():
        for lemma in syn.lemma_names():
            words.add(lemma.replace('_', ' ').lower())
    return tuple(sorted(words))

@app.get('/')
def root(request: Request):
    return templates.TemplateResponse(request, 'index.html', {
        'app_name': APP_NAME,
        'app_version': APP_VERSION,
    })

@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get('/api/health')
def health():
    return {'ok': True, 'service': APP_NAME, 'version': APP_VERSION}

@app.get('/api/lookup')
def lookup(q: str = Query(..., min_length=1)):
    if not _nltk_ready.is_set():
        raise HTTPException(status_code=503, detail='Dictionary data is still loading — please try again in a moment.')
    word = q.strip().lower()
    try:
        synsets = wn.synsets(word)
    except LookupError:
        raise HTTPException(status_code=503, detail='Dictionary data unavailable — NLTK corpus may not have downloaded correctly.')
    items = []
    synonyms = set()
    antonyms = set()
    for syn in synsets[:12]:
        lemmas = syn.lemmas()
        for lemma in lemmas:
            synonyms.add(lemma.name().replace('_', ' '))
            for ant in lemma.antonyms():
                antonyms.add(ant.name().replace('_', ' '))
        items.append({
            'part_of_speech': syn.pos(),
            'definition': syn.definition(),
            'examples': syn.examples(),
            'lemmas': sorted({l.name().replace('_', ' ') for l in lemmas}),
        })
    found = bool(items)
    suggestions = []
    if not found:
        suggestions = get_close_matches(word, lemma_index(), n=8, cutoff=0.78)
    return {
        'query': word,
        'found': found,
        'meanings': items,
        'synonyms': sorted(synonyms)[:40],
        'antonyms': sorted(antonyms)[:20],
        'suggestions': suggestions,
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8133")))
