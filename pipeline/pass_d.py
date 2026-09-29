#!/usr/bin/env python
"""
Pass D — EPA's OWN endangerment-related arguments in the power-plant SPRM (91 FR 2026-19072) that did
NOT appear in the vehicle rescission (final rule 91 FR 2026-03157 + RTC doc 31089).

    python pass_d.py extract-dry [N] | extract-submit | extract-fetch     EPA arguments per SPRM chunk
    python pass_d.py novelty-dry [N] | novelty-submit | novelty-fetch | report

Novelty verdict per SPRM argument: same_argument_in_vehicle | extended_or_reframed | new_in_sprm, with the
vehicle citation (or its absence). Every new_in_sprm is hand-verified against the vehicle texts before use.
"""
import json, random, re, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from retrieval import DocIndex, DOCS, paragraphs

MODEL, EFFORT = "claude-opus-5", "medium"
CHUNK_CHARS, TOPK = 24000, 10
HERE = Path(__file__).resolve().parent
SPRM_TXT = HERE / "powerplant" / "FR_2026-19072_sprm.txt"
CHUNKS   = HERE / "passD_sprm_chunks.jsonl"; ARGS_OUT = HERE / "sprm_epa_arguments.jsonl"
NOV_OUT  = HERE / "sprm_novelty_verdicts.jsonl"; OUT_MD = HERE / "sprm_novelty_summary.md"
LOG_E, LOG_N = HERE / "batches_passD_extract.json", HERE / "batches_passD_novelty.json"
PRICE_IN, PRICE_OUT = 2.50, 12.50

EXTRACT_SYS = """You are reading a chunk of the preamble of EPA's September 2026 supplemental proposal "Rescission of the Greenhouse Gas Findings for Fossil Fuel-Fired Power Plants and Repeal of Regulations for Power Plant Greenhouse Gas Emissions Under Clean Air Act Section 111" (docket EPA-HQ-OAR-2025-0124). Extract every DISTINCT ARGUMENT or RATIONALE that EPA itself advances in this chunk for rescinding the 2015 endangerment/contribution findings for power plants, for reading section 111 as not authorizing GHG regulation for global climate change, or for repealing the standards. Do not extract EPA's descriptions of other parties' views, background history, or requests for comment, except where EPA states a position. For each: a one-sentence claim in EPA's terms; a verbatim quote (<= 60 words); the preamble section heading it falls under; argument_type (legal_text | legal_precedent | major_questions | futility_contribution | science | economic | procedural | policy | other); endangerment_related (true if it concerns whether GHGs endanger public health or welfare, whether power plants cause or contribute to that endangerment, the meaning of "air pollution"/"air pollutant"/"endanger" for GHGs, the role of global vs local effects, or the status of the 2009/2015 findings; false if it is only about BSER, CCS, costs of compliance, or implementation); and any authority cited (case, statute, document)."""
EXTRACT_SCHEMA = {"type": "object", "properties": {"arguments": {"type": "array", "items": {"type": "object", "properties": {
    "claim": {"type": "string"}, "quote": {"type": "string"}, "section": {"type": "string"},
    "argument_type": {"type": "string", "enum": ["legal_text", "legal_precedent", "major_questions", "futility_contribution", "science", "economic", "procedural", "policy", "other"]},
    "endangerment_related": {"type": "boolean"}, "authority_cited": {"type": "string"}},
    "required": ["claim", "quote", "section", "argument_type", "endangerment_related", "authority_cited"], "additionalProperties": False}}},
    "required": ["arguments"], "additionalProperties": False}

