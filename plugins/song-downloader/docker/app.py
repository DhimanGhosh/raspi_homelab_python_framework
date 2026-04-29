from __future__ import annotations

from flask import Flask

from app.config import PORT
from app.jobs import startup_reconcile_jobs
from app.routes import routes_bp

# ── App ────────────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

app.register_blueprint(routes_bp)

# Recover any jobs that were mid-flight when the container last stopped
startup_reconcile_jobs()

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
