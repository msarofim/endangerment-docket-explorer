#!/usr/bin/env python
"""
Stance classification of the mass-comment-campaign layer of docket
EPA-HQ-OAR-2025-0194 (2009 Endangerment Finding rescission).

Input : campaigns_<docket>.csv  (from fetch_campaigns.py) + campaign_texts.jsonl
Output: campaign_stance_<docket>.csv, campaign_stance_summary_<docket>.md

Method (2026-09-15, Marcus Sarofim / Claude):
  * Every campaign's representative text (inline comment + first 2,500 chars of
    each attachment) was read by hand. Default stance is OPPOSE (the rescission);
    the exceptions are enumerated below with the phrase that decided them.
  * As a guard on the hand pass, an OPPOSE row must ALSO contain an opposition
    phrase from OPP_RX in its text, or carry a manual note explaining why not
    (scanned petition, cover letter only, etc.). Rows failing both are flagged
    NEEDS_REVIEW and excluded from the numerator.
  * `duplicateComments` (regulations.gov's count of near-identical submissions
    folded into the entry) is the comment-volume weight.
"""
import json, re
from pathlib import Path
import pandas as pd

DOCKET_ID = "EPA-HQ-OAR-2025-0194"
HERE = Path(__file__).resolve().parent
IN_CSV  = HERE / f"campaigns_{DOCKET_ID}.csv"
IN_TXT  = HERE / "campaign_texts.jsonl"
OUT_CSV = HERE / f"campaign_stance_{DOCKET_ID}.csv"
OUT_MD  = HERE / f"campaign_stance_summary_{DOCKET_ID}.md"
EPA_TOTAL_COMMENTS = 572_000      # 91 FR (Feb 18 2026) preamble: "approximately 572,000"
EPA_UNIQUE_ENTITIES = 31_000      # ibid.: "more than 31,000 unique entities"
EPA_CAMPAIGNS = 169               # ibid.: "169 mass letter writing campaigns"
EPA_CAMPAIGN_COMMENTS = 534_000   # RTC (docket doc 31089) intro: "about 534,000 comments ... 169 mass comment campaigns"
APPX_A = HERE / "rtc" / "rtc_appendixA_campaigns.csv"   # parsed from RTC Appendix A (169 rows, 534,160 signatures)
# WEIGHT: EPA's Appendix-A signature count where the campaign is listed there; regulations.gov's
# duplicateComments otherwise (the 17 late mailers). duplicateComments is WRONG (=1) for the
# 2626-2679 block, which Appendix A shows are campaigns of up to 22,263 signatures.

OPP_RX = re.compile(
    r"(oppos\w*|against (the|this|epa)|reject\w*|do not (rescind|repeal|revoke|reverse|finaliz\w+|advance)"
    r"|don.t (rescind|repeal)|not (to )?(rescind|repeal|revoke|reconsider|reopen|reverse)"
    r"|withdraw (the|this|your|its) propos\w+|should withdraw|abandon (the|this|its) (plan|propos|reconsid)\w*"
    r"|keep (the|strong|cutting)|retain\w*|uphold\w*|maintain\w*|preserve|hands off"
    r"|support (of |for )?the (epa.s |2009 )?(2009 )?(greenhouse gas )?endangerment finding"
    r"|support the comment|reaffirm the 2009)", re.I)

