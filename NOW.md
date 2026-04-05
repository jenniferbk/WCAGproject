# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: LaTeX support shipped, report redesign + formatting improvements next

## LaTeX Accessibility Support — COMPLETE (this session)
Full .tex/.zip LaTeX document support deployed:

### Pipeline
- LaTeXML converts LaTeX → HTML with MathML (subprocess, 2-step: latexml → latexmlpost)
- BeautifulSoup parses LaTeXML HTML into DocumentModel with MathInfo
- ziamath renders MathML → SVG for PDF and LMS-safe HTML
- Math complexity classifier: trivial (deterministic) vs complex (Claude API)
- Algorithm pseudocode parser: \Function/\If/\State → formatted `<pre>` blocks
- TikZ diagram detection → descriptive placeholders
- ltx_ERROR cleanup strips leaked LaTeX commands
- Zip upload with security (path traversal, size limits)

### New files
- `src/tools/latex_parser.py` (1122 lines) — LaTeXML subprocess + HTML→DocumentModel parser
- `src/tools/math_renderer.py` — MathML/LaTeX → SVG via ziamath
- `src/tools/math_descriptions.py` — classify trivial/complex, trivial descriptions
- `src/prompts/math_description.md` — Claude prompt for equation descriptions
- `tests/test_latex_parser.py` (65 tests), `tests/test_math_descriptions.py` (16 tests), `tests/test_math_renderer.py` (7 tests)
- 5 test documents in `tests/test_docs/` (.tex files)

### Dependencies added
- LaTeXML (system: `apt install latexml` / `brew install latexml`)
- ziamath (Python: pure Python MathML→SVG, ~1.3MB)
- BeautifulSoup4 (Python: HTML parsing)

### Known issues (Batch 2)
- Algorithm pseudocode: args/conditions not fully extracted from split error spans
- Two-column CSS grid layout causes broken rendering for scanned PDFs (Mayer)
- OCR table recognition: Gemini returns table cells as paragraphs for some tables
- Problem ordering in homework.tex: correct (matches source), but looks odd

## OCR Quality Fixes — COMPLETE (this session, earlier)
- Column-aware sorting with full-width fences
- Garbled text detection + 300 DPI retry
- Page header/footer pattern filtering
- Paragraph deduplication (normalized + fuzzy prefix)
- Improved OCR prompt for formatting and anti-duplication
- 747 → 806+ tests

## Up Next (Priority Order)
1. **Report redesign** — combined WCAG compliance + human-readable summary
   - "What we did" / "What needs attention" / "Your outputs" at top
   - WCAG technical details as expandable section at bottom
   - Per-equation review for LaTeX, per-image review for all types
   - Benefits ALL document types, not just LaTeX
2. **Page-section layout (Approach B)** — wrap content by page with CSS column-count for scanned PDFs
3. **Better OCR table recognition** — strengthen prompt, add post-processing detection
4. **Visual diff QA** (Phase 2) — AI compares original page image vs rendered HTML to detect gaps
5. **TikZ AI descriptions** — send TikZ source to Claude for diagram description (Phase 2)
6. **LaTeX .tex remediation output** — return fixed .tex source (Phase 2, needs LaTeX3 maturity)

## Test Counts
- 806+ tests all passing (up from 704 at start of session)
- ~95 new tests added this session
