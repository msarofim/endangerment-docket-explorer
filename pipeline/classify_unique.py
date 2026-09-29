#!/usr/bin/env python
"""
Pass A — stance classification of the NON-campaign ("unique entity") comments on
docket EPA-HQ-OAR-2025-0194, with Claude Opus 5 via the Message Batches API.

    python classify_unique.py dry-run [N]   N random rows + the 180 hand-classified campaign
                                            controls, synchronous; prints agreement with the hand
                                            tally and the projected cost of the full batch
    python classify_unique.py submit        submit the full corpus as batch(es); records ids
    python classify_unique.py fetch         pull finished batch results -> stance_unique_<docket>.jsonl
    python classify_unique.py report        tally + write stance_unique_summary_<docket>.md

Provenance: every output row carries model id, prompt SHA-256, batch id and run date.
"""
import hashlib, json, random, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

# ---- named constants -------------------------------------------------------
DOCKET_ID   = "EPA-HQ-OAR-2025-0194"
import os
MODEL_A     = os.environ.get("PASSA_MODEL", "claude-sonnet-5")   # Marcus 2026-09-16: Sonnet 5 for the bulk stance pass
MODEL_RECHK = "claude-opus-5"                                    # Opus 5 re-check of non-high-confidence / mixed / unclear rows
MODEL       = MODEL_A
EFFORT      = "low"            # classification; adaptive thinking stays on
PRICES      = {"claude-sonnet-5": (1.00, 5.00), "claude-opus-5": (2.50, 12.50)}   # $/M in, out — BATCH pricing (50 % of list)
RECHECK_IF  = lambda d: d.get("confidence") != "high" or d.get("stance") in ("mixed", "unclear")
MAX_TOKENS  = 400
TRUNC_CHARS = 6000             # Marcus 2026-09-15: stance is stated in the opening
BATCH_MAX   = 15000            # requests per batch (API cap 100k / 256 MB; keep well under)
SEED        = 20260916
HERE        = Path(__file__).resolve().parent
CORPUS      = HERE / f"unique_comments_{DOCKET_ID}.jsonl"
CAMPAIGNS   = HERE / f"campaign_stance_{DOCKET_ID}.csv"
CAMP_TEXTS  = HERE / "campaign_texts.jsonl"
BATCH_LOG   = HERE / "batches_passA.json"
OUT         = HERE / f"stance_unique_{DOCKET_ID}.jsonl"          # Sonnet pass
OUT_RECHK   = HERE / f"stance_unique_recheck_{DOCKET_ID}.jsonl"  # Opus re-check
OUT_FINAL   = HERE / f"stance_unique_final_{DOCKET_ID}.jsonl"    # merged: Opus where re-checked, else Sonnet
BATCH_LOG_R = HERE / "batches_passA_recheck.json"
OUT_MD      = HERE / f"stance_unique_summary_{DOCKET_ID}.md"

SYSTEM = """You classify public comments submitted to EPA docket EPA-HQ-OAR-2025-0194, the July 2025 proposal "Reconsideration of 2009 Endangerment Finding and Greenhouse Gas Vehicle Standards". The PROPOSAL would (1) rescind the 2009 finding that greenhouse gases endanger public health and welfare and (2) repeal all greenhouse-gas emission standards for light-, medium- and heavy-duty vehicles.

Classify the commenter's STANCE TOWARD THE PROPOSAL:
- support_rescission: the commenter wants EPA to finalize the rescission and/or repeal (e.g., "I support the repeal", "rescind the endangerment finding", "CO2 is not a pollutant", "end these mandates").
- oppose_rescission: the commenter wants EPA to keep the Endangerment Finding and/or the vehicle standards, i.e., NOT finalize the proposal (e.g., "I oppose the repeal", "keep the finding", "do not rescind", "withdraw this proposal", "the science is clear that GHGs endanger health"). Statements that "support the 2009 Endangerment Finding" are oppose_rescission.
- mixed: the commenter explicitly supports one part and opposes another (e.g., keep the finding but repeal the heavy-duty standards), or supports the proposal only under stated conditions.
- unclear: the text is about this rulemaking but the stance cannot be determined (blank, template placeholder, only a question, only a request for extension, or hopelessly ambiguous).
- off_topic: the text is not about this rulemaking at all.

Also classify ENTITY_TYPE from the text and signature: individual, elected_official, government (state/local/tribal/federal agency), academic_or_scientist, health_professional_or_org, ngo_or_advocacy, business_or_industry, trade_association, religious_org, other_org.

Rules: judge only the stance toward THIS proposal, not general views on climate policy. Sarcasm and rhetorical questions count by their evident intent. Do not infer stance from the commenter's identity alone. The key_phrase must be copied verbatim from the text (<= 25 words) and must be the sentence that decided the stance. Confidence is high when the stance is explicit, medium when inferred from clear context, low when inferred from weak cues."""

SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["oppose_rescission", "support_rescission", "mixed", "unclear", "off_topic"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "key_phrase": {"type": "string"},
        "entity_type": {"type": "string", "enum": ["individual", "elected_official", "government", "academic_or_scientist",
                                                   "health_professional_or_org", "ngo_or_advocacy", "business_or_industry",
                                                   "trade_association", "religious_org", "other_org"]},
    },
    "required": ["stance", "confidence", "key_phrase", "entity_type"],
    "additionalProperties": False,
}
PROMPT_SHA = hashlib.sha256((SYSTEM + json.dumps(SCHEMA, sort_keys=True) + EFFORT + str(TRUNC_CHARS)).encode()).hexdigest()[:16]

def user_content(row):
    t = (row["text"] or "")[:TRUNC_CHARS]
    trunc = " [TRUNCATED]" if len(row["text"] or "") > TRUNC_CHARS else ""
    head = f"Docket entry {row['id']} | title: {row.get('title')} | subtype: {row.get('subtype')}{trunc}\n\n"
    return head + (t if t.strip() else "[no text]")

def params(row, model=None):
    return MessageCreateParamsNonStreaming(
        model=model or MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user_content(row)}],
    )

def parse(msg):
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)

def load_corpus():
    return [json.loads(l) for l in CORPUS.open()]

def load_controls():
    import pandas as pd
    st = pd.read_csv(CAMPAIGNS).set_index("id").stance.to_dict()
    rows = []
    for l in CAMP_TEXTS.open():
        r = json.loads(l)
        if st.get(r["id"]) in ("OPPOSE", "SUPPORT"):
            rows.append({"id": r["id"], "title": r["title"], "subtype": "Mass Mail Campaign", "text": r["text"],
                         "hand_stance": {"OPPOSE": "oppose_rescission", "SUPPORT": "support_rescission"}[st[r["id"]]]})
    return rows

def dry_run(n):
    client = anthropic.Anthropic()
    corpus = load_corpus(); random.Random(SEED).shuffle(corpus)
    sample, controls = corpus[:n], load_controls()
    print(f"dry run: model {MODEL}; {n} corpus rows + {len(controls)} campaign controls; prompt {PROMPT_SHA}")
    tok_in = tok_out = 0; agree = 0; disagreements = []
    t0 = time.time()
    for r in controls + sample:
        msg = client.messages.create(**params(r))
        tok_in += msg.usage.input_tokens; tok_out += msg.usage.output_tokens
        try:
            d = parse(msg)
        except Exception as e:
            print("  PARSE FAIL", r["id"], e); continue
        if "hand_stance" in r:
            if d["stance"] == r["hand_stance"]: agree += 1
            else: disagreements.append((r["id"], r["hand_stance"], d["stance"], d["key_phrase"]))
        else:
            print(f"  {r['id']} {d['stance']:<19} {d['confidence']:<6} {d['entity_type']:<26} | {d['key_phrase'][:90]}")
    print(f"\ncontrols: {agree}/{len(controls)} agree with hand tally")
    for x in disagreements: print("  DISAGREE", x)
    n_calls = len(controls) + len(sample)
    per_in, per_out = tok_in / n_calls, tok_out / n_calls
    N = len(corpus); PRICE_IN, PRICE_OUT = PRICES[MODEL]
    cost = (per_in * N * PRICE_IN + per_out * N * PRICE_OUT) / 1e6
    print(f"\nmeasured: {per_in:.0f} in / {per_out:.0f} out tokens per comment; {time.time()-t0:.0f}s for {n_calls} sync calls")
    print(f"projected full batch ({N:,} comments, batch pricing): ${cost:,.0f}   "
          f"[this dry run cost ≈ ${(tok_in*PRICE_IN*2 + tok_out*PRICE_OUT*2)/1e6:.2f} at sync pricing]")

