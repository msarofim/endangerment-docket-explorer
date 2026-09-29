# Endangerment Docket Explorer

Every public comment on EPA's 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding
(regulations.gov docket **EPA-HQ-OAR-2025-0194**), classified by stance and commenter type — and every
substantive argument checked against what EPA actually said in reply, across four EPA documents.

**Site:** https://msarofim.github.io/endangerment-docket-explorer/

Built because EPA's September 2026 power-plant proposal describes that docket as "hundreds of thousands
of comments from a variety of perspectives" (91 FR 59016) without ever quantifying it. The record shows
**98.1%** of comments with a determinable stance opposed the rescission.

## What is here

| | |
|---|---|
| `site/dist/index.html` | the whole tool — one self-contained file, no server, no build step to view it |
| `site/dist/data/*.csv` | the published tables, linked from the Methods page |
| `site/` | the build: `build_data.py` → `make_downloads.py` → `check_release.py` → `build_site.py` |
| `pipeline/` | the analysis that produced the data — docket pull, corpus, and the four model passes |

## The analysis

- **Campaign layer.** EPA collapses each letter-writing campaign to one docket entry; signature counts
  come from Appendix A of EPA's own Response to Comments, not from regulations.gov's `duplicateComments`
  field, which reads 1 for a block of 54 campaigns Appendix A shows are up to 22,263 signatures.
- **Pass A — stance.** Every non-campaign entry classified from its text into a stance, a commenter type
  and a verbatim key phrase. Sonnet 5 in bulk; every row not classified with high confidence, and every
  mixed/unclear row, re-classified by Opus 5, which prevails.
- **Pass B — arguments.** Each substantive comment (organization, government, Member of Congress or
  petition subtype, or ≥5 pages) read by Opus 5, which extracts each distinct argument with a verbatim
  quote, tags it to a section of EPA's own Response to Comments outline, and records whether its logic
  transfers to the power-plant rescission.
- **Pass C — coverage.** Arguments merged into canonical arguments, then each checked against four EPA
  documents — the vehicle Response to Comments, the vehicle final rule (91 FR 2026-03157), the
  power-plant supplemental proposal (91 FR 2026-19072) and the power-plant partial repeal
  (91 FR 2026-19071) — for a verdict with the decisive EPA sentence and its page.
- **Pass D — novelty.** EPA's own endangerment arguments in the power-plant proposal, each checked
  against the vehicle documents for whether EPA had already made it.

Numbers, limits and the audit are on the site's Methods page. Everything is auditable from the CSVs and
the linked source documents.

## Submitter names

A submitter's name is published only where the comment was filed in a **public capacity**: a substantive
entry whose commenter type is not "individual" — 984 of 30,748 entries. The rest appear as "Individual
commenter" with a link to their docket entry, where regulations.gov shows the name.

The names are already public. What this tool declines to do is reproduce ~29,800 private citizens' names
in a single searchable table. The `substantive` flag rather than the commenter type carries that test,
because the docket title is the *submitter* while the type describes who they write *about*: 98% of
non-substantive rows typed as an organization category in fact carry an ordinary personal name.

`site/check_release.py` enforces this and scans every published string for contact details. It is
mutation-tested — `python check_release.py --mutate` plants each class of violation and requires the
gate to fail on every one — because a gate that has never failed is not a gate.

## Rebuilding

```bash
cd site
python build_data.py        # outputs -> data.json
python make_downloads.py    # data.json -> dist/data/*.csv, writes the download manifest back
python check_release.py     # privacy + name-policy + manifest gate; exits 1 on any finding
python build_site.py        # template.html + data.json -> dist/index.html
```

`build_data.py` reads the pipeline outputs, which are not in this repository — they run to several
gigabytes, and the comment text is regulations.gov's corpus rather than an analysis product. `pipeline/`
regenerates them from the docket; a regulations.gov API key and an Anthropic API key are required.

## Corrections

Every stance, commenter type and coverage verdict was assigned by a language model and may be wrong in
any individual row. If your comment is classified incorrectly, or you want your organization's name
removed, open an issue — corrections are made and the page rebuilt.

## Licence

Code MIT (`LICENSE`). Classifications, canonical arguments and coverage verdicts CC BY 4.0
(`LICENSE-DATA`). The comments themselves are U.S. federal public records and carry no copyright; 103
copyright-restricted attachments on the docket were never reproduced, only their titles and abstracts.

Marcus C. Sarofim. Classification by Claude (Anthropic) models.
