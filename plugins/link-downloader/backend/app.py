import os
import runpy
from pathlib import Path

os.environ.setdefault("PORT", "18160")
legacy = Path(__file__).resolve().parent / "legacy_app.py"
runpy.run_path(str(legacy), run_name="__main__")
