# Endangerment Docket Explorer

Every public comment on EPA's 2025 proposal to rescind the 2009 Greenhouse Gas Endangerment Finding
(regulations.gov docket **EPA-HQ-OAR-2025-0194**), classified by stance and commenter type — and every
substantive argument checked against what EPA actually said in reply, across four EPA documents.

**Site:** https://msarofim.github.io/endangerment-docket-explorer/

Built because of how EPA characterised that record. The September 2026 power-plant proposal describes it
as "hundreds of thousands of comments from a variety of perspectives" (91 FR 59016). The vehicle final
rule it builds on says the same thing four times over:

> "The EPA received supportive and adverse comments on virtually all substantive aspects of the proposal
> from a wide variety of stakeholders" — 91 FR 7695

> "In reviewing the public response to the proposal, the Administrator appreciated the wide variety of
> perspectives and significant interest in the issues raised for further consideration." — 91 FR 7701

> "The EPA received comments from a variety of stakeholders supporting and criticizing the legal
> rationale set out in the proposed rule." — 91 FR 7721, repeated verbatim at 7726

And the Response to Comments says the same of the hearing — "the comment period included an extensive
public hearing with testimony from a variety of perspectives" (§1.3.2).

The hearing is the one part of this record someone independently counted. EPA heard "oral testimony from
more than 600 speakers" across four days, 19–22 August 2025 (91 FR 7693; the RTC adds "over more than 30
hours"). *Eos* sat through all four days and tallied them:

> "By our count, at the end of the four full days of public hearing testimony, we'd heard hundreds of
> Americans speak out against the EPA proposal and fewer than 20 speak in favor."
>
> — Grace van Deelen, [*Eos*, 25 August 2025](https://eos.org/research-and-developments/public-speaks-out-against-epa-plan-to-rescind-endangerment-finding)

Fewer than 20 of more than 600 is under 3%. This explorer covers the written comments, a separate layer
of the same record; the two layers agree.

EPA counted the comments — "approximately 572,000 written comments from more than 31,000 unique entities
and 169 mass letter writing campaigns" (91 FR 7693) — without ever discussing the relative number of
comments that supported the rule relative to the ones that opposed it. The record shows **98.1%** of
comments with a determinable stance opposed the rescission.

The vehicle rule lists the categories of stakeholder EPA heard from. Every one of them ran heavily
against the rescission except two:

| | oppose | support | opposed |
|---|---:|---:|---:|
| Individual citizens | 25,591 | 2,296 | 91.8% |
| Environmental / advocacy groups | 638 | 28 | 95.8% |
| Health professionals and organizations | 532 | 0 | 100% |
| Academics and scientists | 500 | 20 | 96.2% |
| State and local governments | 171 | 8 | 95.5% |
| Elected officials | 112 | 5 | 95.7% |
| Religious organizations | 74 | 1 | 98.7% |
| **Business and industry** | 105 | **266** | **28.3%** |
| **Trade associations** | 17 | **34** | **33.3%** |

## What is here

| | |
|---|---|
| `docs/index.html` | the whole tool — one self-contained file, no server, no build step to view it |
| `docs/data/*.csv` | the published tables, linked from the Methods page |
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
