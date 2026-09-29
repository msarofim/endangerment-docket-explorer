#!/usr/bin/env python
"""Locate every verdict quote in its source document: page number (PDF page for the RTC, Federal Register
page for the FR documents), RTC section, and whether an RTC quote sits in an 'EPA Response' block, an
'EPA Summary of Comments' block, or an unsplit section. Writes the 'loc' field into coverage_verdicts."""
import json, re, bisect
from pathlib import Path
HERE = Path(__file__).resolve().parent; D = "EPA-HQ-OAR-2025-0194"
DOCS = {"vehicle_rtc": HERE/"rtc"/"RTC_31089.txt", "vehicle_fr": HERE/"vehicle_rule"/"FR_2026-03157.txt",
        "sprm_fr": HERE/"powerplant"/"FR_2026-19072_sprm.txt", "partial_repeal": HERE/"powerplant"/"FR_2026-19071_partial_repeal.txt"}
def build(raw):
    """normalized text + map from normalized index -> raw offset"""
    out, idx, prev_space = [], [], True
    for i, ch in enumerate(raw):
        c = ch.lower()
        if c.isalnum(): out.append(c); idx.append(i); prev_space = False
        elif not prev_space: out.append(" "); idx.append(i); prev_space = True
    return "".join(out), idx
def norm(s): return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
texts = {k: p.read_text(errors="replace") for k, p in DOCS.items()}
built = {k: build(t) for k, t in texts.items()}
# RTC: page = pdf page (form feeds); section by line; block by the nearest preceding label
rtc = texts["vehicle_rtc"]; ff = [m.start() for m in re.finditer("\f", rtc)]
lines_off = [0] + [m.end() for m in re.finditer("\n", rtc)]
S = json.load((HERE/"rtc"/"rtc_sections.json").open()); sec_starts = sorted((s["line_start"], s["num"]) for s in S)
labels = sorted((m.start(), "response" if "Response" in m.group(1) else "summary") for m in re.finditer(r"\n\s*(EPA Summary of Comments|EPA Response|Response)\s*\n", rtc))
def rtc_loc(off):
    page = bisect.bisect_right(ff, off) + 1
    line = bisect.bisect_right(lines_off, off) - 1
    j = bisect.bisect_right([l for l, _ in sec_starts], line) - 1; sec = sec_starts[j][1] if j >= 0 else None
    k = bisect.bisect_right([o for o, _ in labels], off) - 1
    # a label belongs to the block only if it is inside the same section
    block = labels[k][1] if k >= 0 and bisect.bisect_right(lines_off, labels[k][0]) - 1 >= sec_starts[j][0] else "unsplit"
    return {"page": page, "section": sec, "block": block}
def fr_loc(key, off):
    t = texts[key]; m = None
    for m2 in re.finditer(r"\[\[Page (\d+)\]\]", t[:off]): m = m2
    return {"page": int(m.group(1)) if m else None}
def locate(key, quote):
    q = norm(quote)
    if len(q) < 25: return None
    ntext, idx = built[key]
    for k in (q[:45], q[8:53], q[-45:], q[:30]):
        if len(k) < 25: continue
        p = ntext.find(k)
        if p >= 0:
            off = idx[p]
            return rtc_loc(off) if key == "vehicle_rtc" else fr_loc(key, off)
    return None
rows = [json.loads(l) for l in (HERE/f"coverage_verdicts_{D}.jsonl").open()]
stats = {k: {"located": 0, "missing": 0} for k in DOCS}; blocks = {}
for r in rows:
    if "verdicts" not in r: continue
    for k in DOCS:
        v = r["verdicts"][k]; loc = locate(k, v.get("quote") or "") if v.get("quote") else None
        v["loc"] = loc; stats[k]["located" if loc else "missing"] += 1
        if k == "vehicle_rtc" and loc: blocks[loc["block"]] = blocks.get(loc["block"], 0) + 1
(HERE/f"coverage_verdicts_{D}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
print("located:", stats); print("RTC quote blocks:", blocks)
