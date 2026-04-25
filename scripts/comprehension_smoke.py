"""Smoke-test the new comprehension fields on a single PDF.

Verifies after the 2026-04-25 prompt extension:
- suggested_language is BCP-47 ("en" not "English")
- element_purposes with suggested_action="convert_to_heading" carry heading_level (1, 2, or 3)
- link_text_proposals is populated for raw-URL links (and empty for self-descriptive links)

Run: python3 scripts/comprehension_smoke.py [path-to-pdf]
Default: testdocs/EMAT 8030 syllabus spring 2026.pdf (no images, fast, has both links and headings)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.comprehension import comprehend
from src.tools.pdf_parser import parse_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DEFAULT_PDF = Path(__file__).parent.parent / "testdocs" / "EMAT 8030 syllabus spring 2026.pdf"


def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    print(f"Comprehending: {pdf_path.name}")

    parse = parse_pdf(str(pdf_path))
    if not parse.success or parse.document is None:
        print(f"PARSE FAILED: {parse.error}")
        sys.exit(1)
    doc = parse.document
    print(f"Parsed: {len(doc.paragraphs)} paragraphs, {len(doc.images)} images, {len(doc.tables)} tables, {len(doc.links)} links")

    comprehension = comprehend(doc, course_name="EMAT 8030", department="Mathematics Education")

    print("\n=== RESULTS ===")
    print(f"document_type: {comprehension.document_type.value}")
    print(f"suggested_title: {comprehension.suggested_title!r}")
    print(f"suggested_language: {comprehension.suggested_language!r}  (expect BCP-47 e.g. 'en')")
    print(f"image_descriptions: {len(comprehension.image_descriptions)}")
    print(f"element_purposes: {len(comprehension.element_purposes)}")
    print(f"link_text_proposals: {len(comprehension.link_text_proposals)}")

    headings = [ep for ep in comprehension.element_purposes if ep.suggested_action == "convert_to_heading"]
    print(f"\n=== HEADING DETECTION ({len(headings)} flagged) ===")
    levels = {1: 0, 2: 0, 3: 0, None: 0}
    for ep in headings:
        levels[ep.heading_level] = levels.get(ep.heading_level, 0) + 1
    print(f"By level: H1={levels[1]} H2={levels[2]} H3={levels[3]} unset={levels[None]}")
    for ep in headings[:6]:
        print(f"  {ep.element_id}  H{ep.heading_level}  conf={ep.confidence:.2f}  '{ep.purpose[:60]}'")
    if len(headings) > 6:
        print(f"  ... and {len(headings) - 6} more")

    print(f"\n=== LINK PROPOSALS ({len(comprehension.link_text_proposals)} of {len(doc.links)} links) ===")
    for link_id, proposed in list(comprehension.link_text_proposals.items())[:8]:
        link = next((l for l in doc.links if l.id == link_id), None)
        if link:
            url_short = link.url[:60] + ("..." if len(link.url) > 60 else "")
            print(f"  {link_id}  url={url_short}")
            print(f"           orig text: {link.text!r}")
            print(f"           proposed:  {proposed!r}")

    # Pass/fail signals
    print("\n=== SMOKE CHECKS ===")
    lang_ok = comprehension.suggested_language and len(comprehension.suggested_language) <= 6
    print(f"  language is short BCP-47-ish: {'OK' if lang_ok else 'FAIL'} ({comprehension.suggested_language!r})")
    headings_have_levels = sum(1 for ep in headings if ep.heading_level in (1, 2, 3))
    print(f"  headings carry valid level:   {headings_have_levels}/{len(headings)}")
    raw_url_links = [l for l in doc.links if l.text.startswith(("http://", "https://"))]
    proposals_for_raw = sum(1 for l in raw_url_links if l.id in comprehension.link_text_proposals)
    print(f"  raw-URL links proposed:       {proposals_for_raw}/{len(raw_url_links)}")


if __name__ == "__main__":
    main()