# --- hand classification (id -> (stance, decisive phrase / reason)) ---------
SUPPORT = {
    "EPA-HQ-OAR-2025-0194-2689":  "'End EPA's Fake Green Mandates and Overreach! I strongly support the EPA's proposal to reconsider and repeal'",
    "EPA-HQ-OAR-2025-0194-1508":  "same 'Fake Green Mandates' text (letter cites docket 0137 but is posted in 0194)",
    "EPA-HQ-OAR-2025-0194-30782": "same 'Fake Green Mandates' text",
    "EPA-HQ-OAR-2025-0194-2706":  "'As an Arizona resident, I support the repeal of the Endangerment Finding'",
    "EPA-HQ-OAR-2025-0194-2656":  "'I am writing to strongly support the agency's proposed action to repeal'",
    "EPA-HQ-OAR-2025-0194-2671":  "'I strongly support the EPA's proposal to repeal the Endangerment Finding'",
    "EPA-HQ-OAR-2025-0194-2653":  "'I support President Trump's efforts to reconsider the EPA's Endangerment Finding'",
    "EPA-HQ-OAR-2025-0194-2658":  "'express my support for the agency's proposed repeal'",
    "EPA-HQ-OAR-2025-0194-2657":  "'I support the agency's proposed repeal of the CO2 greenhouse gas endangerment finding'",
}
UNCLEAR = {
    "EPA-HQ-OAR-2025-0194-0145": "American Lung Association, 228 signatures per RTC Appendix A; NOT posted on regulations.gov (API 404), so the text cannot be checked",
    "EPA-HQ-OAR-2025-0194-1458": "Grace United Methodist Church: docket holds only the e-mail cover note; member comments not extractable",
    "EPA-HQ-OAR-2025-0194-2721": "EveryAction template submitted blank ('Write your message here using the talking points above')",
}
EXCLUDE = {
    "EPA-HQ-OAR-2025-0194-1457": "title = 'Duplicate of EPA-HQ-OAR-2025-0194-1434'; duplicateComments = 0",
}
# OPPOSE rows whose text carries no regex-detectable phrase; reason read by hand
OPPOSE_MANUAL = {
    "EPA-HQ-OAR-2025-0194-0777": "NPCA scanned petition (25 pp): header reads 'I strongly oppose the proposal to eliminate the Endangerment Finding' (read from page image)",
    "EPA-HQ-OAR-2025-0194-1455": "NPCA scanned petition, same form as 0777 (read from page image)",
    "EPA-HQ-OAR-2025-0194-0780": "Town of Concord Climate Action Committee, scanned letter: 'vehemently opposed' (read from page image)",
    "EPA-HQ-OAR-2025-0194-1442": "AGU + 900 climate experts: letter affirms the science 'in response to the proposal to overturn'; AGU testified against at the hearing",
    "EPA-HQ-OAR-2025-0194-1504": "duplicate posting of AGU letter (1442)",
    "EPA-HQ-OAR-2025-0194-1444": "Expert Working Group on Climate Change and Health: 'we strongly oppose this proposed rule change' (p.5 of attachment, beyond the 2,500-char head)",
    "EPA-HQ-OAR-2025-0194-1505": "Pace law-student coalition: 'EPA should withdraw the proposed rule and reaffirm the 2009 Endangerment Finding' (p.2+)",
    "EPA-HQ-OAR-2025-0194-2716": "subject line 'Hands off our climate safeguards!'",
    "EPA-HQ-OAR-2025-0194-1449": "'EPA's proposal ... is arbitrary, harmful, costly to consumers, and should be withdrawn'",
    "EPA-HQ-OAR-2025-0194-2720": "'I urge you to abandon this attempt'",
    "EPA-HQ-OAR-2025-0194-2663": "'Please reconsider ... The science is stronger now than in 2009' (asks EPA to drop the reconsideration)",
    "EPA-HQ-OAR-2025-0194-2667": "'in support of the comment submitted by the American Physical Society' (APS opposed the repeal)",
    "EPA-HQ-OAR-2025-0194-2645": "American Sustainable Business Network: 'unwavering supporters of policies that strengthen the US commitment to clean energy'",
    "EPA-HQ-OAR-2025-0194-2628": "'I support the 2009 Endangerment Finding and Greenhouse Gas Vehicle Standards.'",
    "EPA-HQ-OAR-2025-0194-2679": "'voice my concerns on the federal government plans to roll back the endangerment rule'",
    "EPA-HQ-OAR-2025-0194-2668": "quotes the scientific-consensus statement against the repeal",
    "EPA-HQ-OAR-2025-0194-2672": "'As a scientist ... I affirm that climate change ... is unequivocally driven by human activity' (AGU letter text)",
    "EPA-HQ-OAR-2025-0194-2641": "'vested interest in maintaining the physical livability of the United States'",
    "EPA-HQ-OAR-2025-0194-2647": "'Your mission is to protect the environment, not give in to corporate interests'",
    "EPA-HQ-OAR-2025-0194-2655": "'The Trump administration is gutting multiple protections'",
    "EPA-HQ-OAR-2025-0194-2678": "'If global temperatures keep rising, the issues ... will continue to worsen'",
    "EPA-HQ-OAR-2025-0194-0788": "'abandon its reconsideration of the 2009 endangerment finding'",
    "EPA-HQ-OAR-2025-0194-2711": "'abandon plans to roll back vehicle pollution standards'",
    "EPA-HQ-OAR-2025-0194-2688": "'one of the countless Americans who support the EPA's 2009 Greenhouse Gas Endangerment finding'",
    "EPA-HQ-OAR-2025-0194-30793": "same text as 2688",
    "EPA-HQ-OAR-2025-0194-0251": "Sierra Club, 16,098 signers: 'The 2009 endangerment finding is based on overwhelming scientific evidence ... Please do your job and protect our environment'",
    "EPA-HQ-OAR-2025-0194-2692": "'I am deeply concerned by your plans to rescind the Endangerment Finding ... Denying the basic facts of climate change'",
    "EPA-HQ-OAR-2025-0194-2627": "'tremendously disappointed to see the EPA's dangerous proposal to rescind'",
    "EPA-HQ-OAR-2025-0194-2662": "'I urge the EPA to rescind its proposal to repeal the endangerment finding'",
    "EPA-HQ-OAR-2025-0194-2648": "Sierra Club letter text: 'The 2009 endangerment finding is based on overwhelming scientific evidence'",
    "EPA-HQ-OAR-2025-0194-2687": "'respectfully request that the EPA abandon its reconsideration'",
}

