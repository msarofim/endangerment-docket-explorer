#!/usr/bin/env python
"""Re-run the vehicle-RTC verdict for canonical arguments whose RTC quote was located in an 'EPA Summary of
Comments' block (i.e. the model quoted EPA restating the commenter). Retrieval here is restricted to EPA
RESPONSE text only. Synchronous; updates coverage_verdicts in place (previous verdict kept as *_v1)."""
import json, re, time, anthropic
from pathlib import Path
from rank_bm25 import BM25Okapi
from retrieval import paragraphs, tok
from rtc_response_text import response_blocks, response_text
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
HERE = Path(__file__).resolve().parent; D = "EPA-HQ-OAR-2025-0194"; MODEL = "claude-opus-5"
S = json.load((HERE/"rtc"/"rtc_sections.json").open()); SEC = {s["num"]: s for s in S}
# response-only corpus: every EPA Response block, plus unsplit sections (whole text = EPA's words)
resp_paras = []
for s in S:
    src = response_blocks(s)
    for blk in src:
        for p in paragraphs(blk): resp_paras.append((s["num"], p))
bm = BM25Okapi([tok(p) for _, p in resp_paras])
SYS = """You are checking whether U.S. EPA's Response to Comments (RTC) on its 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding RESPONDED to an argument raised by commenters. You will be given one canonical argument and passages drawn ONLY from the RTC's 'EPA Response' text (never from its summaries of what commenters said). Judge: addressed_directly (engages this specific argument on its merits), addressed_generally (responds to the topic in a way that covers it without engaging its specific point), dismissed_out_of_scope (explicitly declines to respond), not_addressed. Quote the decisive EPA sentence verbatim (<= 30 words) and give the passage index; the quote must be EPA speaking in its own voice, not EPA restating a commenter."""
SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["addressed_directly", "addressed_generally", "dismissed_out_of_scope", "not_addressed"]}, "quote": {"type": "string"}, "passage_index": {"type": ["integer", "null"]}, "note": {"type": "string"}}, "required": ["verdict", "quote", "passage_index", "note"], "additionalProperties": False}
rows = [json.loads(l) for l in (HERE/f"coverage_verdicts_{D}.jsonl").open()]
todo = [r for r in rows if "verdicts" in r and (r["verdicts"]["vehicle_rtc"].get("loc") or {}).get("block") == "summary"]
print(len(todo), "to re-run"); c = anthropic.Anthropic(); tin = tout = 0
for i, r in enumerate(todo, 1):
    q = r["canonical_claim"] + " " + r["sample_quote"]; sc = bm.get_scores(tok(q)); top = sorted(range(len(sc)), key=lambda j: -sc[j])[:6]
    sec = SEC.get(r["topic_code"]); sec_resp = response_text(sec)
    user = (f"CANONICAL ARGUMENT [{r['canon_id']}] (RTC topic {r['topic_code']}; {r['n_commenters']} commenters; position {r['position']})\n{r['canonical_claim']}\nSample quote: \"{r['sample_quote']}\"\n\n"
            + (f"=== EPA RESPONSE text of RTC section {r['topic_code']} (first 8,000 chars) ===\n{sec_resp}\n\n" if sec_resp else "")
            + "=== Top EPA RESPONSE passages retrieved from the whole RTC ===\n" + "\n".join(f"[{j}] (section {resp_paras[j][0]}) {resp_paras[j][1][:1600]}" for j in top))
    p = MessageCreateParamsNonStreaming(model=MODEL, max_tokens=1500, system=[{"type": "text", "text": SYS, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}, messages=[{"role": "user", "content": user}])
    try: msg = c.messages.create(**p)
    except anthropic.APIStatusError as e: print("  err", r["canon_id"], str(e)[:80]); continue
    d = json.loads(next(b.text for b in msg.content if b.type == "text")); tin += msg.usage.input_tokens; tout += msg.usage.output_tokens
    v = r["verdicts"]["vehicle_rtc"]; v["verdict_v1"] = v["verdict"]; v["quote_v1"] = v["quote"]
    v["verdict"] = d["verdict"]; v["quote"] = d["quote"]; v["passage_index"] = d["passage_index"]; v["retrieval_note"] = d["note"]; v["rerun"] = "response-only 2026-09-18"
    if i % 25 == 0: print(f"  {i}/{len(todo)}", flush=True)
(HERE/f"coverage_verdicts_{D}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
print(f"done; ${(tin*5+tout*25)/1e6:.1f}")
