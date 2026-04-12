from __future__ import annotations

import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

APP_VERSION = os.getenv("APP_VERSION", "7.1.2")
APP_NAME = os.getenv("APP_NAME", "Music Player")
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS = {"chorus", "others", "other", "music"}
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def render_index_html() -> str:
    template = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return (
        template
        .replace("{{APP_NAME}}", APP_NAME)
        .replace("{{APP_NAME_JSON}}", json.dumps(APP_NAME))
        .replace("{{APP_VERSION_JSON}}", json.dumps(APP_VERSION))
    )


def serve_static_asset(handler: "Handler", relative_path: str) -> bool:
    asset = (STATIC_DIR / relative_path).resolve()
    if not asset.exists() or not asset.is_file() or (STATIC_DIR.resolve() != asset and STATIC_DIR.resolve() not in asset.parents):
        return False
    content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    data = asset.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"



def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_filename(name: str):
    base = Path(name).stem
    base = re.sub(r"[_\.]+", " ", base)
    base = normalize_spaces(base)
    if " - " in base:
        title, artists_raw = base.split(" - ", 1)
        artists = []
        for x in ARTIST_SPLIT_RE.split(artists_raw):
            item = normalize_spaces(x)
            if item and item.lower() not in IGNORE_ARTISTS:
                artists.append(item)
        if artists:
            return title.strip(), artists
    return base, []


def read_playlists():
    if PLAYLISTS_FILE.exists():
        try:
            data = json.loads(PLAYLISTS_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return {str(k): [str(x) for x in v or []] for k, v in data.items()}
        except Exception:
            pass
    return {}


def write_playlists(data):
    PLAYLISTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def scan_tracks():
    tracks = []
    if not MUSIC_ROOT.exists():
        return tracks
    for p in sorted(MUSIC_ROOT.rglob('*')):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = p.relative_to(MUSIC_ROOT).as_posix()
            title, artists = parse_filename(p.name)
            track_id = str(abs(hash(rel)))
            tracks.append({'id': track_id, 'path': rel, 'title': title, 'artist': ', '.join(artists) if artists else 'Unknown Artist', 'artists': artists, 'folder': '' if str(Path(rel).parent) == '.' else str(Path(rel).parent), 'filename': p.name, 'duration': None, 'stream_url': '/api/stream/' + rel})
    return tracks


def auto_artist_playlists(tracks):
    out = {}
    for t in tracks:
        for a in (t.get('artists') or []):
            key = (a or '').strip()
            if not key or key.lower() in IGNORE_ARTISTS:
                continue
            out.setdefault(key, []).append(t['id'])
    return [{'name': k, 'tracks': v, 'count': len(v)} for k, v in sorted(out.items())]


def folders_tree(tracks):
    seen = sorted({t['folder'] for t in tracks if t['folder']})
    return [{'path': f, 'name': Path(f).name} for f in seen]


class Handler(BaseHTTPRequestHandler):
    server_version = 'MusicPlayer/' + APP_VERSION

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def _json(self, payload, code=200):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text, code=200):
        data = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ['/', '/index.html']:
            return self._html(render_index_html())
        if path.startswith('/static/'):
            relative_path = path[len('/static/'):].lstrip('/')
            if serve_static_asset(self, relative_path):
                return
            return self._json({'error': 'not found'}, 404)
        if path == '/api/health':
            return self._json({'status': 'ok', 'version': APP_VERSION, 'name': APP_NAME})
        if path == '/api/library':
            tracks = scan_tracks()
            playlists = [{'name': k, 'tracks': v, 'count': len(v)} for k, v in read_playlists().items()]
            return self._json({'tracks': tracks, 'playlists': playlists, 'artist_playlists': auto_artist_playlists(tracks), 'folders': folders_tree(tracks), 'name': APP_NAME, 'version': APP_VERSION})
        if path.startswith('/api/stream/'):
            rel = unquote(path[len('/api/stream/'):])
            target = (MUSIC_ROOT / rel).resolve()
            if not target.exists() or not target.is_file() or (MUSIC_ROOT != target and MUSIC_ROOT not in target.parents):
                return self._json({'error': 'not found'}, 404)
            ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            size = target.stat().st_size
            rng = self.headers.get('Range')
            if rng and rng.startswith('bytes='):
                spec = rng.split('=', 1)[1]
                first, _, last = spec.partition('-')
                start = int(first) if first else 0
                end = int(last) if last else size - 1
                end = min(end, size - 1)
                if start > end:
                    start, end = 0, size - 1
                length = end - start + 1
                self.send_response(206)
                self.send_header('Content-Type', ctype)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length', str(length))
                self.end_headers()
                with target.open('rb') as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
                return
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            with target.open('rb') as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/playlists':
            length = int(self.headers.get('Content-Length', '0') or '0')
            body = self.rfile.read(length) if length else b'{}'
            payload = json.loads(body.decode('utf-8'))
            name = str(payload.get('name', '')).strip()
            track_ids = [str(x) for x in payload.get('track_ids', [])]
            if not name:
                return self._json({'error': 'playlist name required'}, 400)
            data = read_playlists()
            existing = data.get(name, [])
            data[name] = list(dict.fromkeys(existing + track_ids))
            write_playlists(data)
            return self._json({'ok': True, 'name': name, 'count': len(data[name])})
        return self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8140'))
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'{APP_NAME} listening on {port}', flush=True)
    server.serve_forever()
