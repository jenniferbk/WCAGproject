# Mistral OCR 3 Experimental Mode: Markdown Parser + Parallel Comparison

## Problem

The hybrid OCR pipeline (Tesseract + Gemini + Haiku) works but uses 3 models, ~800 lines of orchestration code, and costs ~$0.08/doc. Mistral OCR 3 does text + structure in one call at $0.001/page with no RECITATION filter. Initial evaluation on the Mayer paper (11 pages) showed:
- All 11 pages processed in 7.1 seconds (vs ~5-10 min hybrid)
- 4/4 tables found (hybrid misses Table 3)
- 20 headings detected natively (vs heuristic-based)
- Cost: $0.011 (vs ~$0.08)

We need to evaluate Mistral on more documents before considering it as a replacement. Ship it as a parallel comparison mode — both pipelines run on every scanned PDF, report shows side-by-side OCR quality comparison.

## Architecture

### Pipeline Fork Point

```
pdf_parser.py → detect scanned pages
    │
    ├─→ Hybrid OCR (existing) → DocumentModel (primary, full pipeline)
    │
    └─→ Mistral OCR (new) → DocumentModel (comparison only, raw output in report)
    │
    ▼
Orchestrator continues with hybrid DocumentModel → comprehension → strategy → execution → review
Report includes "OCR Comparison" section with Mistral vs Hybrid stats
```

Both OCR paths run concurrently. Only the hybrid result feeds into the full remediation pipeline. The Mistral result is included in the report for quality comparison — not remediated.

### Cost Impact

- Hybrid: ~$0.08/doc (unchanged)
- Mistral: ~$0.011/doc additional
- Total: ~$0.09/doc (~12% increase)
- No additional Claude costs (Mistral result is not remediated)

## New File: `src/tools/mistral_ocr.py`

### Public Interface

```python
def process_scanned_pages_mistral(
    pdf_path: Path,
    page_numbers: list[int],  # 0-based
    api_key: str | None = None,
) -> ScannedPageResult:
    """Process scanned PDF pages through Mistral OCR 3.

    Returns the same ScannedPageResult type as the hybrid pipeline
    for direct comparison.
    """
```

### Internal Functions

```python
def _upload_and_ocr(client: Mistral, pdf_path: Path, pages: list[int]) -> OCRResponse:
    """Upload PDF to Mistral, run OCR on specified pages, cleanup."""

def _parse_ocr_response(response: OCRResponse) -> tuple[
    list[ParagraphInfo], list[TableInfo], list[ImageInfo], list[LinkInfo]
]:
    """Convert Mistral OCR response to DocumentModel components.

    Walks the markdown-it-py token stream for each page and produces
    model objects with sequential IDs.
    """

def _parse_page_markdown(
    markdown: str,
    page_index: int,
    tables: list[TableObject],
    para_offset: int,
    table_offset: int,
    link_offset: int,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[LinkInfo], list[ContentOrderItem]]:
    """Parse a single page's markdown into model objects.

    Token mapping:
    - heading_open/close → ParagraphInfo(heading_level=N)
    - paragraph_open/close → ParagraphInfo with inline RunInfo
    - strong_open/close → RunInfo(bold=True)
    - em_open/close → RunInfo(italic=True)
    - link_open/close → LinkInfo
    - blockquote_open/close → ParagraphInfo (text content preserved)
    - table_open/close → TableInfo from Mistral's structured TableObject
    - [tbl-N.md] link in text → match to TableObject by ID

    Page header detection: first line matching r'^\d+ [A-Z]+$' (e.g. '152 MAYER')
    is dropped — it's a scanned page header, not document content.
    """

def _extract_inline_runs(tokens: list, start: int, end: int) -> list[RunInfo]:
    """Walk inline tokens between open/close and build RunInfo list.

    Handles nested formatting: **bold *bold-italic* bold** produces
    three RunInfo objects with appropriate flags.
    """

def _table_object_to_table_info(
    table_obj: TableObject,
    table_id: str,
    page_number: int,
) -> TableInfo:
    """Convert Mistral's TableObject (markdown table string) to TableInfo.

    Parses the markdown table format:
    | Header1 | Header2 |
    | --- | --- |
    | cell | cell |

    First row = headers (header_row_count=1).
    """
```