NOVELTY_SYS = """You are checking whether an argument EPA advances in its September 2026 power-plant SPRM had ALREADY been advanced by EPA in its February 2026 vehicle Endangerment Finding rescission (final rule preamble and Response to Comments). You will receive the SPRM argument with its quote, and the most relevant passages retrieved from the two vehicle documents. Decide:
- same_argument_in_vehicle: EPA made substantially the same argument (same legal theory / same reasoning / same evidence) in the vehicle rescission.
- extended_or_reframed: the vehicle rescission contains a related argument, but the SPRM materially extends it (new authority, new evidence, new statutory hook, new step in the reasoning) or reframes it for section 111 in a way that adds substance beyond substituting 'section 111' for 'section 202(a)'.
- new_in_sprm: nothing in the retrieved vehicle passages makes this argument; if you suspect it exists elsewhere in the vehicle documents, say so in retrieval_note and still return your best verdict.
Quote the closest vehicle passage verbatim (<= 60 words) with its index, or null. Also state what is new (if anything) in one sentence."""
NOVELTY_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["same_argument_in_vehicle", "extended_or_reframed", "new_in_sprm"]},
    "vehicle_doc": {"type": "string", "enum": ["vehicle_rtc", "vehicle_fr", "none"]}, "vehicle_quote": {"type": ["string", "null"]},
    "vehicle_passage_index": {"type": ["integer", "null"]}, "what_is_new": {"type": "string"}, "retrieval_note": {"type": "string"}},
    "required": ["verdict", "vehicle_doc", "vehicle_quote", "vehicle_passage_index", "what_is_new", "retrieval_note"], "additionalProperties": False}

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
    log_path.write_text(json.dumps(log, indent=1)); print("submitted", b.id, len(reqs))
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
                try: out[res.custom_id] = {"data": _parse(res.result.message), "usage_in": res.result.message.usage.input_tokens, "usage_out": res.result.message.usage.output_tokens}
                except Exception as ex: out[res.custom_id] = {"error": f"parse: {ex}"}
            else: out[res.custom_id] = {"error": res.result.type}
    log_path.write_text(json.dumps(log, indent=1)); return out

# ---------------------------------------------------------------- chunk the SPRM
def make_chunks():
    t = SPRM_TXT.read_text(errors="replace")
    # keep the argumentative body: from "IV. Legal Framework" to "VIII. Statutory and EO Reviews"
    s = t.find("IV. Legal Framework for Action", t.find("III. Background") + 100); e = t.find("VIII. Statutory and Executive Order Reviews", s)
    body = t[s:e if e > s else len(t)]
    paras = paragraphs(body, min_chars=100, max_chars=4000); chunks, cur, head = [], [], ""
    for p in paras:
        m = re.match(r"^(?:[IVX]+\.|[A-H]\.|\d+\.)\s+[A-Z][^.]{3,90}$", p)
        if m: head = p
        if sum(len(x) for x in cur) + len(p) > CHUNK_CHARS and cur:
            chunks.append({"chunk_id": f"sprm|{len(chunks)}", "heading_hint": head, "text": "\n\n".join(cur)}); cur = []
        cur.append(p)
    if cur: chunks.append({"chunk_id": f"sprm|{len(chunks)}", "heading_hint": head, "text": "\n\n".join(cur)})
    CHUNKS.write_text("".join(json.dumps(c) + "\n" for c in chunks)); print(f"{len(paras)} paragraphs -> {len(chunks)} chunks of ~{CHUNK_CHARS} chars"); return chunks

def load_chunks(): return [json.loads(l) for l in CHUNKS.open()] if CHUNKS.exists() else make_chunks()
def chunk_user(c): return f"SPRM PREAMBLE CHUNK {c['chunk_id']} (nearest heading: {c['heading_hint']})\n\n{c['text']}"

def extract_dry(n):
    client = anthropic.Anthropic(); cs = load_chunks()[:n]; tin = tout = 0
    for c in cs:
        msg = client.messages.create(**_params(EXTRACT_SYS, EXTRACT_SCHEMA, chunk_user(c), 12000)); d = _parse(msg)
        tin += msg.usage.input_tokens; tout += msg.usage.output_tokens
        print(f"\n### {c['chunk_id']} [{c['heading_hint'][:60]}] -> {len(d['arguments'])} args, {sum(a['endangerment_related'] for a in d['arguments'])} endangerment-related")
        for a in d["arguments"][:8]: print(f"   {'E' if a['endangerment_related'] else '-'} {a['argument_type']:<21} | {a['claim'][:130]}")
    N = len(load_chunks()); print(f"\nprojected {N} chunks ≈ ${(tin/len(cs)*N*PRICE_IN + tout/len(cs)*N*PRICE_OUT)/1e6:,.0f}")

def extract_submit():
    cs = load_chunks(); _submit([Request(custom_id=c["chunk_id"], params=_params(EXTRACT_SYS, EXTRACT_SCHEMA, chunk_user(c), 12000)) for c in cs], LOG_E, "extract")

