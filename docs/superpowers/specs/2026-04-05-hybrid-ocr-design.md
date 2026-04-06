# Hybrid OCR Architecture: Tesseract Text + Gemini Structure

## Problem

Gemini's RECITATION filter blocks OCR output on pages it believes contain copyrighted content. This is a known, unfixable Gemini limitation ([google/generative-ai-docs#566](https://github.com/google/generative-ai-docs/issues/566)). Currently, when RECITATION fires, the page falls through to Tesseract-only fallback which extracts text but loses all structure (tables, headings, figures, reading order). The Mayer paper loses pages 10-11 entirely to this, and it can affect any page unpredictably.

## Solution

Split OCR into two concerns:
- **Tesseract**: always extracts raw text with bounding boxes (deterministic, no copyright issues)
- **Gemini**: classifies structure only (region types, reading order, columns, heading levels) using the page image + Tesseract text as context — never asked to reproduce text

Gemini is allowed to **produce content** in two cases only:
1. **Table data** — structured headers + rows (Tesseract can't infer cell relationships)
2. **Figure descriptions** — alt text for images (generated, not reproduced)

## Pipeline Flow

```
Page Image → Tesseract (text + bounding boxes)
                ↓
         Tesseract blocks [{id, text, bbox}, ...]
                ↓
    Gemini (page image + Tesseract blocks as context)
                ↓
    Structure annotations:
      - Each Tesseract block → classified (heading/paragraph/caption/footnote/equation/page_header/page_footer)
      - Table regions → Gemini extracts table_data
      - Figure regions → Gemini provides figure_description
      - Reading order, column assignments, heading levels, bold/italic
                ↓
    Merge blocks + structure → ParagraphInfo / TableInfo / ImageInfo
```

## Gemini Prompt Design

Gemini receives:
1. The page image (PNG at 300 DPI)
2. Tesseract blocks as structured JSON context

```
You are a document structure analyzer. Below are text blocks extracted by OCR
from this scanned page, with their bounding box positions. Your job is to:

1. Classify each block (heading, paragraph, caption, footnote, equation, page_header, page_footer)
2. Assign reading order, column (0/1/2), heading level if applicable
3. Detect bold/italic from the visual appearance
4. Identify TABLE regions — extract structured table_data (headers + rows)
5. Identify FIGURE regions — provide figure_description for alt text
6. You may merge or split Tesseract blocks if they were incorrectly segmented

TESSERACT BLOCKS:
[{"id": 0, "text": "LEARNERS AS INFORMATION PROCESSORS", "bbox": [85, 42, 520, 68]}, ...]
```

## Gemini Response Schema

```json
{
  "regions": [
    {
      "block_ids": [0],
      "type": "heading",
      "heading_level": 1,
      "column": 0,
      "reading_order": 1,
      "bold": true,
      "italic": false
    },
    {
      "block_ids": [1, 2, 3],
      "type": "paragraph",
      "column": 1,
      "reading_order": 2,
      "bold": false,
      "italic": true
    },
    {
      "block_ids": [],
      "type": "table",
      "column": 0,
      "reading_order": 5,
      "table_data": {
        "headers": ["Category", "View 1", "View 2"],
        "rows": [["Knowledge", "...", "..."]]
      }
    },
    {
      "block_ids": [],
      "type": "figure",
      "column": 0,
      "reading_order": 8,
      "figure_description": "Bar chart showing mean recall scores..."
    }
  ]
}
```

- **`block_ids`**: references Tesseract blocks by ID. Gemini can merge multiple blocks into one region or leave 1:1.
- **Empty `block_ids`**: for tables and figures where Gemini provides the content directly.
- **No `text` field** on non-table/figure regions — text comes from referenced Tesseract blocks.

## New Functions

All within `src/tools/scanned_page_ocr.py`:

### `_tesseract_extract_blocks(doc, page_number, dpi) -> list[dict]`
Runs Tesseract `image_to_data()` on the page. Returns list of `{"id": int, "text": str, "bbox": [x, y, w, h]}`. Pulls block-grouping logic from existing `_tesseract_fallback()` but outputs raw blocks instead of `ParagraphInfo`.

### `_gemini_classify_structure(client, model, doc, page_number, blocks, dpi) -> dict | None`
Sends page image + Tesseract blocks to Gemini. Returns the annotated regions dict. Returns `None` on failure (RECITATION, rate limit, etc).

### `_merge_blocks_and_structure(blocks, structure, page_number, para_offset, table_offset, img_offset) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]`
Combines Tesseract text with Gemini's classifications. For each region:
- Non-table/figure: concatenate text from referenced `block_ids` → `ParagraphInfo`
- Table: use Gemini's `table_data` → `TableInfo`
- Figure: use Gemini's `figure_description` → `ImageInfo`

### `_heuristic_classify_blocks(blocks, page_number, para_offset) -> list[ParagraphInfo]`
Fallback when Gemini is unavailable. Uses existing heuristics from `_tesseract_fallback()`: ALL CAPS → heading, x-position → column detection, everything else → paragraph.

## Rewritten `_process_single_page()`

```
1. Tesseract extracts blocks
   - If no blocks → page fails (same as today)
2. Gemini classifies structure
   - If Gemini succeeds → merge blocks + structure → return
   - If Gemini fails → heuristic classification → return (degraded but functional)
```

No more Gemini→Gemini HD→crops→Tesseract retry chain.

## Fallback Behavior

| Tesseract | Gemini | Result |
|-----------|--------|--------|
| Success | Success | Full structure (best case) |
| Success | RECITATION/failure | Heuristic structure (same as current Tesseract-only) |
| Failure | N/A | Page fails (same as today) |

Worst case is no worse than today's Tesseract fallback. Common case eliminates RECITATION entirely.

## What Gets Removed

- `_process_ocr_batch()` — no longer sending pages to Gemini for text extraction
- The Gemini→Gemini HD→crops→Tesseract retry chain in `_process_single_page()`
- Current `OCR_PAGE_SCHEMA` — replaced by annotation schema
- `_is_garbled_text()` / `_find_garbled_pages()` — Tesseract doesn't garble the same way
- Half-page crop logic

## What Stays

- `_stitch_page_results()` — still merges per-page results with sequential IDs
- `_rescue_missed_tables()` / table rescue pipeline — safety net for tables Gemini misses
- `_is_leaked_header_footer()` — pre-filter + fallback heuristic
- `_sort_regions_by_column()` — used in fallback mode
- `ScannedPageResult`, `PageOCRResult` dataclasses — unchanged interface
- `_regions_to_model_objects()` — may be partially reusable in merge function

## Orchestrator Impact

Zero. `process_scanned_pages()` still returns `ScannedPageResult`. The hybrid architecture is entirely internal to `scanned_page_ocr.py`.

## Testing

- Primary test document: Mayer paper (fully scanned, 11 pages, tables, figures, two-column layout)
- Success criteria: all 11 pages produce text (no RECITATION losses), tables extracted, headings detected
- Compare output quality against current Gemini-only results for non-RECITATION pages
- Verify fallback works by testing with GEMINI_API_KEY unset
