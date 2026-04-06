# Hybrid OCR Architecture: Tesseract + Gemini Structure + Haiku Correction

## Problem

Gemini's RECITATION filter blocks OCR output on pages it believes contain copyrighted content. This is a known, unfixable Gemini limitation ([google/generative-ai-docs#566](https://github.com/google/generative-ai-docs/issues/566)). Currently, when RECITATION fires, the page falls through to Tesseract-only fallback which extracts text but loses all structure (tables, headings, figures, reading order). The Mayer paper loses pages 10-11 to this, and it can affect any page unpredictably.

## Solution

Split OCR into three concerns across three models:

| Step | Model | Cost | Why this model |
|------|-------|------|----------------|
| Text extraction | Tesseract (local) | Free | Deterministic, no API dependency, no copyright filter |
| Structure classification | Gemini 2.5 Flash | $0.15/$0.60 per MTok | Cheapest vision model, excellent at layout. No RECITATION risk since it never reproduces text |
| Text correction | Claude Haiku 4.5 | $1/$5 per MTok | No RECITATION filter, good at careful textual comparison, already integrated |

**Future:** Evaluate Mistral OCR 3 ($1/1K pages) as a potential single-model replacement for the entire pipeline. Purpose-built for document OCR with structure, no RECITATION. Needs testing on academic papers before adoption.

## Pipeline Flow

```
Page Image
    │
    ├─→ Tesseract (text + bounding boxes)
    │       → blocks: [{id, text, bbox}, ...]
    │
    ├─→ Gemini 2.5 Flash (page image + Tesseract blocks)
    │       → structure: region types, reading order, columns,
    │         heading levels, bold/italic, table_data, figure_descriptions
    │
    └─→ Claude Haiku 4.5 (page image + Tesseract blocks)
            → corrected text per block where Tesseract made errors
    │
    ▼
Merge: Tesseract text (corrected by Haiku) + Gemini structure
    → ParagraphInfo / TableInfo / ImageInfo
```

Gemini and Haiku calls can run in parallel since they're independent.

## Gemini Prompt (Structure Only)

Gemini receives the page image + Tesseract blocks. It classifies structure but does NOT produce corrected text.

```
You are a document structure analyzer for accessibility remediation.
Below are text blocks extracted by OCR from this scanned page, with their
bounding box positions [x, y, width, height].

Classify each block and determine the document's reading order.

For each region, provide:
- block_ids: which Tesseract blocks belong to this region (merge if needed)
- type: heading, paragraph, caption, footnote, equation, page_header, page_footer
- reading_order: sequential number for screen reader order
- column: 0 (full-width), 1 (left), 2 (right)
- heading_level: 1-6 if type is heading
- bold: true/false based on visual appearance
- italic: true/false based on visual appearance
- font_size_relative: large/normal/small

For TABLE regions (gridlines, aligned columns, "Table N" captions):
- Set block_ids to [] (empty)
- Provide table_data with headers and rows extracted from the image

For FIGURE regions (charts, diagrams, photos):
- Set block_ids to [] (empty)
- Provide figure_description for alt text

TESSERACT BLOCKS:
{blocks_json}
```

## Haiku Prompt (Text Correction)

Haiku receives the page image + Tesseract blocks. It corrects OCR errors but does NOT classify structure.

```
You are an OCR text correction tool. Below are text blocks extracted by
Tesseract OCR from the attached scanned document page. Tesseract sometimes
makes errors: wrong characters, missed ligatures, broken words, garbled
symbols, missed special characters.

Compare each text block against what you can see in the image. Return
corrections ONLY for blocks that have errors. If a block is correct,
omit it from your response.

Return JSON: {"corrections": [{"id": 0, "corrected_text": "..."}]}

TESSERACT BLOCKS:
{blocks_json}
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
      "italic": false,
      "font_size_relative": "large"
    },
    {
      "block_ids": [1, 2, 3],
      "type": "paragraph",
      "column": 1,
      "reading_order": 2,
      "bold": false,
      "italic": true,
      "font_size_relative": "normal"
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

## Haiku Response Schema

```json
{
  "corrections": [
    {"id": 3, "corrected_text": "The learner selects relevant information..."},
    {"id": 7, "corrected_text": "cognitive processing during learning"}
  ]
}
```

Only blocks with errors are returned. Blocks omitted from the response use Tesseract text as-is.

## New Functions

All within `src/tools/scanned_page_ocr.py`:

### `_tesseract_extract_blocks(doc, page_number, dpi) -> list[dict]`
Runs Tesseract `image_to_data()` on the page. Returns list of `{"id": int, "text": str, "bbox": [x, y, w, h]}`. Pulls block-grouping logic from existing `_tesseract_fallback()` but outputs structured blocks instead of `ParagraphInfo`.

### `_gemini_classify_structure(client, model, doc, page_number, blocks, dpi) -> dict | None`
Sends page image + Tesseract blocks to Gemini. Returns the regions dict. Returns `None` on failure.

### `_haiku_correct_text(blocks, doc, page_number, dpi) -> dict[int, str]`
Sends page image + Tesseract blocks to Claude Haiku 4.5. Returns `{block_id: corrected_text}` for blocks with errors. Returns empty dict on failure (Tesseract text used uncorrected).

### `_apply_corrections(blocks, corrections) -> list[dict]`
Applies Haiku corrections to Tesseract blocks. Returns new block list with corrected text.

### `_merge_blocks_and_structure(blocks, structure, page_number, para_offset, table_offset, img_offset) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]`
Combines corrected Tesseract text with Gemini's classifications into model objects.

### `_heuristic_classify_blocks(blocks, page_number, para_offset) -> list[ParagraphInfo]`
Fallback when Gemini is unavailable. Uses existing heuristics: ALL CAPS = heading, x-position = column detection, everything else = paragraph.

## Rewritten `_process_single_page()`

```
1. Tesseract extracts blocks
   - If no blocks → page fails