def _submit(rows, model, log_path, out_path):
    client = anthropic.Anthropic()
    done = {json.loads(l)["id"] for l in out_path.open()} if out_path.exists() else set()
    todo = [r for r in rows if r["id"] not in done]
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    pending = {cid for e in log if e.get("status") != "ended" for cid in e.get("ids", [])}
    todo = [r for r in todo if r["id"] not in pending]
    print(f"{model}: {len(todo)} to submit ({len(done)} done, {len(pending)} pending)")
    for k in range(0, len(todo), BATCH_MAX):
        chunk = todo[k:k+BATCH_MAX]
        b = client.messages.batches.create(requests=[Request(custom_id=r["id"], params=params(r, model)) for r in chunk])
        log.append({"batch_id": b.id, "n": len(chunk), "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "model": model, "prompt_sha": PROMPT_SHA, "status": b.processing_status, "ids": [r["id"] for r in chunk]})
        print(f"submitted {b.id}: {len(chunk)} requests")
    log_path.write_text(json.dumps(log, indent=1))

def submit():
    _submit(load_corpus(), MODEL_A, BATCH_LOG, OUT)

def recheck():
    """Opus 5 re-check of Sonnet rows that are not high-confidence, or mixed/unclear."""
    corpus = {r["id"]: r for r in load_corpus()}
    rows = [corpus[d["id"]] for d in map(json.loads, OUT.open()) if d.get("result") == "ok" and RECHECK_IF(d) and d["id"] in corpus]
    print(f"re-check candidates: {len(rows)}")
    _submit(rows, MODEL_RECHK, BATCH_LOG_R, OUT_RECHK)

def _fetch(log_path, out_path):
    client = anthropic.Anthropic()
    log = json.loads(log_path.read_text())
    have = {json.loads(l)["id"] for l in out_path.open()} if out_path.exists() else set()
    with out_path.open("a") as out:
        for entry in log:
            b = client.messages.batches.retrieve(entry["batch_id"]); entry["status"] = b.processing_status
            c = b.request_counts
            print(f"{b.id}: {b.processing_status} succeeded={c.succeeded} errored={c.errored} processing={c.processing}")
            if b.processing_status != "ended": continue
            n = 0
            sup = set(entry.get("superseded_ids", []))
            for res in client.messages.batches.results(b.id):
                if res.custom_id in have or res.custom_id in sup: continue
                row = {"id": res.custom_id, "batch_id": b.id, "model": entry["model"], "prompt_sha": PROMPT_SHA,
                       "fetched": time.strftime("%Y-%m-%d")}
                if res.result.type == "succeeded" and res.result.message.stop_reason == "refusal":
                    row.update({"result": "refusal", "stance": "unclear", "confidence": "low", "key_phrase": "", "entity_type": "individual"})
                elif res.result.type == "succeeded":
                    try:
                        row.update(parse(res.result.message)); row["result"] = "ok"
                        row["usage_in"] = res.result.message.usage.input_tokens
                        row["usage_out"] = res.result.message.usage.output_tokens
                    except Exception as e:
                        row.update({"result": "parse_error", "error": str(e)})
                else:
                    row.update({"result": res.result.type,
                                "error": getattr(getattr(res.result, "error", None), "type", None)})
                out.write(json.dumps(row) + "\n"); have.add(res.custom_id); n += 1
            print(f"  wrote {n} rows")
    log_path.write_text(json.dumps(log, indent=1))

def fetch():
    _fetch(BATCH_LOG, OUT)
    if BATCH_LOG_R.exists(): _fetch(BATCH_LOG_R, OUT_RECHK)
    merge()

def merge():
    """Final = Opus re-check where present, else Sonnet; logs disagreements."""
    son = {d["id"]: d for d in map(json.loads, OUT.open())} if OUT.exists() else {}
    opu = {d["id"]: d for d in map(json.loads, OUT_RECHK.open())} if OUT_RECHK.exists() else {}
    n_dis = 0
    with OUT_FINAL.open("w") as f:
        for cid, d in son.items():
            r = dict(d); r["sonnet_stance"] = d.get("stance"); r["sonnet_confidence"] = d.get("confidence")
            if cid in opu and opu[cid].get("result") == "ok":
                o = opu[cid]; r.update({k: o[k] for k in ("stance", "confidence", "key_phrase", "entity_type", "model", "batch_id")})
                r["rechecked"] = True; r["disagree"] = o["stance"] != d.get("stance"); n_dis += r["disagree"]
                r["usage_in"] = d.get("usage_in", 0) + o.get("usage_in", 0); r["usage_out"] = d.get("usage_out", 0) + o.get("usage_out", 0)
            else:
                r["rechecked"] = False; r["disagree"] = False
            f.write(json.dumps(r) + "\n")
    print(f"merged {len(son)} rows; {len(opu)} re-checked; {n_dis} stance disagreements (Opus wins)")

def report():
    import pandas as pd
    src = OUT_FINAL if OUT_FINAL.exists() else OUT
    df = pd.DataFrame([json.loads(l) for l in src.open()])
    corpus = pd.DataFrame(load_corpus())[["id", "subtype", "substantive", "pageCount", "text_chars", "late"]]
    df = df.merge(corpus, on="id", how="left")
    ok = df[df.result == "ok"]
    g = ok.stance.value_counts(); s = ok.stance.value_counts(normalize=True)
    tab = pd.DataFrame({"n": g, "share": s.round(4)})
    det = ok[ok.stance.isin(["oppose_rescission", "support_rescission"])]
    by_sub = pd.crosstab(ok.subtype, ok.stance)
    by_ent = pd.crosstab(ok.entity_type, ok.stance)
    def _cost(sub, m): pi, po = PRICES[m]; return (sub.usage_in.sum() * pi + sub.usage_out.sum() * po) / 1e6
    cost = sum(_cost(ok[ok.model == m], m) for m in ok.model.unique())
    rechk = ok.rechecked.sum() if "rechecked" in ok else 0; dis = ok.disagree.sum() if "disagree" in ok else 0
    md = [f"# Pass A — stance of unique-entity comments, docket {DOCKET_ID}", "",
          f"Bulk model {MODEL_A}, re-check model {MODEL_RECHK} (rows not high-confidence or mixed/unclear), effort {EFFORT}, "
          f"prompt {PROMPT_SHA}, truncation {TRUNC_CHARS} chars, batch ids "
          f"{[e['batch_id'] for e in json.loads(BATCH_LOG.read_text())]}. {len(df):,} rows, {len(ok):,} classified, "
          f"{(df.result != 'ok').sum()} failed; {rechk:,} re-checked by Opus, {dis:,} stance changes. "
          f"Tokens: {ok.usage_in.sum():,} in / {ok.usage_out.sum():,} out ≈ ${cost:,.0f} (batch pricing).",
          "", tab.to_markdown(), "",
          f"**Among determinable stances** (oppose + support): {len(det):,}; oppose = "
          f"{(det.stance == 'oppose_rescission').mean():.1%}.", "",
          "## By regulations.gov subtype", "", by_sub.to_markdown(), "",
          "## By entity type (model-assigned)", "", by_ent.to_markdown(), "",
          "## Confidence", "", pd.crosstab(ok.stance, ok.confidence).to_markdown(), ""]
    OUT_MD.write_text("\n".join(md)); print("\n".join(md[:8]))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry-run"
    {"dry-run": lambda: dry_run(int(sys.argv[2]) if len(sys.argv) > 2 else 50),
     "submit": submit, "recheck": recheck, "fetch": fetch, "merge": merge, "report": report}[cmd]()
