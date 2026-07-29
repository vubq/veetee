#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "main" / "network" / "portal"
LIMITS = {
    "index.html": 4096,
    "portal.css": 8192,
    "portal-en.js": 4096,
    "portal-i18n.js": 4096,
    "portal-ui.js": 4096,
    "portal.js": 4096,
}

for name, limit in LIMITS.items():
    path = PORTAL / name
    content = path.read_text(encoding="utf-8")
    size = len(content.encode("utf-8"))
    assert size <= limit, f"{name}: {size} > {limit}"

html = (PORTAL / "index.html").read_text(encoding="utf-8")
css = (PORTAL / "portal.css").read_text(encoding="utf-8")
english = (PORTAL / "portal-en.js").read_text(encoding="utf-8")
i18n = (PORTAL / "portal-i18n.js").read_text(encoding="utf-8")
ui = (PORTAL / "portal-ui.js").read_text(encoding="utf-8")
script = (PORTAL / "portal.js").read_text(encoding="utf-8")
for asset in (css, english, i18n, ui, script):
    assert "http://" not in asset and "https://" not in asset
for reference in ('href="', 'src="'):
    for value in html.split(reference)[1:]:
        target = value.split('"', 1)[0]
        assert target.startswith("/"), target
assert 'role="status"' in html
assert 'aria-live="polite"' in html
assert 'aria-pressed="false"' in html
assert 'role="alert"' in html
assert 'aria-describedby="bootstrapHint bootstrapError"' in html
assert 'aria-controls="password"' in html
assert 'tabindex="-1"' in html
assert "/api/status" in script
assert 'r.value=""' in script
for secret in ("activation_code", "challenge", "token"):
    assert secret not in script
