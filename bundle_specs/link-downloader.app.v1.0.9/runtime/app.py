from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, quote, parse_qs

APP_DATA_DIR = Path(os.environ.get('APP_DATA_DIR', '/data'))
DOWNLOAD_ROOT = Path(os.environ.get('DOWNLOAD_ROOT', str(APP_DATA_DIR / 'downloads')))
CACHE_DIR = Path(os.environ.get('YTDLP_CACHE_DIR', str(APP_DATA_DIR / 'cache' / 'yt-dlp')))
PORT = int(os.environ.get('PORT', '8160'))
HOST = '0.0.0.0'
ALLOWED_SAVE_ROOTS = [Path(p) for p in os.environ.get('ALLOWED_SAVE_ROOTS', '/mnt/nas:/data').split(':') if p]
DEFAULT_EXTERNAL_SAVE_DIR = os.environ.get('DEFAULT_EXTERNAL_SAVE_DIR', '/mnt/nas/media/music')
HOST_DOWNLOAD_ROOT = os.environ.get('HOST_DOWNLOAD_ROOT', '/mnt/nas/homelab/runtime/link-downloader/data/downloads')
UPLOAD_ROOT = APP_DATA_DIR / 'uploads'
CONVERTED_ROOT = APP_DATA_DIR / 'converted'
STATIC_DIR = Path(__file__).resolve().parent / 'static'

for p in [DOWNLOAD_ROOT, UPLOAD_ROOT, CONVERTED_ROOT, CACHE_DIR]:
    p.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def which(cmd: str):
    return shutil.which(cmd)


def tool_status() -> dict:
    yt = which('yt-dlp')
    ff = which('ffmpeg')
    errors = []
    if not yt:
        errors.append('yt-dlp missing')
    if not ff:
        errors.append('ffmpeg missing')
    return {
        'yt_dlp_ready': yt is not None,
        'ffmpeg_ready': ff is not None,
        'installing': False,
        'message': 'yt-dlp + ffmpeg ready' if yt and ff else (' · '.join(errors) or 'Ready'),
        'errors': errors,
    }


def device_hint(user_agent: str) -> str:
    ua = (user_agent or '').lower()
    if 'iphone' in ua or 'ipad' in ua or 'ios' in ua:
        return 'On iPhone/iPad, “Download to this device” usually saves into Safari downloads or the Files app.'
    if 'android' in ua:
        return 'On Android, “Download to this device” usually saves into Downloads unless your browser asks where to save.'
    return 'On desktop browsers, “Download to this device” usually saves into Downloads unless your browser is set to ask every time.'


def now():
    return time.time()


def new_job(kind: str, payload: dict):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            'id': job_id,
            'kind': kind,
            'status': 'queued',
            'progress': 0.0,
            'message': 'Queued',
            'created_at': now(),
            'updated_at': now(),
            'payload': payload,
            'output_path': None,
            'output_name': None,
            'output_relative': None,
            'log': [],
        }
    return job_id


