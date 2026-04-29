from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import APP_NAME, APP_VERSION, MUSIC_PLAYER_API, FILES_API, PIHOLE_API
from app.core import templates
from app.models import (
    ArtistImagePayload,
    MetadataUpdatePayload,
    PlaylistAddTracksPayload,
    PlaylistPayload,
)
from app.upstream import _service_status, _upstream, _upstream_raw

router = APIRouter()


# ── Gateway ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    """Serve the API Gateway dashboard."""
    return templates.TemplateResponse(request, "index.html", {
        "app_name": APP_NAME, "app_version": APP_VERSION,
    })


@router.get("/api/health", tags=["gateway"])
def health():
    """Gateway health check."""
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION}


@router.get("/api/debug/upstreams", tags=["gateway"])
def debug_upstreams():
    """Check connectivity to all upstream services."""
    return {
        "music_player": _service_status("music-player", f"{MUSIC_PLAYER_API}/api/library"),
        "files":        _service_status("files",        f"{FILES_API}/"),
        "pihole":       _service_status("pihole",       f"{PIHOLE_API}/admin/"),
    }


# ── Music Player  (/api/music/*) ───────────────────────────────────────────────

@router.get("/api/music/library", tags=["music-player"])
def music_library():
    """Full music library — tracks, albums, artists, folders, playlists."""
    return _upstream(f"{MUSIC_PLAYER_API}/api/library", timeout=30)


@router.get("/api/music/stream/{relpath:path}", tags=["music-player"])
def music_stream(relpath: str):
    """Stream an audio file by its relative path."""
    upstream_resp = _upstream_raw(f"{MUSIC_PLAYER_API}/api/stream/{relpath}")
    return StreamingResponse(
        upstream_resp.iter_content(chunk_size=65536),
        status_code=upstream_resp.status_code,
        media_type=upstream_resp.headers.get("content-type", "audio/mpeg"),
        headers={
            "Accept-Ranges": upstream_resp.headers.get("Accept-Ranges", "bytes"),
            "Content-Length": upstream_resp.headers.get("Content-Length", ""),
        },
    )


@router.get("/api/music/art-cache/{filename}", tags=["music-player"])
def music_art_cache(filename: str):
    """Fetch a cached album art image by filename."""
    upstream_resp = _upstream_raw(f"{MUSIC_PLAYER_API}/api/art-cache/{filename}")
    return StreamingResponse(
        upstream_resp.iter_content(chunk_size=65536),
        status_code=upstream_resp.status_code,
        media_type=upstream_resp.headers.get("content-type", "image/jpeg"),
    )


@router.get("/api/music/artist-images/{filename}", tags=["music-player"])
def music_artist_images(filename: str):
    """Fetch a stored artist image by filename."""
    upstream_resp = _upstream_raw(f"{MUSIC_PLAYER_API}/api/artist-images/{filename}")
    return StreamingResponse(
        upstream_resp.iter_content(chunk_size=65536),
        status_code=upstream_resp.status_code,
        media_type=upstream_resp.headers.get("content-type", "image/jpeg"),
    )


@router.get("/api/music/metadata/{relpath:path}", tags=["music-player"])
def music_get_metadata(relpath: str):
    """Get metadata for a specific track."""
    return _upstream(f"{MUSIC_PLAYER_API}/api/metadata/{relpath}")


@router.post("/api/music/metadata/{relpath:path}", tags=["music-player"])
def music_update_metadata(relpath: str, payload: MetadataUpdatePayload):
    """Update metadata for a specific track."""
    return _upstream(
        f"{MUSIC_PLAYER_API}/api/metadata/{relpath}",
        method="POST",
        json=payload.model_dump(exclude_none=True),
        timeout=40,
    )


@router.post("/api/music/playlists", tags=["music-player"])
def music_create_playlist(payload: PlaylistPayload):
    """Create a new playlist or add tracks to an existing one."""
    return _upstream(
        f"{MUSIC_PLAYER_API}/api/playlists",
        method="POST",
        json=payload.model_dump(),
        timeout=20,
    )


@router.post("/api/music/playlists/add-tracks", tags=["music-player"])
def music_playlist_add_tracks(payload: PlaylistAddTracksPayload):
    """Add specific tracks to an existing playlist."""
    return _upstream(
        f"{MUSIC_PLAYER_API}/api/playlists/add-tracks",
        method="POST",
        json=payload.model_dump(),
        timeout=20,
    )


@router.post("/api/music/artist-image/{artist}", tags=["music-player"])
def music_set_artist_image(artist: str, payload: ArtistImagePayload):
    """Set or update the image for an artist."""
    return _upstream(
        f"{MUSIC_PLAYER_API}/api/artist-image/{artist}",
        method="POST",
        json=payload.model_dump(exclude_none=True),
        timeout=30,
    )


# ── Files  (/api/files/*) ──────────────────────────────────────────────────────

@router.get("/api/files/health", tags=["files"])
def files_health():
    """Check whether the Files service is reachable."""
    return _service_status("files", f"{FILES_API}/")


@router.get("/api/files/info", tags=["files"])
def files_info():
    """Return connection info for the Files service."""
    return {
        "service": "files",
        "base_url": FILES_API,
        "browse_path": "/files/Incoming/",
        "note": "FileBrowser does not expose a REST API; use the web UI directly.",
    }


# ── Pi-hole  (/api/pihole/*) ───────────────────────────────────────────────────

@router.get("/api/pihole/health", tags=["pihole"])
def pihole_health():
    """Check whether the Pi-hole admin interface is reachable."""
    return _service_status("pihole", f"{PIHOLE_API}/admin/")


@router.get("/api/pihole/summary", tags=["pihole"])
def pihole_summary():
    """Fetch Pi-hole statistics summary."""
    return _upstream(f"{PIHOLE_API}/admin/api.php", params={"summary": ""}, timeout=10)


@router.get("/api/pihole/status", tags=["pihole"])
def pihole_status():
    """Get Pi-hole enabled/disabled status."""
    return _upstream(f"{PIHOLE_API}/admin/api.php", params={"status": ""}, timeout=10)


@router.get("/api/pihole/top-items", tags=["pihole"])
def pihole_top_items(count: int = Query(default=10, ge=1, le=100)):
    """Get top queried/blocked domains from Pi-hole."""
    return _upstream(f"{PIHOLE_API}/admin/api.php", params={"topItems": count}, timeout=10)


@router.get("/api/pihole/query-types", tags=["pihole"])
def pihole_query_types():
    """Get DNS query type breakdown from Pi-hole."""
    return _upstream(f"{PIHOLE_API}/admin/api.php", params={"getQueryTypes": ""}, timeout=10)
