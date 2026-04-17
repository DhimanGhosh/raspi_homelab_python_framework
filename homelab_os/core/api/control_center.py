from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def inject_patch_script(index_html: str) -> str:
    patch = """
<script>
document.addEventListener('click', function (event) {
  const summary = event.target.closest('details summary');
  if (!summary) return;
  event.stopPropagation();
}, true);

document.addEventListener('toggle', function (event) {
  const details = event.target;
  if (!(details instanceof HTMLDetailsElement)) return;
  if (!details.classList.contains('bundle-details')) return;
  if (!details.open) return;
  document.querySelectorAll('details.bundle-details[open]').forEach(function (node) {
    if (node !== details) node.removeAttribute('open');
  });
}, true);
</script>
"""
    if "</body>" in index_html:
        return index_html.replace("</body>", patch + "\n</body>")
    return index_html + patch


def patch_index_file(index_path: str | Path) -> dict[str, Any]:
    path = Path(index_path)
    if not path.exists():
        return {"ok": False, "error": f"missing file: {path}"}
    original = path.read_text(encoding="utf-8")
    updated = inject_patch_script(original)
    path.write_text(updated, encoding="utf-8")
    return {"ok": True, "path": str(path)}
