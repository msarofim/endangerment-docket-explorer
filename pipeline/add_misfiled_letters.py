#!/usr/bin/env python
"""Multi-author letters that EPA's RTC Appendix A logged as "Mass Comment Campaign … organization unknown
(Name et al)" (found 2026-09-21 by reading every Appendix A row with <= 110 signatures). Four are single
substantive letters with a named author list and belong in the individually-submitted layer; four others
are genuine sign-on / compiled submissions and stay in the campaign layer with a label.

    python add_misfiled_letters.py            run everything below, synchronously (Opus 5; ~$5)

Steps: corpus rows + attachments -> Pass A stance (Opus) -> Pass B argument extraction (Opus) -> raw args
attached to the nearest canonical argument in their RTC topic (TF-IDF cosine >= SIM_MIN) or made new
canonicals -> Pass C verdicts for the new canonicals (RTC judged on response text only) -> reports.
Idempotent: every step skips ids already present in its output file.
"""
import json, re, shutil, subprocess, sys, time
from pathlib import Path
import anthropic, numpy as np, pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import classify_unique as A, pass_b as B, pass_c as C
from retrieval import paragraphs, tok
from rtc_response_text import response_blocks, response_text
from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent; D = "EPA-HQ-OAR-2025-0194"; RUN = "sync-2026-09-21"
SIM_MIN = 0.35            # cosine below which a new raw argument becomes its own canonical argument
MOVED = {                 # Appendix A id -> what the letter is (moved to the individual layer)
    "1461": "10 atmospheric chemists, University of Colorado Boulder (Nathan Sweet et al.)",
    "1462": "9 transportation/energy economists (Antonio Bento, Kenneth Gillingham, Mark Jacobsen, Christopher Knittel, Benjamin Leard, Joshua Linn, David Rapson, Arthur van Benthem, Kate Whitefoot)",
    "1448": "34 students and faculty, Northeastern University (joint letter)",
    "1449": "28 current and former state public utility commissioners (joint letter)",
}
RELABELED = {             # stay in the campaign layer; note shown on the site
    "1446": ("OPPOSE", "sign-on letter, 108 Wyoming residents"),
    "1506": ("OPPOSE", "sign-on letter with handwritten signature sheets (61)"),
    "1429": ("OPPOSE", "compiled individual letters from Quinhagak, AK students (25)"),
    "1450": ("EXCLUDE", "duplicate of 1429 (same compiled letters)"),
}
full = lambda s: f"{D}-{s}"
CORPUS = HERE / f"unique_comments_{D}.jsonl"; ATT = HERE / "attachments_unique"; SRC = HERE / "attachments_campaign"
client = anthropic.Anthropic(); spent = [0.0]
def bill(msg, pin=5.0, pout=25.0):
    u = msg.usage; spent[0] += (u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)) * pin / 1e6 + u.output_tokens * pout / 1e6

# ---- 1. campaign table: move / relabel -------------------------------------------------------------
camp = pd.read_csv(HERE / f"campaign_stance_{D}.csv", dtype=str)
if "note" not in camp.columns: camp["note"] = ""
for sid, what in MOVED.items():
    m = camp.id == full(sid); camp.loc[m, "stance"] = "EXCLUDE"; camp.loc[m, "note"] = f"reclassified {RUN[5:]}: single multi-author letter — {what}; counted in the individually submitted comments"
for sid, (st, what) in RELABELED.items():
    m = camp.id == full(sid); camp.loc[m, "stance"] = st; camp.loc[m, "note"] = what
camp.to_csv(HERE / f"campaign_stance_{D}.csv", index=False); print("campaign table updated")

# ---- 2. corpus rows + attachments -------------------------------------------------------------------
have = {json.loads(l)["id"] for l in CORPUS.open()}
with CORPUS.open("a") as out:
    for sid, what in MOVED.items():
        if full(sid) in have: continue
        src = sorted(SRC.glob(f"{full(sid)}_*.pdf")); dst = ATT / f"{full(sid)}_0.pdf"
        if not dst.exists(): shutil.copy(src[0], dst)
        text = subprocess.run(["pdftotext", "-layout", str(dst), "-"], capture_output=True, text=True).stdout
        a = json.load((HERE / "cache" / f"{full(sid)}.json").open())["data"]["attributes"]
        row = {"id": full(sid), "title": a["title"], "subtype": a["subtype"], "organization": a.get("organization"), "govAgency": a.get("govAgency"),
               "govAgencyType": a.get("govAgencyType"), "city": a.get("city"), "state": a.get("stateProvinceRegion"), "country": a.get("country"),
               "postedDate": a["postedDate"], "receiveDate": a.get("receiveDate"), "late": False, "duplicateComments": a.get("duplicateComments"),
               "pageCount": len(text.split("\f")) - 1, "n_attachments": 1, "attachment_urls": [], "text_source": "attachment", "text": text,
               "text_chars": len(text), "substantive": True, "substantive_reason": "multi-author letter logged by EPA Appendix A as a mass-mail campaign",
               "reclassified": f"{RUN[5:]}: from campaign layer — {what}"}
        out.write(json.dumps(row) + "\n"); print("corpus +", sid, len(text), "chars")
