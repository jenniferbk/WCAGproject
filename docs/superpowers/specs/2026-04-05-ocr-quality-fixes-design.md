# OCR Pipeline Quality Fixes Design

**Date:** 2026-04-05
**Problem:** Visual diff QA on the Mayer paper revealed 3 high-severity content gaps caused by OCR pipeline issues: dropped left column (page 9), duplicate table extraction, and missed table rescue.
**Approach:** Three targeted fixes in the OCR layer — column sorting validation with fallback, table deduplication, and expanded table caption matching.

## Fix 1: Column Sorting Validation (Page 9 Text Loss)

**Root cause:** `_sort_regions_by_column()` in `scanned_page_ocr.py` loses content when Gemini marks left-column regions as `column=0` (full-width). Each full-width region triggers `_flush_columns()`, which emits accumulated left/right content and resets. If left-column regions are all marked `column=0`, they get treated as full-width fences and the actual left-column text is never accumulated.

**Fix:** After the existing column sorting completes, validate column balance. If a page has any `column=1` or `column=2` regions but one side is empty while the other has substantial content, the column metadata is unreliable. Fall back to positional grouping:

1. After `_sort_regions_by_column()` returns, check if the result has a suspicious imbalance (e.g., regions marked column=2 exist but no column=1 regions, or vice versa).
2. If imbalanced: re-sort using a spatial heuristic. Since we don't have bounding boxes from Gemini's structured output, use the `reading_order` values — in a two-column layout, left-column regions have lower reading_order values than right-column regions for the same vertical position.
3. Alternatively, detect when a full-width region (`column=0`) appears between column-marked regions and has text that looks like it belongs in a column (short text, not a heading or title).

The simplest effective fix: after sorting, if no column=1 regions exist but column=2 regions do, reassign all `column=0` non-heading regions that appear before the first column=2 region as column=1, then re-sort.

**Validation:** Add a `_validate_column_balance()` function that:
- Counts regions per column assignment
- If column=2 exists but column=1 doesn't (or vice versa), log a warning and attempt reassignment
- If total column-marked regions < 2, skip validation (single-column page)

File: `src/tools/scanned_page_ocr.py`

## Fix 2: Table Deduplication (Duplicate Table 2)

**Root cause:** Gemini sometimes extracts the same table twice from a scanned page (column-boundary artifact where a table spanning both columns gets read as two separate tables). `_merge_ocr_into_model()` in `orchestrator.py` has `_deduplicate_ocr_paragraphs()` but no equivalent for tables.

**Fix:** Add `_deduplicate_ocr_tables()` in `orchestrator.py`:

1. For each pair of tables, normalize cell text (lowercase, strip whitespace) and compute overlap
2. If two tables share >80% of their cell text content (comparing flattened cell lists), they're duplicates
3. Keep the first occurrence (likely has better structure), drop the duplicate
4. Log when duplicates are removed

The comparison should be order-independent — compare sorted sets of normalized cell texts, not row-by-row, since Gemini may extract rows in different order.

File: `src/agent/orchestrator.py`

## Fix 3: Table Caption Matching (Table 3 Not Rescued)

**Root cause:** Table 3's caption appeared as "Two Views of the Information-Processing Metaphor" — a descriptive title without the leading "TABLE 3" prefix. The rescue regex `r'^(?:TABLE|Table|table)\s+(?:\d+|[IVXLC]+)\b'` requires the paragraph to START with "TABLE N".

In the Mayer paper, the OCR extracted "TABLE 3" as a separate region but the title text was in another region. The issue is that the caption paragraph containing "TABLE 3" may have been consumed by deduplication or column sorting before the rescue ran.

**Two-part fix:**

### Part A: Improve OCR prompt

Add to `src/prompts/scanned_ocr.md` in the TABLES section: "Table captions always start with 'TABLE' or 'Table' followed by a number. Keep the caption number and title together in a single caption region, e.g., 'TABLE 3 Two Views of the Information-Processing Metaphor'."

### Part B: Broaden caption detection

Expand `_find_table_captions()` to also detect captions where "Table N" appears as a standalone short paragraph (<=20 chars) immediately before a descriptive title paragraph. When found, merge them conceptually — treat the "TABLE 3" paragraph as the caption and include the title paragraph in the collected cell range.

Also: handle the case where the caption regex matches but the actual table content was already extracted as a table by Gemini (just with wrong numbering). Check if the next content after the caption is already a `TableInfo` in the tables list — if so, skip rescue but log for attribution checking.

File: `src/tools/scanned_page_ocr.py`, `src/prompts/scanned_ocr.md`

## Testing

All fixes get unit tests in `tests/test_scanned_ocr.py`:

- Fix 1: Test `_validate_column_balance()` with imbalanced column assignments
- Fix 2: Test `_deduplicate_ocr_tables()` with duplicate tables, near-duplicates, and distinct tables
- Fix 3: Test expanded `_find_table_captions()` with standalone "TABLE N" paragraphs followed by title text

End-to-end validation: re-run Mayer document and verify the 3 high-severity findings are resolved.

## Success criteria

After these fixes, re-running visual diff QA on the Mayer paper should show:
- Page 9 left column text present in rendered output (Fix 1)
- No duplicate Table 2 (Fix 2)
- Table 3 properly extracted as a table (Fix 3)
- Zero high-severity findings (or at most 1 — the table structure may still need tuning)
