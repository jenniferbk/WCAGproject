# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: LaTeX + OCR fixes shipped, polish and table recognition next
- **Tests**: 835+ passing

## What Was Shipped (2026-04-04/05 session)

### OCR Quality Fixes
- Column-aware sorting with full-width fences (`_sort_regions_by_column`)
- Garbled text detection + 300 DPI retry (`_is_garbled_text`)
- Page header/footer pattern filtering (`_is_leaked_header_footer`)
- Paragraph deduplication with normalized + fuzzy prefix matching
- Improved OCR prompt (formatting, anti-duplication, landscape pages)

### LaTeX Accessibility Support
- LaTeXML conversion (LaTeX → HTML with MathML, subprocess 2-step)
- MathML → SVG rendering via ziamath (pure Python, verified 169/169)
- MathInfo model, math complexity classifier (trivial/complex)
- Algorithm pseudocode parser (`\Function`/`\If`/`\State` → formatted blocks)
- TikZ diagram detection → descriptive placeholders
- ltx_ERROR cleanup (strips leaked LaTeX commands)
- Zip upload with security validation
- .tex/.zip accepted in web app and orchestrator

### Report Redesign
- Human-readable summary at top: "What We Did" / "What Needs Attention" / "Your Output Files"
- Plain language grouped by impact (navigation, images, text, math)
- WCAG technical details in collapsible `<details>` section at bottom
- No more element IDs or WCAG codes in the faculty-facing summary

### Layout
- Dropped two-column CSS (unreliable with OCR data, single-column is better)
- Page sections kept for semantic grouping

## Up Next (Priority Order)
1. **OCR table recognition** — Gemini returns table cells as paragraphs for some tables (Mayer TABLE 2/3/4). Improve OCR prompt + post-processing detection.
2. **Visual diff QA** — AI compares original page image vs rendered HTML to detect gaps. Low cost (~$0.03/doc), adds ~30s. Surfaces issues in report.
3. **TikZ AI descriptions** — send TikZ source to Claude for diagram description
4. **Per-equation review in report** — for LaTeX docs, show rendered equation + LaTeX + description for professor verification
5. **LaTeX .tex remediation output** — return fixed .tex source (Phase 2+)

## Key Architecture Notes
- `src/tools/latex_parser.py` (1122 lines) — LaTeXML subprocess + HTML→DocumentModel
- `src/tools/math_renderer.py` — ziamath MathML/LaTeX → SVG
- `src/tools/math_descriptions.py` — classify trivial/complex, trivial descriptions
- `src/tools/report_generator.py` — redesigned with human + technical layers
- MathInfo on DocumentModel, math_ids on ParagraphInfo, ContentType.MATH
- Algorithm: `format_algorithmic_block()` handles algpseudocode → `<pre class="algorithm">`
- Known: algorithm parser incomplete (args/conditions from split error spans)
- LaTeX test docs in `tests/test_docs/*.tex` (5 files)
