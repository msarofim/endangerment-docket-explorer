#!/usr/bin/env python
"""
Fetch the mass-comment-campaign entries for an EPA docket from the
regulations.gov v4 API and write a CSV (one row per campaign) with the
representative comment text and the `duplicateComments` count.

This is the CAMPAIGN LAYER only: regulations.gov collapses each mass
letter-writing campaign to a single posted entry whose `duplicateComments`
attribute is the number of near-identical submissions it stands for. The
~31k non-campaign ("unique entity") entries are NOT fetched here.

Usage:
    REGS_API_KEY=... python fetch_campaigns.py

Requires a regulations.gov API key (free: https://open.gsa.gov/api/regulationsgov/).
DEMO_KEY is capped at 10 req/h and will not finish.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

# ---- named constants (labels/filenames derive from these) -------------------
DOCKET_ID    = "EPA-HQ-OAR-2025-0194"          # 2009 Endangerment Finding rescission
SEARCH_TERM  = "Mass Comment Campaign"          # regulations.gov title convention
PAGE_SIZE    = 250                               # API max
API_ROOT     = "https://api.regulations.gov/v4"
HERE         = Path(__file__).resolve().parent
CACHE_DIR    = HERE / "cache"
OUT_CSV      = HERE / f"campaigns_{DOCKET_ID}.csv"
OUT_JSONL    = HERE / f"campaigns_{DOCKET_ID}.jsonl"
FETCHED_AT   = time.strftime("%Y-%m-%d")
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("REGS_API_KEY")
if not API_KEY:
    sys.exit("Set REGS_API_KEY (DEMO_KEY is rate-limited to 10/h and will not finish).")

SESSION = requests.Session()
SESSION.headers["X-Api-Key"] = API_KEY


def get(url, params=None, retries=6):
    """GET with cache-less retry on 429 / 5xx; honours the hourly limit by sleeping."""
    for attempt in range(retries):
        r = SESSION.get(url, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            wait = 60 * (attempt + 1)
            print(f"  429 rate-limited; sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"gave up on {url}")


def cached_detail(comment_id):
    """Fetch /comments/{id} once; cache the raw JSON to disk."""
    f = CACHE_DIR / f"{comment_id}.json"
    if f.exists():
        return json.loads(f.read_text())
    data = get(f"{API_ROOT}/comments/{comment_id}",
               params={"include": "attachments"})
    f.write_text(json.dumps(data, indent=1))
    return data


def list_campaign_ids():
    """All comment ids in the docket whose title matches SEARCH_TERM."""
    ids, page = [], 1
    while True:
        data = get(f"{API_ROOT}/comments", params={
            "filter[docketId]": DOCKET_ID,
            "filter[searchTerm]": f'"{SEARCH_TERM}"',
            "page[size]": PAGE_SIZE,
            "page[number]": page,
            "sort": "postedDate",
        })
        for d in data["data"]:
            ids.append((d["id"], d["attributes"]["title"]))
        meta = data["meta"]
        print(f"  listed page {page}/{meta['totalPages']} "
              f"({meta['totalElements']} total)", file=sys.stderr)
        if not meta.get("hasNextPage"):
            break
        page += 1
    return ids


def main():
    CACHE_DIR.mkdir(exist_ok=True)
    ids = list_campaign_ids()
    print(f"{len(ids)} entries titled '{SEARCH_TERM}' in {DOCKET_ID}", file=sys.stderr)

    rows = []
    with OUT_JSONL.open("w") as jl:
        for i, (cid, title) in enumerate(ids, 1):
            d = cached_detail(cid)
            a = d["data"]["attributes"]
            att = [inc["attributes"] for inc in d.get("included", [])
                   if inc.get("type") == "attachments"]
            att_urls = [fmt["fileUrl"] for x in att
                        for fmt in (x.get("fileFormats") or [])]
            row = {
                "id": cid,
                "title": a.get("title"),
                "duplicateComments": a.get("duplicateComments"),
                "organization": a.get("organization"),
                "postedDate": a.get("postedDate"),
                "receiveDate": a.get("receiveDate"),
                "subtype": a.get("subtype"),
                "docAbstract": a.get("docAbstract"),
                "n_attachments": len(att_urls),
                "attachment_urls": ";".join(att_urls),
                "comment": a.get("comment"),
                "docket": DOCKET_ID,
                "fetched_at": FETCHED_AT,
            }
            rows.append(row)
            jl.write(json.dumps(row) + "\n")
            if i % 25 == 0:
                print(f"  {i}/{len(ids)} detail records", file=sys.stderr)

    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    tot = df["duplicateComments"].fillna(0).sum()
    print(f"wrote {OUT_CSV}: {len(df)} campaigns, "
          f"{int(tot):,} comments represented via duplicateComments")


if __name__ == "__main__":
    main()
