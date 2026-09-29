#!/usr/bin/env python
"""
Pass B — argument extraction from the SUBSTANTIVE comments on docket EPA-HQ-OAR-2025-0194
(orgs / governments / Members of Congress / petitions / individuals with >= 5 pages), Claude Opus 5,
Message Batches API, structured JSON output.

    python pass_b.py prepare          build passB_inputs_<docket>.jsonl (page-aware text per entry, reading policy applied)
    python pass_b.py dry-run [N]      N stratified entries, synchronous; prints arguments + projected batch cost
    python pass_b.py submit | fetch | report

READING POLICY (Marcus 2026-09-15): main letter (the first attachment of a single entry or of Part 1)
read in full up to MAIN_MAX_PAGES; every other attachment / every "(Part k of n)" with k > 1 is APPENDED
material: first APPX_HEAD_PAGES pages + the abstract page(s) of any embedded journal article.
Taxonomy for topic_code = the section numbers of EPA's own Response to Comments (rtc/rtc_sections.json).
"""
import hashlib, json, random, re, subprocess, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

DOCKET_ID       = "EPA-HQ-OAR-2025-0194"
MODEL           = "claude-opus-5"
EFFORT          = "medium"
MAX_TOKENS      = 12000
MAIN_MAX_PAGES  = 200
APPX_HEAD_PAGES = 5
ABSTRACT_CHARS  = 3000
APPX_MAX_CHARS  = 40000          # cap on appended material per entry
MAIN_MAX_CHARS  = 700000         # ~200 pp safety cap for docx/txt
BATCH_MAX       = 2000
SEED            = 20260917
HERE     = Path(__file__).resolve().parent
CORPUS   = HERE / f"unique_comments_{DOCKET_ID}.jsonl"
ATT_DIR  = HERE / "attachments_unique"
RTC_SECT = HERE / "rtc" / "rtc_sections.json"
INPUTS   = HERE / f"passB_inputs_{DOCKET_ID}.jsonl"
OUT      = HERE / f"arguments_{DOCKET_ID}.jsonl"
OUT_MD   = HERE / f"arguments_summary_{DOCKET_ID}.md"
BATCH_LOG= HERE / "batches_passB.json"
PRICE_IN, PRICE_OUT = 2.50, 12.50        # Opus 5 batch $/M
ARTICLE_RX = re.compile(r"\bdoi\b|doi\.org|\bAbstract\b|Received:|Accepted:|Journal of |Proceedings of |Elsevier|Springer|Wiley", re.I)
PART_RX    = re.compile(r"\((?:Part|Batch)\s+(\d+)\s+of\s+(\d+)\)|\[(?:Part|Batch)\s+(\d+)\s+of\s+(\d+)\]", re.I)

def taxonomy():
    S = json.load(RTC_SECT.open())
    return "\n".join(f"{s['num']} {s['title']}" for s in S)

