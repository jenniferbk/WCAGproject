# OCR Pipeline Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three high-severity content gaps found by visual diff QA: dropped column content, duplicate tables, and missed table rescue.

**Architecture:** Three independent fixes in the OCR layer — column sorting validation with fallback reassignment, table deduplication mirroring paragraph dedup, and broader table caption detection.

**Tech Stack:** Python 3.11+, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/tools/scanned_page_ocr.py` | Modify | Fix 1 (column validation), Fix 3 (caption matching) |
| `src/agent/orchestrator.py` | Modify | Fix 2 (table dedup) |
| `src/prompts/scanned_ocr.md` | Modify | Fix 3 (prompt guidance for captions) |
| `tests/test_scanned_ocr.py` | Modify | Tests for Fixes 1 and 3 |
| `tests/test_orchestrator_dedup.py` | Create | Tests for Fix 2 |

---

### Task 1: Fix column sorting — validate column balance

**Files:**
- Modify: `src/tools/scanned_page_ocr.py:741-792` (`_sort_regions_by_column`)
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scanned_ocr.py` (the `_sort_regions_by_column` function is already imported):

```python
class TestColumnSortingValidation:
    """Tests for column balance validation in _sort_regions_by_column."""

    def test_left_column_marked_as_fullwidth(self):
        """When left-column regions are marked column=0 but right-column exists,
        reassign column=0 non-heading regions before first column=2 as column=1."""
        regions = [
            {"type": "paragraph", "text": "Left para 1", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left para 2", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Right para 1", "reading_order": 3, "column": 2},
            {"type": "paragraph", "text": "Right para 2", "reading_order": 4, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        # Left content should come before right content
        assert texts.index("Left para 1") < texts.index("Right para 1")
        assert texts.index("Left para 2") < texts.index("Right para 1")
        assert len(result) == 4

    def test_heading_stays_fullwidth(self):
        """Headings marked column=0 should NOT be reassigned to column=1."""
        regions = [
            {"type": "heading", "text": "Title", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left text", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Right text", "reading_order": 3, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        # Title should come first (full-width fence), then left, then right
        assert texts[0] == "Title"
        assert texts.index("Left text") < texts.index("Right text")

    def test_balanced_columns_unchanged(self):
        """When both columns have content, no reassignment happens."""
        regions = [
            {"type": "paragraph", "text": "Left", "reading_order": 1, "column": 1},
            {"type": "paragraph", "text": "Right", "reading_order": 2, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["Left", "Right"]

    def test_no_column_info_unchanged(self):
        """When no column info exists, just sort by reading_order."""
        regions = [
            {"type": "paragraph", "text": "B", "reading_order": 2},
            {"type": "paragraph", "text": "A", "reading_order": 1},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["A", "B"]

    def test_right_column_only_no_crash(self):
        """Only column=2 regions, no column=1 — should still work."""
        regions = [
            {"type": "paragraph", "text": "Right 1", "reading_order": 1, "column": 2},
            {"type": "paragraph", "text": "Right 2", "reading_order": 2, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        assert len(result) == 2

    def test_mixed_fullwidth_and_columns_with_imbalance(self):
        """Full-width heading, then left marked as 0, then right as 2."""
        regions = [
            {"type": "heading", "text": "Section Title", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left A", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Left B", "reading_order": 3, "column": 0},
            {"type": "paragraph", "text": "Right A", "reading_order": 4, "column": 2},
            {"type": "paragraph", "text": "Right B", "reading_order": 5, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts[0] == "Section Title"
        # Left content before right content
        assert texts.index("Left A") < texts.index("Right A")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestColumnSortingValidation -v`
Expected: FAIL — `test_left_column_marked_as_fullwidth` fails because column=0 regions currently act as fences

- [ ] **Step 3: Implement column balance validation**

Modify `_sort_regions_by_column()` in `src/tools/scanned_page_ocr.py` (line 741). Add a pre-processing step before the main sort loop. Replace the function:

