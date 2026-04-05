# LaTeX Accessibility Support — Design Spec

**Date:** 2026-04-04
**Status:** Approved
**Scope:** Add `.tex` / `.zip` LaTeX document support to the a11y remediation pipeline

## Problem

University STEM courses produce LaTeX documents (homework, lecture notes, exams, problem sets) containing mathematical equations, diagrams, and structured content. These are inaccessible to blind students because:

- Compiled PDFs from LaTeX have no structure tags, no alt text, no MathML
- Traditional accessibility tools don't understand math — they produce useless descriptions like "mathematical expression"
- LaTeX has no native alt text mechanism for equations or figures

The DOJ Title II ADA deadline (April 24, 2026) requires all digital course materials to meet WCAG 2.1 AA.

## Solution

Accept LaTeX project uploads (`.tex` or `.zip`), convert to accessible HTML with interactive MathML equations and downloadable PDF, using LaTeXML for parsing and our AI pipeline for accessibility enhancement.

## User Stories

**Blind student:** "I need to read this week's problem set with my screen reader and understand every equation."
- Gets interactive HTML with navigable MathML (arrow key exploration via MathJax + SRE)
- Every equation has a full natural language description as fallback

**Professor (distributor):** "I need to post something accessible on Canvas by Monday."
- Uploads .tex/.zip, gets back a .zip with accessible HTML + PDF
- Clear report shows what was done and what needs review

**Professor (author):** "I want to keep editing my LaTeX and have it stay accessible."
- Deferred to Phase 2 (remediated .tex source with LaTeX3 tagging)

## Architecture

### Upload and Extraction

1. User uploads `.tex` (single file) or `.zip` (project folder)
2. If `.zip`: extract to temp directory, find main `.tex` by locating the file containing `\documentclass`. If multiple match, prefer the one in the root directory.
3. Collect all image files (`.png`, `.jpg`, `.pdf`, `.eps`, `.svg`) from the project for alt text generation
4. If single `.tex`: use as-is; referenced files (images, .bib) that are missing get flagged in the report

**Zip security:** Max 100MB extracted size, max 500 files, reject paths containing `..`, reject symlinks, extract to isolated temp directory. Use Python `zipfile` module with validation before extraction.

**File size:** Consistent with existing 20MB upload limit. Zips may be larger due to images — raise to 50MB for .zip uploads.

### LaTeXML Conversion

Two-step process via subprocess:

```bash
# Step 1: LaTeX → XML
latexml main.tex --destination=output.xml --path=project_dir

# Step 2: XML → HTML with MathML
latexmlpost output.xml --destination=output.html --format=html5 --pmml
```

Note: `latexml` produces XML, `latexmlpost` converts to HTML. The `--pmml` flag requests Presentation MathML. The `--path` flag sets the TeX input search path for `\input{}`/`\include{}` resolution. Image paths from `\includegraphics` are resolved relative to the source directory.

**LaTeXML produces semantic HTML with `ltx_` CSS classes:**
- Headings: `<section class="ltx_subsection">` with `<h2 class="ltx_title ltx_title_subsection">`
- Paragraphs: `<p class="ltx_p">`
- Inline math: `<math class="ltx_Math" alttext="y(t)" display="inline">` — full MathML inside, original LaTeX in `alttext`
- Block equations: wrapped in `<table class="ltx_equation ltx_eqn_table">` (not `<div>` — LaTeXML uses tables for equation layout)
- Equation numbers: `<span class="ltx_tag ltx_tag_equation">(1)</span>`
- Figures: `<figure>` with `<figcaption class="ltx_caption">`
- Tables: `<table class="ltx_tabular">` with `<thead>`/`<th>` for headers
- Errors/undefined macros: `<span class="ltx_ERROR undefined">`
- Theorem environments: `<div class="ltx_theorem">` with correct numbering

**Conversion quality assessment:** After conversion, count `<span class="ltx_ERROR undefined">` elements and `class="ltx_math_unparsed"` math elements. Include counts in the report. High error counts trigger a warning: "This document uses LaTeX features that couldn't be fully converted."

**Fallback:** If LaTeXML fails (non-zero exit, empty output, timeout at 120s), fall back to Pandoc (`pandoc -f latex -t html5 --mathml`). Pandoc produces different HTML structure (clean semantic HTML without `ltx_` classes), so the parser needs a detection mode. Flag in the report that conversion was degraded and math accuracy may be reduced.

**stderr capture:** LaTeXML writes warnings and errors to stderr. Capture and parse these for the report — missing packages, undefined macros, unparsed math.

### Parsing into DocumentModel