### ID Scheme

Same as current OCR path: `ocr_p_0`, `ocr_tbl_0`, `ocr_img_0`, `ocr_link_0`. Sequential across all pages.

### Dependencies

- `markdown-it-py` — token-based markdown parser (PyPI, MIT license)
- `mistralai` — already installed (v2.3.0)

## Modified File: `src/agent/orchestrator.py`

### Changes

In the scanned PDF path (after `_detect_scanned_pages()` returns True):

```python
# Run both OCR pipelines concurrently
import concurrent.futures

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    hybrid_future = pool.submit(process_scanned_pages, doc, page_numbers, ...)
    mistral_future = pool.submit(process_scanned_pages_mistral, pdf_path, page_numbers)

    hybrid_result = hybrid_future.result()

    try:
        mistral_result = mistral_future.result()
    except Exception as e:
        logger.warning(f"Mistral OCR failed (non-fatal): {e}")
        mistral_result = None
```

- Mistral failure is non-fatal — if it fails, the hybrid result is used alone and comparison section is skipped
- Only runs when `MISTRAL_API_KEY` is set in environment. If not set, skip Mistral entirely.
- The `mistral_result` (a `ScannedPageResult`) is passed through to the report generator

### Data Flow

The orchestrator stores the Mistral result on `RemediationResult` (or passes it as a separate argument to the report generator). No changes to the primary pipeline flow.

## Modified File: `src/tools/report_generator.py`

### New Section: "OCR Engine Comparison (Experimental)"

Added when Mistral result is available. Shows:

```
### OCR Engine Comparison (Experimental)

We ran your document through two OCR engines for quality comparison.

| Metric | Standard (Hybrid) | Experimental (Mistral) |
|--------|-------------------|----------------------|
| Paragraphs | 91 | 87 |
| Headings | 10 | 20 |
| Tables | 4 | 4 |
| Processing time | 312s | 7s |
| Cost | $0.08 | $0.01 |

#### Differences Found
- Mistral found 10 additional headings not detected by the standard engine
- Table 3 (page 6): found by Mistral, missed by standard engine
- [additional diffs...]
```

The comparison focuses on structural completeness (headings, tables) not text content diffs — text quality is hard to display meaningfully in a report.

## Modified File: `pyproject.toml`

Add to dependencies:
```
"markdown-it-py>=3.0",
"mistralai>=2.3",
```

## New File: `tests/test_mistral_parser.py`

Unit tests for the markdown parser using saved Mistral output (no API calls):

- `test_parse_heading_levels` — `#` → level 1, `##` → level 2
- `test_parse_paragraphs_with_runs` — bold, italic, nested formatting
- `test_parse_table_from_object` — Mistral TableObject → TableInfo with headers
- `test_parse_links` — inline links → LinkInfo
- `test_parse_blockquote` — `>` blocks → ParagraphInfo
- `test_page_header_stripping` — `"152 MAYER\n\n..."` drops the header line
- `test_content_order` — elements appear in correct sequence
- `test_sequential_ids` — IDs are `ocr_p_0`, `ocr_p_1`, etc. across pages
- `test_full_mayer_page` — parse saved page_1.md, page_6.md, page_7.md end-to-end
- `test_empty_page` — graceful handling of empty markdown
- `test_mistral_api_failure` — verify non-fatal fallback

Test fixtures: use the markdown files already saved in `testdocs/output/mistral_ocr_eval/`.

## Environment Variables

```bash
# Optional — enables Mistral experimental comparison
MISTRAL_API_KEY=...
```

No new required variables. Mistral comparison is entirely opt-in via the API key.

## Risks

- **Mistral API availability** — If Mistral is down during a job, the comparison silently skips. Non-fatal by design.
- **Markdown format changes** — Mistral could change their markdown output format. Tests against real output files catch this.
- **Token stream edge cases** — Unusual markdown (nested tables, HTML in markdown) could break the parser. Handle gracefully with try/except per page.
- **Table reference format** — Mistral uses `[tbl-N.md](tbl-N.md)` links to reference tables. If this format changes, table matching breaks. Fall back to parsing markdown tables inline.