def extract_fetch():
    res = _fetch(LOG_E); rows = []
    for cid, r in res.items():
        if "error" in r: print("ERR", cid, r["error"]); continue
        for j, a in enumerate(r["data"]["arguments"]): rows.append({"arg_id": f"{cid}#{j}", "chunk_id": cid, **a})
    ARGS_OUT.write_text("".join(json.dumps(x) + "\n" for x in rows))
    print(f"{len(rows)} EPA arguments extracted; endangerment-related: {sum(r['endangerment_related'] for r in rows)}")

# ---------------------------------------------------------------- novelty
_IX = None
def index():
    global _IX
    if _IX is None: _IX = DocIndex(keys=["vehicle_rtc", "vehicle_fr"])
    return _IX
def load_args(): return [json.loads(l) for l in ARGS_OUT.open()]
def novelty_user(a):
    hits = index().query(a["claim"] + " " + a["quote"] + " " + a["authority_cited"], k=TOPK)
    parts = [f"SPRM ARGUMENT [{a['arg_id']}] (section: {a['section']}; type {a['argument_type']}; authority: {a['authority_cited']})\n{a['claim']}\nQuote: \"{a['quote']}\""]
    for key in ("vehicle_fr", "vehicle_rtc"):
        parts.append(f"\n=== {DOCS[key][0]} — top retrieved passages ===\n" + "\n".join(f"[{key} #{h['para_idx']}] {h['text']}" for h in hits.get(key, [])))
    return "\n".join(parts)

def novelty_dry(n):
    client = anthropic.Anthropic(); args = [a for a in load_args() if a["endangerment_related"]]; random.Random(3).shuffle(args); tin = tout = 0
    for a in args[:n]:
        msg = client.messages.create(**_params(NOVELTY_SYS, NOVELTY_SCHEMA, novelty_user(a), 3000)); d = _parse(msg); tin += msg.usage.input_tokens; tout += msg.usage.output_tokens
        print(f"\n### {a['arg_id']} {d['verdict']:<26} | {a['claim'][:130]}\n    new: {d['what_is_new'][:140]}\n    vehicle: {(d['vehicle_quote'] or '')[:120]}")
    N = sum(1 for a in load_args() if a["endangerment_related"]); print(f"\nprojected {N} endangerment-related args ≈ ${(tin/n*N*PRICE_IN + tout/n*N*PRICE_OUT)/1e6:,.0f}")

def novelty_submit():
    args = [a for a in load_args() if a["endangerment_related"]]
    _submit([Request(custom_id=a["arg_id"], params=_params(NOVELTY_SYS, NOVELTY_SCHEMA, novelty_user(a), 3000)) for a in args], LOG_N, "novelty")

def novelty_fetch():
    res = _fetch(LOG_N); args = {a["arg_id"]: a for a in load_args()}; rows = []
    for aid, r in res.items():
        row = {**args[aid]}; row.update(r["data"] if "data" in r else {"error": r["error"]}); rows.append(row)
    NOV_OUT.write_text("".join(json.dumps(x) + "\n" for x in rows)); print(len(rows), "novelty rows")

def report():
    import pandas as pd
    df = pd.DataFrame([json.loads(l) for l in NOV_OUT.open()]); ok = df[df.verdict.notna()] if "verdict" in df else df
    md = [f"# Pass D — EPA's endangerment-related arguments in the SPRM vs the vehicle rescission", "",
          f"{len(ok)} endangerment-related SPRM arguments checked. Model {MODEL}.", "", ok.verdict.value_counts().to_markdown(), "",
          "## new_in_sprm (hand-verify each before use)", "", ok[ok.verdict == "new_in_sprm"][["arg_id", "section", "argument_type", "claim", "what_is_new"]].to_markdown(index=False), "",
          "## extended_or_reframed", "", ok[ok.verdict == "extended_or_reframed"][["arg_id", "section", "argument_type", "claim", "what_is_new"]].to_markdown(index=False), ""]
    OUT_MD.write_text("\n".join(md)); ok.to_csv(HERE / "sprm_novelty_flat.csv", index=False); print("\n".join(md[:8]))

if __name__ == "__main__":
    cmd = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    {"chunks": make_chunks, "extract-dry": lambda: extract_dry(n), "extract-submit": extract_submit, "extract-fetch": extract_fetch,
     "novelty-dry": lambda: novelty_dry(n if len(sys.argv) > 2 else 8), "novelty-submit": novelty_submit, "novelty-fetch": novelty_fetch, "report": report}[cmd]()