def update_job(job_id: str, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        log_line = fields.pop('log_line', None)
        job.update(fields)
        job['updated_at'] = now()
        if log_line:
            job.setdefault('log', []).append(log_line)
            if len(job['log']) > 120:
                job['log'] = job['log'][-120:]


def clear_finished_jobs():
    with JOBS_LOCK:
        remove_ids = [jid for jid, job in JOBS.items() if job.get('status') in ('completed', 'failed')]
        for jid in remove_ids:
            JOBS.pop(jid, None)
        return len(remove_ids)


def safe_name(text: str) -> str:
    text = re.sub(r'[^A-Za-z0-9._ -]+', '_', str(text)).strip().strip('.')
    text = re.sub(r'\s+', '_', text)
    return text[:180] or 'download'


def list_saved_files():
    items = []
    for root in [DOWNLOAD_ROOT, CONVERTED_ROOT, UPLOAD_ROOT]:
        if not root.exists():
            continue
        for path in sorted([p for p in root.rglob('*') if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True):
            rel = path.relative_to(APP_DATA_DIR).as_posix()
            ext = path.suffix.lower()
            if ext in ('.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg'):
                kind = 'audio'
            elif ext in ('.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'):
                kind = 'video'
            else:
                kind = 'file'
            items.append({
                'name': path.name,
                'kind': kind,
                'relative_path': rel,
                'full_path': str(path),
                'size_bytes': path.stat().st_size,
                'modified_at': path.stat().st_mtime,
                'browser_open_url': '/open/' + '/'.join(quote(part) for part in rel.split('/')),
                'download_url': '/downloaded/' + '/'.join(quote(part) for part in rel.split('/')),
            })
    return items


def is_direct_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    direct_exts = ('.mp4', '.mp3', '.m4a', '.mkv', '.webm', '.mov', '.avi', '.wav', '.flac', '.aac', '.jpg', '.jpeg', '.png', '.pdf', '.zip', '.rar', '.7z')
    return path.endswith(direct_exts)


def pick_latest_file(folder: Path, started_at: float) -> Path | None:
    candidates = [p for p in folder.rglob('*') if p.is_file() and p.stat().st_mtime >= started_at - 2]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def resolve_saved_file(relative_path: str) -> Path:
    rel = relative_path.strip().lstrip('/')
    path = (APP_DATA_DIR / rel).resolve()
    base = APP_DATA_DIR.resolve()
    if base not in path.parents and path != base:
        raise ValueError('relative_path must point to a file inside app data')
    if not path.exists() or not path.is_file():
        raise FileNotFoundError('selected source file does not exist')
    return path


def ensure_allowed_destination(dest_dir: str) -> Path:
    if not dest_dir:
        raise ValueError('destination_path is required')
    dest = Path(dest_dir).expanduser()
    if not dest.is_absolute():
        raise ValueError('destination_path must be an absolute path')
    resolved = dest.resolve()
    allowed = []
    for root in ALLOWED_SAVE_ROOTS:
        root_resolved = root.resolve()
        allowed.append(str(root_resolved))
        if resolved == root_resolved or root_resolved in resolved.parents:
            resolved.mkdir(parents=True, exist_ok=True)
            return resolved
    raise ValueError('destination_path must stay inside one of: ' + ', '.join(allowed))


def build_target_name(source: Path, new_name: str) -> str:
    name = (new_name or '').strip()
    if not name:
        return source.name
    cleaned = safe_name(name)
    if '.' not in cleaned and source.suffix:
        cleaned += source.suffix
    return cleaned


def reserve_target(dest_dir: Path, target_name: str) -> Path:
    target = dest_dir / target_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    idx = 1
    while True:
        candidate = dest_dir / f'{stem}_{idx}{suffix}'
        if not candidate.exists():
            return candidate
        idx += 1


def serve_file_bytes(url: str, target_dir: Path, job_id: str):
    from urllib.request import Request, urlopen
    parsed = urlparse(url)
    filename = safe_name(Path(unquote(parsed.path)).name or f'{job_id}.bin')
    target_dir.mkdir(parents=True, exist_ok=True)
    target = reserve_target(target_dir, filename)
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req) as resp, open(target, 'wb') as out:
        total = int(resp.headers.get('Content-Length', '0') or '0')
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            pct = round((downloaded / total) * 100, 2) if total else 0
            update_job(job_id, status='downloading', progress=min(pct, 99.0), message=f'Downloading direct file… {pct:.2f}%' if total else 'Downloading direct file…')
    return target


def finalize_job_file(job_id: str, output: Path, message='Completed'):
    rel = output.relative_to(APP_DATA_DIR).as_posix()
    update_job(job_id, status='completed', progress=100, message=message, output_path=str(output), output_name=output.name, output_relative=rel, log_line=f'Saved to {output}')


def run_ytdlp(job_id: str, url: str, mode: str, audio_format: str):
    import yt_dlp
    target_dir = DOWNLOAD_ROOT / ('audio' if mode == 'audio' else 'video')
    target_dir.mkdir(parents=True, exist_ok=True)
    started_at = now()
    update_job(job_id, status='starting', progress=1, message='Starting yt-dlp…')
    error_messages = []
    try:
        def hook(d):
            status = d.get('status')
            if status == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                done = d.get('downloaded_bytes') or 0
                pct = (done / total * 100) if total else 0
                msg = d.get('_percent_str', '').strip() or f'Downloading… {pct:.1f}%'
                update_job(job_id, status='downloading', progress=min(pct, 99.0), message=msg)
            elif status == 'finished':
                filename = d.get('filename')
                update_job(job_id, status='processing', progress=99.0, message='Post-processing…', log_line=f'Finished download: {filename}')
        ydl_opts = {
            'paths': {'home': str(target_dir)},
            'outtmpl': {'default': '%(title).150B [%(id)s].%(ext)s'},
            'restrictfilenames': True,
            'noplaylist': True,
            'cachedir': str(CACHE_DIR),
            'progress_hooks': [hook],
            'quiet': True,
            'no_warnings': True,
            'windowsfilenames': False,
            'consoletitle': False,
        }
        if mode == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format or 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({'format': 'bv*+ba/b', 'merge_output_format': 'mp4'})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        error_messages.append(str(exc))
    output = pick_latest_file(target_dir, started_at)
    if output and output.exists():
        finalize_job_file(job_id, output, 'Download completed')
        return
    if error_messages:
        update_job(job_id, status='failed', progress=0, message=error_messages[-1], log_line=error_messages[-1])
        return
    update_job(job_id, status='failed', progress=0, message='Download failed with no output file')


def run_convert_to_mp3(job_id: str, source_rel: str, new_name: str | None = None):
    try:
        source = resolve_saved_file(source_rel)
    except Exception as exc:
        update_job(job_id, status='failed', message=str(exc), progress=0)
        return
    ffmpeg = which('ffmpeg')
    if not ffmpeg:
        update_job(job_id, status='failed', message='FFmpeg is not available inside the container.', progress=0)
        return
    target_dir = CONVERTED_ROOT
    target_dir.mkdir(parents=True, exist_ok=True)
    base_name = safe_name(new_name or source.stem)
    target = reserve_target(target_dir, f'{base_name}.mp3')
    cmd = [ffmpeg, '-y', '-i', str(source), '-vn', '-codec:a', 'libmp3lame', '-q:a', '2', str(target)]
    update_job(job_id, status='converting', progress=1, message='Converting to MP3…')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for raw_line in proc.stdout or []:
        line = raw_line.strip()
        if line:
            lines.append(line)
            update_job(job_id, status='converting', message='Converting to MP3…', log_line=line)
    code = proc.wait()
    if code != 0 or not target.exists():
        message = lines[-1] if lines else 'FFmpeg conversion failed.'
        update_job(job_id, status='failed', message=message, progress=0, log_line=message)
        return
    finalize_job_file(job_id, target, 'Conversion completed')


def run_save_as(job_id: str, source_rel: str, destination_path: str, new_name: str, operation: str):
    try:
        source = resolve_saved_file(source_rel)
        dest_dir = ensure_allowed_destination(destination_path)
        target_name = build_target_name(source, new_name)
        target = reserve_target(dest_dir, target_name)
        update_job(job_id, status='processing', progress=25, message='Preparing file save…', log_line=f'Source: {source}')
        if operation == 'move':
            shutil.move(str(source), str(target))
            action = 'Moved'
        else:
            shutil.copy2(str(source), str(target))
            action = 'Copied'
        update_job(job_id, status='completed', progress=100, message=f'{action} to {target}', output_path=str(target), output_name=target.name, output_relative=None, log_line=f'{action} file to {target}')
    except Exception as exc:
        update_job(job_id, status='failed', progress=0, message=str(exc), log_line=str(exc))


def start_download_worker(job_id: str):
    payload = JOBS[job_id]['payload']
    url = payload['url'].strip()
    mode = payload.get('mode', 'video')
    audio_format = payload.get('audio_format', 'mp3')
    try:
        if is_direct_file_url(url):
            target = serve_file_bytes(url, DOWNLOAD_ROOT / 'files', job_id)
            if mode == 'audio' and target.suffix.lower() != '.mp3':
                update_job(job_id, status='processing', progress=99, message='Download complete, converting to MP3…', output_path=str(target), output_name=target.name)
                run_convert_to_mp3(job_id, target.relative_to(APP_DATA_DIR).as_posix())
            else:
                finalize_job_file(job_id, target, 'Direct file downloaded')
            return
        run_ytdlp(job_id, url, mode, audio_format)
    except Exception as exc:
        update_job(job_id, status='failed', progress=0, message=str(exc), log_line=str(exc))


def start_upload_convert_worker(job_id: str, upload_rel: str, new_name: str | None):
    run_convert_to_mp3(job_id, upload_rel, new_name=new_name)


def json_response(handler: BaseHTTPRequestHandler, payload: dict | list, code: int = HTTPStatus.OK):
    body = json.dumps(payload).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)



