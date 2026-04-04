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

### LaTeXML Conversion

Call LaTeXML via subprocess:

```bash
latexmlc main.tex \
  --destination=output.html \
  --format=html5 \
  --mathml \
  --path=project_dir \
  --timeout=120
```

LaTeXML produces semantic HTML with:
- Heading hierarchy from `\section`/`\subsection`/etc.
- MathML for all equations (with original LaTeX in `<annotation encoding="application/x-tex">`)
- `<figure>` + `<figcaption>` for figure environments
- `<img src="...">` for `\includegraphics` (no alt text — our job)
- `<table>` structure from tabular environments
- Bibliography from `.bib` files (if present)
- Theorem/definition/proof environments as labeled divs with correct numbering

**Fallback:** If LaTeXML fails (non-zero exit, empty output, timeout), fall back to Pandoc (`pandoc -f latex -t html5 --mathml`) for a degraded-but-usable conversion. Flag in the report that conversion was degraded.

### Parsing into DocumentModel

`latex_parser.py` parses the LaTeXML HTML output into our existing `DocumentModel`:

- HTML headings → ParagraphInfo with heading_level
- HTML paragraphs → ParagraphInfo
- MathML blocks → MathInfo (new model) + ContentOrderItem with MATH type
- Inline math → stored as math_ids on the containing ParagraphInfo
- `<img>` tags → ImageInfo (image_data loaded from project directory if available)
- `<table>` → TableInfo
- `<figure>` → ImageInfo with caption text

**New data model — MathInfo:**

```python
class MathInfo(BaseModel, frozen=True):
    id: str                      # math_0, math_1, ...
    latex_source: str            # original LaTeX from <annotation>
    mathml: str                  # MathML markup from LaTeXML
    display: str = "block"       # "block" (display math) or "inline"
    description: str = ""        # full natural language reading (AI-generated)
    confidence: float = 1.0      # AI confidence in the description
    page_number: int | None = None
```

**DocumentModel extensions:**
- `math: list[MathInfo]` field on DocumentModel
- `math_ids: list[str]` field on ParagraphInfo (for inline math references)
- `ContentType.MATH` variant for block equations in content_order
- `DocumentStats` updated with `math_count`, `math_missing_description`

### AI Enhancement Pipeline

The existing 4-phase pipeline runs on the DocumentModel:

**Phase 1 — Comprehension (Gemini):** Analyze document type (homework, lecture notes, exam), identify the role of each element, understand course context. For math: understand what each equation represents in context ("this is the definition of conditional probability" not just "a fraction").

**Phase 2 — Strategy (Claude):** Generate remediation actions:
- `add_math_description` for each MathInfo — generate natural language description
- `add_alt_text` for each ImageInfo missing alt text — use Gemini vision on image files from the zip
- `fix_heading_hierarchy` if headings are wrong
- `set_metadata` for document title and language
- Flag TikZ diagrams that LaTeXML couldn't render for human review
- Flag complex equations where description confidence is low

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

User receives a downloadable `.zip` containing:

**`accessible.html`** — Interactive accessible HTML:
- MathJax 3 loaded from CDN with Speech Rule Engine
- MathML equations with `aria-label` set to the full `description`
- Visually-hidden `<span class="sr-only">` with description near each equation
- Alt text on all images
- Proper heading hierarchy, `<html lang>`, `<title>`
- `<noscript>` fallback showing LaTeX source for equations (if MathJax CDN unavailable)
- Responsive CSS

**`accessible.pdf`** — Downloadable PDF/UA-1:
- Generated via WeasyPrint from the HTML
- `<Formula>` structure tags with `description` as alt text
- Proper heading tags, image alt text
- Validated with veraPDF where possible

**`report.html`** — Human-readable remediation report:
- **What we did:** summary of fixes (X equations described, Y images given alt text, Z heading fixes)
- **What needs your attention:** flagged items with reasons (low confidence descriptions, missing images, unconverted TikZ)
- **How to use the outputs:** which file to give students, which to upload to Canvas
- **Per-equation detail:** rendered equation (MathJax) + LaTeX source + our description + confidence level, so professor can verify accuracy
- **Per-image detail:** image + our alt text, for review

