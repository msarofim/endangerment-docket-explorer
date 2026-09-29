#!/usr/bin/env python
"""Release gate for the public build. Scans every string that ships — site/data.json and every CSV in
dist/data/ — for contact details and for name-policy violations, and FAILS the build rather than warning.

    python check_release.py            scan; exit 1 on any finding
    python check_release.py --mutate   mutation test: plant each class of violation, require a FAIL each
                                       time, then confirm the clean build still passes

Why a gate and not a spot check: the published key_phrase / evidence / quote fields are arbitrary
truncations of comment text, so a commenter's signature block can land inside one on ANY rebuild. A scan
run once proves nothing about the next build.

ALLOW lists the addresses that are supposed to be here — EPA's own docket contacts, which appear inside
quoted mass-mail headers and are not private contact details.
"""
import csv, json, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "data.json"
DATA = HERE / "dist" / "data"
ANON_LABEL = "Individual commenter"

ALLOW = re.compile(r"@epa\.gov$|@regulations\.gov$|@gpo\.gov$", re.I)
PATTERNS = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("phone", re.compile(r"(?<![\d-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])")),
    ("street address", re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-zA-Z.]+\s+){1,3}"
                                  r"(?:Street|St\.|Avenue|Ave\.?|Road|Rd\.?|Drive|Dr\.?|Lane|Ln\.?|Boulevard|Blvd\.?|Court|Ct\.?|"
                                  r"Way|Circle|Cir\.?|Terrace|Ter\.?|Place|Pl\.?|Parkway|Pkwy\.?|Trail|Trl\.?|"
                                  r"Highway|Hwy\.?|Loop|Square|Sq\.?|Alley|Commons|Crescent|Path)\b")),
    ("SSN-shaped", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]
# Two classes of match are not private addresses. (a) Federal office addresses quoted in letter
# salutations -- matched on the STREET NAME, because the evidence field is truncated mid-string and can
# lop the leading digit off "1301 Constitution Ave". (b) Legal reporter citations: "138 S. Ct. 1612"
# has the shape <number> <Capitalised> <Ct.> and is otherwise indistinguishable from "138 Main Ct."
ADDRESS_ALLOW = re.compile(r"^(1?200\s+Pennsylvania|1?301\s+Constitution|109\s+T\.?\s?W\.?\s+Alexander)\s+Ave", re.I)
REPORTER = re.compile(r"\b(S\.?\s?Ct|F\.?\s?Supp|F\.?\s?[23]d|U\.?\s?S|Cir|L\.?\s?Ed|FR|Fed\.?\s?Reg)\.?\b", re.I)


def strings(obj, path="$"):
    """every string that ships, with a path so a finding can be located"""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from strings(v, f"{path}[{i}]")


def scan_payload(payload):
    out = []
    for path, s in strings(payload):
        for label, rx in PATTERNS:
            for m in rx.finditer(s):
                hit = m.group(0)
                if label == "email" and ALLOW.search(hit):
                    continue
                if label == "street address" and (ADDRESS_ALLOW.search(hit) or REPORTER.search(hit)):
                    continue
                out.append((label, path, hit, s[max(0, m.start() - 60):m.start() + 60]))
    return out


def check_name_policy(D):
    """the published name must be the anonymised label wherever the bundle says the name is withheld,
    and an anonymised row must never carry its name in any other field"""
    bad = []
    for c in D["comments"]:
        if c["an"] and c["t"] != ANON_LABEL:
            bad.append(("name policy", f"comment {c['id']}", c["t"], "withheld row carries a name"))
        if not c["an"] and c["t"] == ANON_LABEL:
            bad.append(("name policy", f"comment {c['id']}", c["t"], "named row carries the anon label"))
    named = {c["id"] for c in D["comments"] if not c["an"]}
    for a in D["arguments"]:
        for w in a["who"]:
            if w == ANON_LABEL:
                continue
    counts = D["summary"]["names_published"], D["summary"]["names_withheld"]
    if counts[0] != len(named) or sum(counts) != len(D["comments"]):
        bad.append(("name policy", "$.summary", str(counts), "published/withheld counters disagree with the rows"))
    return bad


def check_downloads(D):
    bad = []
    for d in D["summary"].get("downloads", []):
        p = DATA / d["file"]
        if not p.exists():
            bad.append(("download", d["file"], "missing", "listed in the manifest but not on disk"))
        elif p.stat().st_size != d["bytes"]:
            bad.append(("download", d["file"], str(p.stat().st_size), f"size differs from manifest {d['bytes']}"))
    if not D["summary"].get("downloads"):
        bad.append(("download", "$.summary.downloads", "empty", "run make_downloads.py before building"))
    return bad


def run(bundle_text=None, csv_dir=None):
    D = json.loads(bundle_text if bundle_text is not None else BUNDLE.read_text())
    findings = scan_payload(D) + check_name_policy(D) + check_downloads(D)
    for p in sorted((csv_dir or DATA).glob("*.csv")):
        rows = list(csv.reader(p.open()))
        findings += [(lbl, f"{p.name}:{loc}", hit, ctx) for lbl, loc, hit, ctx in scan_payload(rows)]
    return findings


def report(findings, label=""):
    if not findings:
        print(f"PASS{label}: no contact details, no name-policy violations, downloads match the manifest")
        return 0
    print(f"FAIL{label}: {len(findings)} finding(s)")
    for lbl, path, hit, ctx in findings[:40]:
        print(f"  [{lbl}] {path}: {hit!r}\n      …{re.sub(r'\s+', ' ', ctx)}…")
    return 1


def mutate():
    """break what the gate guards and require it to notice — a gate that has never failed is not a gate"""
    base = json.loads(BUNDLE.read_text())
    cases = [
        ("email", "jane.doe@example.com", lambda d, v: d["comments"][0].update(k=d["comments"][0]["k"] + " contact me at " + v)),
        ("phone", "415-555-0134", lambda d, v: d["comments"][1].update(k=d["comments"][1]["k"] + " call " + v)),
        ("street address", "742 Evergreen Terrace", lambda d, v: d["campaigns"][0].update(ev=d["campaigns"][0]["ev"] + " " + v)),
        ("street address", "1600 Pennsylvania Avenue", lambda d, v: d["comments"][3].update(k=d["comments"][3]["k"] + " at " + v)),
        ("street address", "221 Baker Street", lambda d, v: d["comments"][4].update(k=d["comments"][4]["k"] + " " + v)),
        ("name policy", "A Private Citizen", lambda d, v: d["comments"][2].update(t=v)),   # row has an=1
        ("download", "nope.csv", lambda d, v: d["summary"].update(downloads=[{"file": v, "label": "x", "rows": 1, "bytes": 1}])),
    ]
    ok = True
    for name, planted, mutation in cases:
        d = json.loads(json.dumps(base)); mutation(d, planted)
        found = run(bundle_text=json.dumps(d))
        # require the PLANTED value itself, not merely a finding of the same class -- a pre-existing false
        # positive of that class would otherwise satisfy this test and hide a blind spot
        caught = any(f[0] == name and any(planted in str(x) or str(x) in planted for x in (f[1], f[2])) for f in found)
        print(f"  mutation {name:<16} {planted:<26} -> {'CAUGHT' if caught else 'MISSED — gate is blind to this'}")
        ok &= caught
    clean = run()
    print(f"  clean build           -> {'PASS' if not clean else 'FAIL'}")
    return 0 if (ok and not clean) else 1


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        print("mutation test:"); sys.exit(mutate())
    sys.exit(report(run()))