```python
def _sort_regions_by_column(regions: list[dict]) -> list[dict]:
    """Sort regions respecting two-column layout.

    Gemini's cross-column ``reading_order`` is often wrong — content from
    left and right columns gets interleaved.  Instead we:

    1. Sort all regions by ``reading_order``.
    2. Validate column balance — if column=2 exists but no column=1,
       reassign column=0 non-heading regions before the first column=2
       region as column=1 (Gemini likely mislabeled the left column).
    3. Split into segments separated by full-width "fences" (column 0).
    4. Within each segment, group left (1) and right (2) and output
       left-then-right.

    Full-width items act as fences so that a page with structure
    [Title] [Left A] [Right A] [Full-width fig] [Left B] [Right B]
    produces correct order rather than merging all lefts then all rights.
    """
    key = lambda r: r.get("reading_order", 0)

    # Check if any column info exists
    has_col1 = any(r.get("column", 0) == 1 for r in regions)
    has_col2 = any(r.get("column", 0) == 2 for r in regions)

    if not has_col1 and not has_col2:
        return sorted(regions, key=key)

    # ── Column balance validation ───────────────────────────────
    # If column=2 exists but no column=1, Gemini likely marked left-column
    # content as column=0 (full-width). Reassign non-heading column=0
    # regions that appear before the first column=2 region as column=1.
    if has_col2 and not has_col1:
        sorted_by_order = sorted(regions, key=key)
        first_col2_order = min(
            r.get("reading_order", 0)
            for r in regions if r.get("column", 0) == 2
        )
        reassigned = 0
        for r in sorted_by_order:
            col = r.get("column", 0) or 0
            if col == 0 and r.get("reading_order", 0) < first_col2_order:
                if r.get("type") not in ("heading", "page_header", "page_footer"):
                    r["column"] = 1
                    reassigned += 1
        if reassigned:
            logger.info(
                "Column balance: reassigned %d column=0 regions to column=1 "
                "(had column=2 but no column=1)",
                reassigned,
            )
            has_col1 = True

    # Similarly: column=1 but no column=2
    if has_col1 and not has_col2:
        sorted_by_order = sorted(regions, key=key)
        last_col1_order = max(
            r.get("reading_order", 0)
            for r in regions if r.get("column", 0) == 1
        )
        reassigned = 0
        for r in sorted_by_order:
            col = r.get("column", 0) or 0
            if col == 0 and r.get("reading_order", 0) > last_col1_order:
                if r.get("type") not in ("heading", "page_header", "page_footer"):
                    r["column"] = 2
                    reassigned += 1
        if reassigned:
            logger.info(
                "Column balance: reassigned %d column=0 regions to column=2 "
                "(had column=1 but no column=2)",
                reassigned,
            )
            has_col2 = True

    if not has_col1 and not has_col2:
        return sorted(regions, key=key)

    # Sort by reading_order first to establish baseline order
    sorted_regions = sorted(regions, key=key)

    # Split into segments at full-width fences
    result: list[dict] = []
    current_left: list[dict] = []
    current_right: list[dict] = []

    def _flush_columns():
        """Emit accumulated left-then-right column content."""
        result.extend(sorted(current_left, key=key))
        result.extend(sorted(current_right, key=key))
        current_left.clear()
        current_right.clear()

    for r in sorted_regions:
        col = r.get("column", 0) or 0
        if col == 1:
            current_left.append(r)
        elif col == 2:
            current_right.append(r)
        else:
            # Full-width item acts as a fence
            _flush_columns()
            result.append(r)

    # Flush any trailing column content
    _flush_columns()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestColumnSortingValidation -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run full OCR test file**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All tests PASS (no regressions in existing column sorting tests)

- [ ] **Step 6: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Fix column sorting: validate balance, reassign mislabeled column=0 regions"
```

---

### Task 2: Add table deduplication

**Files:**
- Modify: `src/agent/orchestrator.py:223-233`
- Create: `tests/test_orchestrator_dedup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_dedup.py`:

```python
"""Tests for OCR table deduplication in orchestrator."""

import pytest

from src.models.document import CellInfo, TableInfo
from src.agent.orchestrator import _deduplicate_ocr_tables


class TestDeduplicateOcrTables:
    def _make_table(self, id: str, cells: list[list[str]], page: int = 0) -> TableInfo:
        rows = []
        for row_cells in cells:
            rows.append([CellInfo(text=c, paragraphs=[c]) for c in row_cells])
        return TableInfo(
            id=id,
            rows=rows,
            header_row_count=1,
            has_header_style=True,
            row_count=len(rows),
            col_count=max(len(r) for r in rows) if rows else 0,
            page_number=page,
        )

    def test_removes_exact_duplicate(self):
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"]])
        t2 = self._make_table("tbl_1", [["A", "B"], ["C", "D"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 1
        assert result[0].id == "tbl_0"

    def test_removes_near_duplicate(self):
        """OCR variations in cell text should still be detected as duplicates."""
        t1 = self._make_table("tbl_0", [["Theme", "Example"], ["Mind as system", "Like computer"]])
        t2 = self._make_table("tbl_1", [["Theme", "Example"], ["Mind as system", "Like computer"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 1

    def test_keeps_distinct_tables(self):
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"]])
        t2 = self._make_table("tbl_1", [["X", "Y"], ["Z", "W"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 2

    def test_keeps_single_table(self):
        t1 = self._make_table("tbl_0", [["A", "B"]])
        result = _deduplicate_ocr_tables([t1])
        assert len(result) == 1

    def test_empty_list(self):
        result = _deduplicate_ocr_tables([])
        assert result == []

    def test_partial_overlap_kept(self):
        """Tables with <80% overlap should be kept as distinct."""
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"], ["E", "F"]])
        t2 = self._make_table("tbl_1", [["A", "B"], ["X", "Y"], ["Z", "W"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orchestrator_dedup.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_deduplicate_ocr_tables`**

