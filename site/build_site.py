#!/usr/bin/env python
"""Inline data.json into template.html -> dist/index.html (single self-contained file)."""
from pathlib import Path
HERE = Path(__file__).resolve().parent
data = (HERE / "data.json").read_text()
# the JSON sits inside a <script> block: escape any '</script' so it cannot end the block early
data = data.replace("</", "<\\/")
html = (HERE / "template.html").read_text().replace("__DATA__", data)
(HERE / "dist").mkdir(exist_ok=True); out = HERE / "dist" / "index.html"; out.write_text(html)
print(f"{out}: {out.stat().st_size/1e6:.1f} MB")
