"""Paragraph index + BM25 retrieval over the four coverage documents (shared by Pass C and Pass D)."""
import json, re
from pathlib import Path
from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent
DOCS = {   # short key -> (label, path)
    "vehicle_rtc":     ("Vehicle RTC (docket doc 31089, Feb 2026)",            HERE / "rtc" / "RTC_31089.txt"),
    "vehicle_fr":      ("Vehicle final rule preamble (91 FR 2026-03157)",      HERE / "vehicle_rule" / "FR_2026-03157.txt"),
    "sprm_fr":         ("Power-plant SPRM (91 FR 2026-19072, Sept 2026)",      HERE / "powerplant" / "FR_2026-19072_sprm.txt"),
    "partial_repeal":  ("Power-plant final partial repeal (91 FR 2026-19071)", HERE / "powerplant" / "FR_2026-19071_partial_repeal.txt"),
}
TOK = re.compile(r"[a-z0-9]+")
def tok(s): return TOK.findall(s.lower())

def paragraphs(text, min_chars=200, max_chars=2500):
    """Split on blank lines; glue very short fragments to the previous paragraph; hard-wrap long ones."""
    out, cur = [], ""
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", " ", p).strip()
        if not p: continue
        if len(cur) < min_chars: cur = (cur + " " + p).strip()
        else: out.append(cur); cur = p
    if cur: out.append(cur)
    final = []
    for p in out:
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars); cut = cut if cut > min_chars else max_chars
            final.append(p[:cut+1]); p = p[cut+1:].strip()
        final.append(p)
    return final

class DocIndex:
    def __init__(self, keys=None):
        self.docs = {}
        for k, (label, path) in DOCS.items():
            if keys and k not in keys: continue
            paras = paragraphs(path.read_text(errors="replace"))
            self.docs[k] = {"label": label, "paras": paras, "bm25": BM25Okapi([tok(p) for p in paras])}
    def query(self, text, k=8, keys=None):
        q = tok(text); res = {}
        for key, d in self.docs.items():
            if keys and key not in keys: continue
            scores = d["bm25"].get_scores(q)
            top = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
            res[key] = [{"para_idx": i, "score": float(scores[i]), "text": d["paras"][i]} for i in top if scores[i] > 0]
        return res

if __name__ == "__main__":
    ix = DocIndex()
    for k, d in ix.docs.items(): print(k, len(d["paras"]), "paragraphs")
    r = ix.query("EPA improperly imports the significantly contribute standard from section 111 to define de minimis under section 202")
    for k, hits in r.items(): print("\n", k, "->", hits[0]["text"][:200] if hits else None)
