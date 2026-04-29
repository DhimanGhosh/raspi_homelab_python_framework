from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, APP_VERSION, PORT
from app.routes import router

_BASE = Path(__file__).parent.resolve()

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Unified API Gateway for all Homelab services. "
        "Proxies Music Player, Files, and Pi-hole APIs through a single entry point."
    ),
)
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