SYSTEM_HEAD = """You are analysing a public comment submitted on EPA docket EPA-HQ-OAR-2025-0194, the July 2025 proposal "Reconsideration of 2009 Endangerment Finding and Greenhouse Gas Vehicle Standards" (rescind the 2009 finding that six well-mixed greenhouse gases endanger public health and welfare under Clean Air Act section 202(a); repeal all GHG emission standards for light-, medium- and heavy-duty vehicles). EPA's proposed bases were: (1) a "best reading" of section 202(a) under which it cannot regulate GHGs for global climate change; (2) the major questions doctrine; (3) futility / de minimis contribution of US vehicle GHGs; and alternative bases on climate science (the DOE Climate Working Group report), lack of requisite technology, and vehicle cost. The final rule (Feb 2026) rested only on the legal bases and declared the science, technology and cost comments out of scope.

Your job: extract every DISTINCT ARGUMENT the commenter makes about the proposal, so that each can later be checked against EPA's Response to Comments (RTC). An argument is a claim with a reason or evidence, not a paragraph; merge restatements; keep genuinely different points separate. Include arguments that support the proposal as well as those opposing it. Capture specific evidence (a study, a dataset, a legal authority, a reliance-interest fact) as its own argument when the commenter relies on it.

For each argument give:
- claim: one sentence in the commenter's own terms.
- topic_code: the RTC section number below whose subject best matches the argument, or "OTHER" if none fits. Use the most specific (deepest) matching section. Comments on climate science / the DOE CWG report go to 3.1; on vehicle technology to 3.2; on vehicle cost to 3.3.
- topic_label: the section title you chose (or your own short label for OTHER).
- argument_type: legal | scientific | economic | health | procedural | reliance | technology | other.
- position: opposes_proposal | supports_proposal | neutral.
- quote: a verbatim sentence (<= 60 words) from the text supporting the claim. Never paraphrase inside quote.
- page: the page number from the nearest preceding "[p. N]" marker, or null.
- applies_to: whether the argument's logic transfers to EPA's parallel rescission of GHG findings and standards for fossil-fuel POWER PLANTS under CAA section 111 — "both" if it does not depend on section 202(a) or vehicles specifically (e.g., science, futility/de-minimis logic, major questions, health, procedure), "vehicle_only" if it turns on section 202(a) text or vehicle facts, "power_plant_only" if it is about section 111 or power plants, "unsure" otherwise.
- specificity: specific_evidence (cites a study, number, statute, case, or concrete fact) | general.

Also record: the commenter's name and type; whom they represent; the overall position; a list of any appended exhibits (title + kind) visible in the appended material; and any explicit requests (e.g., extension, hearing, withdrawal). Do not invent content: if the text is a cover note only, say so in notes and return an empty arguments list.

RTC SECTION TAXONOMY (number title):
"""

SCHEMA = {
  "type": "object",
  "properties": {
    "commenter_name": {"type": "string"},
    "commenter_type": {"type": "string", "enum": ["individual", "elected_official", "government", "academic_or_scientist", "health_professional_or_org", "ngo_or_advocacy", "business_or_industry", "trade_association", "religious_org", "other_org"]},
    "represents": {"type": "string"},
    "overall_position": {"type": "string", "enum": ["opposes_proposal", "supports_proposal", "mixed", "unclear"]},
    "arguments": {"type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "string"}, "topic_code": {"type": "string"}, "topic_label": {"type": "string"},
        "argument_type": {"type": "string", "enum": ["legal", "scientific", "economic", "health", "procedural", "reliance", "technology", "other"]},
        "position": {"type": "string", "enum": ["opposes_proposal", "supports_proposal", "neutral"]},
        "quote": {"type": "string"}, "page": {"type": ["integer", "null"]},
        "applies_to": {"type": "string", "enum": ["both", "vehicle_only", "power_plant_only", "unsure"]},
        "specificity": {"type": "string", "enum": ["specific_evidence", "general"]}},
        "required": ["claim", "topic_code", "topic_label", "argument_type", "position", "quote", "page", "applies_to", "specificity"],
        "additionalProperties": False}},
    "exhibits": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "kind": {"type": "string", "enum": ["journal_article", "report", "data", "legal_document", "letter", "other"]}}, "required": ["title", "kind"], "additionalProperties": False}},
    "requests": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string"}},
  "required": ["commenter_name", "commenter_type", "represents", "overall_position", "arguments", "exhibits", "requests", "notes"],
  "additionalProperties": False}

def system_blocks():
    return [{"type": "text", "text": SYSTEM_HEAD + taxonomy(), "cache_control": {"type": "ephemeral"}}]
PROMPT_SHA = hashlib.sha256((SYSTEM_HEAD + taxonomy() + json.dumps(SCHEMA, sort_keys=True) + EFFORT).encode()).hexdigest()[:16]

# ---------------------------------------------------------------- prepare
def pdf_pages_text(p: Path):
    r = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=600)
    return r.stdout.split("\f")

def clean(t): return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()

