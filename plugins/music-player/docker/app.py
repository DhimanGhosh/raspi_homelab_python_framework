from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import urlopen

from mutagen import File as MutagenFile
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, USLT, TIT2, TALB, TPE1, TDRC

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT.parent
PLUGIN_JSON = PLUGIN_DIR / 'plugin.json'
PLUGIN_META = json.loads(PLUGIN_JSON.read_text(encoding='utf-8')) if PLUGIN_JSON.exists() else {}
APP_VERSION = str(PLUGIN_META.get('version') or os.getenv('APP_VERSION', '7.3.4'))
APP_NAME = str(PLUGIN_META.get('name') or os.getenv('APP_NAME', 'Music Player'))
MUSIC_ROOT = Path(os.getenv('MUSIC_ROOT', '/mnt/nas/media/music')).resolve()
APP_DATA_DIR = Path(os.getenv('APP_DATA_DIR', '/mnt/nas/homelab/runtime/music-player/data')).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / 'playlists.json'
PLAYLISTS_CANDIDATES = [
    PLAYLISTS_FILE,
    APP_DATA_DIR.parent / 'playlists.json',
    Path('/mnt/nas/homelab/runtime/music-player/playlists.json').resolve(),
    Path('/mnt/nas/homelab/runtime/music-player/data/playlists.json').resolve(),
    Path('/home/pi/homelab_os/runtime/installed_plugins/music-player/data/playlists.json').resolve(),
]
SUPPORTED_EXTENSIONS = {'.mp3', '.flac', '.wav', '.m4a', '.aac', '.ogg', '.opus', '.webm', '.oga'}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|，|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
IGNORE_ARTISTS = {'chorus', 'others', 'other', 'music'}
TEMPLATES_DIR = ROOT / 'templates'
STATIC_DIR = ROOT / 'static'
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def normalize_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text or '').replace('，', ',')).strip()


