#!/usr/bin/env python
"""
Pass C — canonicalize the Pass-B arguments and map each canonical argument onto the four coverage
documents (vehicle RTC, vehicle final rule, power-plant SPRM, power-plant final partial repeal).

    python pass_c.py canon-submit | canon-fetch      merge restatements within each RTC topic -> canonical arguments
    python pass_c.py verdict-dry [N] | verdict-submit | verdict-fetch | report

Verdict per (canonical argument × document): addressed_directly | addressed_generally |
dismissed_out_of_scope | not_addressed, each with a verbatim quote + paragraph index from that document.
Every not_addressed / dismissed verdict on an `applies_to = both` argument is hand-verified before publication.
Retrieval: BM25 top-K paragraphs per document (retrieval.py) + the full RTC section for the topic.
"""
import hashlib, json, random, re, sys, time
from collections import defaultdict
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from retrieval import DocIndex, DOCS

DOCKET_ID = "EPA-HQ-OAR-2025-0194"
MODEL, EFFORT = "claude-opus-5", "medium"
CANON_CHUNK, TOPK = 120, 4          # TOPK trimmed 8 -> 4 (2026-09-18, budget)
PASSAGE_CAP = 1600                 # chars per retrieved passage shown
RTC_SECTION_CAP = 10000            # chars of the matching RTC section shown (was 30000)
MERGE_MAX_TOKENS = 48000
CONVERGE_FRAC = 0.90      # stop merging a topic when a round shrinks it by less than 10 %
MERGE_CHUNK = 60          # claims per merge request (smaller than round-0 chunks: outputs carry long member lists)

def semantic_chunks(items, size):
    """Group items (dicts with 'claim') into clusters of ~size by TF-IDF k-means; returns list of lists."""
    if len(items) <= size: return [items]
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    import numpy as np
    X = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1, sublinear_tf=True).fit_transform([it["claim"] for it in items])
    k = max(2, int(np.ceil(len(items) / size)))
    labels = KMeans(n_clusters=k, n_init=5, random_state=0).fit_predict(X)
    groups = [[it for it, l in zip(items, labels) if l == g] for g in range(k)]
    out = []
    for g in groups:                       # split any oversize cluster
        for j in range(0, len(g), size): out.append(g[j:j+size])
    return [g for g in out if g]
HERE = Path(__file__).resolve().parent
ARGS_CSV  = HERE / f"arguments_flat_{DOCKET_ID}.csv"
RTC_SECT  = HERE / "rtc" / "rtc_sections.json"
CANON_IN  = HERE / "passC_canon_inputs.jsonl"; CANON_OUT = HERE / f"canonical_arguments_{DOCKET_ID}.jsonl"
VERD_OUT  = HERE / f"coverage_verdicts_{DOCKET_ID}.jsonl"; OUT_MD = HERE / f"coverage_summary_{DOCKET_ID}.md"
LOG_C, LOG_V = HERE / "batches_passC_canon.json", HERE / "batches_passC_verdict.json"
PRICE_IN, PRICE_OUT = 2.50, 12.50

CANON_SYS = """You are consolidating arguments extracted from public comments on EPA's 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding and repeal vehicle GHG standards. You will receive a list of argument claims, all pre-tagged to the same topic of EPA's Response to Comments outline, each with an id and the commenter. Merge restatements of the SAME argument into one canonical argument; keep genuinely distinct arguments separate (different legal theory, different evidence, different mechanism). A canonical argument should be stated precisely enough that one can check whether an agency document responds to it. Preserve specific evidence: if several commenters cite the same study or fact, that is one canonical argument; if they cite different specific facts for the same general point, keep the general point as one argument and list distinct evidence items as separate arguments only when a response would have to engage them separately. Return every input id in exactly one canonical argument's member_ids."""
CANON_SCHEMA = {"type": "object", "properties": {"canonical": {"type": "array", "items": {"type": "object", "properties": {
    "canonical_claim": {"type": "string"}, "member_ids": {"type": "array", "items": {"type": "string"}},
    "position": {"type": "string", "enum": ["opposes_proposal", "supports_proposal", "neutral", "mixed"]},
    "applies_to": {"type": "string", "enum": ["both", "vehicle_only", "power_plant_only", "unsure"]}},
    "required": ["canonical_claim", "member_ids", "position", "applies_to"], "additionalProperties": False}}},
    "required": ["canonical"], "additionalProperties": False}