`latex_parser.py` uses BeautifulSoup (already a project dependency via other tools) to parse the LaTeXML HTML into our existing `DocumentModel`:

**Mapping from LaTeXML HTML to DocumentModel:**

| LaTeXML HTML | DocumentModel | Notes |
|-------------|---------------|-------|
| `<h1-h6 class="ltx_title">` | ParagraphInfo with heading_level | |
| `<p class="ltx_p">` | ParagraphInfo | May contain inline `<math>` |
| `<math class="ltx_Math" display="inline">` | MathInfo (inline) | Stored as math_id on containing ParagraphInfo |
| `<table class="ltx_equation">` containing `<math display="block">` | MathInfo (block) | ContentType.MATH in content_order |
| `<math class="ltx_math_unparsed">` | MathInfo with flag | LaTeXML couldn't parse; use alttext LaTeX as fallback |
| `<img src="...">` | ImageInfo | Load image_data from project dir if available |
| `<figure>` with `<figcaption>` | ImageInfo with caption | |
| `<table class="ltx_tabular">` | TableInfo | Distinguish from equation tables by class |
| `<span class="ltx_ERROR">` | Logged as warning | Count for quality assessment |
| `<div class="ltx_theorem">` | ParagraphInfo with style_name="Theorem" | Preserve theorem labels |

**Inline math in paragraphs:** When a paragraph contains inline math (`let <math>x</math> be real`), the ParagraphInfo.text gets the LaTeX source substituted: "let x be real". The MathInfo is created separately and referenced via `math_ids`. For simple inline math, the text representation is sufficient for screen readers. Complex inline math still gets a MathInfo with description.

**Content ordering:** DOM order of the HTML = content_order. Walk the DOM tree top-to-bottom, emit ContentOrderItem for each paragraph, math block, table, or image encountered.

### MathInfo Model

```python
class MathInfo(BaseModel, frozen=True):
    id: str                      # math_0, math_1, ...
    latex_source: str            # from alttext attribute on <math>
    mathml: str                  # full MathML markup from LaTeXML
    display: str = "block"       # "block" or "inline"
    description: str = ""        # full natural language reading (AI-generated)
    confidence: float = 1.0      # AI confidence in the description
    unparsed: bool = False       # True if LaTeXML couldn't parse (ltx_math_unparsed)
```

**DocumentModel extensions:**
- `math: list[MathInfo]` field on DocumentModel
- `math_ids: list[str]` field on ParagraphInfo (for inline math references)
- `ContentType.MATH` variant for block equations in content_order
- `DocumentStats` updated with `math_count`, `math_missing_description`

Note: `page_number` omitted from MathInfo — LaTeX documents don't have page numbers until compiled.

### Math Description Generation

Not all math needs an AI-generated description. Classification by complexity:

**Trivial (no API call):** Single symbol, variable, number, Greek letter, simple subscript/superscript. LaTeX source ≤ 10 chars with no `\frac`, `\int`, `\sum`, `\begin`, `\sqrt`. Description = direct text rendering.
- `$x$` → "x"
- `$\alpha$` → "alpha"
- `$x_i$` → "x sub i"
- `$n!$` → "n factorial"

**Moderate (template-based, no API call):** Short expressions ≤ 50 LaTeX chars with no nested structures. Use Speech Rule Engine or deterministic template to generate reading.
- `$x^2 + y^2$` → "x squared plus y squared"
- `$f(x) = 0$` → "f of x equals zero"

**Complex (Claude API call with course context):** Multi-line equations, nested fractions, integrals, summations, matrices, anything with `\begin{align}` or `\begin{equation}`. Send to Claude with surrounding paragraph context and course context.
- `\int_0^\infty f(t)e^{-st}dt` → "the Laplace transform integral: the integral from 0 to infinity of f of t times e to the negative s t, d t"

**Batching:** Group complex equations and send to Claude in batches (5-10 per API call) with document context, rather than one call per equation.

### AI Enhancement Pipeline

The existing 4-phase pipeline runs on the DocumentModel:

**Phase 1 — Comprehension (Gemini):** Analyze document type (homework, lecture notes, exam), identify the role of each element, understand course context. For math: understand what each equation represents in context.

**Phase 2 — Strategy (Claude):** Generate remediation actions:
- `add_math_description` for each complex MathInfo — generate natural language description
- `add_alt_text` for each ImageInfo missing alt text — use Gemini vision on image files from the zip
- `fix_heading_hierarchy` if headings are wrong
- `set_metadata` for document title and language
- Flag TikZ diagrams that LaTeXML couldn't render for human review
- Flag `unparsed` math elements for human review