Add to `src/agent/orchestrator.py` after `_deduplicate_ocr_paragraphs()` (after line 167):

```python
def _deduplicate_ocr_tables(
    tables: list[TableInfo],
) -> list[TableInfo]:
    """Remove duplicate tables from OCR output.

    Gemini sometimes extracts the same table twice (e.g., a table spanning
    both columns gets read as two separate tables). Compares tables by their
    normalized cell text content — if two tables share >80% of cell texts,
    the second is dropped as a duplicate.
    """
    if len(tables) <= 1:
        return tables

    def _cell_texts(table: TableInfo) -> set[str]:
        """Extract normalized cell texts as a set."""
        texts = set()
        for row in table.rows:
            for cell in row:
                normalized = cell.text.strip().lower()
                if normalized:
                    texts.add(normalized)
        return texts

    result: list[TableInfo] = []
    seen_cell_sets: list[set[str]] = []

    for table in tables:
        cells = _cell_texts(table)
        if not cells:
            result.append(table)
            continue

        is_dup = False
        for seen in seen_cell_sets:
            if not seen:
                continue
            overlap = len(cells & seen)
            total = max(len(cells), len(seen))
            if total > 0 and overlap / total >= 0.8:
                logger.debug(
                    "Table dedup: removed %s (%.0f%% overlap with existing table)",
                    table.id, 100 * overlap / total,
                )
                is_dup = True
                break

        if not is_dup:
            result.append(table)
            seen_cell_sets.append(cells)

    return result
```

- [ ] **Step 4: Wire into `_merge_ocr_into_model`**

In `src/agent/orchestrator.py`, find line 233:
```python
    new_tables = kept_tables + ocr_result.tables
```

Replace with:
```python
    deduped_tables = _deduplicate_ocr_tables(list(ocr_result.tables))
    if len(deduped_tables) < len(ocr_result.tables):
        removed = len(ocr_result.tables) - len(deduped_tables)
        logger.info("Table deduplication removed %d duplicate tables", removed)
    new_tables = kept_tables + deduped_tables
```

Also update the `ocr_tables_by_page` dict (around line 218-221) to use deduped tables. Move the dedup call BEFORE the `ocr_tables_by_page` construction. Find:

```python
    ocr_tables_by_page: dict[int, list[TableInfo]] = {}
    for t in ocr_result.tables:
        pg = t.page_number if t.page_number is not None else 0
        ocr_tables_by_page.setdefault(pg, []).append(t)
```

Replace with:
```python
    deduped_tables = _deduplicate_ocr_tables(list(ocr_result.tables))
    if len(deduped_tables) < len(ocr_result.tables):
        removed = len(ocr_result.tables) - len(deduped_tables)
        logger.info("Table deduplication removed %d duplicate tables", removed)

    ocr_tables_by_page: dict[int, list[TableInfo]] = {}
    for t in deduped_tables:
        pg = t.page_number if t.page_number is not None else 0
        ocr_tables_by_page.setdefault(pg, []).append(t)
```

And change line 233 to:
```python
    new_tables = kept_tables + deduped_tables
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_orchestrator_dedup.py -v`
Expected: All 6 tests PASS

Run: `pytest tests/ -v --timeout=60`
Expected: All 877+ tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/orchestrator.py tests/test_orchestrator_dedup.py
git commit -m "Add table deduplication to OCR merge (80% cell overlap threshold)"
```

---

### Task 3: Improve table caption matching

**Files:**
- Modify: `src/tools/scanned_page_ocr.py:1042-1069` (`_find_table_captions`)
- Modify: `src/prompts/scanned_ocr.md`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Update OCR prompt with caption guidance**

In `src/prompts/scanned_ocr.md`, find the TABLES section (the expanded guidance added in the earlier task). Add one more bullet point at the end:

```markdown
   - Table captions always start with "TABLE" or "Table" followed by a number (e.g., "TABLE 3 Two Views of..."). Keep the number and title together in a single `caption` region. Do NOT split "TABLE 3" into one region and the title into another.