VERD_SYS = """You are checking whether U.S. EPA responded to an argument raised in public comments on its 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding and repeal vehicle GHG standards (finalized Feb 2026), and whether EPA's parallel September 2026 power-plant documents engage the same argument. You will be given ONE canonical argument (with the commenters who raised it and a sample verbatim quote) and, for each of four EPA documents, the most relevant passages retrieved by keyword search plus (for the vehicle RTC) the full text of the matching outline section. Judge each document separately:
- addressed_directly: the document engages this specific argument on its merits (agrees, rebuts, or explains why it does not change the outcome).
- addressed_generally: the document responds to the general topic in a way that covers the argument, but does not engage its specific point or evidence.
- dismissed_out_of_scope: the document explicitly declines to respond (out of scope, alternative rationale not adopted, not relied upon, beyond this action).
- not_addressed: none of the retrieved passages respond to it, and the argument is not the kind the document would obviously cover elsewhere. If you suspect it IS covered elsewhere in the document but the retrieval missed it, say so in retrieval_note and still return your best verdict.
Quote the decisive sentence verbatim (<= 30 words) and give the passage index. Be strict: a passage that merely mentions the same subject is 'addressed_generally' at most. For the two power-plant documents also record whether the argument is even applicable there (applies: yes/no)."""
VERD_SCHEMA = {"type": "object", "properties": {
    "verdicts": {"type": "object", "properties": {k: {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["addressed_directly", "addressed_generally", "dismissed_out_of_scope", "not_addressed"]},
        "quote": {"type": "string"}, "passage_index": {"type": ["integer", "null"]}, "applies": {"type": "boolean"}, "retrieval_note": {"type": "string"}},
        "required": ["verdict", "quote", "passage_index", "applies", "retrieval_note"], "additionalProperties": False} for k in DOCS},
        "required": list(DOCS), "additionalProperties": False},
    "applies_to": {"type": "string", "enum": ["both", "vehicle_only", "power_plant_only", "unsure"]},
    "summary": {"type": "string"}}, "required": ["verdicts", "applies_to", "summary"], "additionalProperties": False}

def _params(system, schema, user, max_tokens):
    return MessageCreateParamsNonStreaming(model=MODEL, max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}])
def _parse(msg): return json.loads(next(b.text for b in msg.content if b.type == "text"))

def _safe(cid):  # Batch API custom_id must match ^[a-zA-Z0-9_-]{1,64}$
    return re.sub(r"[^A-Za-z0-9_-]", "_", cid)[:64]

def _submit(reqs, log_path, tag):
    client = anthropic.Anthropic(); log = json.loads(log_path.read_text()) if log_path.exists() else []
    idmap = {}
    for r in reqs:
        s = _safe(r["custom_id"]); assert s not in idmap, f"custom_id collision {s}"; idmap[s] = r["custom_id"]; r["custom_id"] = s
    b = client.messages.batches.create(requests=reqs)
    log.append({"batch_id": b.id, "n": len(reqs), "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": MODEL, "status": b.processing_status, "tag": tag, "idmap": idmap})
    log_path.write_text(json.dumps(log, indent=1)); print("submitted", b.id, len(reqs)); return b.id

def _fetch(log_path):
    client = anthropic.Anthropic(); log = json.loads(log_path.read_text()); out = {}
    for e in log:
        b = client.messages.batches.retrieve(e["batch_id"]); e["status"] = b.processing_status; c = b.request_counts
        print(f"{b.id}: {b.processing_status} ok={c.succeeded} err={c.errored} proc={c.processing}")
        if b.processing_status != "ended": continue
        idmap = e.get("idmap", {})
        for res in client.messages.batches.results(b.id):
            res.custom_id = idmap.get(res.custom_id, res.custom_id)
            if res.result.type == "succeeded" and res.result.message.stop_reason != "refusal":
                u = res.result.message.usage
                try: out[res.custom_id] = {"data": _parse(res.result.message), "usage_in": u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0), "usage_out": u.output_tokens, "stop_reason": res.result.message.stop_reason}
                except Exception as ex: out[res.custom_id] = {"error": f"parse: {ex}"}
            else: out[res.custom_id] = {"error": res.result.type}
    log_path.write_text(json.dumps(log, indent=1)); return out

