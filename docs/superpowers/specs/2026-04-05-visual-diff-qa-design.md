# Visual Diff QA Design

**Date:** 2026-04-05
**Problem:** No automated way to detect content gaps between original PDF pages and remediated HTML output. Missing tables, dropped images, truncated text, and garbled equations go undetected until manual review.
**Approach:** After execution (Phase 3.5), render original PDF pages and companion HTML to images, send both to Gemini for content comparison, surface findings with side-by-side thumbnails in a dedicated report section.

## Architecture

```
Phase 3: Execution → companion HTML + remediated PDF
  ↓
Phase 3.5: Visual Diff QA (NEW)
  ├─ For each page in original PDF:
  │   ├─ Render original page → PNG (200 DPI) via PyMuPDF
  │   ├─ Render matching HTML section → PNG via Playwright
  │   ├─ Send both to Gemini: "What content is missing or incorrect?"
  │   ├─ Save thumbnail pair (scaled down for report embedding)
  │   └─ Collect structured findings
  ├─ Save findings to visual_qa_findings.json
  └─ Pass findings to report generator
  ↓
Phase 4: Review (Claude)
```

## Scope

- **PDFs only** — DOCX/PPTX/LaTeX out of scope (in-place remediation has lower content loss risk)
- **Detection + reporting only** — auto-fix loop is future work
- **Content gaps only** — ignores expected visual differences (layout, fonts, colors, spacing)

## Visual QA Module

File: `src/tools/visual_qa.py`

### `render_original_pages(pdf_path, page_numbers) -> dict[int, bytes]`

Renders specified pages from the original PDF to PNG at 200 DPI using PyMuPDF. Returns `{page_number: png_bytes}`.

### `render_html_pages(html_path, page_count) -> list[bytes]`

Renders the companion HTML to PNG screenshots using Playwright (headless Chromium). The companion HTML uses `<section class="page" data-page="N">` for page breaks. Captures each page section as a separate screenshot. Returns list of PNG bytes ordered by page.

Falls back gracefully if Playwright is not installed — logs a warning and returns empty list (visual QA skipped, not a pipeline-breaking failure).

### `compare_page(original_png, rendered_png, page_number, client, model) -> list[VisualQAFinding]`

Sends both images to Gemini with the comparison prompt. Returns list of findings for this page (empty if no issues).

### `run_visual_qa(pdf_path, html_path, page_numbers, client, model) -> VisualQAResult`

Orchestrates the full flow: render originals, render HTML, compare each pair, collect findings. Returns `VisualQAResult` with findings list and API usage.

## Gemini Prompt

File: `src/prompts/visual_qa.md`

Focused on content comparison, not visual fidelity:

- "Compare these two images of the same document page. The first is the original scanned page. The second is the accessible HTML rendering."
- "Identify any educational content present in the original that is missing, truncated, garbled, or incorrectly represented in the rendered version."
- "Ignore differences in: layout, fonts, colors, spacing, margins, headers/footers. These are expected."
- "Focus on: missing text paragraphs, truncated or missing tables, dropped images/figures, garbled equations, missing captions or labels."
- Returns JSON: `{"has_issues": bool, "findings": [{"type": "missing_table|truncated_text|dropped_image|garbled_equation|other", "description": "...", "severity": "high|medium|low"}]}`
- If no content issues: `{"has_issues": false, "findings": []}`

## Data Model

```python
class VisualQAFinding:
    page_number: int           # 0-based
    finding_type: str          # missing_table, truncated_text, dropped_image, garbled_equation, other
    description: str           # Human-readable description
    severity: str              # high, medium, low
    original_thumbnail: bytes  # Small PNG for report embedding
    rendered_thumbnail: bytes  # Small PNG for report embedding

class VisualQAResult:
    findings: list[VisualQAFinding]
    pages_checked: int
    api_usage: list[ApiUsage]
```

These models go in `src/models/pipeline.py` alongside existing pipeline models.

## Report Integration

File: `src/tools/report_generator.py`

New "Visual Quality Check" section in the HTML report, placed between "What We Did" and "What Needs Attention":

- Only appears if there are visual QA findings (omitted entirely if all pages pass)
- For each page with findings:
  - Side-by-side thumbnails: original page (left) | rendered page (right)
  - Thumbnails embedded as base64 data URIs, scaled to ~400px wide
  - Finding text below the thumbnail pair, grouped by type
- Summary line at top: "Visual quality check found issues on N of M pages"

### Thumbnail generation

Original and rendered PNGs are scaled down to ~400px wide for report embedding. Uses PyMuPDF or Pillow for resizing. Stored as base64 data URIs in the HTML report.

## Pipeline Integration

File: `src/agent/orchestrator.py`

After Phase 3 (execution), before Phase 4 (review):

```python
# Phase 3.5: Visual Diff QA
if file_type == "pdf" and exec_result.companion_html_path:
    visual_qa_result = run_visual_qa(
        pdf_path=request.file_path,
        html_path=exec_result.companion_html_path,
        page_numbers=list(range(total_pages)),
        client=gemini_client,
        model=gemini_model,
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

- **Playwright** (`pip install playwright`, then `playwright install chromium`) for HTML→PNG rendering
- Add to `pyproject.toml` as optional dependency: `playwright >= 1.40`
- Graceful fallback: if Playwright not installed, visual QA is skipped with a warning (not a pipeline failure)
- PyMuPDF (already installed) for original page rendering and thumbnail resizing

## Cost & Performance

- ~$0.003/page (Gemini 2.5 Flash, two images per call)
- ~$0.03 for a 10-page document
- ~2-3 seconds per page (rendering + API call)
- ~30 seconds total for a 10-page PDF
- Playwright startup: ~1-2 seconds (one-time per run)

## What This Doesn't Do (Yet)

- **Auto-fix detected gaps** — future: feed findings back into executor for targeted re-remediation, creating a detect→fix→re-check loop
- **Compare non-PDF documents** — DOCX/PPTX remediate in-place with lower content loss risk
- **Sub-page comparison** — future: crop specific regions for more targeted analysis
- **Automated pipeline improvement** — findings are persisted for manual analysis; automated pattern detection is future work
