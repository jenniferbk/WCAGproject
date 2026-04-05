# OCR Table Recognition Design

**Date:** 2026-04-05
**Problem:** Gemini OCR returns table cells as individual paragraphs for scanned PDF tables (e.g., Mayer TABLE 1/2/3)
**Approach:** Improve OCR prompt + post-OCR caption-triggered table rescue via Gemini re-send

## Architecture

```
Scanned page image
  ↓
Gemini OCR (improved prompt with table visual indicators)
  ↓
_regions_to_model_objects() → paragraphs + tables
  ↓
_rescue_missed_tables()
  ├─ Scan paragraphs for table caption pattern (TABLE N, Table N:, etc.)
  ├─ For each caption found:
  │   ├─ Collect consecutive paragraphs after caption (until next heading/caption/gap)
  │   ├─ Re-send original page image to Gemini with focused table extraction prompt
  │   ├─ Parse response into TableInfo
  │   └─ Replace caption + paragraph group with TableInfo (caption preserved)
  └─ Return updated paragraphs + tables
  ↓
DocumentModel (tables correctly represented)
```

## Layer 1: Improved OCR Prompt

File: `src/prompts/scanned_ocr.md`

Add to the table section:
- Visual indicators: gridlines, horizontal rules, column alignment, header formatting (bold/shaded)
- Academic paper hint: "If you see a caption like 'TABLE 1', 'Table 2:', 'TABLE III' etc., the content below is a table. Extract it as type: table with table_data, not as paragraphs."
- Example of what a missed table looks like (cells become separate paragraph regions) vs. correct extraction

## Layer 2: Post-OCR Table Rescue

File: `src/tools/scanned_page_ocr.py`

### Detection

New function `_find_table_captions(paragraphs: list[ParagraphInfo]) -> list[dict]`:
- Regex: `r'^(?:TABLE|Table|table)\s+(?:\d+|[IVXLC]+)\.?\s*[:.]?\s*(.*)'`
- Returns list of `{caption_text, caption_index, caption_paragraph_id}`

### Paragraph collection

New function `_collect_table_paragraphs(paragraphs: list[ParagraphInfo], caption_index: int) -> list[int]`:
- Starting from `caption_index + 1`, collect consecutive paragraph indices
- Stop when encountering:
  - Another table caption
  - A heading (heading_level > 0)
  - A paragraph with significantly different formatting (e.g., much longer text suggesting body prose — threshold: >300 chars and no tab/column patterns)
  - End of paragraph list
- Return list of paragraph indices that belong to this table

### Gemini re-send

New function `_rescue_table_from_page(page_image: bytes, caption: str, gemini_client) -> TableInfo | None`:
- Sends page image to Gemini with focused prompt:
  ```
  This page from a scanned academic document contains a table with the caption: "{caption}"
  
  Extract the complete table structure. Return JSON with:
  - "headers": array of column header strings
  - "rows": array of arrays of cell value strings
  
  Include ALL rows and columns. For multi-line cells, join the text with spaces.
  If you cannot identify the table structure, return {"headers": [], "rows": []}.
  ```
- Uses same Gemini model as OCR (gemini-2.5-flash)
- Parses response into TableInfo with CellInfo cells
- Returns None if extraction fails (empty headers and rows)

### Integration

New function `_rescue_missed_tables(paragraphs, tables, page_images, gemini_client) -> (paragraphs, tables)`:
- Called inside `_integrate_page_data()` after `_regions_to_model_objects()`
- Calls `_find_table_captions()` on paragraphs
- For each caption where the paragraphs after it don't already correspond to a table in the tables list:
  - Determine which page the caption is on (from paragraph page tracking)
  - Call `_rescue_table_from_page()` with that page's image
  - If successful: remove caption + collected paragraphs from paragraph list, add TableInfo to tables list
  - If failed: leave paragraphs as-is (they'll flow through as text, which is acceptable fallback)
- Returns updated (paragraphs, tables)

### ID scheme

Rescued tables use existing OCR ID scheme: `ocr_tbl_N` where N continues from the last table index.

## Tests

File: `tests/test_scanned_ocr.py`

1. `test_find_table_captions` — detects "TABLE 1", "Table 2:", "TABLE III", "table 4." patterns; ignores "The table below" or "see Table 1 for..."
2. `test_collect_table_paragraphs` — stops at headings, other captions, long prose
3. `test_rescue_missed_tables_replaces_paragraphs` — mock Gemini response, verify paragraphs replaced with TableInfo
4. `test_rescue_missed_tables_skips_existing_tables` — caption already has a corresponding table → no re-send
5. `test_rescue_missed_tables_handles_failure` — Gemini returns empty → paragraphs left as-is
6. `test_caption_not_at_line_start` — "see TABLE 1" mid-sentence should NOT trigger rescue

## Cost impact

- ~$0.001 per missed table (one Gemini call per table)
- Typical scanned paper: 0-4 missed tables → $0.000-$0.004 additional cost
- Negligible impact on total processing time (~2-3s per table rescue)

## What this doesn't cover

- Uncaptioned tables (future work — would need visual pattern detection)
- Tables spanning multiple pages (flag for human review)
- Non-scanned PDF tables (handled by PyMuPDF path in pdf_parser.py)
