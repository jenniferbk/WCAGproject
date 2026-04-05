# Visual Diff QA Design

**Date:** 2026-04-05
**Problem:** No automated way to detect content gaps between original scanned PDF pages and the remediated HTML output. Missing tables, dropped images, truncated text, and garbled equations go undetected until manual review.
**Approach:** After execution (Phase 3.5), render original PDF pages and companion HTML (via WeasyPrint→PDF→PNG) to images, send batches to Gemini for content comparison, surface findings with side-by-side thumbnails in a dedicated report section.

## Architecture

```
Phase 3: Execution → companion HTML + remediated PDF
  ↓
Phase 3.5: Visual Diff QA (NEW) — scanned PDFs only
  ├─ Render all original scanned pages → PNGs (200 DPI) via PyMuPDF
  ├─ Render companion HTML → temp PDF via WeasyPrint → per-page PNGs via PyMuPDF
  ├─ Batch into Gemini calls (all originals + all rendered pages, split across 2-3 calls):
  │   "What educational content in the originals is missing from the rendered version?"
  │   Gemini handles page alignment itself — no 1:1 page mapping assumed
  ├─ Collect structured findings (high + medium surfaced; low logged only)
  ├─ Save page PNGs for report thumbnail embedding
  ├─ Save findings to visual_qa_findings.json
  └─ Pass findings to report generator
  ↓
Phase 4: Review (Claude)
```

## Scope

- **Scanned PDFs only** — native PDFs are remediated in-place (iText tagging) with low content loss risk; DOCX/PPTX/LaTeX also out of scope
- **Detection + reporting only** — auto-fix loop is future work
- **Content gaps only** — ignores expected visual differences (layout, fonts, colors, spacing)
- **High + medium severity** surfaced in report — low findings logged but not shown (reduces false positives from minor layout differences)

## Visual QA Module

File: `src/tools/visual_qa.py`

### `render_original_pages(pdf_path, page_numbers) -> dict[int, bytes]`

Renders specified pages from the original PDF to PNG at 200 DPI using PyMuPDF. Returns `{page_number: png_bytes}`.

### `render_html_to_page_pngs(html_path) -> list[bytes]`

Two-step rendering using only existing dependencies:
1. Render companion HTML → temporary PDF via WeasyPrint (`HTML(filename=html_path).write_pdf(tmp_path)`)
2. Open temp PDF with PyMuPDF → render each page to PNG at 200 DPI

Returns list of PNG bytes, one per rendered page. Page count will differ from original (a 10-page scanned PDF may render to 7 or 15 pages depending on content density). This is expected — Gemini handles alignment.

### `compare_pages(original_pngs, rendered_pngs, client, model) -> list[VisualQAFinding]`

Sends original page images + rendered page images to Gemini. For documents with many pages, splits into 2-3 calls (each with a subset of originals + all rendered pages). Gemini identifies which original content is missing from the rendered version and attributes findings to specific original page numbers.

No 1:1 page mapping assumed — Gemini sees both sets of images and determines correspondence itself.

Returns list of findings across all pages (empty if no issues).

### `run_visual_qa(pdf_path, html_path, scanned_page_numbers, client, model, output_dir) -> VisualQAResult`

Orchestrates the full flow: render originals, render HTML pages, save PNGs to output_dir for report thumbnails, batch into Gemini calls, collect findings, save findings JSON. Returns `VisualQAResult` with findings list and API usage.

## Gemini Prompt

File: `src/prompts/visual_qa.md`

Focused on content comparison, not visual fidelity:

- "You are comparing original scanned document pages against an accessible HTML rendering to verify no educational content was lost during remediation."
- "The original pages are scanned images. The rendered pages are from an accessible HTML rendering converted to PDF — different layout, fonts, styling, and page count are expected."
- "Identify any educational content present in the originals that is MISSING, TRUNCATED, GARBLED, or INCORRECTLY REPRESENTED in the rendered version."
- "Ignore differences in: layout, fonts, colors, spacing, margins, headers/footers, page breaks. These are expected and intentional."
- "Focus on: missing text paragraphs, truncated or missing tables (check row/column counts), dropped images/figures, garbled equations or mathematical notation, missing captions or labels."
- "For each finding, specify which original page number it appears on and which rendered page number is the closest match (for side-by-side comparison)."
- Returns JSON: `{"findings": [{"original_page": 1, "rendered_page": 1, "type": "missing_table|truncated_text|dropped_image|garbled_equation|other", "description": "...", "severity": "high|medium|low"}]}`
- If no content issues: `{"findings": []}`