### Report Redesign (All Document Types)

The improved report format benefits all document types, not just LaTeX. The current compliance report is a technical checklist. The new report is a human-readable summary with three sections:

1. **What we did** — plain language summary of changes
2. **What needs attention** — actionable items for the professor
3. **Your outputs** — which file to use for what

This is a separate workstream that can be built independently and applied to DOCX/PDF/PPTX outputs as well.

## File Changes

### New files
| File | Purpose |
|------|---------|
| `src/tools/latex_parser.py` | Call LaTeXML, parse HTML into DocumentModel |
| (add to `src/models/document.py`) | MathInfo model alongside ParagraphInfo, ImageInfo, etc. |
| `src/tools/math_descriptions.py` | Claude generates natural language descriptions for equations |
| `src/prompts/math_description.md` | Prompt for equation description generation |
| `src/tools/report_builder.py` | New human-readable report generator |
| `tests/test_latex_parser.py` | Tests for LaTeX parsing |
| `tests/test_math_descriptions.py` | Tests for math description generation |

### Modified files
| File | Change |
|------|--------|
| `src/models/document.py` | Add MathInfo, math_ids on ParagraphInfo, MATH content type |
| `src/agent/orchestrator.py` | Support .tex/.zip, route to latex_parser |
| `src/tools/html_builder.py` | MathJax script injection, MathInfo rendering |
| `src/agent/executor.py` | Handle add_math_description action |
| `src/agent/comprehension.py` | Recognize math elements |
| `src/agent/strategy.py` | Generate math description actions |
| `src/web/app.py` | Accept .tex/.zip uploads, return zip output |
| `docs/data_schema.md` | Document MathInfo model |

## Dependencies

| Dependency | Type | Installation |
|-----------|------|-------------|
| LaTeXML | System package | `apt install latexml` (Ubuntu) / `brew install latexml` (macOS) |
| Pandoc | System package (fallback) | `apt install pandoc` / `brew install pandoc` |
| MathJax 3 | CDN | `cdn.jsdelivr.net/npm/mathjax@3` (no install, loaded in HTML) |

No new Python packages required. LaTeXML and Pandoc called via subprocess, same pattern as veraPDF and iText.

## Test Documents

Five LaTeX files in `tests/test_docs/`:

| File | Content | Tests |
|------|---------|-------|
| `homework.tex` | Stats problems, proofs, TikZ, algorithms | Complex math, custom environments |
| `diffeq_power_series.tex` | Power series, summations | Heavy math notation |
| `diffeq_laplace.tex` | Laplace transforms, fractions | Display math, tables |
| `homework_template.tex` | Theorems, lemmas, proofs, integrals | amsthm environments |
| `syllabus.tex` | Tables, sections, lists | Basic LaTeX, no math |

## Scope Boundaries

**In scope (v1):**
- Single .tex file upload
- .zip project folder upload
- LaTeXML conversion to HTML with MathML
- AI-generated equation descriptions (full natural language reading)
- AI-generated image alt text (for images in the project)
- Interactive HTML output with MathJax + SRE
- PDF output via WeasyPrint
- Human-readable report
- Pandoc fallback for LaTeXML failures
- Flagging unconverted content (TikZ, missing files) for human review

**Out of scope (Phase 2+):**
- Remediated .tex source output (requires LaTeX3 tagging maturity)
- TikZ diagram rendering (would need a TeX engine)
- Compiling LaTeX (no TeX installation required)
- Custom .cls/.sty file execution (LaTeXML handles common packages; custom ones degrade gracefully)
- PDF/UA-2 with embedded MathML associated files (future iText enhancement)
- Bundled MathJax (CDN for now, offline bundle later if needed)

## Success Criteria

1. All 5 test documents convert successfully through the pipeline
2. MathML renders correctly in the output HTML with MathJax
3. Screen reader (VoiceOver) can read equation descriptions in both HTML and PDF
4. Professor can verify equation descriptions in the report
5. Missing images and unconverted content are clearly flagged
6. Processing time under 5 minutes for a typical homework document
