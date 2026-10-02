#!/usr/bin/env python
"""Assemble the site's data bundle (site/data.json) from the comment_docket outputs. Labels and numbers
in the site derive from this file, never hand-typed."""
import json, re, pandas as pd
from pathlib import Path
HERE = Path(__file__).resolve().parent; CD = HERE.parent; D = "EPA-HQ-OAR-2025-0194"
def J(p): return [json.loads(l) for l in (CD / p).open()]
def trunc(s, n):
    # collapse whitespace; the FR text uses TeX-style ``quotes'' — shown as straight quotes
    s = re.sub(r"\s+", " ", str(s or "")).strip().replace("``", "\"").replace("''", "\"")
    return s if len(s) <= n else s[:n-1] + "…"

# ---- NAME POLICY (Marcus 2026-09-29) --------------------------------------------------------------
# A submitter's name is published only where the comment was filed in a PUBLIC capacity: a substantive
# entry (organization / government / Member of Congress / petition subtype, or >= 5 pages) whose
# commenter type is not "individual". Everyone else becomes "Individual commenter" with a link to
# their docket entry, where regulations.gov shows the name. The entity type alone is NOT a safe proxy
# --- only 78 of the 522 rows typed academic_or_scientist are substantive entries, and the other 444
# carry ordinary personal names --- so the substantive flag carries the public-capacity test and the
# type only removes the private citizen who happened to write five pages.
PRIVATE_TYPE = "individual"
def named(entity, substantive): return bool(substantive) and entity != PRIVATE_TYPE
ANON_LABEL = "Individual commenter"

# ---- CORRECTIONS ROUTING (Marcus 2026-10-01) -------------------------------------------------------
# Split by purpose. A misclassification report is discussion and belongs in the comment thread on the
# Substack post that accompanies the page; a name-removal or data correction belongs in a GitHub issue,
# which leaves a record the rebuild can be traced to.
# SUBSTACK_POST_URL does not exist until the post is published, and the page and the post launch
# TOGETHER -- so check_release.py FAILS while it is empty rather than shipping a page whose first
# correction route points at a thread no reader can reach. Fill it in at launch, rebuild, re-sync.
REPO_URL          = "https://github.com/msarofim/endangerment-docket-explorer"
SUBSTACK_POST_URL = ""      # e.g. https://<publication>.substack.com/p/<slug>

# ---- unique comments (Pass A final) + corpus metadata
fin = {r["id"]: r for r in J(f"stance_unique_final_{D}.jsonl")}
corpus = J(f"unique_comments_{D}.jsonl")
comments = []
for r in corpus:
    f = fin.get(r["id"], {})
    show = named(f.get("entity_type", ""), r["substantive"])
    comments.append({"id": r["id"].split("-")[-1], "t": trunc(r["title"].replace("Comment submitted by ", ""), 60) if show else ANON_LABEL,
                     "d": (r["postedDate"] or "")[:10],
                     "st": r["subtype"] or "", "e": f.get("entity_type", ""), "s": f.get("stance", ""), "c": f.get("confidence", ""),
                     "k": trunc(f.get("key_phrase", ""), 120), "sub": int(bool(r["substantive"])), "n": r["text_chars"], "rc": int(bool(f.get("rechecked"))),
                     "an": int(not show),
                     "rl": trunc(r.get("reclassified", ""), 160)})   # multi-author letters EPA's Appendix A had logged as mass-mail campaigns
NAMED_IDS = {c["id"] for c in comments if not c["an"]}
cdf = pd.DataFrame(comments)
# ---- campaigns
camp = pd.read_csv(CD / f"campaign_stance_{D}.csv")
campaigns = [{"id": r.id.split("-")[-1], "t": trunc(r.title.replace("Mass Comment Campaign ", ""), 90), "w": int(r.weight), "src": r.weight_source, "s": r.stance, "ev": trunc(r.evidence, 220),
              "note": trunc(getattr(r, "note", "") if isinstance(getattr(r, "note", ""), str) else "", 220)} for r in camp.itertuples()]
