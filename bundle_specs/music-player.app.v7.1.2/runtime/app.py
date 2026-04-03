from __future__ import annotations

import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

APP_VERSION = "7.1.2"
MUSIC_ROOT = Path(os.getenv("MUSIC_ROOT", "/mnt/nas/media/music")).resolve()
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/music-player/data")).resolve()
PLAYLISTS_FILE = APP_DATA_DIR / "playlists.json"
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".oga"}
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|/|&| feat\.? | ft\.? | featuring )\s*", re.I)
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

HTML = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><title>Music Player</title>
<style>
:root{--bg:#0b0d12;--panel:#121722;--line:#242b3a;--text:#eef2ff;--muted:#9aa4bd;--accent:#7c9cff}
*{box-sizing:border-box} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text)}
.app{display:grid;grid-template-columns:260px 1fr;min-height:100vh;padding-bottom:96px}.sidebar{border-right:1px solid var(--line);padding:16px;background:rgba(18,23,34,.95);position:sticky;top:0;height:100vh;overflow:auto}
.brand{font-weight:800;font-size:24px;margin-bottom:12px}.navbtn{display:block;width:100%;text-align:left;padding:10px 12px;border-radius:12px;border:1px solid transparent;background:transparent;color:var(--text);cursor:pointer}.navbtn.active,.navbtn:hover{background:#1a2130;border-color:var(--line)}
.content{padding:18px;min-width:0}.top{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}.search{flex:1;min-width:220px;padding:12px 14px;border-radius:14px;border:1px solid var(--line);background:var(--panel);color:var(--text)}
.sectionTitle{font-size:22px;font-weight:800;margin:8px 0 14px}.list{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:var(--panel)}.row{display:grid;grid-template-columns:1fr auto;gap:12px;padding:10px 14px;border-top:1px solid var(--line);cursor:pointer;align-items:center}.row:first-child{border-top:none}.row:hover{background:#171e2b}
.meta{min-width:0}.title{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sub{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pill{font-size:12px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px;cursor:pointer}.player{position:fixed;left:0;right:0;bottom:0;height:96px;background:rgba(10,13,18,.96);backdrop-filter:blur(10px);border-top:1px solid var(--line);display:grid;grid-template-columns:1fr minmax(220px,520px) 180px;gap:14px;align-items:center;padding:10px 14px}
.np{min-width:0}.controls{display:flex;align-items:center;gap:10px;justify-content:center;flex-wrap:wrap}.bar{width:100%}button{background:#1b2331;border:1px solid var(--line);color:var(--text);padding:10px 12px;border-radius:12px;cursor:pointer}button:hover{filter:brightness(1.08)}.tiny{padding:7px 10px;font-size:12px}.sideSection{margin-top:18px}.miniList button{margin:4px 0;width:100%}.mobileOnly{display:none}.backdrop{display:none}
@media (max-width:900px){.app{grid-template-columns:1fr}.sidebar{display:block;position:fixed;inset:0 auto 96px 0;width:82vw;max-width:320px;z-index:30;transform:translateX(-100%);transition:transform .18s ease, box-shadow .18s ease;height:auto}.sidebar.open{transform:translateX(0);box-shadow:0 0 0 9999px rgba(2,6,14,.45)}.backdrop.open{display:block;position:fixed;inset:0 0 96px 0;z-index:25;background:rgba(2,6,14,.45)}.mobileOnly{display:inline-block}.player{grid-template-columns:1fr;height:auto;padding:10px 12px;gap:8px}}
</style></head><body>
<div id='backdrop' class='backdrop'></div><div class='app'><aside id='sidebar' class='sidebar'><div class='brand'>Music Player</div>
<button class='navbtn active' data-view='all'>All Songs</button><button class='navbtn' data-view='playlists'>Playlists</button><button class='navbtn' data-view='artists'>Artists</button><button class='navbtn' data-view='folders'>Folders</button>
<div class='sideSection'><div class='sub'>Saved playlists</div><div id='savedPlaylists' class='miniList'></div></div></aside>
<main class='content'><div class='top'><button id='menuBtn' class='mobileOnly'>☰</button><input id='search' class='search' placeholder='Search songs, artists, folders'><button id='shuffleAll' class='tiny'>Shuffle all</button><button id='refreshBtn' class='tiny'>Refresh</button></div>
<div id='home'></div><div class='sectionTitle' id='sectionTitle'>All Songs</div><div id='content'></div></main></div>
<div class='player'><div class='np'><div id='nowTitle' class='title'>Nothing playing</div><div id='nowArtist' class='sub'>Select a track</div></div><div class='controls'><button id='prevBtn'>⏮</button><button id='playBtn'>▶</button><button id='nextBtn'>⏭</button><button id='shuffleBtn'>🔀</button><button id='repeatBtn'>🔁 Off</button><input id='seek' class='bar' type='range' min='0' max='100' value='0'></div><div><audio id='audio' preload='metadata'></audio><div class='sub' id='timeLabel'>0:00 / 0:00</div></div></div>
<script>
const state={tracks:[],filtered:[],playlists:[],artists:[],folders:[],view:'all',current:null,queue:[],queueIndex:-1,shuffle:false,repeat:'off'};
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
function fmt(sec){if(!isFinite(sec)||sec<0)return '0:00'; const m=Math.floor(sec/60), s=Math.floor(sec%60).toString().padStart(2,'0'); return `${m}:${s}`;}
async function j(url,opts){const r=await fetch(url,opts); if(!r.ok) throw new Error(await r.text()); return r.json();}
function isMobile(){return window.innerWidth<=900;}
function openDrawer(){ if(!isMobile()) return; $('#sidebar').classList.add('open'); $('#backdrop').classList.add('open'); }
function closeDrawer(){ $('#sidebar').classList.remove('open'); $('#backdrop').classList.remove('open'); }
function toggleDrawer(){ if($('#sidebar').classList.contains('open')) closeDrawer(); else openDrawer(); }
function renderSavedPlaylists(){const host=$('#savedPlaylists'); host.innerHTML=''; state.playlists.forEach(p=>{const b=document.createElement('button'); b.className='navbtn'; b.textContent=`${p.name} (${p.count})`; b.onclick=()=>{ renderList(p.tracks.map(id=>state.tracks.find(t=>t.id===id)).filter(Boolean),p.name); closeDrawer(); }; host.appendChild(b);});}
function card(title,sub,onclick){const d=document.createElement('div'); d.className='card'; d.innerHTML=`<div class='title'>${title}</div><div class='sub'>${sub}</div>`; d.onclick=()=>{ onclick&&onclick(); closeDrawer(); }; return d;}
function row(track,index){const d=document.createElement('div'); d.className='row'; d.innerHTML=`<div class='meta'><div class='title'>${track.title}</div><div class='sub'>${track.artist} • ${track.folder||'Music'}</div></div><div class='pill'></div>`; d.onclick=()=>{ playFromList(state.filtered,index); closeDrawer(); }; return d;}
function renderHome(){const home=$('#home'); const cards=document.createElement('div'); cards.className='cards'; cards.append(card('All Songs',`${state.tracks.length} tracks`,()=>setView('all'))); cards.append(card('Playlists',`${state.playlists.length} playlists`,()=>setView('playlists'))); cards.append(card('Artists',`${state.artists.length} artists`,()=>setView('artists'))); cards.append(card('Folders',`${state.folders.length} folders`,()=>setView('folders'))); home.innerHTML=''; home.append(cards);}
function renderList(list,title='All Songs'){state.filtered=list; $('#sectionTitle').textContent=title; const c=$('#content'); const wrap=document.createElement('div'); wrap.className='list'; list.forEach((t,i)=>wrap.append(row(t,i))); c.innerHTML=''; c.append(wrap);}
function renderArtists(){const c=$('#content'); const cards=document.createElement('div'); cards.className='cards'; state.artists.forEach(a=>cards.append(card(a.name,`${a.count} songs`,()=>renderList(a.tracks.map(id=>state.tracks.find(t=>t.id===id)).filter(Boolean),a.name)))); c.innerHTML=''; c.append(cards); $('#sectionTitle').textContent='Artists';}
function renderFolders(){const c=$('#content'); const cards=document.createElement('div'); cards.className='cards'; state.folders.forEach(f=>cards.append(card(f.name,f.path,()=>renderList(state.tracks.filter(t=>t.folder===f.path),f.name)))); c.innerHTML=''; c.append(cards); $('#sectionTitle').textContent='Folders';}
function renderPlaylists(){const c=$('#content'); const cards=document.createElement('div'); cards.className='cards'; cards.append(card('+ New Playlist','Create from current filtered songs', async()=>{const name=prompt('Playlist name'); if(!name) return; await j('/api/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,track_ids:state.filtered.map(t=>t.id)})}); await load(); setView('playlists'); })); state.playlists.forEach(p=>cards.append(card(p.name,`${p.count} songs`,()=>renderList(p.tracks.map(id=>state.tracks.find(t=>t.id===id)).filter(Boolean),p.name)))); c.innerHTML=''; c.append(cards); $('#sectionTitle').textContent='Playlists';}
function setView(view){state.view=view; $$('.navbtn[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view)); if(view==='all') renderList(applySearch(state.tracks)); else if(view==='artists') renderArtists(); else if(view==='folders') renderFolders(); else renderPlaylists(); closeDrawer();}
function applySearch(list){const q=$('#search').value.trim().toLowerCase(); if(!q) return list; return list.filter(t=>[t.title,t.artist,t.folder,t.filename].join(' ').toLowerCase().includes(q));}
function playFromList(list,idx){state.queue=list.slice(); state.queueIndex=idx; playTrack(state.queue[idx]);}
function playTrack(track){state.current=track; $('#nowTitle').textContent=track.title; $('#nowArtist').textContent=track.artist; const a=$('#audio'); a.src=track.stream_url; a.play().catch(()=>{}); $('#playBtn').textContent='⏸';}
function nextTrack(){if(!state.queue.length) return; if(state.repeat==='one' && state.current) return playTrack(state.current); if(state.shuffle) state.queueIndex=Math.floor(Math.random()*state.queue.length); else state.queueIndex=(state.queueIndex+1)%state.queue.length; if(state.repeat==='off' && state.queueIndex===0 && !state.shuffle){state.queue=state.tracks.slice(); state.queueIndex=Math.floor(Math.random()*Math.max(state.queue.length,1));} playTrack(state.queue[state.queueIndex]);}
function prevTrack(){if(!state.queue.length) return; state.queueIndex=state.queueIndex<=0?0:state.queueIndex-1; playTrack(state.queue[state.queueIndex]);}
async function load(){const data=await j('/api/library'); state.tracks=data.tracks; state.playlists=data.playlists; state.artists=data.artist_playlists; state.folders=data.folders; renderHome(); renderSavedPlaylists(); setView(state.view||'all');}
$('#search').oninput=()=>{if(state.view==='all') renderList(applySearch(state.tracks));}; $('#menuBtn').onclick=(e)=>{ e.stopPropagation(); toggleDrawer(); }; $('#backdrop').onclick=()=>closeDrawer(); $('#refreshBtn').onclick=()=>load(); $('#shuffleAll').onclick=()=>{state.shuffle=true; $('#shuffleBtn').style.outline='2px solid var(--accent)'; const arr=state.tracks.slice().sort(()=>Math.random()-0.5); playFromList(arr,0); closeDrawer();}; $('#playBtn').onclick=()=>{const a=$('#audio'); if(!a.src && state.tracks.length) return playFromList(state.tracks,0); if(a.paused){a.play(); $('#playBtn').textContent='⏸';} else {a.pause(); $('#playBtn').textContent='▶';}}; $('#nextBtn').onclick=nextTrack; $('#prevBtn').onclick=prevTrack; $('#shuffleBtn').onclick=()=>{state.shuffle=!state.shuffle; $('#shuffleBtn').style.outline=state.shuffle?'2px solid var(--accent)':'none';}; $('#repeatBtn').onclick=()=>{state.repeat=state.repeat==='off'?'all':state.repeat==='all'?'one':'off'; $('#repeatBtn').textContent='🔁 '+(state.repeat==='all'?'All':state.repeat==='one'?'One':'Off');}; $('#audio').addEventListener('timeupdate',()=>{const a=$('#audio'); const p=(a.currentTime/(a.duration||1))*100; $('#seek').value=isFinite(p)?p:0; $('#timeLabel').textContent=`${fmt(a.currentTime)} / ${fmt(a.duration)}`;}); $('#audio').addEventListener('ended',nextTrack); $('#seek').addEventListener('input',e=>{const a=$('#audio'); if(isFinite(a.duration)) a.currentTime=(e.target.value/100)*a.duration;}); document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeDrawer(); }); window.addEventListener('resize',()=>{ if(!isMobile()) closeDrawer(); }); load();
</script></body></html>"""


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_filename(name: str):
    base = Path(name).stem
    base = re.sub(r"[_\.]+", " ", base)
    base = normalize_spaces(base)
    if " - " in base:
        title, artists_raw = base.split(" - ", 1)
        artists = [normalize_spaces(x) for x in ARTIST_SPLIT_RE.split(artists_raw) if normalize_spaces(x)]
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
            tracks.append({
                'id': track_id,
                'path': rel,
                'title': title,
                'artist': ', '.join(artists) if artists else 'Unknown Artist',
                'artists': artists,
                'folder': '' if str(Path(rel).parent) == '.' else str(Path(rel).parent),
                'filename': p.name,
                'duration': None,
                'stream_url': '/api/stream/' + rel,
            })
    return tracks


def auto_artist_playlists(tracks):
    out = {}
    for t in tracks:
        for a in (t.get('artists') or []):
            out.setdefault(a, []).append(t['id'])
    return [{'name': k, 'tracks': v, 'count': len(v)} for k, v in sorted(out.items())]


def folders_tree(tracks):
    seen = sorted({t['folder'] for t in tracks if t['folder']})
    return [{'path': f, 'name': Path(f).name} for f in seen]


class Handler(BaseHTTPRequestHandler):
    server_version = 'MusicPlayer/7.1.2'

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
            return self._html(HTML)
        if path == '/api/health':
            return self._json({'status': 'ok', 'version': APP_VERSION})
        if path == '/api/library':
            tracks = scan_tracks()
            playlists = [{'name': k, 'tracks': v, 'count': len(v)} for k, v in read_playlists().items()]
            return self._json({'tracks': tracks, 'playlists': playlists, 'artist_playlists': auto_artist_playlists(tracks), 'folders': folders_tree(tracks)})
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
            data[name] = list(dict.fromkeys(track_ids))
            write_playlists(data)
            return self._json({'ok': True, 'name': name, 'count': len(data[name])})
        return self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8140'))
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'Music Player listening on {port}', flush=True)
    server.serve_forever()
