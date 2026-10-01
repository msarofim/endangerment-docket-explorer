#!/usr/bin/env python
"""Re-judge the canonical arguments whose Pass-C verdict failed to parse. Both 2026-09-18 failures were
`Unterminated string` — the model's JSON was cut off at max_tokens=3000 mid-quote — so the fix is headroom,
not a different prompt. Same prompt, same retrieval, MAX_TOKENS raised; then the RTC verdict is re-judged
against EPA RESPONSE text only, as every other RTC verdict on the page is.

    python rerun_failed_verdicts.py            re-run every row that has `error` and no `verdicts`
"""
import json, sys
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from rank_bm25 import BM25Okapi
from retrieval import paragraphs, tok
from rtc_response_text import response_blocks, response_text
import pass_c as C

HERE = Path(__file__).resolve().parent; D = "EPA-HQ-OAR-2025-0194"
MAX_TOKENS = 8000          # was 3000; both failures truncated mid-string
RUN = "rerun-2026-09-29"

RSYS = """You are checking whether U.S. EPA's Response to Comments (RTC) on its 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding RESPONDED to an argument raised by commenters. You will be given one canonical argument and passages drawn ONLY from the RTC's 'EPA Response' text (never from its summaries of what commenters said). Judge: addressed_directly (engages this specific argument on its merits), addressed_generally (responds to the topic in a way that covers it without engaging its specific point), dismissed_out_of_scope (explicitly declines to respond), not_addressed. Quote the decisive EPA sentence verbatim (<= 30 words) and give the passage index; the quote must be EPA speaking in its own voice, not EPA restating a commenter."""
RSCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["addressed_directly", "addressed_generally", "dismissed_out_of_scope", "not_addressed"]},
    "quote": {"type": "string"}, "passage_index": {"type": ["integer", "null"]}, "note": {"type": "string"}},
    "required": ["verdict", "quote", "passage_index", "note"], "additionalProperties": False}


def main():
    rows = [json.loads(l) for l in (HERE / f"coverage_verdicts_{D}.jsonl").open()]
    todo = [r for r in rows if "verdicts" not in r]
    if not todo:
        print("nothing to re-run"); return 0
    print(f"{len(todo)} to re-run: " + ", ".join(f"{r['canon_id']} ({r.get('error','')[:40]})" for r in todo))

    S_ = json.load((HERE / "rtc" / "rtc_sections.json").open()); SEC = {s["num"]: s for s in S_}
    resp_paras = [(s["num"], p) for s in S_
                  for blk in response_blocks(s)
                  for p in paragraphs(blk)]
    bm = BM25Okapi([tok(p) for _, p in resp_paras])
    client = anthropic.Anthropic(); spent = 0.0

    for r in todo:
        # a topic_label lost in merging would print as an empty section name on the page
        if not r.get("topic_label"):
            r["topic_label"] = SEC.get(r["topic_code"], {}).get("title", "")

        msg = client.messages.create(**C._params(C.VERD_SYS, C.VERD_SCHEMA, C.verdict_user(r, SEC), MAX_TOKENS))
        d = C._parse(msg); u = msg.usage
        spent += (u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)) * 5 / 1e6 + u.output_tokens * 25 / 1e6
        r.update(d); r["usage_in"] = u.input_tokens; r["usage_out"] = u.output_tokens
        r["rerun_failed"] = f"{RUN}: max_tokens {MAX_TOKENS} (previous attempt truncated: {r.pop('error', '')})"

        # RTC on response text only, matching every other RTC verdict on the page
        q = r["canonical_claim"] + " " + r["sample_quote"]
        top = sorted(range(len(resp_paras)), key=lambda j: -bm.get_scores(tok(q))[j])[:6]
        sec = SEC.get(r["topic_code"]); sec_resp = response_text(sec)
        user = (f"CANONICAL ARGUMENT [{r['canon_id']}] (RTC topic {r['topic_code']}; {r['n_commenters']} commenters; position {r['position']})\n"
                f"{r['canonical_claim']}\nSample quote: \"{r['sample_quote']}\"\n\n"
                + (f"=== EPA RESPONSE text of RTC section {r['topic_code']} (first 8,000 chars) ===\n{sec_resp}\n\n" if sec_resp else "")
                + "=== Top EPA RESPONSE passages retrieved from the whole RTC ===\n"
                + "\n".join(f"[{j}] (section {resp_paras[j][0]}) {resp_paras[j][1][:1600]}" for j in top))
        m2 = client.messages.create(**MessageCreateParamsNonStreaming(
            model=C.MODEL, max_tokens=2000, system=[{"type": "text", "text": RSYS, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": RSCHEMA}},
            messages=[{"role": "user", "content": user}]))
        d2 = json.loads(next(b.text for b in m2.content if b.type == "text")); u2 = m2.usage
        spent += (u2.input_tokens + (u2.cache_creation_input_tokens or 0) + (u2.cache_read_input_tokens or 0)) * 5 / 1e6 + u2.output_tokens * 25 / 1e6
        v = r["verdicts"]["vehicle_rtc"]
        v.update({"verdict_v1": v["verdict"], "quote_v1": v["quote"], "verdict": d2["verdict"], "quote": d2["quote"],
                  "passage_index": d2["passage_index"], "retrieval_note": d2["note"], "rerun": f"response-only {RUN[6:]}"})
        print(f"  [{r['canon_id']}] " + "  ".join(f"{k}={r['verdicts'][k]['verdict']}" for k in C.DOCS))
        print(f"      RTC: {v['quote'][:150]}")

    (HERE / f"coverage_verdicts_{D}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    print(f"\nspent ≈ ${spent:.2f}; run locate_quotes.py and pass_c.py report next")
    return 0


if __name__ == "__main__":
    sys.exit(main())