n_moved = int(camp.note.fillna("").str.startswith("reclassified").sum()); n_relabeled = int((camp.note.fillna("") != "").sum()) - n_moved
# ---- canonical arguments + verdicts
# commenter names on the arguments tab follow the same policy: a Pass-B commenter name is shown only
# if every docket entry it came from is name-eligible (one name can span a multi-part filing)
_af = pd.read_csv(CD / f"arguments_flat_{D}.csv", low_memory=False)
_ent = {c["id"]: (c["e"], c["sub"]) for c in comments}
NAME_OK = {}
for nm, g in _af.groupby("commenter"):
    ids = {str(i).split("-")[-1] for i in g.id}
    NAME_OK[nm] = all(named(*_ent.get(i, ("individual", 0))) for i in ids)
def who_label(w): return trunc(w, 60) if NAME_OK.get(w, False) else ANON_LABEL

cov = J(f"coverage_verdicts_{D}.jsonl")
DOCS = ["vehicle_rtc", "vehicle_fr", "sprm_fr", "partial_repeal"]
args = []
for r in cov:
    if "verdicts" not in r: continue
    args.append({"id": r["canon_id"], "tc": r["topic_code"], "tl": trunc(r["topic_label"], 70), "cl": trunc(r["canonical_claim"], 400), "pos": r["position"], "ap": r["applies_to"],
                 "nc": r["n_commenters"], "ncm": r.get("n_commenters_model", 0), "who": [who_label(w) for w in r["commenters"][:6]], "doc": r["sample_doc"].split("-")[-1], "q": trunc(r["sample_quote"], 240),
                 "v": {k: r["verdicts"][k]["verdict"] for k in DOCS}, "vq": {k: trunc(r["verdicts"][k]["quote"], 200) for k in DOCS},
                 "vl": {k: (lambda l: ({"p": l.get("page"), "s": l.get("section"), "b": l.get("block")} if l else None))(r["verdicts"][k].get("loc")) for k in DOCS},
                 "ho": bool(r["verdicts"]["vehicle_rtc"].get("hand_override")), "rr": bool(r["verdicts"]["vehicle_rtc"].get("rerun"))})
adf = pd.DataFrame(args)
un = lambda s: s.isin(["not_addressed", "dismissed_out_of_scope"])
vr = adf.v.map(lambda v: v["vehicle_rtc"]); vf = adf.v.map(lambda v: v["vehicle_fr"]); vs = adf.v.map(lambda v: v["sprm_fr"])
adf["short"] = (adf.ap == "both") & (adf.pos == "opposes_proposal") & un(vr) & un(vf) & un(vs)
for a, s in zip(args, adf.short): a["sl"] = int(bool(s))
# ---- SPRM novelty
nov = J("sprm_novelty_verdicts.jsonl")
sprm = [{"id": r["arg_id"], "sec": trunc(r["section"], 70), "ty": r["argument_type"], "cl": trunc(r["claim"], 300), "q": trunc(r["quote"], 220), "v": r["verdict"], "new": trunc(r["what_is_new"], 300), "vq": trunc(r.get("vehicle_quote") or "", 200), "vd": r.get("vehicle_doc") or ""} for r in nov]
# ---- the three endangerment themes (Marcus 2026-09-18): RTC sections grouped, with EPA's response text per section
THEMES = [
  {"key": "precedent", "name": "New Supreme Court precedent (major questions, Loper Bright) undermines the 2009 Finding",
   "sections": ["2.1.1.1.7", "2.1.1.1.8", "2.1.2", "2.1.2.1", "2.1.2.1.1", "2.1.2.1.2", "2.1.2.1.3", "2.1.2.1.4", "2.1.2.1.5", "2.1.2.1.6", "2.1.2.2", "2.1.2.2.1", "2.1.2.2.2", "2.1.2.3", "2.1.2.4", "2.1.2.4.1"]},
  {"key": "local", "name": "The 1970 Clean Air Act addresses only local and regional air pollution; CO2 was not a 'pollutant' as written",
   "sections": ["2.1.1.1", "2.1.1.1.1", "2.1.1.1.2", "2.1.1.1.3", "2.1.1.1.4", "2.1.1.1.5", "2.1.1.1.6", "2.1.1.2", "2.1.1.2.1", "2.1.1.2.2", "2.1.1.3", "2.1.1.3.5"]},
  {"key": "deminimis", "name": "Futility / de minimis: U.S. vehicle (or power-plant) emissions cannot materially affect global climate",
   "sections": ["2.1.1.3.1", "2.1.1.3.2", "2.1.1.3.3", "2.1.1.3.4", "2.1.1.3.6", "2.1.1.3.7", "2.1.1.3.8", "2.2", "2.2.1", "2.2.2", "2.2.3", "2.2.4", "2.2.5", "2.2.6"]},
]
S_RTC = {x["num"]: x for x in json.load((CD / "rtc" / "rtc_sections.json").open())}
def resp_head(num, n=900):
    # response text only; a parent section with no "EPA Response" block of its own gets "" (the page marks it heading-only)
    x = S_RTC.get(num)
    if not x or not x["responses"]: return ""
    r = re.sub(r"\s+", " ", " ".join(x["responses"])).strip()
    if r.lower().startswith("response"): r = r[len("response"):].strip(" :")
    return trunc(r, n)
