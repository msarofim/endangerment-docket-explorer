#!/usr/bin/env python
"""
Split the vehicle-rescission RTC (docket doc EPA-HQ-OAR-2025-0194-31089) into
its numbered sections using its own table of contents, and within each section
separate the "EPA Summary of Comments" block(s) from the "Response" block(s).

Output: rtc/rtc_sections.json  — list of {num, title, level, page_start, text,
        summaries: [...], responses: [...]}; this is the TAXONOMY for Pass B
        (topic_code = section num) and the lookup table for Pass C.
"""
import json, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TXT  = HERE / "rtc" / "RTC_31089.txt"
OUT  = HERE / "rtc" / "rtc_sections.json"
TOC_START, TOC_END = "Table of Contents", "1 Legal Framework for Action"

lines = TXT.read_text().split("\n")
# ---- 1. parse the TOC: "1.3.2   Reasons for Changing Position .......... 8"
toc, in_toc = [], False
for l in lines:
    if TOC_START in l: in_toc = True; continue
    if in_toc and l.strip() == TOC_END: break
    if in_toc:
        m = re.match(r"^\s*(\d+(?:\.\d+)*)\s+(.+?)\s*(?:\.{3,}\s*(\d+))?\s*$", l)
        if m and not re.match(r"^\d", m.group(2)):
            toc.append({"num": m.group(1), "title": re.sub(r"\s+", " ", m.group(2)).strip(),
                        "page": int(m.group(3)) if m.group(3) else None, "level": m.group(1).count(".") + 1})
# TOC titles wrap: a wrapped continuation line has no number; we keep the first-line title only.
nums = [t["num"] for t in toc]
print(f"TOC entries: {len(toc)}; leaves: {sum(1 for t in toc if not any(n.startswith(t['num']+'.') for n in nums))}")

# ---- 2. locate each heading in the body (after the TOC), in order
body_start = next(i for i, l in enumerate(lines) if l.strip() == TOC_END and i > 200)
pos, cursor = {}, body_start
for t in toc:
    pat = re.compile(r"^\s*" + re.escape(t["num"]) + r"\s+" + re.escape(t["title"][:40]))
    for i in range(cursor, len(lines)):
        if pat.match(lines[i]):
            pos[t["num"]] = i; cursor = i + 1; break
    else:
        # wrapped title: match number + first 3 words
        w = " ".join(t["title"].split()[:3])
        pat2 = re.compile(r"^\s*" + re.escape(t["num"]) + r"\s+" + re.escape(w))
        for i in range(cursor, len(lines)):
            if pat2.match(lines[i]):
                pos[t["num"]] = i; cursor = i + 1; break
missing = [t["num"] for t in toc if t["num"] not in pos]
print("headings not located:", missing)

# ---- 3. slice text; split summary/response blocks
ordered = [t for t in toc if t["num"] in pos]
app_a = next((i for i, l in enumerate(lines) if l.startswith("Appendix A: Mass Comment Campaigns") and i > body_start), len(lines))
secs = []
for k, t in enumerate(ordered):
    s = pos[t["num"]]; e = pos[ordered[k+1]["num"]] if k+1 < len(ordered) else app_a
    text = "\n".join(lines[s:e])
    # drop page-number-only lines
    text = re.sub(r"\n\s*\d{1,3}\s*\n", "\n", text)
    parts = re.split(r"\n\s*(EPA Summary of Comments|EPA Response|Response)\s*\n", text)
    summaries, responses = [], []
    for j in range(1, len(parts) - 1, 2):
        (summaries if parts[j].startswith("EPA Summary") else responses).append(parts[j+1].strip())
    secs.append({**t, "line_start": s, "text": text.strip(), "n_chars": len(text),
                 "summaries": summaries, "responses": responses,
                 "is_leaf": not any(n.startswith(t["num"] + ".") for n in nums)})
OUT.write_text(json.dumps(secs, indent=1))
leaves = [s for s in secs if s["is_leaf"]]
print(f"wrote {len(secs)} sections ({len(leaves)} leaves) -> {OUT}")
print("leaves with no Summary/Response split:", [s["num"] for s in leaves if not s["summaries"] and not s["responses"]][:30])
print("total chars:", sum(s["n_chars"] for s in leaves))