def main():
    df = pd.read_csv(IN_CSV)
    txt = {json.loads(l)["id"]: json.loads(l)["text"] for l in IN_TXT.open()}
    rows = []
    for _, r in df.iterrows():
        cid, t = r.id, re.sub(r"\s+", " ", txt.get(r.id, ""))
        if cid in EXCLUDE:
            st, how, why = "EXCLUDE", "manual", EXCLUDE[cid]
        elif cid in SUPPORT:
            st, how, why = "SUPPORT", "manual", SUPPORT[cid]
        elif cid in UNCLEAR:
            st, how, why = "UNCLEAR", "manual", UNCLEAR[cid]
        elif cid in OPPOSE_MANUAL:
            st, how, why = "OPPOSE", "manual", OPPOSE_MANUAL[cid]
        else:
            m = OPP_RX.search(t)
            if m:
                st, how = "OPPOSE", "hand-read + regex"
                why = t[max(0, m.start()-60): m.end()+60]
            else:
                st, how, why = "NEEDS_REVIEW", "none", "no opposition phrase and no manual note"
        rows.append({"id": cid, "title": r.title, "duplicateComments": int(r.duplicateComments),
                     "subtype": r.subtype, "stance": st, "method": how, "evidence": why})
    out = pd.DataFrame(rows)
    appx = pd.read_csv(APPX_A).rename(columns={"sig": "signatures_epa", "commenter": "title_epa"})
    # EPA-listed campaigns absent from the API listing (e.g. 0145) get a row so their weight is visible
    for _, a in appx[~appx.id.isin(out.id)].iterrows():
        st = "UNCLEAR" if a.id in UNCLEAR else "PENDING"
        out.loc[len(out)] = {"id": a.id, "title": a.title_epa, "duplicateComments": 0, "subtype": "Mass Mail Campaign",
                             "stance": st, "method": "RTC Appendix A only", "evidence": UNCLEAR.get(a.id, "not yet fetched")}
    out = out.merge(appx[["id", "signatures_epa"]], on="id", how="left")
    out["in_rtc_appendix_a"] = out.signatures_epa.notna()
    out["weight"] = out.signatures_epa.fillna(out.duplicateComments).astype(int)
    out["weight_source"] = out.in_rtc_appendix_a.map({True: "RTC Appendix A signatures", False: "regulations.gov duplicateComments"})
    out.to_csv(OUT_CSV, index=False)

    live = out[out.stance != "EXCLUDE"]
    g = live.groupby("stance").agg(campaigns=("id", "size"), comments=("weight", "sum"),
                                   comments_api_dup=("duplicateComments", "sum"))
    g["campaign_share"] = g.campaigns / g.campaigns.sum()
    g["comment_share"] = g.comments / g.comments.sum()
    classified = g.loc[[s for s in ["OPPOSE", "SUPPORT"] if s in g.index]]
    md = [f"# Mass-comment-campaign stance tally — docket {DOCKET_ID}",
          "",
          f"Fetched from regulations.gov v4 API {df.fetched_at.iloc[0]}; {len(df)} entries titled "
          f"'Mass Comment Campaign' (EPA's preamble: {EPA_CAMPAIGNS} campaigns, ~{EPA_TOTAL_COMMENTS:,} "
          f"comments, >{EPA_UNIQUE_ENTITIES:,} unique entities; RTC: ~{EPA_CAMPAIGN_COMMENTS:,} in campaigns). "
          f"Weight = RTC Appendix A signature count where listed (166 + 1 unposted), else API `duplicateComments` "
          f"(the 17 late mailers). `comments_api_dup` shows the API-only weighting for comparison.",
          "", g.to_markdown(floatfmt=".3f"), "",
          f"**Among campaigns with a determinable stance** (OPPOSE + SUPPORT): "
          f"{classified.campaigns.sum()} campaigns / {classified.comments.sum():,} comments; "
          f"OPPOSE = {classified.loc['OPPOSE','campaigns']/classified.campaigns.sum():.1%} of campaigns, "
          f"{classified.loc['OPPOSE','comments']/classified.comments.sum():.1%} of comment volume.",
          "",
          f"Campaign-layer coverage of EPA's headline count: {live.weight.sum():,} / "
          f"{EPA_TOTAL_COMMENTS:,} = {live.weight.sum()/EPA_TOTAL_COMMENTS:.1%} "
          f"(EPA's own campaign figure: ~{EPA_CAMPAIGN_COMMENTS:,}); "
          f"implied non-campaign residual ≈ {EPA_TOTAL_COMMENTS - live.weight.sum():,} "
          f"(EPA's own unique-entity figure is >{EPA_UNIQUE_ENTITIES:,}).",
          "", "## SUPPORT campaigns", "",
          out[out.stance == "SUPPORT"][["id", "weight", "weight_source", "evidence"]].to_markdown(index=False),
          "", "## UNCLEAR / EXCLUDED", "",
          out[out.stance.isin(["UNCLEAR", "EXCLUDE", "NEEDS_REVIEW", "PENDING"])][["id", "weight", "stance", "evidence"]].to_markdown(index=False),
          "", "## Caveats (read before quoting)", "",
          "1. **Scope = campaign layer only.** The ~31,000 non-campaign entries ('unique entities') are classified separately "
          "(`classify_unique.py`, pending). With Appendix-A weights the campaign layer + EPA's unique-entity count closes the "
          f"572,000 headline: {live.weight.sum():,} + ~31,000 ≈ 572,000. Bounding case: if every unique-entity comment were "
          f"pro-repeal, opposition would still be {live.loc[live.stance=='OPPOSE','weight'].sum():,} / {EPA_TOTAL_COMMENTS:,} = "
          f"{live.loc[live.stance=='OPPOSE','weight'].sum()/EPA_TOTAL_COMMENTS:.0%} of all comments.",
          "2. **Weights.** regulations.gov's `duplicateComments` is 1 for the whole 2626-2679 block, which RTC Appendix A shows "
          "are campaigns of 10 to 22,263 signatures (2629 = 22,263; 2648 = 11,348; 2635 = 10,541). The API-only weighting "
          "(`comments_api_dup`) therefore undercounts by ~58k, almost all on the OPPOSE side; the share moves 98.9% -> 98.5%. "
          "Appendix A is used wherever it lists the campaign.",
          "3. **SUPPORT side, by weight:** 2689 'Fake Green Mandates' 2,976 + its re-posts 1508 (1,546, late) and 30782 (404, late); "
          "2653 'support President Trump's efforts' 1,997; 2671 658; 2706 (Arizona) 185; 2656 81; 2657 58; 2658 16. "
          "Nine campaigns, ~7,900 signatures, of which three are one text.",
          "4. **Likely double posts on the oppose side:** Green America (0782 / 1438, 9,465 each) and AGU (1442 / 1504, 919 each) — "
          "both pairs appear in Appendix A with both counts, so EPA's 534k carries the same duplication. Dropping them moves "
          "OPPOSE by ~10k; share unchanged to one decimal.",
          "5. **Classification method:** representative text (inline comment + first 2,500 chars of each attachment) read by hand; "
          "every OPPOSE row must also carry either a regex-detectable opposition phrase or a manual note quoting the decisive "
          "sentence (`evidence` column). Two scanned petitions (NPCA, Town of Concord) were read from page images. "
          "0145 (ALA, 228) is in Appendix A but not posted on regulations.gov, so it is UNCLEAR by construction.",
          "6. **Comparison point:** EPA's 2009 Endangerment Finding preamble reported ~370,000 mass-mail comments, 'about "
          "two-thirds' supportive of the Finding (74 FR 66496, Dec 15 2009). The 2026 rescission RTC (doc 31089, p.1 and "
          "Appendix A) reports counts per campaign but no direction: 'Some mass mail campaigns opposed ... other mass comment "
          "campaign commenters urged the EPA to finalize'.",
          "", "Provenance: `fetch_campaigns.py` (regulations.gov v4 API, fetched 2026-09-15) + `rtc/rtc_appendixA_campaigns.csv` "
          "(parsed from RTC doc EPA-HQ-OAR-2025-0194-31089, Appendix A) -> `classify_campaigns.py`. Raw per-entry JSON in "
          "`cache/`; attachments (gitignored) in `attachments/`.",
          ]
    OUT_MD.write_text("\n".join(md))
    print("\n".join(md[:12]))
    print("NEEDS_REVIEW:", (out.stance == "NEEDS_REVIEW").sum())

if __name__ == "__main__":
    main()
