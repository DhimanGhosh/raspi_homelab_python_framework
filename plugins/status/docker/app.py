from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
APP_NAME = os.getenv("APP_NAME", "Pi Status Board")
APP_VERSION = os.getenv("APP_VERSION", "1.3.0")
NAS_PATH = Path(os.getenv("NAS_PATH", "/mnt/nas"))
TAILSCALE_SOCKET = Path(os.getenv("TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock"))
CONTROL_CENTER_SUMMARY_URL = os.getenv(
    "CONTROL_CENTER_SUMMARY_URL",
    "http://127.0.0.1:9000/api/control-center/summary",
)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def check_url(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pi-statusboard/1.3"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            return {"ok": True, "status": resp.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def disk_info(path: Path) -> dict:
    try:
        u = shutil.disk_usage(path)
        used = u.total - u.free
        return {
            "path": str(path),
            "total_gb": round(u.total / 1024**3, 2),
            "used_gb": round(used / 1024**3, 2),
            "free_gb": round(u.free / 1024**3, 2),
            "used_pct": round((used / u.total) * 100, 2) if u.total else 0,
        }
    except Exception:
        return {"path": str(path), "total_gb": 0, "used_gb": 0, "free_gb": 0, "used_pct": 0}


def run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=8).strip()
    except Exception:
        return ""


def read_proc_uptime() -> str:
    try:
        total_seconds = float(Path("/proc/uptime").read_text().split()[0])
        seconds = int(total_seconds)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, _ = divmod(seconds, 60)
        parts = []
        if days:
            parts.append(f"{days} day" + ("s" if days != 1 else ""))
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if minutes or not parts:
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
        return "up " + ", ".join(parts)
    except Exception:
        return ""


def uptime_text() -> str:
    return run(["uptime", "-p"]) or run(["uptime"]) or read_proc_uptime() or "-"


def tailscale_status_json() -> dict:
    raw = run(["tailscale", "status", "--json"])
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    if not TAILSCALE_SOCKET.exists():
        return {}
    try:
        import socket as pysocket

        client = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(str(TAILSCALE_SOCKET))
        client.sendall(
            b"GET /localapi/v0/status HTTP/1.1\r\n"
            b"Host: local-tailscaled.sock\r\n"
            b"Connection: close\r\n\r\n"
        )
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        client.close()
        raw_resp = b"".join(chunks).decode("utf-8", "ignore")
        body = raw_resp.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw_resp else raw_resp
        return json.loads(body)
    except Exception:
        return {}


def tailscale_devices() -> list[dict]:
    data = tailscale_status_json()
    peers = []
    peer_map = data.get("Peer") or {}
    for peer in peer_map.values():
        peers.append(
            {
                "name": peer.get("HostName") or (peer.get("DNSName", "").rstrip(".")) or (peer.get("TailscaleIPs") or [""])[0],
                "online": bool(peer.get("Online")),
                "os": peer.get("OS", ""),
                "ip": (peer.get("TailscaleIPs") or [""])[0],
            }
        )
    if not peers:
        for peer in data.get("Peers") or []:
            peers.append(
                {
                    "name": peer.get("HostName") or (peer.get("DNSName", "").rstrip(".")) or (peer.get("TailscaleIPs") or [""])[0],
                    "online": bool(peer.get("Online")),
                    "os": peer.get("OS", ""),
                    "ip": (peer.get("TailscaleIPs") or [""])[0],
                }
            )
    peers = [p for p in peers if p["name"] or p["ip"]]
    peers.sort(key=lambda x: (not x["online"], x["name"]))
    return peers


def tailscale_ip() -> str:
    ip = run(["tailscale", "ip", "-4"])
    if ip:
        return ip
    data = tailscale_status_json()
    self_info = data.get("Self") or {}
    return (self_info.get("TailscaleIPs") or [""])[0]


def fetch_services() -> list[dict]:
    try:
        req = urllib.request.Request(CONTROL_CENTER_SUMMARY_URL, headers={"User-Agent": "pi-statusboard/1.3"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        apps = payload.get("apps", [])
        services = []
        for item in apps:
            public_url = item.get("public_url")
            if not public_url:
                continue
            services.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("id"),
                    "url": public_url,
                    "port": item.get("port", "-"),
                    "installed_version": item.get("installed_version", "-"),
                    "status_hint": item.get("status", "unknown"),
                }
            )
        return services
    except Exception:
        return []


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION}


@app.get("/api/system")
def system() -> JSONResponse:
    services = []
    ok_count = 0
    for item in fetch_services():
        res = check_url(item["url"])
        if res.get("ok"):
            ok_count += 1
        services.append(
            {
                **item,
                **res,
                "version": item.get("installed_version", "-"),
                "port": item.get("port", "-"),
            }
        )
    return JSONResponse(
        {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "hostname": socket.gethostname(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {"ok": ok_count, "total": len(services)},
            "uptime": uptime_text(),
            "tailscale_ip": tailscale_ip(),
            "nas_storage": disk_info(NAS_PATH),
            "root_storage": disk_info(Path("/")),
            "tailscale_devices": tailscale_devices(),
            "services": services,
        }
    )