# ---------------------------------------------------------------- canonicalize
def load_args():
    import pandas as pd
    a = pd.read_csv(ARGS_CSV); a["arg_id"] = [f"{r.id.split('-')[-1]}#{i}" for i, r in a.iterrows()]
    return a

def canon_submit():
    a = load_args(); reqs, inputs = [], []
    for topic, g in a.groupby("topic_code"):
        rows = g.to_dict("records"); random.Random(1).shuffle(rows)
        for k in range(0, len(rows), CANON_CHUNK):
            chunk = rows[k:k+CANON_CHUNK]; cid = f"{topic}|{k//CANON_CHUNK}"
            user = f"TOPIC {topic} — {chunk[0]['topic_label']}\n\n" + "\n".join(f"[{r['arg_id']}] ({r['commenter'][:60]}; {r['position']}; {r['applies_to']}) {r['claim']}" for r in chunk)
            reqs.append(Request(custom_id=cid, params=_params(CANON_SYS, CANON_SCHEMA, user, 16000)))
            inputs.append({"custom_id": cid, "topic": topic, "n": len(chunk), "ids": [r["arg_id"] for r in chunk]})
    CANON_IN.write_text("".join(json.dumps(x) + "\n" for x in inputs))
    CANON_ROUNDS.write_text(json.dumps({"round": 0, "topics": {}, "pending": {x["custom_id"]: {"topic": x["topic"], "lookup": None} for x in inputs}}))
    print(f"{len(a)} arguments, {a.topic_code.nunique()} topics, {len(reqs)} canonicalization requests")
    for k in range(0, len(reqs), 2000): _submit(reqs[k:k+2000], LOG_C, "canon")

CANON_ROUNDS = HERE / "passC_canon_rounds.json"   # state of the recursive merge: {topic: [ {claim, member_ids, position, applies_to} ... ]}

