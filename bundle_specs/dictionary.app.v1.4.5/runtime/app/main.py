from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from difflib import get_close_matches

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import nltk
from nltk.corpus import wordnet as wn

app = FastAPI(title="Offline Dictionary", version="1.4.5")
app.mount('/static', StaticFiles(directory='/opt/offline-dictionary/static'), name='static')

@app.on_event('startup')
def startup():
    base = Path('/opt/offline-dictionary/data/nltk_data')
    base.mkdir(parents=True, exist_ok=True)
    if str(base) not in nltk.data.path:
        nltk.data.path.insert(0, str(base))
    for pkg in ['wordnet', 'omw-1.4']:
        try:
            nltk.data.find(f'corpora/{pkg}')
        except LookupError:
            nltk.download(pkg, download_dir=str(base), quiet=True)

@lru_cache(maxsize=1)
def lemma_index() -> tuple[str, ...]:
    words = set()
    for syn in wn.all_synsets():
        for lemma in syn.lemma_names():
            words.add(lemma.replace('_', ' ').lower())
    return tuple(sorted(words))

@app.get('/')
def root():
    return FileResponse('/opt/offline-dictionary/static/index.html')

@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'Offline Dictionary', 'version': '1.4.5'}

@app.get('/api/lookup')
def lookup(q: str = Query(..., min_length=1)):
    word = q.strip().lower()
    synsets = wn.synsets(word)
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