def read_attachment(p: Path, role: str):
    """role = 'main' -> full up to MAIN_MAX_PAGES; 'appended' -> head pages + article abstracts."""
    ext = p.suffix.lower(); flags = []
    if ext == ".pdf":
        pages = pdf_pages_text(p); n = len(pages)
        if role == "main":
            use = pages[:MAIN_MAX_PAGES]
            if n > MAIN_MAX_PAGES: flags.append(f"main truncated {n}->{MAIN_MAX_PAGES} pp")
            body = "\n".join(f"[p. {i+1}]\n{clean(pg)}" for i, pg in enumerate(use) if pg.strip())
        else:
            head = pages[:APPX_HEAD_PAGES]
            parts = [f"[p. {i+1}]\n{clean(pg)}" for i, pg in enumerate(head) if pg.strip()]
            starts = [i for i in range(APPX_HEAD_PAGES, n) if ARTICLE_RX.search(pages[i]) and re.search(r"\bAbstract\b", pages[i])]
            for i in starts[:12]:
                parts.append(f"[p. {i+1}] (article abstract)\n{clean(pages[i])[:ABSTRACT_CHARS]}")
            body = "\n".join(parts)
            if n > APPX_HEAD_PAGES: flags.append(f"appended: {n} pp, read {min(n,APPX_HEAD_PAGES)} head pp + {len(starts[:12])} abstracts")
        return body, n, flags
    if ext == ".docx":
        import docx
        t = clean("\n".join(x.text for x in docx.Document(str(p)).paragraphs))
    elif ext in (".txt", ".md", ".csv"):
        t = clean(p.read_text(errors="replace"))
    else:
        return "", 0, [f"unreadable {ext}"]
    cap = MAIN_MAX_CHARS if role == "main" else APPX_HEAD_PAGES * 3000
    if len(t) > cap: flags.append(f"{role} text truncated {len(t)}->{cap} chars")
    return t[:cap], None, flags

def prepare():
    rows = [json.loads(l) for l in CORPUS.open()]
    subs = [r for r in rows if r["substantive"]]
    print(f"{len(subs)} substantive entries")
    with INPUTS.open("w") as out:
        for k, r in enumerate(subs, 1):
            m = PART_RX.search(r["title"] or ""); part = int(m.group(1) or m.group(3)) if m else 1
            nparts = int(m.group(2) or m.group(4)) if m else 1
            group = PART_RX.sub("", r["title"] or "").strip()
            atts = sorted(ATT_DIR.glob(f"{r['id']}_*"))
            flags, sections, exhibits_pages = [], [], 0
            inline = r["text"] if r["text_source"] == "inline" else ""
            if inline: sections.append("[inline comment]\n" + inline[:MAIN_MAX_CHARS])
            # attachments the API lists but does not serve (copyright-restricted, Public Reading Room only)
            cache = json.load((HERE / "cache" / f"{r['id']}.json").open())
            for inc in cache.get("included", []):
                a = inc.get("attributes", {})
                if inc.get("type") == "attachments" and not a.get("fileFormats"):
                    sections.append("=== RESTRICTED ATTACHMENT (not served by regulations.gov; metadata only) ===\n"
                                    f"title: {a.get('title')}\nauthors: {a.get('authors')}\nrestriction: {a.get('restrictReasonType')}\n"
                                    f"abstract: {a.get('docAbstract')}")
                    flags.append(f"restricted attachment: {a.get('title')}")
            for j, p in enumerate(atts):
                role = "main" if (j == 0 and part == 1 and not inline) else "appended"
                body, n, fl = read_attachment(p, role)
                flags += [f"{p.name}: {f}" for f in fl]
                if body: sections.append(f"=== {'MAIN LETTER' if role=='main' else 'APPENDED MATERIAL'} ({p.name}{', ' + str(n) + ' pp' if n else ''}) ===\n{body}")
            text = "\n\n".join(sections)
            # scanned / image-only PDFs: no extractable text -> ship the PDF itself as a document block
            scanned = []
            if len(text) < 200:
                for p in atts:
                    if p.suffix.lower() == ".pdf" and p.stat().st_size < 30_000_000:
                        scanned.append(str(p.relative_to(HERE)))
                if scanned: flags.append(f"image-only PDF sent as document block: {len(scanned)} file(s)")
            appended = "\n".join(s for s in sections if s.startswith("=== APPENDED"))
            if len(appended) > APPX_MAX_CHARS:
                text = text.replace(appended, appended[:APPX_MAX_CHARS] + "\n[appended material truncated]"); flags.append("appended capped")
            out.write(json.dumps({"id": r["id"], "title": r["title"], "subtype": r["subtype"], "group": group, "part": part,
                                  "nparts": nparts, "pageCount": r["pageCount"], "substantive_reason": r["substantive_reason"],
                                  "n_attachments": len(atts), "flags": flags, "text": text, "text_chars": len(text),
                                  "scanned_pdfs": scanned}) + "\n")
            if k % 100 == 0: print(f"  {k}/{len(subs)}")
    print("wrote", INPUTS)