## Data Model

```python
class VisualQAFinding(BaseModel, frozen=True):
    original_page: int         # 0-based original page number
    rendered_page: int | None  # 0-based rendered page number (closest match), None if no match
    finding_type: str          # missing_table, truncated_text, dropped_image, garbled_equation, other
    description: str           # Human-readable description
    severity: str              # high, medium, low

class VisualQAResult(BaseModel):
    findings: list[VisualQAFinding] = Field(default_factory=list)
    pages_checked: int = 0
    api_usage: list[ApiUsage] = Field(default_factory=list)
```

These models go in `src/models/pipeline.py` alongside existing pipeline models. Thumbnail bytes are NOT stored in the model — they're generated at report time from the saved PNGs in the output directory.

## Report Integration

File: `src/tools/report_generator.py`

New "Visual Quality Check" section in the HTML report, placed between "What We Did" and "What Needs Attention":

- Only appears if there are high or medium severity visual QA findings (omitted entirely if all pages pass or only low findings)
- For each page with findings:
  - Side-by-side thumbnails: original page (left) | closest rendered page (right, as identified by Gemini)
  - Thumbnails embedded as base64 data URIs, scaled to ~400px wide
  - Finding text below the thumbnail pair
- Summary line at top: "Visual quality check found N content issues across M pages"

### Thumbnail storage

During visual QA, original page PNGs are saved to `{output_dir}/visual_qa/original_page_{N}.png` and rendered page PNGs to `{output_dir}/visual_qa/rendered_page_{N}.png`. The report generator reads these at report time and scales them down to ~400px wide for base64 embedding. This avoids storing large PNG bytes in the data model.

## Pipeline Integration

File: `src/agent/orchestrator.py`

After Phase 3 (execution), before Phase 4 (review). Only runs for scanned PDFs:

```python
# Phase 3.5: Visual Diff QA (scanned PDFs only)
if file_type == "pdf" and scanned_page_numbers and exec_result.companion_html_path:
    visual_qa_result = run_visual_qa(
        pdf_path=request.file_path,
        html_path=exec_result.companion_html_path,
        scanned_page_numbers=scanned_page_numbers,
        client=gemini_client,
        model=gemini_model,
        output_dir=output_dir,
    )
```

Visual QA findings are passed to `generate_report_html()` as an additional parameter.

## Structured Findings Persistence

After each run, findings are saved to `{output_dir}/visual_qa_findings.json`:

```json
{
  "document": "Mayer_1996.pdf",
  "timestamp": "2026-04-05T12:53:00Z",
  "pages_checked": 11,
  "findings": [
    {
      "page": 5,
      "type": "missing_table",
      "description": "Table 2 appears truncated — only 2 of 4 rows visible",
      "severity": "high"
    }
  ]
}
```

The batch runner (`scripts/test_batch.py`) aggregates these into `testdocs/visual_qa_summary.json` for cross-document pattern analysis. Repeated patterns (e.g., "tables on scanned pages frequently truncated") become pipeline improvement targets.

## Dependencies

- **No new dependencies.** WeasyPrint (already installed) renders HTML→PDF. PyMuPDF (already installed) renders PDF pages→PNG and handles image resizing. Two-step HTML→PDF→PNG uses only existing tools.

## Cost & Performance

- ~$0.003 per batch of 2-3 pages (Gemini 2.5 Flash, multiple images per call)
- ~$0.01-0.02 for a 10-page scanned document (4-5 Gemini calls)
- ~2-3 seconds per batch (rendering + API call)
- ~15-20 seconds total for a 10-page scanned PDF
- No browser startup overhead (WeasyPrint is in-process)

## What This Doesn't Do (Yet)

- **Auto-fix detected gaps** — future: feed findings back into executor for targeted re-remediation, creating a detect→fix→re-check loop
- **Compare non-scanned PDFs** — native PDFs are tagged in-place with low content loss risk
- **Compare non-PDF documents** — DOCX/PPTX remediate in-place
- **Sub-page comparison** — future: crop specific regions for more targeted analysis
- **Automated pipeline improvement** — findings are persisted for manual analysis; automated pattern detection is future work
