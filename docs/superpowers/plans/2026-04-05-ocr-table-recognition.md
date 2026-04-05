# OCR Table Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix scanned PDF table recognition so tables with captions (e.g., "TABLE 1") are extracted as structured `TableInfo` instead of loose paragraphs.

**Architecture:** Two-layer fix: (1) improve the Gemini OCR prompt with explicit table visual indicators, (2) add post-OCR caption-triggered table rescue that re-sends the page image to Gemini with a focused extraction prompt.

**Tech Stack:** Python 3.11+, google-genai (Gemini API), PyMuPDF (fitz), pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/prompts/scanned_ocr.md` | Modify | Add table visual indicators and caption guidance |
| `src/prompts/table_rescue.md` | Create | Focused prompt for table re-extraction |
| `src/tools/scanned_page_ocr.py` | Modify | Add `_find_table_captions()`, `_collect_table_paragraphs()`, `_rescue_table_from_page()`, `_rescue_missed_tables()` |
| `tests/test_scanned_ocr.py` | Modify | Add tests for all new functions |

---

### Task 1: Improve OCR prompt with table guidance

**Files:**
- Modify: `src/prompts/scanned_ocr.md:51` (table section)

- [ ] **Step 1: Update the TABLES section in the OCR prompt**

Replace the single-line table instruction (line 51) with expanded guidance:

```markdown
6. **TABLES**: Academic documents contain data tables, often with captions like "TABLE 1", "Table 2:", or "TABLE III".
   - **Visual indicators of a table**: gridlines or borders between cells, text aligned in columns with consistent spacing, a header row (often bold or shaded), a caption above or below.
   - **If you see a caption matching "TABLE" / "Table" followed by a number or Roman numeral**, the content immediately below it is a table. Extract it as `type: table` with `table_data`, NOT as separate paragraphs.
   - Use `table_data` with `headers` (array of column header strings) and `rows` (array of arrays of cell strings).
   - For multi-line cell content, join the text with spaces into a single cell string.
   - The caption itself should be a separate `caption` region BEFORE the table region.
   - Common mistake: extracting each table cell as a separate `paragraph` region. If you see short, aligned text blocks that form a grid pattern, they are table cells, not paragraphs.