class SimpleField:
    def __init__(self, filename=None, file=None, value=None):
        self.filename = filename
        self.file = file
        self.value = value

class FormData(dict):
    def getvalue(self, key, default=None):
        item = self.get(key)
        if item is None:
            return default
        return item.value if hasattr(item, 'value') else item

def parse_multipart(handler: BaseHTTPRequestHandler):
    content_type = handler.headers.get('Content-Type', '')
    length = int(handler.headers.get('Content-Length', '0') or '0')
    body = handler.rfile.read(length) if length else b''
    form = FormData()
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        return form
    boundary = (match.group(1) or match.group(2) or '').strip()
    if not boundary:
        return form
    boundary_bytes = ('--' + boundary).encode('utf-8')
    for part in body.split(boundary_bytes):
        part = part.strip()
        if not part or part == b'--':
            continue
        if part.endswith(b'--'):
            part = part[:-2]
        part = part.strip(b'\r\n')
        headers_blob, sep, content = part.partition(b'\r\n\r\n')
        if not sep:
            continue
        headers = {}
        for line in headers_blob.decode('utf-8', 'replace').split('\r\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        disp = headers.get('content-disposition', '')
        name_match = re.search(r'name="([^"]+)"', disp)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disp)
        if filename_match:
            filename = filename_match.group(1)
            form[name] = SimpleField(
                filename=filename,
                file=io.BytesIO(content),
                value=filename,
            )
        else:
            value = content.decode('utf-8', 'replace')
            form[name] = SimpleField(value=value)
    return form


