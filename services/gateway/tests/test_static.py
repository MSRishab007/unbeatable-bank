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


def test_app_pages_open_directly_and_unknown_paths_are_404(tmp_path, monkeypatch):
    """Clean URLs: /home or /accounts/<id> opened from a link or a reload get the app."""
    import importlib
    from fastapi.testclient import TestClient
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "classic").mkdir()
    (tmp_path / "classic" / "index.html").write_text("classic")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    c = TestClient(main.app)
    for page in ("/home", "/pay", "/accounts/1cb7147a-5a6f-4d5b-9e26-75d1be4ad8b7", "/signin", "/staff/fraud"):
        r = c.get(page)
        assert r.status_code == 200 and "id=root" in r.text and r.headers["cache-control"] == "no-cache", page
    assert c.get("/classic/").text == "classic"
    assert c.get("/nothing-here").status_code == 404
    assert c.get("/assets/missing.js").status_code == 404