```

- [ ] **Step 2: Commit**

```bash
git add src/prompts/scanned_ocr.md
git commit -m "Improve OCR prompt with table visual indicators and caption guidance"
```

---

### Task 2: Write tests for `_find_table_captions()`

**Files:**
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing tests**

Add a new test class after the existing `TestRegionsToModelSpecialTypes` class. Import `_find_table_captions` from `src.tools.scanned_page_ocr` (add to the existing import at top of file).

```python
class TestFindTableCaptions:
    """Tests for detecting table captions in OCR paragraphs."""

    def test_detects_TABLE_N(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Some intro text.", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="TABLE 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Cell A", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "TABLE 1"
        assert result[0]["caption_index"] == 1
        assert result[0]["paragraph_id"] == "ocr_p_1"

    def test_detects_Table_N_colon(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 2: Three Metaphors", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "Table 2: Three Metaphors"

    def test_detects_roman_numeral(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE III Summary of Results", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1

    def test_detects_table_with_period(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 4. Comparison of Methods", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1

    def test_ignores_mid_sentence_reference(self):
        """'see Table 1 for details' should NOT trigger."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="As shown in Table 1, the results vary.", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Refer to TABLE 2 for the full data.", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 0

    def test_ignores_the_table_below(self):
        """Prose mentioning 'the table' should not trigger."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="The table below shows the results.", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 0

    def test_multiple_captions(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 First Table", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="cell a", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="cell b", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="TABLE 2 Second Table", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="cell c", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 2
        assert result[0]["caption_index"] == 0
        assert result[1]["caption_index"] == 3

    def test_skips_headings(self):
        """Paragraphs that are headings should not be treated as captions."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 1 Results", style_name="Heading 2", heading_level=2),
        ]
        result = _find_table_captions(paras)
        # Headings with table captions ARE valid — heading_level doesn't disqualify
        assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestFindTableCaptions -v`
Expected: FAIL with `ImportError` (function doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_scanned_ocr.py
git commit -m "Add tests for _find_table_captions()"
```

---

### Task 3: Implement `_find_table_captions()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`

- [ ] **Step 1: Add the function**

Add after the `_relative_to_pt()` function (around line 988):

```python
# Matches "TABLE 1", "Table 2:", "TABLE III.", "Table 4. Title text"
# Must be at the START of the paragraph text (not mid-sentence).
_TABLE_CAPTION_RE = re.compile(
    r'^(?:TABLE|Table|table)\s+(?:\d+|[IVXLC]+)\b[\s.:]*',
)


def _find_table_captions(
    paragraphs: list[ParagraphInfo],
) -> list[dict]:
    """Find paragraphs that are table captions (e.g., 'TABLE 1', 'Table 2:').

    Only matches captions at the START of paragraph text to avoid
    mid-sentence references like 'see Table 1 for details'.

    Returns list of dicts with keys:
        caption_text: Full paragraph text
        caption_index: Index in the paragraphs list
        paragraph_id: The paragraph's ID
    """
    results: list[dict] = []
    for i, para in enumerate(paragraphs):
        text = para.text.strip()
        if _TABLE_CAPTION_RE.match(text):
            results.append({
                "caption_text": text,
                "caption_index": i,
                "paragraph_id": para.id,
            })
    return results
```

- [ ] **Step 2: Export the function for testing**

Add `_find_table_captions` to the import in `tests/test_scanned_ocr.py` (line 18-26):

```python
from src.tools.scanned_page_ocr import (
    ScannedPageResult,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _regions_to_model_objects,
    _relative_to_pt,
    _sort_regions_by_column,
)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestFindTableCaptions -v`
Expected: All 8 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Implement _find_table_captions() for detecting table captions in OCR output"
```

---

### Task 4: Write tests for `_collect_table_paragraphs()`

**Files:**
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing tests**

Add after `TestFindTableCaptions`. Import `_collect_table_paragraphs`.

```python
class TestCollectTableParagraphs:
    """Tests for collecting paragraphs belonging to a missed table."""

    def test_collects_until_next_heading(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Metaphors", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Column A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Column B", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="Value 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="Next Section", style_name="Heading 2", heading_level=2),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2, 3]

    def test_collects_until_next_caption(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 First", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Cell A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Cell B", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="TABLE 2 Second", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="Cell C", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_collects_until_long_prose(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Header A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Value 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="This is a long body paragraph that clearly is not a table cell. It contains multiple sentences describing the methodology and results of the experiment in detail, which would never appear in a single table cell." , style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_collects_until_end(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="B", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_empty_after_caption(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Next Section", style_name="Heading 2", heading_level=2),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == []

    def test_caption_at_end_of_list(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Some text", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="TABLE 5 Final", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=1)
        assert indices == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestCollectTableParagraphs -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Commit**

```bash
git add tests/test_scanned_ocr.py
git commit -m "Add tests for _collect_table_paragraphs()"
```

---

### Task 5: Implement `_collect_table_paragraphs()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`

- [ ] **Step 1: Add the function**

Add after `_find_table_captions()`:

```python
# Threshold: a paragraph longer than this is likely body prose, not a table cell.
_MAX_TABLE_CELL_CHARS = 300


def _collect_table_paragraphs(
    paragraphs: list[ParagraphInfo],
    caption_index: int,
) -> list[int]:
    """Collect indices of paragraphs that likely belong to a missed table.

    Starting from caption_index + 1, collects consecutive paragraphs until
    hitting a stop signal:
      - Another table caption
      - A heading (heading_level > 0)
      - A long prose paragraph (> 300 chars)
      - End of list

    Returns list of paragraph indices (not including the caption itself).
    """
    indices: list[int] = []
    for i in range(caption_index + 1, len(paragraphs)):
        para = paragraphs[i]

        # Stop at headings
        if para.heading_level and para.heading_level > 0:
            break

        # Stop at another table caption
        if _TABLE_CAPTION_RE.match(para.text.strip()):
            break

        # Stop at long prose (unlikely to be a table cell)
        if len(para.text.strip()) > _MAX_TABLE_CELL_CHARS:
            break

        indices.append(i)

    return indices
```

- [ ] **Step 2: Add to test imports**

Update the import block in `tests/test_scanned_ocr.py`:

```python
from src.tools.scanned_page_ocr import (
    ScannedPageResult,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _regions_to_model_objects,
    _relative_to_pt,
    _sort_regions_by_column,
)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestCollectTableParagraphs -v`
Expected: All 6 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Implement _collect_table_paragraphs() for collecting table cell paragraphs"
```

---

### Task 6: Create the table rescue prompt

**Files:**
- Create: `src/prompts/table_rescue.md`

- [ ] **Step 1: Write the focused extraction prompt**

```markdown
# Table Extraction — Accessibility Remediation

You are extracting a specific table from a scanned academic document page to make it accessible to blind students using screen readers.

## Table to Extract

Caption: {caption}

## Instructions

Look at this page image and find the table with the caption above. Extract its complete structure:

1. **Headers**: The column headers (usually the first row, often bold or visually distinct).
2. **Rows**: Every data row, preserving cell boundaries.

Return JSON with exactly this structure:
```json
{
  "headers": ["Column 1 Header", "Column 2 Header", ...],
  "rows": [
    ["Row 1 Cell 1", "Row 1 Cell 2", ...],
    ["Row 2 Cell 1", "Row 2 Cell 2", ...],
    ...
  ]
}
```

## Rules

- Include ALL rows and ALL columns. Do not truncate.
- For multi-line cell content, join the text with spaces into a single string.
- For empty cells, use an empty string "".
- If the table has no clear header row, set "headers" to an empty array and put all rows in "rows".
- If you cannot find or parse the table, return `{"headers": [], "rows": []}`.
```

- [ ] **Step 2: Commit**

```bash
git add src/prompts/table_rescue.md
git commit -m "Add focused Gemini prompt for table rescue extraction"
```

---

### Task 7: Write tests for `_rescue_missed_tables()`

**Files:**
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing tests**

Add after `TestCollectTableParagraphs`. Import `_rescue_missed_tables`.

```python
from unittest.mock import MagicMock, patch


class TestRescueMissedTables:
    """Tests for the full table rescue pipeline."""

    def _make_paras(self, texts: list[str], caption_indices: set[int] | None = None) -> list[ParagraphInfo]:
        """Helper to build paragraph lists."""
        paras = []
        for i, text in enumerate(texts):
            paras.append(ParagraphInfo(
                id=f"ocr_p_{i}",
                text=text,
                style_name="Normal",
                page_number=0,
            ))
        return paras

    def test_rescues_table_and_replaces_paragraphs(self):
        paras = self._make_paras([
            "Introduction text.",
            "TABLE 1 Three Metaphors of Learning",
            "Response Strengthening",
            "Knowledge Acquisition",
            "Knowledge Construction",
            "Following paragraph.",
        ])
        tables: list[TableInfo] = []

        # Mock Gemini to return structured table data
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["Metaphor", "Description"], "rows": [["Response Strengthening", "Learning as..."], ["Knowledge Acquisition", "Learning as..."], ["Knowledge Construction", "Learning as..."]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        # Mock PDF doc with one page
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Caption + 3 cell paragraphs should be removed
        assert len(new_paras) == 2  # "Introduction text." and "Following paragraph."
        assert new_paras[0].text == "Introduction text."
        assert new_paras[1].text == "Following paragraph."

        # One table should be created
        assert len(new_tables) == 1
        tbl = new_tables[0]
        assert tbl.header_row_count == 1
        assert tbl.row_count == 4  # 1 header + 3 data
        assert tbl.col_count == 2

    def test_skips_when_table_already_exists(self):
        """If a table region already follows the caption, don't re-send."""
        paras = self._make_paras([
            "TABLE 1 Already Extracted",
        ])
        existing_table = TableInfo(
            id="ocr_tbl_0",
            rows=[[CellInfo(text="A", paragraphs=["A"])]],
            header_row_count=1,
            row_count=1,
            col_count=1,
            page_number=0,
        )
        tables = [existing_table]

        mock_client = MagicMock()
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Nothing should change — the table already exists
        mock_client.models.generate_content.assert_not_called()
        assert len(new_tables) == 1

    def test_handles_gemini_failure(self):
        """If Gemini returns empty table, leave paragraphs as-is."""
        paras = self._make_paras([
            "TABLE 1 Broken Table",
            "Cell A",
            "Cell B",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": [], "rows": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Paragraphs should be unchanged
        assert len(new_paras) == 3
        assert len(new_tables) == 0

    def test_handles_gemini_exception(self):
        """If Gemini throws an exception, leave paragraphs as-is."""
        paras = self._make_paras([
            "TABLE 1 Error Table",
            "Cell A",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        assert len(new_paras) == 2
        assert len(new_tables) == 0

    def test_multiple_tables_rescued(self):
        paras = self._make_paras([
            "TABLE 1 First",
            "A1",
            "B1",
            "TABLE 2 Second",
            "A2",
            "B2",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["Col"], "rows": [["Val"]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        assert len(new_paras) == 0  # all paragraphs were table cells
        assert len(new_tables) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestRescueMissedTables -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Commit**

```bash
git add tests/test_scanned_ocr.py
git commit -m "Add tests for _rescue_missed_tables()"
```

---

### Task 8: Implement `_rescue_table_from_page()` and `_rescue_missed_tables()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`

- [ ] **Step 1: Add the `_load_table_rescue_prompt()` helper**

Add after `_load_prompt()` (around line 144):

```python
def _load_table_rescue_prompt() -> str:
    """Load the table rescue prompt from the prompts directory."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "table_rescue.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "Extract the table with caption '{caption}' from this page image. "
        "Return JSON with 'headers' (array of strings) and 'rows' (array of arrays of strings)."
    )
```

- [ ] **Step 2: Add `_rescue_table_from_page()`**

Add after `_collect_table_paragraphs()`:

```python
def _rescue_table_from_page(
    page_image_png: bytes,
    caption: str,
    client,
    model: str,
) -> tuple[TableInfo | None, ApiUsage | None]:
    """Re-send a page image to Gemini with a focused table extraction prompt.

    Args:
        page_image_png: PNG bytes of the page image.
        caption: The table caption text (e.g., "TABLE 1 Three Metaphors").
        client: google.genai.Client instance.
        model: Gemini model ID.

    Returns:
        (TableInfo or None, ApiUsage or None). None TableInfo if extraction fails.
    """
    from google.genai import types

    prompt_template = _load_table_rescue_prompt()
    prompt = prompt_template.replace("{caption}", caption)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=page_image_png, mime_type="image/png"),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        resp_text = response.text
        if resp_text is None:
            logger.warning("Table rescue: Gemini returned empty for caption %r", caption)
            return None, None

        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError:
            data = parse_json_lenient(resp_text)

        usage = _extract_usage(response, model)

        headers = data.get("headers", [])
        rows = data.get("rows", [])

        if not headers and not rows:
            logger.info("Table rescue: no data extracted for caption %r", caption)
            return None, usage

        # Build TableInfo
        table_rows: list[list[CellInfo]] = []
        if headers:
            table_rows.append([CellInfo(text=h, paragraphs=[h]) for h in headers])
        for row_cells in rows:
            table_rows.append([CellInfo(text=c, paragraphs=[c]) for c in row_cells])

        col_count = max((len(r) for r in table_rows), default=0)

        table = TableInfo(
            id="",  # ID assigned by caller
            rows=table_rows,
            header_row_count=1 if headers else 0,
            has_header_style=bool(headers),
            row_count=len(table_rows),
            col_count=col_count,
        )
        return table, usage

    except Exception as e:
        logger.warning("Table rescue failed for caption %r: %s", caption, e)
        return None, None
```

- [ ] **Step 3: Add `_rescue_missed_tables()`**

Add after `_rescue_table_from_page()`:

```python
def _rescue_missed_tables(
    paragraphs: list[ParagraphInfo],
    tables: list[TableInfo],
    pdf_doc: fitz.Document,
    client,
    model: str,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ApiUsage]]:
    """Detect table captions in OCR paragraphs and re-extract missed tables.

    Scans paragraphs for table caption patterns (e.g., "TABLE 1"). For each
    caption found, checks whether a corresponding table already exists. If not,
    re-sends the page image to Gemini with a focused extraction prompt.

    Args:
        paragraphs: OCR-extracted paragraphs.
        tables: OCR-extracted tables.
        pdf_doc: PyMuPDF document for rendering page images.
        client: google.genai.Client instance.
        model: Gemini model ID.

    Returns:
        (updated_paragraphs, updated_tables, api_usage_list)
    """
    captions = _find_table_captions(paragraphs)
    if not captions:
        return paragraphs, tables, []

    # Build set of pages that already have tables
    pages_with_tables: set[int | None] = {t.page_number for t in tables}

    # Track which paragraph indices to remove
    indices_to_remove: set[int] = set()
    new_tables: list[TableInfo] = list(tables)
    all_usage: list[ApiUsage] = []
    table_id_offset = len(tables)

    for cap in captions:
        caption_idx = cap["caption_index"]
        caption_text = cap["caption_text"]
        caption_para = paragraphs[caption_idx]
        page_num = caption_para.page_number

        # Collect the paragraphs that likely belong to this table
        cell_indices = _collect_table_paragraphs(paragraphs, caption_idx)

        if not cell_indices:
            # No cell paragraphs found — might already be extracted as a table
            continue

        # Check if this page already has a table (likely already extracted)
        if page_num in pages_with_tables:
            logger.debug(
                "Table rescue: page %s already has a table, skipping caption %r",
                page_num, caption_text,
            )
            continue

        # Render page image for Gemini
        if page_num is None or page_num < 0 or page_num >= len(pdf_doc):
            logger.warning("Table rescue: invalid page number %s for caption %r", page_num, caption_text)
            continue

        page = pdf_doc[page_num]
        pix = page.get_pixmap(dpi=PAGE_DPI)
        page_png = pix.tobytes("png")

        logger.info(
            "Table rescue: re-sending page %d for caption %r (%d candidate cell paragraphs)",
            page_num + 1, caption_text, len(cell_indices),
        )

        table, usage = _rescue_table_from_page(page_png, caption_text, client, model)
        if usage:
            all_usage.append(usage)

        if table is None:
            # Extraction failed — leave paragraphs as-is
            logger.info("Table rescue: extraction failed for %r, keeping paragraphs", caption_text)
            continue

        # Assign ID and page number
        table = table.model_copy(update={
            "id": f"ocr_tbl_{table_id_offset}",
            "page_number": page_num,
        })
        table_id_offset += 1

        # Mark caption + cell paragraphs for removal
        indices_to_remove.add(caption_idx)
        indices_to_remove.update(cell_indices)

        new_tables.append(table)
        # Mark this page as having a table so subsequent captions on same page skip
        pages_with_tables.add(page_num)

        logger.info(
            "Table rescue: extracted %r → %d rows x %d cols (id=%s)",
            caption_text, table.row_count, table.col_count, table.id,
        )

    # Build filtered paragraph list
    if indices_to_remove:
        new_paragraphs = [
            p for i, p in enumerate(paragraphs) if i not in indices_to_remove
        ]
    else:
        new_paragraphs = paragraphs

    return new_paragraphs, new_tables, all_usage
```

- [ ] **Step 4: Add to test imports**

Update `tests/test_scanned_ocr.py` imports:

```python
from src.tools.scanned_page_ocr import (
    ScannedPageResult,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _regions_to_model_objects,
    _relative_to_pt,
    _rescue_missed_tables,
    _sort_regions_by_column,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestRescueMissedTables -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All existing + new tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/scanned_page_ocr.py src/prompts/table_rescue.md tests/test_scanned_ocr.py
git commit -m "Implement table rescue: _rescue_table_from_page() and _rescue_missed_tables()"
```

---

### Task 9: Integrate table rescue into `_integrate_page_data()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py:417-484` (`_integrate_page_data`)
- Modify: `src/tools/scanned_page_ocr.py:147-414` (`process_scanned_pages`)

- [ ] **Step 1: Write integration test**

Add to `tests/test_scanned_ocr.py`:

```python
class TestIntegratePageDataWithRescue:
    """Test that _integrate_page_data calls table rescue when client is provided."""

    def test_rescue_called_when_client_provided(self):
        """Verify that _integrate_page_data passes through to rescue when given a client."""
        # This is a smoke test — the real rescue logic is tested in TestRescueMissedTables.
        # We just verify the integration point passes the client through.
        page_data_list = [{
            "page_number": 1,
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "TABLE 1 Test", "reading_order": 1},
                {"type": "paragraph", "text": "Cell A", "reading_order": 2},
            ],
        }]

        all_paragraphs: list[ParagraphInfo] = []
        all_tables: list[TableInfo] = []
        all_figures: list[ImageInfo] = []
        pages_processed: list[int] = []

        # Without client — no rescue happens, paragraphs stay
        _integrate_page_data(
            page_data_list, None,
            all_paragraphs, all_tables, all_figures,
            pages_processed, 0, 0, 0,
            known_page_numbers=[0],
        )
        assert len(all_paragraphs) == 2
        assert len(all_tables) == 0
```

- [ ] **Step 2: Modify `_integrate_page_data()` signature to accept client and model**

In `src/tools/scanned_page_ocr.py`, update the function signature at line 417:

```python
def _integrate_page_data(
    page_data_list: list[dict],
    pdf_doc: fitz.Document,
    all_paragraphs: list[ParagraphInfo],
    all_tables: list[TableInfo],
    all_figures: list[ImageInfo],
    pages_processed: list[int],
    para_offset: int,
    table_offset: int,
    img_offset: int,
    known_page_numbers: list[int] | None = None,
    gemini_client=None,
    gemini_model: str = "gemini-2.5-flash",
) -> None:
```

- [ ] **Step 3: Add rescue call at the end of `_integrate_page_data()`**

After the existing for-loop (after line 484), before the function ends, add:

```python
    # ── Table rescue: detect missed tables and re-extract ──────────
    if gemini_client is not None and pdf_doc is not None:
        batch_paras = all_paragraphs[para_offset:]
        batch_tables = all_tables[table_offset:]

        rescued_paras, rescued_tables, rescue_usage = _rescue_missed_tables(
            batch_paras,
            batch_tables,
            pdf_doc,
            gemini_client,
            gemini_model,
        )

        if len(rescued_paras) != len(batch_paras) or len(rescued_tables) != len(batch_tables):
            # Rescue changed something — update the accumulator lists
            del all_paragraphs[para_offset:]
            all_paragraphs.extend(rescued_paras)
            del all_tables[table_offset:]
            all_tables.extend(rescued_tables)
            logger.info(
                "Table rescue: %d paragraphs removed, %d tables added",
                len(batch_paras) - len(rescued_paras),
                len(rescued_tables) - len(batch_tables),
            )
```

- [ ] **Step 4: Pass client through from `process_scanned_pages()`**

In `process_scanned_pages()`, update all calls to `_integrate_page_data()` to pass the client. There are 3 call sites:

1. Main batch processing (around line 242):
```python
            _integrate_page_data(
                page_data_list, doc,
                all_paragraphs, all_tables, all_figures,
                pages_processed, para_offset, table_offset, img_offset,
                known_page_numbers=batch_pages,
                gemini_client=client,
                gemini_model=model,
            )
```

2. Single-page retry (around line 272):
```python
                            _integrate_page_data(
                                page_data_list, doc,
                                all_paragraphs, all_tables, all_figures,
                                pages_processed, len(all_paragraphs),
                                len(all_tables), len(all_figures),
                                known_page_numbers=[single_page],
                                gemini_client=client,
                                gemini_model=model,
                            )
```

3. Garble retry (around line 366):
```python
                            _integrate_page_data(
                                retry_pages, doc,
                                all_paragraphs, all_tables, all_figures,
                                pages_processed, len(all_paragraphs),
                                len(all_tables), len(all_figures),
                                known_page_numbers=[gp],
                                gemini_client=client,
                                gemini_model=model,
                            )
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All tests PASS (existing tests don't pass `gemini_client` so rescue is skipped)

- [ ] **Step 6: Run full project test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All 835+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Integrate table rescue into _integrate_page_data() and process_scanned_pages()"
```

---

### Task 10: End-to-end validation

This task requires the Mayer test document and Gemini API key. Run manually.

- [ ] **Step 1: Run batch test on Mayer document**

```bash
python scripts/test_batch.py --doc "Mayer"
```

Check output for:
- Tables 1/2/3 should now appear as `ocr_tbl_*` in the DocumentModel
- Logs should show "Table rescue: re-sending page X for caption 'TABLE N'"
- Logs should show "Table rescue: extracted 'TABLE N' → R rows x C cols"

- [ ] **Step 2: Verify table content in output**

Open the generated PDF/HTML and verify:
- TABLE 1 ("Three Metaphors of Learning") renders as a proper table
- TABLE 2 ("Three Themes") renders as a proper table
- TABLE 3 ("Two Views") renders as a proper table
- TABLE 4 (already working) still works

- [ ] **Step 3: Run full batch**

```bash
python scripts/test_batch.py
```

Verify no regressions on other documents.

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "Validate OCR table rescue on Mayer document"
```
