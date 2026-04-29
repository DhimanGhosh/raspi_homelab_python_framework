from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Resolves to the plugin root (parent of this app/ package)
_PLUGIN_ROOT = Path(__file__).parent.parent.resolve()

templates = Jinja2Templates(directory=str(_PLUGIN_ROOT / "templates"))