def split_artists(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        raw = ', '.join(str(x) for x in value if x)
    else:
        raw = str(value or '')
    artists: list[str] = []
    for chunk in ARTIST_SPLIT_RE.split(raw):
        item = normalize_spaces(chunk)
        if item and item.lower() not in IGNORE_ARTISTS and item not in artists:
            artists.append(item)
    return artists


def stable_track_id(rel_path: str) -> str:
    import hashlib
    return hashlib.sha1(rel_path.encode('utf-8')).hexdigest()[:16]


def parse_filename(name: str) -> tuple[str, str, list[str]]:
    stem = Path(name).stem
    stem = normalize_spaces(stem.replace('，', ',').replace('–', '-').replace('—', '-'))
    parts = [normalize_spaces(p) for p in stem.split(' - ') if normalize_spaces(p)]
    if len(parts) >= 3:
        return parts[0], parts[-2] or 'Unknown', split_artists(parts[-1])
    if len(parts) == 2:
        return parts[0], 'Unknown', split_artists(parts[1])
    return stem, 'Unknown', []


def choose_title(file_title: str, tag_title: str, artists: list[str]) -> str:
    file_title = normalize_spaces(file_title)
    tag_title = normalize_spaces(tag_title)
    if not tag_title:
        return file_title
    noisy_markers = ['|', 'official', 'audio', 'video', 'lyrics', 'lyrical', 't-series']
    if any(marker in tag_title.lower() for marker in noisy_markers):
        return file_title or tag_title
    lowered = tag_title.lower()
    if artists and any(a and a.lower() in lowered for a in artists):
        return file_title or tag_title
    if len(tag_title) > max(48, len(file_title) + 12):
        return file_title or tag_title
    return tag_title


def read_playlists() -> dict[str, list[str]]:
    for candidate in PLAYLISTS_CANDIDATES:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return {str(k): [str(x) for x in (v or [])] for k, v in data.items()}
            except Exception:
                continue
    return {}


def write_playlists(data: dict[str, list[str]]) -> None:
    PLAYLISTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def extract_embedded_lyrics(path: Path) -> str:
    try:
        tags = ID3(path)
        for key in tags.keys():
            if key.startswith('USLT'):
                text = normalize_spaces(getattr(tags[key], 'text', ''))
                if text:
                    return text
    except Exception:
        pass
    lrc = path.with_suffix('.lrc')
    if lrc.exists():
        try:
            content = lrc.read_text(encoding='utf-8', errors='ignore')
            content = re.sub(r'\[[^\]]+\]', '', content)
            return normalize_spaces(content)
        except Exception:
            return ''
    return ''


def extract_album_art_data_uri(path: Path) -> str:
    try:
        tags = ID3(path)
        for key in tags.keys():
            if key.startswith('APIC'):
                frame = tags[key]
                mime = getattr(frame, 'mime', 'image/jpeg') or 'image/jpeg'
                data = base64.b64encode(frame.data).decode('ascii')
                return f'data:{mime};base64,{data}'
    except Exception:
        pass
    return ''


def read_track_metadata(path: Path) -> dict:
    file_title, file_album, file_artists = parse_filename(path.name)
    title = file_title
    album = file_album or 'Unknown'
    artists = file_artists[:]
    duration = 0
    lyrics = ''
    album_art = ''
    year = ''
    try:
        audio = MutagenFile(path)
        if audio is not None:
            duration = int(getattr(getattr(audio, 'info', None), 'length', 0) or 0)
            tags = getattr(audio, 'tags', None)
            if tags:
                def first(keys: list[str]) -> str:
                    for key in keys:
                        if key in tags:
                            value = tags.get(key)
                            if isinstance(value, list) and value:
                                return str(value[0])
                            text = getattr(value, 'text', None)
                            if isinstance(text, list) and text:
                                return str(text[0])
                            if text:
                                return str(text)
                            if value:
                                return str(value)
                    return ''
                tag_title = normalize_spaces(first(['TIT2', 'title', 'TITLE']))
                tag_album = normalize_spaces(first(['TALB', 'album', 'ALBUM']))
                tag_artist = normalize_spaces(first(['TPE1', 'artist', 'ARTIST']))
                tag_year = normalize_spaces(first(['TDRC', 'TYER', 'date', 'DATE', 'year', 'YEAR']))
                if tag_title:
                    title = choose_title(file_title, tag_title, artists or file_artists)
                if tag_album:
                    album = tag_album
                if tag_artist:
                    artists = split_artists(tag_artist) or artists
                if tag_year:
                    m = re.search(r'(19|20)\d{2}', tag_year)
                    if m:
                        year = m.group(0)
    except Exception:
        pass
    lyrics = extract_embedded_lyrics(path)
    album_art = extract_album_art_data_uri(path)
    if not artists:
        artists = ['Unknown Artist']
    return {'title': title, 'album': album or 'Unknown', 'artists': artists, 'artist': ', '.join(artists), 'duration': duration, 'lyrics': lyrics, 'album_art': album_art, 'year': year}


def scan_tracks() -> list[dict]:
    tracks: list[dict] = []
    if not MUSIC_ROOT.exists():
        return tracks
    for path in sorted(MUSIC_ROOT.rglob('*')):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            rel = path.relative_to(MUSIC_ROOT).as_posix()
            meta = read_track_metadata(path)
            tracks.append({'id': stable_track_id(rel), 'path': rel, 'title': meta['title'], 'album': meta['album'], 'artist': meta['artist'], 'artists': meta['artists'], 'duration': meta['duration'], 'lyrics': meta['lyrics'], 'album_art': meta['album_art'], 'year': meta.get('year', ''), 'folder': '' if str(Path(rel).parent)=='.' else str(Path(rel).parent), 'filename': path.name, 'stream_url': '/api/stream/' + rel})
    tracks.sort(key=lambda x: (x['title'].lower(), x['artist'].lower(), x['path'].lower()))
    return tracks


def library_payload() -> dict:
    tracks = scan_tracks()
    track_map = {t['id']: t for t in tracks}
    playlists = []
    legacy_unresolved = False
    path_to_id = {t['path']: t['id'] for t in tracks}
    filename_to_ids: dict[str, list[str]] = {}
    title_artist_to_id: dict[tuple[str, str], str] = {}
    for t in tracks:
        filename_to_ids.setdefault(t['filename'], []).append(t['id'])
        title_artist_to_id[(normalize_spaces(t['title']).lower(), normalize_spaces(t['artist']).lower())] = t['id']

    def resolve_playlist_item(item) -> str | None:
        if item is None:
            return None
        if isinstance(item, str):
            if item in track_map:
                return item
            if item in path_to_id:
                return path_to_id[item]
            name = Path(item).name
            ids = filename_to_ids.get(name)
            if ids:
                return ids[0]
            parsed_title, _, parsed_artists = parse_filename(name)
            key = (normalize_spaces(parsed_title).lower(), normalize_spaces(', '.join(parsed_artists)).lower())
            return title_artist_to_id.get(key)
        if isinstance(item, dict):
            path = str(item.get('path') or item.get('file') or '')
            if path and path in path_to_id:
                return path_to_id[path]
            fname = str(item.get('filename') or Path(path).name or '')
            ids = filename_to_ids.get(fname)
            if ids:
                return ids[0]
            title = normalize_spaces(item.get('title') or '')
            artist = normalize_spaces(item.get('artist') or item.get('artists') or '')
            if title and artist:
                return title_artist_to_id.get((title.lower(), artist.lower()))
        return None

    for name, raw_ids in sorted(read_playlists().items()):
        resolved_ids = []
        for entry in raw_ids:
            resolved = resolve_playlist_item(entry)
            if resolved and resolved not in resolved_ids:
                resolved_ids.append(resolved)
        unresolved = len(raw_ids) - len(resolved_ids)
        if unresolved:
            legacy_unresolved = True
        playlists.append({'name': name, 'tracks': resolved_ids, 'count': len(resolved_ids), 'stored_count': len(raw_ids), 'unresolved_count': unresolved})

    artist_map: dict[str, list[str]] = {}
    folder_map: dict[str, list[str]] = {}
    album_map: dict[str, list[str]] = {}
    release_year_map: dict[str, list[str]] = {}
    from datetime import datetime
    current_year = datetime.now().year
    def year_bin_label(year_str: str) -> str:
        if not year_str:
            return 'Unknown'
        try:
            y = int(year_str)
        except Exception:
            return 'Unknown'
        start = (y // 10) * 10
        end = min(start + 9, current_year)
        return f'{start}-{end}'
    for track in tracks:
        for artist in (track.get('artists') or ['Unknown Artist']):
            artist_map.setdefault(artist.strip(), []).append(track['id'])
        folder_map.setdefault(track['folder'] or 'Root', []).append(track['id'])
        album_map.setdefault(track.get('album') or 'Unknown', []).append(track['id'])
        release_year_map.setdefault(year_bin_label(track.get('year', '')), []).append(track['id'])
    return {
        'tracks': tracks,
        'playlists': playlists,
        'artists': [{'name': k, 'tracks': v, 'count': len(v)} for k,v in sorted(artist_map.items())],
        'folders': [{'name': k, 'tracks': v, 'count': len(v)} for k,v in sorted(folder_map.items())],
        'albums': [{'name': k, 'tracks': v, 'count': len(v)} for k,v in sorted(album_map.items())],
        'release_years': [{'name': k, 'tracks': v, 'count': len(v)} for k,v in sorted(release_year_map.items())],
        'name': APP_NAME,
        'version': APP_VERSION,
        'playlist_note': 'Some older playlists may have unresolved legacy entries from a previous broken ID format.' if legacy_unresolved else ''
    }


def resolve_target(rel: str) -> Path | None:
    target = (MUSIC_ROOT / rel).resolve()
    if not target.exists() or not target.is_file():
        return None
    if MUSIC_ROOT != target and MUSIC_ROOT not in target.parents:
        return None
    return target


def safe_filename(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    idx = 1
    while True:
        option = target_dir / f'{stem} ({idx}){suffix}'
        if not option.exists():
            return option
        idx += 1


def move_tracks_to_folder(track_ids: list[str], folder_name: str) -> int:
    folder_name = normalize_spaces(folder_name).strip('/\\')
    if not folder_name:
        raise ValueError('folder name required')
    target_dir = (MUSIC_ROOT / folder_name).resolve()
    if MUSIC_ROOT != target_dir and MUSIC_ROOT not in target_dir.parents:
        raise ValueError('invalid folder target')
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for track in scan_tracks():
        if track['id'] not in track_ids:
            continue
        source = resolve_target(track['path'])
        if source is None:
            continue
        dest = safe_filename(target_dir, source.name)
        shutil.move(str(source), str(dest))
        moved += 1
    return moved


def update_track_metadata(rel_path: str, title: str, artist: str, album: str, year: str, lyrics: str, album_art_url: str) -> None:
    target = resolve_target(rel_path)
    if target is None:
        raise FileNotFoundError('track not found')
    try:
        tags = ID3(target)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall('TIT2'); tags.delall('TPE1'); tags.delall('TALB'); tags.delall('TDRC'); tags.delall('USLT'); tags.delall('APIC')
    if title.strip(): tags.add(TIT2(encoding=3, text=title.strip()))
    if artist.strip(): tags.add(TPE1(encoding=3, text=artist.strip()))
    if album.strip(): tags.add(TALB(encoding=3, text=album.strip()))
    if year.strip(): tags.add(TDRC(encoding=3, text=year.strip()))
    if lyrics.strip(): tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics.strip()))
    if album_art_url.strip():
        with urlopen(album_art_url.strip(), timeout=20) as resp:
            data = resp.read()
            ctype = resp.headers.get_content_type() or 'image/jpeg'
        tags.add(APIC(encoding=3, mime=ctype, type=3, desc='Cover', data=data))
    tags.save(target)


class Handler(BaseHTTPRequestHandler):
    server_version = 'MusicPlayer/' + APP_VERSION
    def end_headers(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()
    def _json(self, payload, code: int = 200, include_body: bool = True) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)
    def _text(self, text: str, code: int = 200, ctype: str = 'text/plain; charset=utf-8', include_body: bool = True) -> None:
        data = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,HEAD,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Range')
        self.end_headers()
    def do_HEAD(self) -> None: self._handle_request(head_only=True)
    def do_GET(self) -> None: self._handle_request(head_only=False)
    def do_POST(self) -> None: self._handle_post()
    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')
    def _serve_static(self, rel_path: str, head_only: bool) -> None:
        rel = rel_path.lstrip('/')
        target = (STATIC_DIR / rel).resolve()
        if not target.exists() or not target.is_file() or STATIC_DIR not in target.parents:
            return self._json({'error': 'not found'}, 404, include_body=not head_only)
        ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)
    def _handle_request(self, head_only: bool) -> None:
        path = urlparse(self.path).path
        if path in ['/', '/index.html']:
            return self._text(read_text(TEMPLATES_DIR / 'index.html'), ctype='text/html; charset=utf-8', include_body=not head_only)
        if path.startswith('/static/'):
            return self._serve_static(path[len('/static/'):], head_only=head_only)
        if path == '/favicon.ico':
            self.send_response(204); self.end_headers(); return
        if path == '/api/health':
            return self._json({'status':'ok','version':APP_VERSION,'name':APP_NAME}, include_body=not head_only)
        if path == '/api/library':
            return self._json(library_payload(), include_body=not head_only)
        if path.startswith('/api/stream/'):
            rel = unquote(path[len('/api/stream/'):])
            target = resolve_target(rel)
            if target is None:
                return self._json({'error':'not found'},404,include_body=not head_only)
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return
        return self._json({'error':'not found'},404,include_body=not head_only)
    def _handle_post(self) -> None:
        path = urlparse(self.path).path
        payload = self._read_json()
        playlists = read_playlists()
        if path == '/api/playlists':
            name = normalize_spaces(payload.get('name',''))
            ids = [str(x) for x in (payload.get('track_ids') or [])]
            if not name or not ids: return self._json({'error':'invalid request'},400)
            playlists.setdefault(name, [])
            for tid in ids:
                if tid not in playlists[name]: playlists[name].append(tid)
            write_playlists(playlists); return self._json({'ok':True})
        if path == '/api/playlists/rename':
            old_name = normalize_spaces(payload.get('old_name','')); new_name = normalize_spaces(payload.get('new_name',''))
            if not old_name or not new_name or old_name not in playlists: return self._json({'error':'invalid request'},400)
            playlists[new_name] = playlists.pop(old_name); write_playlists(playlists); return self._json({'ok':True})
        if path == '/api/playlists/delete':
            name = normalize_spaces(payload.get('name',''))
            if not name or name not in playlists: return self._json({'error':'invalid request'},400)
            playlists.pop(name,None); write_playlists(playlists); return self._json({'ok':True})
        if path == '/api/folders/create':
            name = normalize_spaces(payload.get('name','')).strip('/\\')
            if not name: return self._json({'error':'invalid request'},400)
            (MUSIC_ROOT / name).resolve().mkdir(parents=True, exist_ok=True); return self._json({'ok':True})
        if path == '/api/folders/add':
            name = normalize_spaces(payload.get('name','')); ids = [str(x) for x in (payload.get('track_ids') or [])]
            if not name or not ids: return self._json({'error':'invalid request'},400)
            moved = move_tracks_to_folder(ids, name); return self._json({'ok':True,'moved':moved})
        if path == '/api/metadata/update':
            rel_path = str(payload.get('path') or '')
            if not rel_path: return self._json({'error':'path required'},400)
            try:
                update_track_metadata(rel_path, str(payload.get('title') or ''), str(payload.get('artist') or ''), str(payload.get('album') or ''), str(payload.get('year') or ''), str(payload.get('lyrics') or ''), str(payload.get('album_art_url') or ''))
                return self._json({'ok':True})
            except Exception as exc:
                return self._json({'error':str(exc)},400)
        return self._json({'error':'not found'},404)


def main() -> None:
    port = int(os.getenv('PORT', '8140'))
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'{APP_NAME} {APP_VERSION} listening on :{port}')
    server.serve_forever()


if __name__ == '__main__':
    main()
