from __future__ import annotations

from typing import Any

import requests
from fastapi import HTTPException


def _upstream(url: str, method: str = "GET", timeout: int = 20, **kwargs) -> Any:
    """Proxy a request to an upstream service and return parsed JSON."""
    try:
        r = requests.request(method, url, timeout=timeout, **kwargs)
        r.raise_for_status()
    except requests.RequestException as exc:
        body = getattr(getattr(exc, "response", None), "text", "")[:400]
        raise HTTPException(
            status_code=502,
            detail={"message": "Upstream request failed", "url": url, "error": str(exc), "body": body},
        )
    try:
        return r.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail={"message": "Upstream returned non-JSON", "url": url, "body": r.text[:400]},
        )


def _upstream_raw(url: str, timeout: int = 60) -> requests.Response:
    """Proxy a request and return the raw response (for streaming)."""
    try:
        return requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _service_status(name: str, url: str) -> dict:
    """Check reachability of a service and return a status dict."""
    try:
        r = requests.get(url, timeout=5)
        return {"service": name, "ok": r.ok, "status_code": r.status_code}
    except Exception as exc:
        return {"service": name, "ok": False, "error": str(exc)}
