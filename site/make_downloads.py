#!/usr/bin/env python
"""Build the site's downloadable tables. Every file is derived from site/data.json — the same bundle the
page renders — so a download can never disagree with the figure above it, and the name policy applied in
build_data.py is inherited rather than re-implemented.

    python make_downloads.py            -> dist/data/*.csv  (+ prints row counts and sizes)

Deliberately NOT published: unique_comments_<docket>.jsonl (321 MB of comment full text). That is
regulations.gov's corpus, not an analysis product; the page links to each entry there instead.
"""
import csv, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "data.json"
OUT = HERE / "dist" / "data"
DOCKET = "EPA-HQ-OAR-2025-0194"
REGS = f"https://www.regulations.gov/comment/{DOCKET}-"
DOCS = ["vehicle_rtc", "vehicle_fr", "sprm_fr", "partial_repeal"]
AUDIT_N, AUDIT_SEED = 120, 20260929   # Marcus's hand audit: short, stratified by final model stance


MANIFEST = []          # (file, label) pairs, written back into data.json so the page never hand-lists them
LABELS = {"comments_stance.csv": "per-comment stance", "arguments_canonical.csv": "canonical arguments + verdicts",
          "shortlist_unaddressed.csv": "unaddressed shortlist", "campaigns.csv": "campaigns",
          "sprm_novelty.csv": "power-plant novelty", "audit_sample.csv": "hand audit"}


def write(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    with p.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"  {name:<28} {len(rows):>7,} rows  {p.stat().st_size/1e6:>6.2f} MB")
    MANIFEST.append({"file": name, "label": LABELS[name], "rows": len(rows), "bytes": p.stat().st_size})
    return p


def main():
    D = json.loads(BUNDLE.read_text()); S = D["summary"]

    write("comments_stance.csv",
          ["docket_id", "url", "posted", "submitter", "name_withheld", "commenter_type", "stance",
           "confidence", "rechecked", "substantive", "key_phrase", "text_chars"],
          [[f"{DOCKET}-{c['id']}", REGS + c["id"], c["d"], c["t"], c["an"], c["e"], c["s"],
            c["c"], c["rc"], c["sub"], c["k"], c["n"]] for c in D["comments"]])

    a = D["arguments"]
    write("arguments_canonical.csv",
          ["canon_id", "rtc_section", "rtc_section_title", "claim", "position", "applies_to",
           "n_commenters_model", "n_commenters_filled", "commenters_shown", "sample_docket_id", "sample_quote",
           "on_unaddressed_shortlist"]
          + [f"{k}_{s}" for k in DOCS for s in ("verdict", "quote", "location")],
          [[r["id"], r["tc"], r["tl"], r["cl"], r["pos"], r["ap"], r["ncm"], r["nc"],
            "; ".join(r["who"]), f"{DOCKET}-{r['doc']}", r["q"], r["sl"]]
           + [v for k in DOCS for v in (
               r["v"][k], r["vq"][k],
               (lambda l: "" if not l else (f"RTC {l.get('s') or '?'} p.{l.get('p')}" if k == "vehicle_rtc"
                                            else f"FR p.{l.get('p')}"))(r["vl"][k]))]
           for r in a])

    sl = [r for r in a if r["sl"]]
    write("shortlist_unaddressed.csv",
          ["canon_id", "rtc_section", "claim", "n_commenters_model", "commenters_shown",
           "vehicle_rtc", "vehicle_fr", "sprm_fr"],
          [[r["id"], r["tc"], r["cl"], r["ncm"], "; ".join(r["who"]),
            r["v"]["vehicle_rtc"], r["v"]["vehicle_fr"], r["v"]["sprm_fr"]]
           for r in sorted(sl, key=lambda r: -r["ncm"])])

    write("campaigns.csv",
          ["docket_id", "url", "sponsor", "signatures", "signature_source", "stance", "decisive_text", "note"],
          [[f"{DOCKET}-{c['id']}", REGS + c["id"], c["t"], c["w"], c["src"], c["s"], c["ev"], c.get("note", "")]
           for c in sorted(D["campaigns"], key=lambda c: -c["w"])])

    write("sprm_novelty.csv",
          ["arg_id", "preamble_section", "argument_type", "epa_claim", "quote", "verdict",
           "what_is_new", "closest_vehicle_passage", "vehicle_document"],
          [[r["id"], r["sec"], r["ty"], r["cl"], r["q"], r["v"], r["new"], r["vq"], r["vd"]] for r in D["sprm"]])

    # ---- hand audit. The BLIND worksheet carries no model columns, so the auditor cannot see the label
    # being checked; the answer key is written beside it and joined only after the worksheet is filled in.
    # audit_sample.csv is PUBLISHED only once the worksheet has labels — an empty audit is not evidence.
    import random
    rng = random.Random(AUDIT_SEED)
    strata = {"oppose_rescission": 45, "support_rescission": 45, "unclear": 15, "mixed": 8, "off_topic": 7}
    rows = []
    for st, n in strata.items():
        pool = sorted([c for c in D["comments"] if c["s"] == st], key=lambda c: c["id"])
        rows += rng.sample(pool, min(n, len(pool)))
    rng.shuffle(rows)
    cand = sorted(HERE.glob("audit_blind*.csv"), key=lambda q: (q.name == "audit_blind.csv", q.name))
    blind, key = (next((c for c in cand if any((r.get("hand_stance") or "").strip()
                        for r in csv.DictReader(c.open()))), HERE / "audit_blind.csv"), HERE / "audit_key.csv")
    if not any((r.get("hand_stance") or "").strip() for r in csv.DictReader(blind.open())) if blind.exists() else True:
      with (HERE / "audit_blind.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["docket_id", "url", "hand_stance", "hand_note"])
        w.writerows([[f"{DOCKET}-{c['id']}", REGS + c["id"], "", ""] for c in rows])
    with key.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["docket_id", "model_stance", "model_confidence", "commenter_type"])
        w.writerows([[f"{DOCKET}-{c['id']}", c["s"], c["c"], c["e"]] for c in rows])
    print(f"  audit_blind.csv              {len(rows):>7,} rows  (worksheet; seed {AUDIT_SEED}, strata {strata})")
    ha = S.get("hand_audit")
    if ha and ha.get("rows"):
        # emitted from the bundle's own normalised rows -- the label mapping lives in build_data.py and
        # must not be re-implemented here, or the published "agree" column can disagree with the page
        write("audit_sample.csv", ["docket_id", "url", "commenter_type", "hand_stance", "hand_note",
                                   "model_stance", "model_confidence", "agree"],
              [[r["docket_id"], REGS + r["docket_id"].split("-")[-1], r["type"], r["hand"], r["note"],
                r["model"], r["conf"], r["agree"]] for r in ha["rows"]])
    else:
        print("  audit_sample.csv             not published — audit_blind.csv has no labels yet")

    D["summary"]["downloads"] = MANIFEST
    BUNDLE.write_text(json.dumps(D, separators=(",", ":")))
    print(f"\nwrote {len(MANIFEST)} files to {OUT}; manifest written into data.json")


if __name__ == "__main__":
    sys.exit(main())
