#!/usr/bin/env python
"""Repair canonical membership (2026-09-18): merge rounds dropped ~36 % of raw-argument ids and left 731
member-less canonicals. Assign every unassigned raw argument to the most similar canonical claim in its
topic (TF-IDF cosine, flagged), drop canonicals that remain empty, recompute commenter stats."""
import json, pandas as pd, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from pathlib import Path
HERE = Path(__file__).resolve().parent; D = "EPA-HQ-OAR-2025-0194"
canon = [json.loads(l) for l in (HERE / f"canonical_arguments_{D}.jsonl").open()]
a = pd.read_csv(HERE / f"arguments_flat_{D}.csv"); a["arg_id"] = [f"{r.id.split('-')[-1]}#{i}" for i, r in a.iterrows()]
assigned = {m for c in canon for m in c["member_ids"]}
n_sim = 0; low = 0
for topic, g in a.groupby("topic_code"):
    cs = [c for c in canon if c["topic_code"] == topic]
    if not cs: continue
    todo = g[~g.arg_id.isin(assigned)]
    if todo.empty: continue
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True).fit([c["canonical_claim"] for c in cs] + list(todo.claim))
    S = cosine_similarity(vec.transform(todo.claim), vec.transform([c["canonical_claim"] for c in cs]))
    for (_, r), row in zip(todo.iterrows(), S):
        j = int(row.argmax()); cs[j].setdefault("member_ids_sim", []).append(r.arg_id); n_sim += 1
        if row[j] < 0.15: low += 1
print(f"similarity-assigned {n_sim} raw args ({low} with cosine < 0.15)")
out = []
for c in canon:
    members = sorted(set(c["member_ids"]) | set(c.get("member_ids_sim", [])))
    if not members: continue
    m = a.set_index("arg_id").loc[members]
    c.update({"member_ids": members, "n_members": len(members), "n_members_sim": len(c.get("member_ids_sim", [])),
              "commenters": sorted(set(m.commenter)), "n_commenters": int(m.commenter.nunique()),
              "commenter_types": m.ctype.value_counts().to_dict(), "sample_quote": m.iloc[0].quote, "sample_doc": m.iloc[0].id})
    c.pop("member_ids_sim", None); out.append(c)
(HERE / f"canonical_arguments_{D}.jsonl").write_text("".join(json.dumps(c) + "\n" for c in out))
cov = {m for c in out for m in c["member_ids"]}
print(f"{len(out)} canonicals kept ({len(canon)-len(out)} empty dropped); coverage {len(cov):,}/{len(a):,} = {len(cov)/len(a):.1%}")