corpus = {r["id"]: r for r in (json.loads(l) for l in CORPUS.open()) if r["id"] in {full(s) for s in MOVED}}

# ---- 3. Pass A (stance, Opus) -------------------------------------------------------------------------
have = {json.loads(l)["id"] for l in A.OUT_FINAL.open()}
with A.OUT_FINAL.open("a") as out:
    for sid in MOVED:
        if full(sid) in have: continue
        r = corpus[full(sid)]; msg = client.messages.create(**A.params(r, model=A.MODEL_RECHK)); d = A.parse(msg); bill(msg)
        out.write(json.dumps({"id": full(sid), "batch_id": RUN, "model": A.MODEL_RECHK, "prompt_sha": A.PROMPT_SHA, "fetched": time.strftime("%Y-%m-%d"), **d,
                              "result": "ok", "usage_in": msg.usage.input_tokens, "usage_out": msg.usage.output_tokens, "rechecked": True, "disagree": False,
                              "reclassified": corpus[full(sid)]["reclassified"]}) + "\n")
        print("Pass A", sid, d["stance"], d["entity_type"], "|", d["key_phrase"][:80])

# ---- 4. Pass B (arguments, Opus) ----------------------------------------------------------------------
have = {json.loads(l)["id"] for l in B.OUT.open()}
with B.OUT.open("a") as out:
    for sid in MOVED:
        if full(sid) in have: continue
        r = corpus[full(sid)]; body, n, fl = B.read_attachment(ATT / f"{full(sid)}_0.pdf", "main")
        row = {"id": full(sid), "title": r["title"], "subtype": r["subtype"], "group": r["title"], "part": 1, "nparts": 1, "pageCount": n,
               "substantive_reason": r["substantive_reason"], "n_attachments": 1, "flags": fl, "text": f"=== MAIN LETTER ({n} pp) ===\n{body}", "text_chars": len(body), "scanned_pdfs": []}
        msg = client.messages.create(**B.params(row)); d = B.parse(msg); bill(msg)
        out.write(json.dumps({"id": full(sid), "batch_id": RUN, "model": B.MODEL, "prompt_sha": B.PROMPT_SHA, "fetched": time.strftime("%Y-%m-%d"), **d, "result": "ok",
                              "usage_in": msg.usage.input_tokens, "usage_out": msg.usage.output_tokens, "stop_reason": msg.stop_reason}) + "\n")
        print("Pass B", sid, d["commenter_name"][:50], "|", len(d["arguments"]), "arguments")
B.report()   # regenerates arguments_flat (appended rows keep existing row indices, so existing arg_ids are unchanged)

# ---- 5. attach raw args to canonicals, or make new ones ---------------------------------------------
args = C.load_args(); new = args[args.id.isin({full(s) for s in MOVED})]
canon = [json.loads(l) for l in C.CANON_OUT.open()]; verd = [json.loads(l) for l in C.VERD_OUT.open()]
vix = {r["canon_id"]: r for r in verd}; assigned = {m for c in canon for m in c["member_ids"]}
new = new[~new.arg_id.isin(assigned)]; print(len(new), "new raw arguments to place")
made, joined = [], []
for topic, g in new.groupby("topic_code"):
    cs = [c for c in canon if c["topic_code"] == topic]
    if cs:
        vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True).fit([c["canonical_claim"] for c in cs] + list(g.claim))
        S = cosine_similarity(vec.transform(g.claim), vec.transform([c["canonical_claim"] for c in cs]))
    for k, (_, r) in enumerate(g.iterrows()):
        j = int(S[k].argmax()) if cs else -1
        if cs and S[k][j] >= SIM_MIN:
            c = cs[j]; c["member_ids"] = sorted(set(c["member_ids"]) | {r.arg_id}); c["n_members"] = len(c["member_ids"])
            c["commenters"] = sorted(set(c["commenters"]) | {r.commenter}); c["n_commenters"] = len(c["commenters"]); c["n_commenters_model"] = c.get("n_commenters_model", 0) + 1
            c["commenter_types"][r.ctype] = c["commenter_types"].get(r.ctype, 0) + 1; c.setdefault("added", []).append({"arg_id": r.arg_id, "cos": round(float(S[k][j]), 2), "run": RUN})
            if c["canon_id"] in vix:
                v = vix[c["canon_id"]]; v.update({k2: c[k2] for k2 in ("member_ids", "n_members", "commenters", "n_commenters", "n_commenters_model", "commenter_types", "added")})
            joined.append((r.arg_id, c["canon_id"], round(float(S[k][j]), 2)))
        else:
            cid = f"{topic}|{RUN}|{len(made)}"
            c = {"canon_id": cid, "topic_code": topic, "topic_label": r.topic_label, "canonical_claim": r.claim, "position": r.position, "applies_to": r.applies_to,
                 "member_ids": [r.arg_id], "n_members": 1, "commenters": [r.commenter], "n_commenters": 1, "commenter_types": {r.ctype: 1}, "sample_quote": r.quote,
                 "sample_doc": r.id, "n_members_sim": 0, "member_ids_model": [r.arg_id], "n_members_model": 1, "n_commenters_model": 1, "added": [{"arg_id": r.arg_id, "run": RUN}]}
            canon.append(c); cs.append(c); made.append(c)