# ---------------------------------------------------------------- model calls
def user_content(row):
    txt = _user_text(row)
    if not row.get("scanned_pdfs"):
        return txt
    import base64
    blocks = []
    for rel in row["scanned_pdfs"][:3]:
        data = base64.standard_b64encode((HERE / rel).read_bytes()).decode()
        blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
    blocks.append({"type": "text", "text": txt + "\n\n[The attached PDF(s) are scanned images with no extractable text; read them directly. Page numbers = PDF page numbers.]"})
    return blocks

def _user_text(row):
    return (f"Docket entry {row['id']} | title: {row['title']} | regulations.gov subtype: {row['subtype']}"
            + (f" | part {row['part']} of {row['nparts']} of a multi-part submission" if row['nparts'] > 1 else "")
            + (f" | reading notes: {'; '.join(row['flags'])}" if row['flags'] else "") + "\n\n" + row["text"])

def params(row):
    return MessageCreateParamsNonStreaming(model=MODEL, max_tokens=MAX_TOKENS, system=system_blocks(),
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user_content(row)}])

def parse(msg):
    return json.loads(next(b.text for b in msg.content if b.type == "text"))

def load_inputs(): return [json.loads(l) for l in INPUTS.open()]

def dry_run(n):
    client = anthropic.Anthropic(); rows = load_inputs(); rng = random.Random(SEED)
    strata = {"Government State": 4, "Government Local": 2, "Government Tribal": 2, "Member of Congress": 2,
              "Company/Organization Comment": 6, "Public Comment": 4}
    sample = []
    for st, k in strata.items():
        pool = [r for r in rows if r["subtype"] == st]; rng.shuffle(pool); sample += pool[:k]
    sample = sample[:n]
    tok_in = tok_out = cache_r = 0; t0 = time.time()
    for r in sample:
        msg = client.messages.create(**params(r)); u = msg.usage
        tok_in += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0); tok_out += u.output_tokens; cache_r += (u.cache_read_input_tokens or 0)
        d = parse(msg)
        print(f"\n### {r['id']} | {r['subtype']} | {r['text_chars']:,} chars | {d['commenter_name']} ({d['commenter_type']}) — {d['overall_position']} | {len(d['arguments'])} args | exhibits {len(d['exhibits'])} | notes: {d['notes'][:120]}")
        for a in d["arguments"][:8]:
            print(f"   [{a['topic_code']:<9}] {a['argument_type']:<10} {a['applies_to']:<12} {a['specificity'][:8]:<8} | {a['claim'][:120]}")
        if len(d["arguments"]) > 8: print(f"   ... +{len(d['arguments'])-8} more")
    N = len(rows); per_in, per_out = tok_in/len(sample), tok_out/len(sample)
    tot_chars = sum(r["text_chars"] for r in rows); samp_chars = sum(r["text_chars"] for r in sample)
    # scale input by chars (sample is biased to long docs), output by count
    proj_in = tok_in * tot_chars / samp_chars; proj_out = per_out * N
    print(f"\nmeasured: {per_in:.0f} in (cache-read {cache_r/len(sample):.0f}) / {per_out:.0f} out per doc; {time.time()-t0:.0f}s for {len(sample)} sync calls")
    print(f"projected batch over {N:,} docs: ~{proj_in/1e6:.1f}M in + {proj_out/1e6:.2f}M out ≈ ${(proj_in*PRICE_IN + proj_out*PRICE_OUT)/1e6:,.0f}  [dry run ≈ ${(tok_in*5 + tok_out*25)/1e6:.2f}]")

