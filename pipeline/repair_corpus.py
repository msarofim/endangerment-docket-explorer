#!/usr/bin/env python
"""Repair 2026-09-17: rows whose inline text was a >120-char cover note ('see attached ...') never had
their attachments downloaded. Download them, append attachment text, rewrite the corpus rows, and
delete the affected rows from the Pass A outputs so classify_unique.py submit re-runs them."""
import json, re, time
from pathlib import Path
import requests
from build_corpus import extract, clean, pdf_pages, ATT_DIR, HEADERS, OUT as CORPUS, SUBSTANTIVE_SUBTYPES, SUBSTANTIVE_MIN_PAGES
HERE = Path(__file__).resolve().parent
REPAIR_IF_CHARS = 600           # inline shorter than this with an attachment -> also check attachment
DOCKET_ID = "EPA-HQ-OAR-2025-0194"
rows = [json.loads(l) for l in CORPUS.open()]
S = requests.Session(); S.headers.update(HEADERS)
fixed = []
for r in rows:
    if r["text_source"] != "inline" or not r["attachment_urls"]: continue
    if not (re.search("attach", r["text"], re.I) or r["text_chars"] < REPAIR_IF_CHARS): continue
    texts, pages = [], []
    for j, u in enumerate(r["attachment_urls"]):
        ext = "." + u.rsplit(".", 1)[-1].lower().split("?")[0]
        p = ATT_DIR / f"{r['id']}_{j}{ext}"
        if not p.exists():
            try:
                resp = S.get(u, headers=HEADERS, timeout=120)
                if resp.status_code != 200: continue
                p.write_bytes(resp.content); time.sleep(0.15)
            except requests.RequestException: continue
        if ext == ".pdf": pages.append(pdf_pages(p))
        texts.append(clean(extract(p)))
    att = "\n\n".join(t for t in texts if t)
    # the auto-generated regulations.gov PDF just re-renders the inline text: only append if it adds content
    if len(att) > len(r["text"]) + 200:
        r["text"] = r["text"] + "\n\n[attachment]\n" + att; r["text_chars"] = len(r["text"]); r["text_source"] = "inline+attachment"
        npages = max([r["pageCount"] or 0] + [p for p in pages if p]); r["pageCount"] = npages
        r["substantive"] = (r["subtype"] in SUBSTANTIVE_SUBTYPES) or (npages >= SUBSTANTIVE_MIN_PAGES)
        r["substantive_reason"] = "subtype" if r["subtype"] in SUBSTANTIVE_SUBTYPES else (f">= {SUBSTANTIVE_MIN_PAGES} pages" if npages >= SUBSTANTIVE_MIN_PAGES else "")
        r["repaired"] = "2026-09-17 cover-note attachment"; fixed.append(r["id"])
CORPUS.write_text("".join(json.dumps(r) + "\n" for r in rows))
print(f"repaired {len(fixed)} rows")
for name in [f"stance_unique_{DOCKET_ID}.jsonl", f"stance_unique_recheck_{DOCKET_ID}.jsonl"]:
    p = HERE / name
    if p.exists():
        keep = [l for l in p.open() if json.loads(l)["id"] not in set(fixed)]
        p.write_text("".join(keep)); print(f"  dropped {len(fixed) if name.startswith('stance_unique_E') else '(subset)'} rows from {name} -> {len(keep)} remain")
(HERE / "repaired_ids_20260917.txt").write_text("\n".join(fixed))