class Handler(BaseHTTPRequestHandler):
    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw.decode('utf-8')) if raw else {}

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.end_headers()

    def _serve_file(self, full: Path, attachment: bool, download_name: str | None = None):
        if not full.exists() or not full.is_file() or (APP_DATA_DIR.resolve() not in full.resolve().parents and full.resolve() != APP_DATA_DIR.resolve()):
            json_response(self, {'error': 'not found'}, HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(full.name)[0] or 'application/octet-stream'
        body = full.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        filename = safe_name(download_name or full.name)
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        else:
            self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == '/':
            body = (STATIC_DIR / 'index.html').read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == '/api/health':
            json_response(self, {'status': 'ok'})
            return
        if parsed.path == '/api/status':
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j['created_at'], reverse=True)
            json_response(self, {
                'tools': tool_status(),
                'jobs': jobs,
                'saved_files': list_saved_files(),
                'server_save_root': str(DOWNLOAD_ROOT),
                'server_save_root_host': HOST_DOWNLOAD_ROOT,
                'device_hint': device_hint(self.headers.get('User-Agent', '')),
                'default_external_save_dir': DEFAULT_EXTERNAL_SAVE_DIR,
                'allowed_save_roots': [str(p) for p in ALLOWED_SAVE_ROOTS],
                'common_destinations': [DEFAULT_EXTERNAL_SAVE_DIR, '/mnt/nas/media/music', '/mnt/nas/media/videos', '/mnt/nas/downloads', str(DOWNLOAD_ROOT)],
                'ui_info': {
                    'open_action': 'Open in browser from the Raspberry Pi server',
                    'download_action': 'Download to the current device',
                    'save_elsewhere_action': 'Copy or move into another Raspberry Pi/NAS path',
                }
            })
            return
        if parsed.path.startswith('/downloaded/'):
            rel = unquote(parsed.path[len('/downloaded/'):])
            full = APP_DATA_DIR / rel
            download_name = (qs.get('filename') or [None])[0]
            return self._serve_file(full, attachment=True, download_name=download_name)
        if parsed.path.startswith('/open/'):
            rel = unquote(parsed.path[len('/open/'):])
            full = APP_DATA_DIR / rel
            return self._serve_file(full, attachment=False)
        json_response(self, {'error': 'not found'}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/download':
            data = self._read_json()
            url = (data.get('url') or '').strip()
            mode = (data.get('mode') or 'video').strip()
            if not url:
                json_response(self, {'error': 'URL is required'}, HTTPStatus.BAD_REQUEST)
                return
            job_id = new_job('download', {'url': url, 'mode': mode, 'audio_format': data.get('audio_format', 'mp3')})
            threading.Thread(target=start_download_worker, args=(job_id,), daemon=True).start()
            json_response(self, {'ok': True, 'job_id': job_id})
            return
        if parsed.path == '/api/convert':
            data = self._read_json()
            rel = (data.get('relative_path') or '').strip()
            new_name = (data.get('new_name') or '').strip() or None
            if not rel:
                json_response(self, {'error': 'relative_path is required'}, HTTPStatus.BAD_REQUEST)
                return
            job_id = new_job('convert', {'relative_path': rel, 'new_name': new_name})
            threading.Thread(target=run_convert_to_mp3, args=(job_id, rel, new_name), daemon=True).start()
            json_response(self, {'ok': True, 'job_id': job_id})
            return
        if parsed.path == '/api/save-as':
            data = self._read_json()
            rel = (data.get('relative_path') or '').strip()
            destination_path = (data.get('destination_path') or '').strip()
            new_name = (data.get('new_name') or '').strip()
            operation = (data.get('operation') or 'copy').strip().lower()
            if not rel:
                json_response(self, {'error': 'relative_path is required'}, HTTPStatus.BAD_REQUEST)
                return
            if operation not in ('copy', 'move'):
                json_response(self, {'error': 'operation must be copy or move'}, HTTPStatus.BAD_REQUEST)
                return
            try:
                ensure_allowed_destination(destination_path)
            except Exception as exc:
                json_response(self, {'error': str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            job_id = new_job('save-as', {'relative_path': rel, 'destination_path': destination_path, 'new_name': new_name, 'operation': operation})
            threading.Thread(target=run_save_as, args=(job_id, rel, destination_path, new_name, operation), daemon=True).start()
            json_response(self, {'ok': True, 'job_id': job_id})
            return
        if parsed.path == '/api/clear-jobs':
            removed = clear_finished_jobs()
            json_response(self, {'ok': True, 'removed': removed})
            return
        if parsed.path == '/api/upload-convert':
            form = parse_multipart(self)
            file_item = form['file'] if 'file' in form else None
            if not file_item or not getattr(file_item, 'filename', ''):
                json_response(self, {'error': 'Choose a file first'}, HTTPStatus.BAD_REQUEST)
                return
            upload_name = safe_name(Path(file_item.filename).name)
            upload_target = reserve_target(UPLOAD_ROOT, upload_name)
            with open(upload_target, 'wb') as f:
                shutil.copyfileobj(file_item.file, f)
            convert_to = (form.getvalue('convert_to') or 'mp3').strip()
            new_name = (form.getvalue('new_name') or '').strip() or None
            if convert_to != 'mp3':
                json_response(self, {'error': 'Only MP3 conversion is supported right now'}, HTTPStatus.BAD_REQUEST)
                return
            rel = upload_target.relative_to(APP_DATA_DIR).as_posix()
            job_id = new_job('upload-convert', {'relative_path': rel, 'new_name': new_name})
            threading.Thread(target=start_upload_convert_worker, args=(job_id, rel, new_name), daemon=True).start()
            json_response(self, {'ok': True, 'job_id': job_id, 'uploaded_relative_path': rel})
            return
        json_response(self, {'error': 'not found'}, HTTPStatus.NOT_FOUND)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == '__main__':
    main()