RTC_LINES = (CD / "rtc" / "RTC_31089.txt").read_text().split("\n")
def rtc_page(num):
    # PDF page of the section heading (form feeds before its line_start), the same page convention as the verdict citations;
    # the sections file's own "page" field is the PRINTED page number, 8 lower, and None for some sections
    x = S_RTC.get(num)
    return "\n".join(RTC_LINES[:x["line_start"]]).count("\f") + 1 if x else None
themes = [{"key": t["key"], "name": t["name"], "sections": [{"num": n, "title": S_RTC[n]["title"] if n in S_RTC else n, "response": resp_head(n), "page": rtc_page(n)} for n in t["sections"] if n in S_RTC]} for t in THEMES]

# ---- hand audit (Marcus). Reported only once a worksheet carries labels; an unlabelled worksheet yields
# None and the page says nothing, rather than advertising an audit that has not happened. The auditor's
# own file (audit_blind.*.csv) wins over the blank template, and his shorthand is normalised here rather
# than being imposed on him while he reads.
HAND_LABELS = {"support": "support_rescission", "oppose": "oppose_rescission", "mixed": "mixed",
               "unclear": "unclear", "off topic": "off_topic", "off_topic": "off_topic"}
DET = ("oppose_rescission", "support_rescission")

def _wilson(k, n, z=1.96):
    if not n: return (0.0, 1.0)
    import math
    pp = k / n; d = 1 + z * z / n; c = (pp + z * z / (2 * n)) / d
    h = z * math.sqrt(pp * (1 - pp) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))

def _hand_audit():
    import csv as _csv
    cand = sorted(HERE.glob("audit_blind*.csv"), key=lambda q: (q.name == "audit_blind.csv", q.name))
    k = HERE / "audit_key.csv"
    if not (cand and k.exists()): return None
    key = {r["docket_id"]: r for r in _csv.DictReader(k.open())}
    for src in cand:
        rows = [r for r in _csv.DictReader(src.open()) if (r.get("hand_stance") or "").strip()]
        if not rows: continue
        pairs = [(HAND_LABELS[r["hand_stance"].strip().lower()], key[r["docket_id"]]["model_stance"]) for r in rows]
        prec = {}
        for lab in DET:
            sub = [(h, m) for h, m in pairs if m == lab]
            ok = sum(1 for h, m in sub if h == m); lo, _hi = _wilson(ok, len(sub))
            prec[lab] = {"n": len(sub), "ok": ok, "lo": round(lo, 3)}
        coll = lambda x: "no_stance" if x in ("unclear", "off_topic") else x
        per = [{"docket_id": r["docket_id"], "hand": h, "note": (r.get("hand_note") or "").strip(),
                "model": m, "conf": key[r["docket_id"]]["model_confidence"],
                "type": key[r["docket_id"]]["commenter_type"], "agree": int(h == m)}
               for r, (h, m) in zip(rows, pairs)]
        return {"source": src.name, "rows": per, "n": len(rows), "of": sum(1 for _ in _csv.DictReader((HERE / "audit_blind.csv").open())),
                "agree": sum(1 for h, m in pairs if h == m),
                "agree_collapsed": sum(1 for h, m in pairs if coll(h) == coll(m)),
                "flips": sum(1 for h, m in pairs if h in DET and m in DET and h != m),
                "both_minority": sum(1 for h, m in pairs if h != m and h not in DET and m not in DET),
                "prec_oppose": prec["oppose_rescission"], "prec_support": prec["support_rescission"]}
    return None

