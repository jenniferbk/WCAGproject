# Structure Classification — Hybrid OCR Pipeline

You are classifying the layout structure of a scanned academic document page to make it accessible to blind students using screen readers.

## Your Task

Tesseract has already extracted text blocks from this page. You will receive a JSON array of blocks, each with an `id`, `text`, and `bbox` (bounding box). Your job is to classify these blocks into structural regions using the page image as visual context.

**You must NOT reproduce the text content.** The text already exists in the blocks. Only classify structure.

## Tesseract Blocks

```json
{blocks_json}
```

## Region Types

Classify every block into exactly one of the following region types:

- **heading** — A heading, title, or section label. Set `heading_level` (1–6).
- **paragraph** — Body text, narrative prose, or any general text.
- **table** — A tabular data structure. Extract `table_data` (headers + rows). Leave `block_ids` empty — you provide content directly.
- **figure** — An image, diagram, chart, or photo. Provide `figure_description`. Leave `block_ids` empty — you provide content directly.
- **equation** — A mathematical formula or equation.
- **caption** — A caption for a figure or table (e.g., "Figure 1: …", "Table 2: …").
- **footnote** — Footnote or endnote text, typically smaller font at page bottom.
- **page_header** — Repeated header at the top of the page (e.g., chapter title, running head). **Exclude from output.**
- **page_footer** — Repeated footer at the bottom of the page (e.g., page numbers, course name). **Exclude from output.**

## Column Assignment

Use `column` to indicate the layout column for each region:

- `0` — Full-width content (single-column layout, or a block that spans both columns)
- `1` — Left column (in a two-column layout)
- `2` — Right column (in a two-column layout)

If the page is single-column, use `0` for all regions.

## Rules

1. **Every block ID must appear in exactly one region.** No orphaned blocks.
2. **page_header and page_footer regions must be omitted from the output** (do not include them in the `regions` array at all — just discard those block IDs).
3. **For table and figure regions, set `block_ids` to an empty array** — you supply the content via `table_data` or `figure_description` instead.
4. **Do not reproduce text** for heading, paragraph, equation, caption, or footnote regions. Those regions only need `block_ids`, `type`, `reading_order`, `column`, and optional formatting hints (`bold`, `italic`, `font_size_relative`, `heading_level`).
5. **`reading_order`** must be a sequential integer starting at 1, representing the correct reading order across all regions on the page (left-to-right, top-to-bottom, accounting for columns).
6. For two-column layouts, read all of column 1 before column 2.
7. If a block contains only a page number or running header/footer text, discard it (do not include in output).

## Output Format

Return a JSON object with a single key `regions` — an array of region objects. Each region object must include at minimum: `block_ids`, `type`, `reading_order`.