def canon_fetch():
    """Collect chunk-level canonicals per topic; where a topic spans >1 chunk, submit a merge round over the
    canonical claims themselves (each carrying its member ids) and repeat until every topic is one list.
    Run repeatedly: each call fetches finished batches and submits the next round if needed."""
    a = load_args().set_index("arg_id"); res = _fetch(LOG_C)
    inputs = {x["custom_id"]: x for x in map(json.loads, CANON_IN.open())}
    state = json.loads(CANON_ROUNDS.read_text()) if CANON_ROUNDS.exists() else {"round": 0, "topics": {}, "pending": {}}
    # ingest results for this round
    for cid, r in res.items():
        if cid not in state["pending"]: continue
        if "error" in r: print("ERR", cid, r["error"]); continue
        topic = state["pending"][cid]["topic"]; lookup = state["pending"][cid].get("lookup")  # lookup: canonical-id -> member arg_ids (merge rounds)
        for c in r["data"]["canonical"]:
            members = []
            for m in c["member_ids"]:
                members += (lookup.get(m, []) if lookup else ([m] if m in a.index else []))
            state["topics"].setdefault(topic, {}).setdefault(str(state["round"]), []).append({**c, "member_ids": sorted(set(members))})
        del state["pending"][cid]
    if state["pending"]:
        print(f"round {state['round']}: {len(state['pending'])} requests still pending"); CANON_ROUNDS.write_text(json.dumps(state)); return
    # decide next round per topic: topics whose current-round output came from >1 request need another merge
    nxt, reqs, done = state["round"] + 1, [], {}
    finished = set(state.get("done_topics", []))
    for topic, rounds in state["topics"].items():
        last_round = max(int(k) for k in rounds if rounds[k]); cur = rounds[str(last_round)]
        if topic in finished or last_round < state["round"]:      # settled in an earlier round: keep, never resubmit
            done[topic] = cur; finished.add(topic); continue
        n_req = len([k for k in inputs if k.split("|")[0] == topic]) if state["round"] == 0 else state.get("nreq", {}).get(topic, 1)
        prev = rounds.get(str(state["round"] - 1)) if state["round"] > 0 else None
        converged = prev is not None and len(cur) >= CONVERGE_FRAC * len(prev)   # a round that merges < 10 % = genuinely distinct
        if n_req <= 1 or converged:
            done[topic] = cur; finished.add(topic); continue
        # merge round: present the chunk-level canonicals as claims, re-chunked SEMANTICALLY (TF-IDF k-means)
        # so that restatements land in the same request — random chunks merged only ~20 % per round on 3.1
        items = [{"cid": f"{topic}|m{nxt}|{i}", "claim": c["canonical_claim"], "members": c["member_ids"], "position": c["position"], "applies_to": c["applies_to"], "n": len(c["member_ids"])} for i, c in enumerate(cur)]
        k_req = 0
        for ci, chunk in enumerate(semantic_chunks(items, MERGE_CHUNK)):
            cid = f"{topic}|r{nxt}|{ci}"; k_req += 1
            user = f"TOPIC {topic} — MERGE ROUND {nxt}: these are already-consolidated arguments from different subsets of comments on the same topic; merge duplicates across subsets.\n\n" + "\n".join(f"[{it['cid']}] ({it['n']} comments; {it['position']}; {it['applies_to']}) {it['claim']}" for it in chunk)
            reqs.append(Request(custom_id=cid, params=_params(CANON_SYS, CANON_SCHEMA, user, MERGE_MAX_TOKENS)))
            state["pending"][cid] = {"topic": topic, "lookup": {it["cid"]: it["members"] for it in chunk}}
        state.setdefault("nreq", {})[topic] = k_req
    finished_topics = finished; state["done_topics"] = sorted(finished)
    if reqs:
        state["round"] = nxt; CANON_ROUNDS.write_text(json.dumps(state))
        print(f"submitting merge round {nxt}: {len(reqs)} requests over {len(state['pending'])} pending; {len(finished_topics)} topics finished")
        for k in range(0, len(reqs), 2000): _submit(reqs[k:k+2000], LOG_C, f"canon-round{nxt}")
        return
    # all topics finished: write canonical file
    canon = []
    for topic, cs in done.items():
        for j, c in enumerate(cs):
            m = a.loc[[x for x in c["member_ids"] if x in a.index]] if c["member_ids"] else None
            canon.append({"canon_id": f"{topic}|{j}", "topic_code": topic, "topic_label": (m.iloc[0].topic_label if m is not None and len(m) else ""),
                          "canonical_claim": c["canonical_claim"], "position": c["position"], "applies_to": c["applies_to"],
                          "member_ids": c["member_ids"], "n_members": len(c["member_ids"]),
                          "commenters": sorted(set(m.commenter)) if m is not None else [], "n_commenters": (m.commenter.nunique() if m is not None else 0),
                          "commenter_types": (m.ctype.value_counts().to_dict() if m is not None else {}),
                          "sample_quote": (m.iloc[0].quote if m is not None and len(m) else ""), "sample_doc": (m.iloc[0].id if m is not None and len(m) else "")})
    CANON_OUT.write_text("".join(json.dumps(c) + "\n" for c in canon)); CANON_ROUNDS.write_text(json.dumps(state))
    print(f"{len(canon)} canonical arguments across {len(done)} topics (rounds: {state['round']})")

def _pending_prompt(cid, state, a, inputs):
    pend = state["pending"][cid]; topic = pend["topic"]
    if pend.get("lookup") is None:            # round-0 chunk
        rows = a.set_index("arg_id").loc[inputs[cid]["ids"]].reset_index()
        return f"TOPIC {topic} — {rows.iloc[0]['topic_label']}\n\n" + "\n".join(f"[{r['arg_id']}] ({r['commenter'][:60]}; {r['position']}; {r['applies_to']}) {r['claim']}" for _, r in rows.iterrows())
    prev = state["topics"][topic][str(state["round"] - 1)]
    items = {f"{topic}|m{state['round']}|{i}": c for i, c in enumerate(prev)}
    lines = [f"[{k}] ({len(items[k]['member_ids'])} comments; {items[k]['position']}; {items[k]['applies_to']}) {items[k]['canonical_claim']}" for k in pend["lookup"]]
    return f"TOPIC {topic} — MERGE ROUND {state['round']}: these are already-consolidated arguments from different subsets of comments on the same topic; merge duplicates across subsets.\n\n" + "\n".join(lines)

