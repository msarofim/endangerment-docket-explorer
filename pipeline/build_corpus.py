#!/usr/bin/env python
"""
Build the unique-entity corpus for docket EPA-HQ-OAR-2025-0194 from the raw
API cache: one JSONL row per NON-campaign entry with the comment text (inline,
or extracted from attachments when the inline is a "See attached" stub),
metadata, and a `substantive` flag.

Incremental and idempotent: safe to run while fetch_all_comments.py is still
filling cache/; re-running only processes entries not yet in the output.

Outputs
  unique_comments_<docket>.jsonl   one row per entry
  attachments_unique/<id>_<n>.<ext> downloaded attachments (gitignored)
  build_corpus.log
"""
import json, re, subprocess, sys, time
from pathlib import Path
import pandas as pd, requests

DOCKET_ID   = "EPA-HQ-OAR-2025-0194"
HERE        = Path(__file__).resolve().parent
CACHE_DIR   = HERE / "cache"
ATT_DIR     = HERE / "attachments_unique"
CAMPAIGN_CSV= HERE / f"campaign_stance_{DOCKET_ID}.csv"
OUT         = HERE / f"unique_comments_{DOCKET_ID}.jsonl"
LOG         = HERE / "build_corpus.log"
STUB_RX     = re.compile(r"^\s*(see attached( files?\(s\))?|please see attached|attached|comments? attached)\.?\s*$", re.I)
STUB_MAXLEN = 120          # inline text shorter than this AND matching STUB_RX -> use attachment
SUBSTANTIVE_SUBTYPES = {"Company/Organization Comment", "Government Local", "Government State",
                        "Government Federal", "Government Foreign", "Member of Congress", "Petition", "Government Tribal"}
SUBSTANTIVE_MIN_PAGES = 5   # Marcus 2026-09-15: individuals with >= 5 pages count as substantive
PDF_MAX_PAGES_TEXT = 400    # cap per attachment for text extraction
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept": "*/*", "Referer": "https://www.regulations.gov/"}

def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}"; print(line, flush=True); LOG.open("a").write(line+"\n")

def extract(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            r = subprocess.run(["pdftotext", "-l", str(PDF_MAX_PAGES_TEXT), "-layout", str(path), "-"],
                               capture_output=True, text=True, timeout=300)
            return r.stdout
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        if ext in (".txt", ".md", ".csv"):
            return path.read_text(errors="replace")
    except Exception as e:
        return f"[extract error: {e}]"
    return ""

def pdf_pages(path: Path):
    try:
        r = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60)
        m = re.search(r"Pages:\s+(\d+)", r.stdout); return int(m.group(1)) if m else None
    except Exception:
        return None

def clean(t: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", t); t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&rsquo;", "'").replace("&#39;", "'").replace("&ldquo;", '"').replace("&rdquo;", '"').replace("&amp;", "&").replace("&quot;", '"')
    t = re.sub(r"[ \t]+", " ", t); t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()

def main():
    ATT_DIR.mkdir(exist_ok=True)
    campaigns = set(pd.read_csv(CAMPAIGN_CSV).id)
    done = set()
    if OUT.exists():
        done = {json.loads(l)["id"] for l in OUT.open()}
    files = sorted(CACHE_DIR.glob(f"{DOCKET_ID}-*.json"))
    todo = [f for f in files if f.stem not in done and f.stem not in campaigns]
    log(f"{len(files)} cached, {len(done)} already built, {len(todo)} to build")
    S = requests.Session(); S.headers.update(HEADERS)
    n_att = n_fail = 0
    with OUT.open("a") as out:
        for i, f in enumerate(todo, 1):
            d = json.load(f.open()); a = d["data"]["attributes"]
            inline = clean(a.get("comment") or "")
            atts = [inc["attributes"] for inc in d.get("included", []) if inc.get("type") == "attachments"]
            urls = [fmt["fileUrl"] for x in atts for fmt in (x.get("fileFormats") or [])]
            att_texts, pages = [], []
            need_att = bool(urls) and (len(inline) < STUB_MAXLEN and (STUB_RX.match(inline) or not inline))
            if need_att or (urls and len(inline) < STUB_MAXLEN):
                for j, u in enumerate(urls):
                    ext = "." + u.rsplit(".", 1)[-1].lower().split("?")[0]
                    p = ATT_DIR / f"{f.stem}_{j}{ext}"
                    if not p.exists():
                        try:
                            r = S.get(u, timeout=120)
                            if r.status_code == 200: p.write_bytes(r.content); n_att += 1; time.sleep(0.15)
                            else: n_fail += 1; log(f"  {r.status_code} {u}"); continue
                        except requests.RequestException as e:
                            n_fail += 1; log(f"  err {u} {e}"); continue
                    if ext == ".pdf": pages.append(pdf_pages(p))
                    att_texts.append(clean(extract(p)))
            text = inline if len(inline) >= STUB_MAXLEN else "\n\n".join(t for t in att_texts if t) or inline
            page_count = a.get("pageCount") or 0
            npages = max([page_count] + [p for p in pages if p])
            subtype = a.get("subtype") or ""
            row = {
                "id": f.stem, "title": a.get("title"), "subtype": subtype, "organization": a.get("organization"),
                "govAgency": a.get("govAgency"), "govAgencyType": a.get("govAgencyType"),
                "city": a.get("city"), "state": a.get("stateProvinceRegion"), "country": a.get("country"),
                "postedDate": a.get("postedDate"), "receiveDate": a.get("receiveDate"),
                "late": "Late" in subtype or "late" in (a.get("title") or "").lower(),
                "duplicateComments": a.get("duplicateComments"), "pageCount": npages,
                "n_attachments": len(urls), "attachment_urls": urls,
                "text_source": "inline" if len(inline) >= STUB_MAXLEN else ("attachment" if att_texts else "inline"),
                "text": text, "text_chars": len(text),
                "substantive": (subtype in SUBSTANTIVE_SUBTYPES) or (npages >= SUBSTANTIVE_MIN_PAGES),
                "substantive_reason": ("subtype" if subtype in SUBSTANTIVE_SUBTYPES else
                                       (f">= {SUBSTANTIVE_MIN_PAGES} pages" if npages >= SUBSTANTIVE_MIN_PAGES else "")),
            }
            out.write(json.dumps(row) + "\n")
            if i % 500 == 0: log(f"  {i}/{len(todo)} built; {n_att} attachments downloaded, {n_fail} failed")
    log(f"done: {n_att} attachments downloaded, {n_fail} failed")

if __name__ == "__main__":
    main()