2. In parallel:
   a. Gemini classifies structure
   b. Haiku corrects text

3. Apply Haiku corrections to blocks

4. If Gemini succeeded:
   → merge corrected blocks + Gemini structure → return

5. If Gemini failed:
   → heuristic classification of corrected blocks → return (degraded)
```

## Fallback Behavior

| Tesseract | Gemini | Haiku | Result |
|-----------|--------|-------|--------|
| OK | OK | OK | Best: corrected text + full structure |
| OK | OK | Fail | Good: uncorrected text + full structure |
| OK | Fail | OK | Decent: corrected text + heuristic structure |
| OK | Fail | Fail | Baseline: uncorrected text + heuristic structure |
| Fail | — | — | Page fails (same as today) |

Every failure mode is graceful. Worst case equals today's Tesseract-only fallback.

## What Gets Removed

- `_process_ocr_batch()` — no longer sending pages to Gemini for text extraction
- The Gemini→Gemini HD→crops→Tesseract retry chain in `_process_single_page()`
- Current `OCR_PAGE_SCHEMA` — replaced by structure annotation schema
- `_is_garbled_text()` / `_find_garbled_pages()` — not relevant with Tesseract-first
- Half-page crop logic
- `_integrate_page_data()` — replaced by `_merge_blocks_and_structure()`

## What Stays

- `process_scanned_pages()` — unchanged public interface
- `_stitch_page_results()` — still merges per-page results with sequential IDs
- `_rescue_missed_tables()` / table rescue pipeline — safety net for tables Gemini misses
- `_is_leaked_header_footer()` — pre-filter before sending to Gemini + fallback heuristic
- `_sort_regions_by_column()` — used in fallback mode
- `ScannedPageResult`, `PageOCRResult` dataclasses — unchanged
- `_regions_to_model_objects()` — partially reusable in merge function
- `_extract_usage()` — still needed for Gemini usage tracking

## Orchestrator Impact

Zero. `process_scanned_pages()` still returns `ScannedPageResult`. The hybrid architecture is entirely internal to `scanned_page_ocr.py`.

## New Dependencies

- `anthropic` SDK — already in the project, used by strategy/execution/review
- No new packages needed

## New Environment Variables

None. `ANTHROPIC_API_KEY` already exists.

## Cost Estimate

Per scanned page (assuming ~2K tokens of Tesseract text per page):

| Model | Input | Output | Cost/page |
|-------|-------|--------|-----------|
| Gemini 2.5 Flash | ~3K tokens (image + blocks) | ~500 tokens | ~$0.0008 |
| Claude Haiku 4.5 | ~3K tokens (image + blocks) | ~200 tokens | ~$0.004 |
| **Total** | | | **~$0.005/page** |

vs current Gemini-only: ~$0.002/page. About 2.5x more but eliminates RECITATION losses.

For a typical 11-page scanned paper: ~$0.055 (was ~$0.022).

## Testing

- **Primary test document:** Mayer paper (fully scanned, 11 pages, tables, figures, two-column layout)
- **Success criteria:**
  - All 11 pages produce text (no RECITATION losses)
  - Tables extracted with correct headers/rows
  - Headings detected at correct levels
  - Text quality equal or better than current Gemini-only on non-RECITATION pages
  - Two-column reading order correct
- **Comparison:** Run both old and new pipelines on Mayer, diff the output
- **Fallback test:** Verify graceful degradation with GEMINI_API_KEY unset and ANTHROPIC_API_KEY unset
