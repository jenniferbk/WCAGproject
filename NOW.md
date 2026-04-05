# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: LaTeX + OCR fixes shipped, polish and table recognition next
- **Tests**: 891 passing

## What Was Shipped (2026-04-05 session — OCR Quality Fixes)

### OCR Quality Fixes (driven by visual QA findings)
- **Column sorting validation**: detects imbalanced columns (col=2 but no col=1), reassigns mislabeled col=0 regions — fixed Mayer page 9 text loss
- **Table deduplication**: `_deduplicate_ocr_tables()` removes duplicate tables (80% cell overlap threshold) — fixed duplicate Table 2
- **Multi-table rescue**: removed `pages_with_tables` skip so multiple tables on same page can all be rescued — fixed missing Table 3
- **Prompt improvement**: table captions must keep number + title together
- All 3 original high-severity findings RESOLVED
- 3 new findings are Gemini RECITATION refusals (copyright content), not pipeline bugs
- 891 tests passing

## What Was Shipped (2026-04-05 session — Visual Diff QA)

### Visual Diff QA
- Gemini-powered content comparison: original scanned pages vs rendered HTML output
- HTML→PDF (WeasyPrint) → per-page PNG (PyMuPDF) — zero new dependencies
- Batched Gemini calls (4 pages per batch), no 1:1 page alignment assumed
- "Visual Quality Check" section in report with side-by-side thumbnails (base64 embedded)
- High + medium findings surfaced; low logged only
- Findings persisted to `visual_qa_findings.json` for cross-document pattern analysis
- Batch aggregation in `test_batch.py` → `visual_qa_summary.json`
- Mayer test: 6 findings (3 high, 2 medium, 1 low) — real content gaps detected
  - Missing Table 3, missing headings, entire text column dropped from page 9
- Integrated as Phase 3.5 (between execution and review), scanned PDFs only
- 877 tests passing

## What Was Shipped (2026-04-05 session — OCR Table Recognition)

### OCR Table Recognition
- Improved Gemini OCR prompt with table visual indicators and caption guidance
- Post-OCR table rescue pipeline: `_find_table_captions()` → `_collect_table_paragraphs()` → `_rescue_table_from_page()` → `_rescue_missed_tables()`
- Focused table extraction prompt (`src/prompts/table_rescue.md`) for Gemini re-send
- Integrated into all OCR code paths (main batch, single-page retry, garble retry)
- Mayer paper: 1→5 tables extracted, all with proper headers and cell data
- 855 tests passing, ~$0.001 additional cost per rescued table
- Prompt improvement alone was sufficient for Mayer; rescue pipeline is safety net

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
1. **Gemini RECITATION workarounds** — pages 10-11 (references/acknowledgments) refused by Gemini; improve Tesseract fallback quality or use alternate prompt strategies
2. **TikZ AI descriptions** — send TikZ source to Claude for diagram description
3. **Per-equation review in report** — for LaTeX docs, show rendered equation + LaTeX + description for professor verification
4. **LaTeX .tex remediation output** — return fixed .tex source (Phase 2+)

## Key Architecture Notes
- `src/tools/latex_parser.py` (1122 lines) — LaTeXML subprocess + HTML→DocumentModel
- `src/tools/math_renderer.py` — ziamath MathML/LaTeX → SVG
- `src/tools/math_descriptions.py` — classify trivial/complex, trivial descriptions
- `src/tools/report_generator.py` — redesigned with human + technical layers
- MathInfo on DocumentModel, math_ids on ParagraphInfo, ContentType.MATH
- Algorithm: `format_algorithmic_block()` handles algpseudocode → `<pre class="algorithm">`
- Known: algorithm parser incomplete (args/conditions from split error spans)
- LaTeX test docs in `tests/test_docs/*.tex` (5 files)
