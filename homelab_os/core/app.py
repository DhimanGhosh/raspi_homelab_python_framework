from __future__ import annotations

from fastapi import FastAPI

from homelab_os import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="Homelab OS Core",
        version=__version__,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "homelab_os_core",
            "version": __version__,
        }

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "message": "Homelab OS core is running",
            "version": __version__,
        }

    return app


app = create_app()