def submit():
    client = anthropic.Anthropic(); rows = load_inputs()
    done = {json.loads(l)["id"] for l in OUT.open()} if OUT.exists() else set()
    log = json.loads(BATCH_LOG.read_text()) if BATCH_LOG.exists() else []
    pending = {cid for e in log if e.get("status") != "ended" for cid in e.get("ids", [])}
    todo = [r for r in rows if r["id"] not in done and r["id"] not in pending]
    print(f"{len(todo)} to submit")
    for k in range(0, len(todo), BATCH_MAX):
        chunk = todo[k:k+BATCH_MAX]
        b = client.messages.batches.create(requests=[Request(custom_id=r["id"], params=params(r)) for r in chunk])
        log.append({"batch_id": b.id, "n": len(chunk), "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": MODEL,
                    "prompt_sha": PROMPT_SHA, "status": b.processing_status, "ids": [r["id"] for r in chunk]})
        print("submitted", b.id, len(chunk))
    BATCH_LOG.write_text(json.dumps(log, indent=1))

def fetch():
    client = anthropic.Anthropic(); log = json.loads(BATCH_LOG.read_text())
    have = {json.loads(l)["id"] for l in OUT.open()} if OUT.exists() else set()
    with OUT.open("a") as out:
        for e in log:
            b = client.messages.batches.retrieve(e["batch_id"]); e["status"] = b.processing_status; c = b.request_counts
            print(f"{b.id}: {b.processing_status} ok={c.succeeded} err={c.errored} proc={c.processing}")
            if b.processing_status != "ended": continue
            for res in client.messages.batches.results(b.id):
                if res.custom_id in have: continue
                row = {"id": res.custom_id, "batch_id": b.id, "model": MODEL, "prompt_sha": PROMPT_SHA, "fetched": time.strftime("%Y-%m-%d")}
                if res.result.type == "succeeded":
                    try:
                        row.update(parse(res.result.message)); row["result"] = "ok"; u = res.result.message.usage
                        row["usage_in"] = u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0); row["usage_out"] = u.output_tokens
                        row["stop_reason"] = res.result.message.stop_reason
                    except Exception as ex: row.update({"result": "parse_error", "error": str(ex)})
                else: row.update({"result": res.result.type, "error": getattr(getattr(res.result, "error", None), "type", None)})
                out.write(json.dumps(row) + "\n"); have.add(res.custom_id)
    BATCH_LOG.write_text(json.dumps(log, indent=1))

def report():
    import pandas as pd
    rows = [json.loads(l) for l in OUT.open()]; ok = [r for r in rows if r.get("result") == "ok"]
    args = pd.DataFrame([{**a, "id": r["id"], "commenter": r["commenter_name"], "ctype": r["commenter_type"], "overall": r["overall_position"]} for r in ok for a in r["arguments"]])
    cost = sum(r["usage_in"] for r in ok) * PRICE_IN / 1e6 + sum(r["usage_out"] for r in ok) * PRICE_OUT / 1e6
    md = [f"# Pass B — arguments extracted from substantive comments, docket {DOCKET_ID}", "",
          f"Model {MODEL}, effort {EFFORT}, prompt {PROMPT_SHA}. {len(rows)} docs, {len(ok)} ok, {len(rows)-len(ok)} failed; "
          f"{len(args):,} arguments; truncated outputs (stop_reason=max_tokens): {sum(1 for r in ok if r.get('stop_reason')=='max_tokens')}. Cost ≈ ${cost:,.0f}.", "",
          "## Arguments by RTC topic (top 40)", "", args.groupby(["topic_code", "topic_label"]).agg(n=("id","size"), commenters=("id","nunique")).sort_values("n", ascending=False).head(40).to_markdown(), "",
          "## applies_to × position", "", pd.crosstab(args.applies_to, args.position).to_markdown(), "",
          "## argument_type", "", args.argument_type.value_counts().to_markdown(), ""]
    OUT_MD.write_text("\n".join(md)); print("\n".join(md[:4]))
    args.to_csv(HERE / f"arguments_flat_{DOCKET_ID}.csv", index=False)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    {"prepare": prepare, "dry-run": lambda: dry_run(int(sys.argv[2]) if len(sys.argv) > 2 else 20),
     "submit": submit, "fetch": fetch, "report": report}[cmd]()