**Phase 3 — Execution:** Apply fixes to the DocumentModel:
- Set `description` on MathInfo objects
- Set `alt_text` on ImageInfo objects
- Fix heading levels
- Set metadata

**Phase 4 — Review (Claude):** Evaluate from screen reader perspective:
- Are math descriptions mathematically accurate and complete?
- Do descriptions make sense in context (not just symbol-by-symbol reading)?
- Are images adequately described?
- Is reading order logical?

### Output Package

User receives a downloadable `.zip` containing three files:

#### `accessible.html` — LMS-safe, no JavaScript required

Equations rendered as **inline SVG** via `ziamath` (pure Python MathML → SVG). Each SVG has `role="img"` and `aria-label` set to the full `description`. No JavaScript dependency — works when uploaded to Canvas, Blackboard, or any LMS that strips `<script>` tags.

```html
<div class="math-block" role="math">
  <svg aria-label="the Laplace transform: the integral from 0 to infinity...">
    <!-- ziamath SVG rendering -->
  </svg>
</div>
```

Also includes: alt text on all images, proper heading hierarchy, `<html lang>`, `<title>`, responsive CSS.

#### `accessible_interactive.html` — Best screen reader experience

Same content but with **MathML preserved** and **MathJax 3 + SRE loaded from CDN**. Provides interactive equation navigation (arrow keys to explore sub-expressions). For direct download/local use only — not for LMS upload.

```html
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
```

`<noscript>` fallback shows the LaTeX source.

#### `accessible.pdf` — Downloadable PDF/UA-1

