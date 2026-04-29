from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.config import APP_NAME, APP_VERSION
from app.routes import routes_bp

# ── App ────────────────────────────────────────────────────────────────────────
_BASE_DIR  = Path(__file__).resolve().parent
_TEMPLATES = str(_BASE_DIR / "templates")
_STATIC    = str(_BASE_DIR / "static")

app = Flask(__name__, template_folder=_TEMPLATES, static_folder=_STATIC, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

app.register_blueprint(routes_bp)

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8140)