def canon_resubmit_pending():
    """Resubmit every pending canonicalization request as a new batch with the larger merge output budget."""
    a = load_args(); state = json.loads(CANON_ROUNDS.read_text()); inputs = {x["custom_id"]: x for x in map(json.loads, CANON_IN.open())}
    reqs = [Request(custom_id=cid, params=_params(CANON_SYS, CANON_SCHEMA, _pending_prompt(cid, state, a, inputs), MERGE_MAX_TOKENS)) for cid in state["pending"]]
    print(len(reqs), "pending requests resubmitted at", MERGE_MAX_TOKENS, "max_tokens"); _submit(reqs, LOG_C, f"canon-resubmit-r{state['round']}")

def canon_rerun_pending():
    """Synchronously re-run any pending canonicalization request with the merge output budget and ingest it."""
    a = load_args(); ai = set(a.arg_id); client = anthropic.Anthropic()
    state = json.loads(CANON_ROUNDS.read_text()); inputs = {x["custom_id"]: x for x in map(json.loads, CANON_IN.open())}
    for cid in list(state["pending"]):
        pend = state["pending"][cid]; topic = pend["topic"]; user = _pending_prompt(cid, state, a, inputs)
        with client.messages.stream(**_params(CANON_SYS, CANON_SCHEMA, user, MERGE_MAX_TOKENS)) as st: msg = st.get_final_message()
        try: d = _parse(msg)
        except Exception as ex: print("  STILL FAILING", cid, msg.stop_reason, str(ex)[:80]); continue
        for c in d["canonical"]:
            members = []
            for m in c["member_ids"]: members += (pend["lookup"].get(m, []) if pend.get("lookup") else ([m] if m in ai else []))
            state["topics"].setdefault(topic, {}).setdefault(str(state["round"]), []).append({**c, "member_ids": sorted(set(members))})
        del state["pending"][cid]; print(f"  rerun {cid}: {len(d['canonical'])} canonicals ({msg.usage.output_tokens} out)")
        CANON_ROUNDS.write_text(json.dumps(state))
    print("pending now:", len(state["pending"]))

# ---------------------------------------------------------------- verdicts
_IX = None
def index():
    global _IX
    if _IX is None: _IX = DocIndex()
    return _IX

def verdict_user(c, sections):
    ix = index(); hits = ix.query(c["canonical_claim"] + " " + c["sample_quote"], k=TOPK)
    parts = [f"CANONICAL ARGUMENT [{c['canon_id']}] (RTC topic {c['topic_code']} {c['topic_label']}; raised by {c['n_commenters']} commenters incl. {', '.join(c['commenters'][:6])}; position {c['position']}; applies_to per extraction: {c['applies_to']})\n{c['canonical_claim']}\n\nSample verbatim quote ({c['sample_doc']}): \"{c['sample_quote']}\""]
    sec = sections.get(c["topic_code"])
    if sec:
        parts.append(f"\n=== VEHICLE RTC — text of section {sec['num']} {sec['title']} (first {RTC_SECTION_CAP} chars) ===\n{sec['text'][:RTC_SECTION_CAP]}")
    for key, (label, _) in DOCS.items():
        if c["applies_to"] == "vehicle_only" and key in ("sprm_fr", "partial_repeal"):
            parts.append(f"\n=== {label} ===\n(not retrieved: argument tagged vehicle_only; return not_addressed with applies=false)"); continue
        ps = hits.get(key, [])
        parts.append(f"\n=== {label} — top retrieved passages ===\n" + ("\n".join(f"[{key} #{h['para_idx']}] {h['text'][:PASSAGE_CAP]}" for h in ps) if ps else "(no passages retrieved)"))
    return "\n".join(parts)

def load_canon(): return [json.loads(l) for l in CANON_OUT.open()]
def rtc_sections(): return {s["num"]: s for s in json.load(RTC_SECT.open())}

def verdict_dry(n):
    client = anthropic.Anthropic(); cs = load_canon(); random.Random(7).shuffle(cs); secs = rtc_sections()
    cs = sorted(cs, key=lambda c: -c["n_commenters"])[:n//2] + cs[:n - n//2]
    tin = tout = 0
    for c in cs:
        msg = client.messages.create(**_params(VERD_SYS, VERD_SCHEMA, verdict_user(c, secs), 4000)); d = _parse(msg)
        u = msg.usage; tin += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0); tout += u.output_tokens
        print(f"\n### {c['canon_id']} n_commenters={c['n_commenters']} applies={d['applies_to']} | {c['canonical_claim'][:150]}")
        for k, v in d["verdicts"].items(): print(f"   {k:<15} {v['verdict']:<24} applies={v['applies']} | {v['quote'][:100]}")
    print(f"\nmeasured {tin/len(cs):.0f} in / {tout/len(cs):.0f} out per argument; projected {len(load_canon())} args ≈ ${(tin/len(cs)*len(load_canon())*PRICE_IN + tout/len(cs)*len(load_canon())*PRICE_OUT)/1e6:,.0f}")