Generated via WeasyPrint from the SVG version of the HTML (since WeasyPrint doesn't render MathML). Equations appear as SVG images with `description` as alt text on `<Formula>` structure tags. Proper heading tags, image alt text. Validated with veraPDF where possible.

#### `report.html` — Human-readable remediation report

Three sections:
1. **What we did:** summary of fixes (X equations described, Y images given alt text, Z heading fixes)
2. **What needs your attention:** flagged items with reasons (low confidence descriptions, missing images, unconverted TikZ, unparsed math)
3. **How to use the outputs:** which file to give students, which to upload to Canvas

Per-equation detail: rendered equation (SVG) + LaTeX source + our description + confidence level, so professor can verify accuracy.

Per-image detail: image + our alt text, for review.

Conversion quality: LaTeXML error/warning count, list of unsupported packages, undefined macros.

### Report Redesign (All Document Types)

The improved report format benefits all document types, not just LaTeX. The current compliance report is a technical checklist. The new report is a human-readable summary with three sections:

1. **What we did** — plain language summary of changes
2. **What needs attention** — actionable items for the professor
3. **Your outputs** — which file to use for what

This is a separate workstream that can be built independently and applied to DOCX/PDF/PPTX outputs as well. For LaTeX v1, the report is built as part of the LaTeX pipeline. Extending to other formats is a follow-up.

## File Changes

### New files
| File | Purpose |
|------|---------|
| `src/tools/latex_parser.py` | Call LaTeXML, parse HTML into DocumentModel |
| `src/tools/math_descriptions.py` | Classify math complexity, generate descriptions (trivial/template/Claude) |
| `src/tools/math_renderer.py` | MathML → SVG via ziamath for PDF and LMS-safe HTML |
| `src/prompts/math_description.md` | Prompt for equation description generation |
| `src/tools/report_builder.py` | New human-readable report generator |
| `tests/test_latex_parser.py` | Tests for LaTeX parsing |
| `tests/test_math_descriptions.py` | Tests for math description and complexity classification |
| `tests/test_math_renderer.py` | Tests for MathML → SVG rendering |

### Modified files
| File | Change |
|------|--------|
| `src/models/document.py` | Add MathInfo, math_ids on ParagraphInfo, MATH content type, math on DocumentModel |
| `src/agent/orchestrator.py` | Support .tex/.zip, route to latex_parser, zip output |
| `src/tools/html_builder.py` | MathJax script injection, MathInfo rendering (SVG and MathML modes) |
| `src/agent/executor.py` | Handle add_math_description action |
| `src/agent/comprehension.py` | Recognize math elements in comprehension |
| `src/agent/strategy.py` | Generate math description actions |
| `src/web/app.py` | Accept .tex/.zip uploads, zip upload size limit (50MB), return zip output |
| `docs/data_schema.md` | Document MathInfo model |

## Dependencies

| Dependency | Type | Installation | Size |
|-----------|------|-------------|------|
| LaTeXML | System package | `apt install latexml` (Ubuntu) / `brew install latexml` (macOS) | ~11MB |
| Pandoc | System package (fallback) | `apt install pandoc` / `brew install pandoc` | ~30MB |
| ziamath | Python package | `pip install ziamath` | ~1.3MB (pure Python, pulls ziafont + latex2mathml) |
| MathJax 3 | CDN (interactive HTML only) | `cdn.jsdelivr.net/npm/mathjax@3` | No install |
| BeautifulSoup4 | Python package | Already in project or `pip install beautifulsoup4` | |

LaTeXML and Pandoc called via subprocess, same pattern as veraPDF and iText. ziamath is pure Python, no system dependencies, works on ARM.

## Test Documents

Five LaTeX files in `tests/test_docs/`:

| File | Content | Math elements | Tests |
|------|---------|---------------|-------|
| `homework.tex` | Stats problems, proofs, TikZ, algorithms | 96 | Complex math, custom environments, TikZ failure |
| `diffeq_power_series.tex` | Power series, summations | ~80 | Heavy math notation |
| `diffeq_laplace.tex` | Laplace transforms, fractions | 74 | Display math, tables, equation numbering |
| `homework_template.tex` | Theorems, lemmas, proofs, integrals | ~30 | amsthm environments |
| `syllabus.tex` | Tables, sections, lists | 0 | Basic LaTeX, no math |

**Known from spike:** `homework.tex` produces 53 `ltx_ERROR` elements (TikZ + algorithm packages). `diffeq_laplace.tex` has 3 unparsed math elements and 1 undefined macro (TikZ). Both convert successfully with degraded content.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| LaTeXML timeout (>120s) | Fall back to Pandoc. Flag in report. |
| LaTeXML crash (non-zero exit) | Fall back to Pandoc. Flag in report. |
| LaTeXML partial success (warnings, errors) | Use output. Count errors, include in report. |
| Pandoc also fails | Return error: "This LaTeX document couldn't be converted. It may use unsupported packages." |
| Zip bomb / oversized | Reject before extraction: "Upload exceeds size limit." |
| Zip path traversal | Reject: "Invalid zip file." |
| No `\documentclass` in zip | Error: "Couldn't find main LaTeX file in the upload." |
| Missing images | Convert without images. Flag each in report: "Image X referenced but not included." |
| Missing .bib file | Convert without bibliography. Flag: "Bibliography file not included." |

## Scope Boundaries

**In scope (v1):**
- Single .tex file upload
- .zip project folder upload
- LaTeXML conversion to HTML with MathML
- AI-generated equation descriptions (full natural language reading)
- Trivial/moderate math: deterministic descriptions (no API call)
- AI-generated image alt text (for images in the project)
- Two HTML outputs: LMS-safe (SVG) + interactive (MathJax)
- PDF output via WeasyPrint (from SVG HTML)
- Human-readable report with per-equation review
- Pandoc fallback for LaTeXML failures
- Flagging unconverted content (TikZ, missing files, unparsed math) for human review
- Zip security (size limits, path validation)

**Out of scope (Phase 2+):**
- Remediated .tex source output (requires LaTeX3 tagging maturity)
- TikZ diagram rendering (would need a TeX engine)
- Compiling LaTeX (no TeX installation required)
- Custom .cls/.sty file execution (LaTeXML handles common packages; custom ones degrade gracefully)
- PDF/UA-2 with embedded MathML associated files (future iText enhancement)
- Bundled MathJax (CDN for now, offline bundle later if needed)
- Report redesign for DOCX/PDF/PPTX (separate follow-up workstream)

## Cost Estimation

For a typical 10-page homework with ~80 math elements:
- ~60 trivial inline (no API cost)
- ~10 moderate (template-based, no API cost)
- ~10 complex display equations (Claude, batched in 2 calls)
- ~3 images needing alt text (Gemini vision)
- Comprehension (1 Gemini call), Strategy (1 Claude call), Review (1 Claude call)

**Estimated total: ~8 API calls, $0.15-0.30 per document.** Comparable to current DOCX/PDF remediation costs.

## Success Criteria

1. All 5 test documents convert successfully through the pipeline
2. MathML renders correctly in the interactive HTML with MathJax
3. SVG equations render correctly in the LMS-safe HTML
4. Screen reader (VoiceOver) can read equation descriptions in both HTML and PDF
5. Professor can verify equation descriptions in the report
6. Missing images and unconverted content are clearly flagged
7. Processing time under 5 minutes for a typical homework document
8. LaTeXML errors/warnings surfaced in report
9. Pandoc fallback produces usable (if degraded) output