print(f"joined existing canonicals: {len(joined)}; new canonicals: {len(made)}")
C.CANON_OUT.write_text("".join(json.dumps(c) + "\n" for c in canon))

# ---- 6. verdicts for the new canonicals (4 docs), RTC re-judged on response text only ---------------
S_ = json.load((HERE / "rtc" / "rtc_sections.json").open()); SEC = {s["num"]: s for s in S_}
resp_paras = [(s["num"], p) for s in S_ for blk in response_blocks(s) for p in paragraphs(blk)]
bm = BM25Okapi([tok(p) for _, p in resp_paras])
RSYS = """You are checking whether U.S. EPA's Response to Comments (RTC) on its 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding RESPONDED to an argument raised by commenters. You will be given one canonical argument and passages drawn ONLY from the RTC's 'EPA Response' text (never from its summaries of what commenters said). Judge: addressed_directly (engages this specific argument on its merits), addressed_generally (responds to the topic in a way that covers it without engaging its specific point), dismissed_out_of_scope (explicitly declines to respond), not_addressed. Quote the decisive EPA sentence verbatim (<= 30 words) and give the passage index; the quote must be EPA speaking in its own voice, not EPA restating a commenter."""
RSCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["addressed_directly", "addressed_generally", "dismissed_out_of_scope", "not_addressed"]}, "quote": {"type": "string"}, "passage_index": {"type": ["integer", "null"]}, "note": {"type": "string"}}, "required": ["verdict", "quote", "passage_index", "note"], "additionalProperties": False}
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
for c in made:
    if c["canon_id"] in vix: continue
    msg = client.messages.create(**C._params(C.VERD_SYS, C.VERD_SCHEMA, C.verdict_user(c, SEC), 3000)); d = C._parse(msg); bill(msg)
    row = {**c, **d, "usage_in": msg.usage.input_tokens, "usage_out": msg.usage.output_tokens}
    # RTC: response-only judgement (the general verdict retrieves over summaries too)
    q = c["canonical_claim"] + " " + c["sample_quote"]; sc = bm.get_scores(tok(q)); top = sorted(range(len(sc)), key=lambda j: -sc[j])[:6]
    sec = SEC.get(c["topic_code"]); sec_resp = response_text(sec)
    user = (f"CANONICAL ARGUMENT [{c['canon_id']}] (RTC topic {c['topic_code']}; {c['n_commenters']} commenters; position {c['position']})\n{c['canonical_claim']}\nSample quote: \"{c['sample_quote']}\"\n\n"
            + (f"=== EPA RESPONSE text of RTC section {c['topic_code']} (first 8,000 chars) ===\n{sec_resp}\n\n" if sec_resp else "")
            + "=== Top EPA RESPONSE passages retrieved from the whole RTC ===\n" + "\n".join(f"[{j}] (section {resp_paras[j][0]}) {resp_paras[j][1][:1600]}" for j in top))
    m2 = client.messages.create(**MessageCreateParamsNonStreaming(model=C.MODEL, max_tokens=1500, system=[{"type": "text", "text": RSYS, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": RSCHEMA}}, messages=[{"role": "user", "content": user}])); d2 = json.loads(next(b.text for b in m2.content if b.type == "text")); bill(m2)
    v = row["verdicts"]["vehicle_rtc"]; v.update({"verdict_v1": v["verdict"], "quote_v1": v["quote"], "verdict": d2["verdict"], "quote": d2["quote"], "passage_index": d2["passage_index"], "retrieval_note": d2["note"], "rerun": f"response-only {RUN[5:]}"})
    verd.append(row); vix[c["canon_id"]] = row
    print(f"verdict {c['canon_id']}: " + " ".join(f"{k}={row['verdicts'][k]['verdict'][:9]}" for k in C.DOCS) + f" | {c['canonical_claim'][:90]}")
C.VERD_OUT.write_text("".join(json.dumps(r) + "\n" for r in verd))
subprocess.run([sys.executable, str(HERE / "locate_quotes.py")], check=True)
C.report()
print(f"\njoined: {joined}\nspent ≈ ${spent[0]:.2f}")
