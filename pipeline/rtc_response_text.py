#!/usr/bin/env python
"""The one definition of 'EPA's own words' in an RTC section, shared by every response-only re-judgement.

A section labelled "EPA Summary of Comments" / "EPA Response" yields its response blocks. A section with
NO summary block is entirely EPA speaking (the boilerplate out-of-scope dismissals in 3.1, 3.2, 3.3 and
the parent/heading sections), so its whole text is a response.

⚠ The bug this file exists to prevent (2026-09-29): the re-judgement scripts passed `sec["responses"]`
directly as the matching-section text, which is EMPTY for that second class — so 65 re-judged rows were
scored with no section text at all, including every §3.1 argument, whose dismissal then had to be found
by BM25 against the whole RTC and ranked ~53rd. The corpus builder already had the fallback; the
matching-section line did not. Both now call this.
"""


def response_blocks(section):
    """the blocks of a section that are EPA speaking in its own voice; [] if there are none"""
    if not section:
        return []
    if section["responses"]:
        return section["responses"]
    return [section["text"]] if not section["summaries"] else []


def response_text(section, cap=8000):
    return " ".join(response_blocks(section))[:cap]