# ---- RTC out-of-scope table
oos = pd.read_csv(CD / "rtc_out_of_scope_sections.csv").to_dict("records")
for r in oos: r["title"] = S_RTC[str(r["sec"])]["title"] if str(r["sec"]) in S_RTC else r["title"]  # the csv truncates titles at 55 chars
# ---- summaries
det = cdf[cdf.s.isin(["oppose_rescission", "support_rescission"])]
camp_live = camp[camp.stance.isin(["OPPOSE", "SUPPORT"])]
co, cs = int(camp_live[camp_live.stance == "OPPOSE"].weight.sum()), int(camp_live[camp_live.stance == "SUPPORT"].weight.sum())
uo, us = int((cdf.s == "oppose_rescission").sum()), int((cdf.s == "support_rescission").sum())
summary = {
  "docket": D, "epa_total": 572000, "epa_unique": 31000, "epa_campaigns": 169, "epa_campaign_comments": 534000,
  # the speaker count is EPA's OWN (91 FR 7693; RTC p.2 adds "over more than 30 hours"); the direction
  # is the one independent tally of the hearing anyone published
  "hearing": {"speakers": "more than 600", "days": 4, "dates": "August 19-22, 2025",
              "epa_cite": "91 FR 7693",
              "tally": "hundreds of Americans speak out against the EPA proposal and fewer than 20 speak in favor",
              "tally_by": "Grace van Deelen, Eos (25 August 2025)",
              "tally_url": "https://eos.org/research-and-developments/public-speaks-out-against-epa-plan-to-rescind-endangerment-finding"},
  "layers": [{"layer": "Mass comment campaigns (by signature, EPA Appendix A counts)", "o": co, "s": cs, "n": int(len(camp_live)), "unit": "signatures"},
             {"layer": "Individually submitted comments", "o": uo, "s": us, "n": int(len(cdf)), "unit": "comments"},
             {"layer": "All comments", "o": co + uo, "s": cs + us, "n": None, "unit": "comments"}],
  "unique_stance": cdf.s.value_counts().to_dict(), "unique_total": int(len(cdf)),
  "by_entity": pd.crosstab(cdf.e, cdf.s).reindex(columns=["oppose_rescission", "support_rescission", "mixed", "unclear", "off_topic"], fill_value=0).astype(int).reset_index().rename(columns={"e": "entity"}).to_dict("records"),
  "by_subtype": pd.crosstab(cdf.st, cdf.s).reindex(columns=["oppose_rescission", "support_rescission", "mixed", "unclear", "off_topic"], fill_value=0).astype(int).reset_index().rename(columns={"st": "subtype"}).to_dict("records"),
  "campaign_counts": camp.stance.value_counts().to_dict(),
  "substantive_docs": 1167, "raw_arguments": 14114, "canonical": int(len(adf)), "shortlist": int(adf.short.sum()), "shortlist_2plus": int((adf.short & (adf.ncm >= 2)).sum()),
  "verdict_dist": {k: adf.v.map(lambda v: v[k]).value_counts().to_dict() for k in DOCS},
  "oos_whole_sections": 8, "oos_whole_args": 5720, "oos_whole_commenters": 1007, "oos_any_sections": 25, "rtc_leaf_sections": 93,
  "rtc_rerun": 236, "rtc_summary_quotes_left": 4,
  "campaign_moved": n_moved, "campaign_relabeled": n_relabeled,
  "names_published": int(sum(1 for c in comments if not c["an"])), "names_withheld": int(sum(c["an"] for c in comments)),
  "sprm_total": len(sprm), "sprm_verdicts": pd.Series([r["v"] for r in sprm]).value_counts().to_dict(),
  "audit": {"n": 150, "agree": 140, "flips": 0, "prec_lo": 0.94},
  "hand_audit": _hand_audit(),
  "models": "Sonnet 5 (bulk stance) + Opus 5 (re-check, extraction, canonicalization, coverage, novelty); Batch API", "model_bulk": "Sonnet 5",
  "built": pd.Timestamp.now().strftime("%Y-%m-%d"), "sprm_deadline": "2026-11-02",
  "repo": REPO_URL, "substack": SUBSTACK_POST_URL,
}
bundle = {"summary": summary, "comments": comments, "campaigns": campaigns, "arguments": args, "sprm": sprm, "oos": oos, "themes": themes}
(HERE / "data.json").write_text(json.dumps(bundle, separators=(",", ":")))
print(f"data.json: {(HERE/'data.json').stat().st_size/1e6:.1f} MB | comments {len(comments):,} | campaigns {len(campaigns)} | arguments {len(args):,} (shortlist {summary['shortlist']}) | sprm {len(sprm)}")