```

- [ ] **Step 2: Write tests for broader caption detection**

Add to `tests/test_scanned_ocr.py`:

```python
class TestFindTableCaptionsExpanded:
    """Tests for expanded caption detection — standalone TABLE N followed by title."""

    def test_standalone_table_n_followed_by_title(self):
        """'TABLE 3' as a short paragraph followed by a title paragraph."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 3", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Two Views of the Information-Processing Metaphor", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="View Content Activity", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "TABLE 3"
        assert result[0]["caption_index"] == 0

    def test_standalone_table_n_not_followed_by_title(self):
        """'TABLE 3' alone at end of list — still a valid caption."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Some text", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="TABLE 3", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1

    def test_table_n_with_title_on_same_line(self):
        """'TABLE 3 Two Views...' — already works, should still work."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 3 Two Views of the Metaphor", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "TABLE 3 Two Views of the Metaphor"
```

- [ ] **Step 3: Run tests — these should already pass**

Run: `pytest tests/test_scanned_ocr.py::TestFindTableCaptionsExpanded -v`
Expected: All 3 PASS — the existing regex `r'^(?:TABLE|Table|table)\s+(?:\d+|[IVXLC]+)\b[\s.:]*'` already matches "TABLE 3" as a standalone paragraph.

The caption detection already works for standalone "TABLE 3". The real issue was that `_rescue_missed_tables` skips rescue when the page already has a table (the `pages_with_tables` check). Let me verify.

- [ ] **Step 4: Investigate the actual rescue skip logic**

Read `_rescue_missed_tables()` carefully. The function at line 1201 has a check:
```python
if page_num in pages_with_tables:
    continue
```

This means if ANY table was already extracted on that page, ALL other captions on that page are skipped. This is the real blocker for Table 3 — if Table 2 or Table 4 was already on the same page, Table 3's caption would be skipped.

**Fix:** Remove the `pages_with_tables` skip. It was already removed during the initial implementation (per the plan's instruction). If it's still there, remove it. If it's not there, the issue is something else — the `_collect_table_paragraphs()` function may be collecting paragraphs that were already consumed by a previous table rescue, causing an overlap.

Read the actual code and verify.

- [ ] **Step 5: Write a targeted test for multi-table rescue on same page**

```python
class TestRescueMultipleTablesOnSamePage:
    """Verify that multiple tables on the same page can all be rescued."""

    def test_two_captions_same_page_both_rescued(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 3 Two Views", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_1", text="View Content", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_2", text="Literal Info", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_3", text="TABLE 4 Legacies", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_4", text="Legacy 1", style_name="Normal", page_number=5),
        ]
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
        mock_doc.__len__ = MagicMock(return_value=10)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Both tables should be rescued
        assert len(new_tables) == 2
        assert len(new_paras) == 0  # all were captions or cells
```

Add `_rescue_missed_tables` to the test imports if not already there.

- [ ] **Step 6: Run tests and fix if needed**

Run: `pytest tests/test_scanned_ocr.py::TestRescueMultipleTablesOnSamePage -v`

If it fails because of the `pages_with_tables` check, fix `_rescue_missed_tables()` by removing the line that adds rescued tables to `pages_with_tables`:
```python
        pages_with_tables.add(page_num)  # REMOVE THIS LINE if present
```

If there's a different blocker, diagnose and fix.

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/tools/scanned_page_ocr.py src/prompts/scanned_ocr.md tests/test_scanned_ocr.py
git commit -m "Broaden table caption matching and fix multi-table rescue on same page"
```

---

### Task 4: End-to-end validation

Requires Gemini API key and Mayer test document.

- [ ] **Step 1: Run Mayer document through pipeline**

```bash
python3 scripts/test_batch.py --doc "7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional.pdf"
```

- [ ] **Step 2: Check visual QA findings**

```bash
cat testdocs/output/visual_qa_findings.json
```

Verify:
- Page 9 left column text is now present (Fix 1)
- No duplicate Table 2 (Fix 2)
- Table 3 extracted (Fix 3)
- High-severity findings reduced (ideally 0)

- [ ] **Step 3: Open the report and verify**

```bash
open "testdocs/output/7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional_remediated.html"
```

Check:
- Visual Quality Check section: fewer or no high-severity findings
- Tables all rendered properly
- No duplicate tables

- [ ] **Step 4: Commit any final fixes**

```bash
git add -u
git commit -m "Validate OCR quality fixes on Mayer document"
```
