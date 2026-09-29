#!/usr/bin/env python
"""
Resumable pull of EVERY posted comment entry in a regulations.gov docket
(detail record: comment text, duplicateComments, organization, attachment
metadata). Skips ids already in cache/. Respects the 1,000 req/h key limit by
sleeping when x-ratelimit-remaining hits 0 or on HTTP 429.

Usage:  set -a; source .env; set +a; python fetch_all_comments.py
Output: cache/<id>.json (raw API detail response, one file per entry)
        ids_<docket>.txt   (the full id list, from the paginated listing)
        fetch_all.log      (progress)
"""
import json, os, sys, time
from pathlib import Path
import requests

DOCKET_ID  = "EPA-HQ-OAR-2025-0194"
API_ROOT   = "https://api.regulations.gov/v4"
PAGE_SIZE  = 250            # API max per page; max 20 pages per query -> window by lastModifiedDate
HERE       = Path(__file__).resolve().parent
CACHE_DIR  = HERE / "cache"
ID_FILE    = HERE / f"ids_{DOCKET_ID}.txt"
LOG        = HERE / "fetch_all.log"

API_KEY = os.environ["REGS_API_KEY"]
S = requests.Session(); S.headers["X-Api-Key"] = API_KEY

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True); LOG.open("a").write(line + "\n")

def get(url, params=None):
    while True:
        try:
            r = S.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            log(f"  network error {e}; sleeping 60s"); time.sleep(60); continue
        rem = int(r.headers.get("x-ratelimit-remaining", "1") or 1)
        if r.status_code == 200:
            if rem <= 2:
                log("  rate budget exhausted; sleeping 15 min"); time.sleep(900)
            return r.json()
        if r.status_code == 429 or (500 <= r.status_code < 600):
            log(f"  HTTP {r.status_code}; sleeping 5 min"); time.sleep(300); continue
        if r.status_code == 404:
            return None
        r.raise_for_status()

def list_all_ids():
    """Walk the docket by lastModifiedDate windows (API caps any query at 5,000 hits)."""
    if ID_FILE.exists():
        ids = ID_FILE.read_text().split()
        log(f"id list loaded from disk: {len(ids)}"); return ids
    ids, seen = [], set()
    since, expected = None, None
    while True:
        params = {"filter[docketId]": DOCKET_ID, "page[size]": PAGE_SIZE,
                  "sort": "lastModifiedDate,documentId"}
        if since: params["filter[lastModifiedDate][ge]"] = since
        page, last_mod = 1, None
        while True:
            params["page[number]"] = page
            d = get(f"{API_ROOT}/comments", params)
            for x in d["data"]:
                if x["id"] not in seen:
                    seen.add(x["id"]); ids.append(x["id"])
                last_mod = x["attributes"]["lastModifiedDate"]
            meta = d["meta"]
            if expected is None: expected = meta["totalElements"]   # docket total = first, unfiltered window
            log(f"  list window since={since} page {page}/{meta['totalPages']} total_ids={len(ids)}")
            if not meta.get("hasNextPage") or page >= 20: break
            page += 1
        if not meta.get("hasNextPage"):
            break
        # next window starts at the last lastModifiedDate seen. The API RETURNS UTC but
        # FILTERS in US Eastern, so shift back 5 h (over-inclusive; `seen` dedups the overlap).
        from datetime import datetime, timedelta
        t = datetime.strptime(last_mod, "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=5)
        since = t.strftime("%Y-%m-%d %H:%M:%S")
    log(f"id list complete: {len(ids)} ids (API totalElements={expected}) -> {ID_FILE}")
    if expected and len(ids) != expected:
        log(f"  WARNING: listed {len(ids)} != totalElements {expected}; not writing id file")
        sys.exit(1)
    ID_FILE.write_text("\n".join(ids))
    return ids

def main():
    CACHE_DIR.mkdir(exist_ok=True)
    ids = list_all_ids()
    todo = [i for i in ids if not (CACHE_DIR / f"{i}.json").exists()]
    log(f"{len(ids)} ids, {len(ids)-len(todo)} cached, {len(todo)} to fetch")
    for n, cid in enumerate(todo, 1):
        d = get(f"{API_ROOT}/comments/{cid}", {"include": "attachments"})
        if d is None:
            log(f"  404 {cid}"); continue
        (CACHE_DIR / f"{cid}.json").write_text(json.dumps(d))
        if n % 100 == 0:
            log(f"  {n}/{len(todo)} fetched")
    log("done")

if __name__ == "__main__":
    main()
