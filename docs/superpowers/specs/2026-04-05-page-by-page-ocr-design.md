# Page-by-Page OCR Pipeline Design

**Date:** 2026-04-05
**Problem:** The current batch OCR pipeline (PAGES_PER_BATCH=2) causes content duplication and loss when retries/fallbacks fire. Gemini RECITATION refusals, garble retries, and Tesseract fallbacks all append content to shared accumulator lists without cleanly replacing previous attempts. Result: duplicated paragraphs, lost pages, unreliable output.
**Approach:** Rewrite `process_scanned_pages()` to process one page at a time with a clean per-page result container. Each page gets exactly one result (Gemini or Tesseract, never both). Stitching is explicit and ordered.

## Architecture

```
For each scanned page:
  ┌─ Try 1: Gemini at 200 DPI
  │   ├─ Success + not garbled → use this result
  │   └─ Failed or garbled ↓
  ├─ Try 2: Gemini at 300 DPI
  │   ├─ Success + not garbled → use this result
  │   └─ Failed ↓
  └─ Try 3: Tesseract fallback
      └─ Returns paragraphs (no tables/figures)

Each page → exactly one PageResult (paragraphs, tables, figures)
                        ↓
Stitch all PageResults in page order → accumulator lists
                        ↓
Table rescue runs on the stitched result
                        ↓
Return ScannedPageResult
```

## Key change: `_process_single_page()`

New function that encapsulates the entire retry chain for one page. Returns a dataclass:

```python
@dataclass
class PageOCRResult:
    page_number: int
    paragraphs: list[ParagraphInfo]
    tables: list[TableInfo]
    figures: list[ImageInfo]
    api_usage: list[ApiUsage]
    source: str  # "gemini", "gemini_hd", "tesseract", "failed"
    warnings: list[str]
```

The function tries Gemini → Gemini HD → Tesseract, returning the first successful result. No accumulator lists, no shared state between pages.

## Rewrite of `process_scanned_pages()`

The main function becomes:

```python
def process_scanned_pages(...) -> ScannedPageResult:
    # Init client, load prompt
    
    page_results: list[PageOCRResult] = []
    for i, page_num in enumerate(scanned_page_numbers):
        on_progress(f"OCR page {i+1}/{len(scanned_page_numbers)}")
        result = _process_single_page(client, model, doc, page_num, prompt)
        page_results.append(result)
        time.sleep(DELAY_BETWEEN_PAGES)  # rate limit
    
    # Stitch: merge all page results in order, assigning sequential IDs
    all_paragraphs, all_tables, all_figures = _stitch_page_results(page_results, doc)
    
    # Table rescue runs on stitched result
    # (already wired into _integrate_page_data or called separately)
    
    return ScannedPageResult(...)
```

## What stays the same

- `_process_ocr_batch()` — still sends page images to Gemini and returns raw JSON
- `_regions_to_model_objects()` — still converts Gemini regions to model objects
- `_sort_regions_by_column()` — still handles column sorting per-page
- `_integrate_page_data()` — may be simplified or inlined since it's called once per page now
- `_is_garbled_text()`, `_find_garbled_pages()` — still detect garbled OCR
- `_tesseract_fallback()` — still extracts text via Tesseract
- `_rescue_missed_tables()` — still runs on the stitched result
- All table rescue functions from earlier today

## What changes

- `process_scanned_pages()` — rewritten from ~260 lines of nested retry spaghetti to ~40 lines of clean per-page loop + stitch
- New `_process_single_page()` — ~50 lines, encapsulates retry chain
- New `_stitch_page_results()` — ~30 lines, merges PageOCRResults in order with sequential IDs
- New `PageOCRResult` dataclass
- `PAGES_PER_BATCH` removed (always 1 page per call now)
- `DELAY_BETWEEN_BATCHES` renamed to `DELAY_BETWEEN_PAGES` (reduced from 10s to 5s since individual pages are faster)

## Files

| File | Action |
|------|--------|
| `src/tools/scanned_page_ocr.py` | Rewrite `process_scanned_pages()`, add `_process_single_page()`, `_stitch_page_results()`, `PageOCRResult` |
| `tests/test_scanned_ocr.py` | Add tests for new functions, verify existing tests still pass |

## Risk

This is a rewrite of a critical path function. Mitigation:
- All existing unit tests for helper functions still pass (they test `_regions_to_model_objects`, `_sort_regions_by_column`, etc. independently)
- The new `_process_single_page` is simpler to test than the current batch loop
- E2E validation on Mayer document after the rewrite