def verdict_submit():
    cs = load_canon(); secs = rtc_sections()
    pri = lambda c: (c["applies_to"] == "both" and c["position"] == "opposes_proposal" and c.get("n_commenters_model", 0) >= 2)
    order = sorted(cs, key=lambda c: (not pri(c), -c.get("n_commenters_model", 0)))
    n_pri = sum(pri(c) for c in cs); print(f"{len(cs)} canonicals; priority set {n_pri}")
    reqs = [Request(custom_id=c["canon_id"], params=_params(VERD_SYS, VERD_SCHEMA, verdict_user(c, secs), 3000)) for c in order]
    _submit(reqs[:n_pri], LOG_V, "verdict-priority")
    for k in range(n_pri, len(reqs), 1500): _submit(reqs[k:k+1500], LOG_V, "verdict-rest")

def verdict_fetch():
    res = _fetch(LOG_V); cs = {c["canon_id"]: c for c in load_canon()}; rows = []
    for cid, r in res.items():
        row = {**cs[cid]}
        if "error" in r: row["error"] = r["error"]
        else: row.update(r["data"]); row["usage_in"] = r["usage_in"]; row["usage_out"] = r["usage_out"]
        rows.append(row)
    VERD_OUT.write_text("".join(json.dumps(r) + "\n" for r in rows)); print(len(rows), "verdict rows")

def report():
    import pandas as pd
    rows = [json.loads(l) for l in VERD_OUT.open()]; ok = [r for r in rows if "verdicts" in r]
    flat = pd.DataFrame([{"canon_id": r["canon_id"], "topic": r["topic_code"], "n_commenters": r["n_commenters"], "position": r["position"],
                          "applies_to": r["applies_to"], "claim": r["canonical_claim"],
                          **{f"{k}": r["verdicts"][k]["verdict"] for k in DOCS}} for r in ok])
    flat.to_csv(HERE / f"coverage_flat_{DOCKET_ID}.csv", index=False)
    unaddr = flat[(flat.applies_to == "both") & (flat.position == "opposes_proposal") &
                  flat.vehicle_rtc.isin(["not_addressed", "dismissed_out_of_scope"]) & flat.vehicle_fr.isin(["not_addressed", "dismissed_out_of_scope"]) &
                  flat.sprm_fr.isin(["not_addressed", "dismissed_out_of_scope"])].sort_values("n_commenters", ascending=False)
    md = [f"# Pass C — coverage of canonical arguments, docket {DOCKET_ID}", "",
          f"{len(ok)} canonical arguments with verdicts ({len(rows)-len(ok)} failed). Model {MODEL}.", "",
          "## Verdict distribution by document", "", pd.DataFrame({k: flat[k].value_counts() for k in DOCS}).fillna(0).astype(int).to_markdown(), "",
          "## applies_to", "", flat.applies_to.value_counts().to_markdown(), "",
          f"## SHORTLIST — applies to both, opposes proposal, unaddressed or dismissed in vehicle RTC, vehicle FR and SPRM ({len(unaddr)})", "",
          unaddr[["canon_id", "n_commenters", "vehicle_rtc", "vehicle_fr", "sprm_fr", "partial_repeal", "claim"]].head(80).to_markdown(index=False), ""]
    OUT_MD.write_text("\n".join(md)); print("\n".join(md[:12]))

if __name__ == "__main__":
    cmd = sys.argv[1]
    {"canon-submit": canon_submit, "canon-fetch": canon_fetch, "canon-rerun-pending": canon_rerun_pending, "canon-resubmit-pending": canon_resubmit_pending, "verdict-dry": lambda: verdict_dry(int(sys.argv[2]) if len(sys.argv) > 2 else 8),
     "verdict-submit": verdict_submit, "verdict-fetch": verdict_fetch, "report": report}[cmd]()
