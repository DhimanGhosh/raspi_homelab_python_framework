from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from homelab_os import __version__
from homelab_os.core.api.control_center import router as control_center_router
from homelab_os.core.api.jobs import router as jobs_router
from homelab_os.core.api.plugins import router as plugins_router
from homelab_os.core.config import ensure_runtime_dirs, load_settings
from homelab_os.core.services.network_stack import NetworkStackService

def create_app() -> FastAPI:
    app = FastAPI(title='Homelab OS Core', version=__version__)
    app.include_router(plugins_router, prefix='/api')
    app.include_router(jobs_router, prefix='/api')
    app.include_router(control_center_router, prefix='/api')
    static_dir = Path(__file__).resolve().parent / 'static'
    app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')

    @app.on_event('startup')
    def reconcile_public_routes_on_startup() -> None:
        try:
            settings = load_settings('.env')
            ensure_runtime_dirs(settings)
            NetworkStackService(settings).reconcile_routes(include_core=True)
        except Exception as exc:
            print(f'[WARN] Route reconciliation on startup failed: {exc}')

    @app.get('/health')
    def health() -> dict[str, str]:
        return {'status': 'ok', 'service': 'homelab_os_core', 'version': __version__}

    @app.get('/')
    def root() -> dict[str, str]:
        return {'message': 'Homelab OS core is running', 'version': __version__}

    return app

app = create_app()
