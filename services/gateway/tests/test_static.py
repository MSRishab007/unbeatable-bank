"""The web app is plain files with no build step: every file the page loads must be in the repo."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_every_local_script_and_stylesheet_exists():
    html = (STATIC / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="([^"#:]+)"', html)
    local = [r for r in refs if not r.startswith(("/", "data:"))]          # "/api/..." is the API, not a file
    assert {"app.js", "styles.css", "vendor/qrcode.js", "vendor/jsQR.js"} <= set(local)
    missing = [r for r in local if not (STATIC / r).is_file()]
    assert not missing, f"index.html loads files that are not in the repo: {missing}"
