"""Every file the web pages load must be in the repo.

static/            the app, built from web/ (npm run build); its files are under /assets
static/classic/    the original plain-JavaScript app, kept for the staff console
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _refs(html: str) -> list[str]:
    return [r for r in re.findall(r'(?:src|href)="([^"#:]+)"', html) if not r.startswith("data:")]


def test_classic_app_files_exist():
    base = STATIC / "classic"
    local = [r for r in _refs((base / "index.html").read_text()) if not r.startswith("/")]
    assert {"app.js", "styles.css", "vendor/qrcode.js", "vendor/jsQR.js"} <= set(local)
    missing = [r for r in local if not (base / r).is_file()]
    assert not missing, f"classic/index.html loads files that are not in the repo: {missing}"


def test_built_app_files_exist():
    refs = [r for r in _refs((STATIC / "index.html").read_text()) if r.startswith("/") and not r.startswith("/api")]
    assert any(r.startswith("/assets/") and r.endswith(".js") for r in refs), "run `npm run build` in web/"
    missing = [r for r in refs if not (STATIC / r.lstrip("/")).is_file()]
    assert not missing, f"index.html loads files that are not in the repo (rebuild web/): {missing}"
