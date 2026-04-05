# Page-by-Page OCR Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `process_scanned_pages()` to process one page at a time with clean per-page results, eliminating content duplication and loss from retry/fallback overlap.

**Architecture:** Replace the batch loop (~260 lines of nested retry logic) with a per-page pipeline: `_process_single_page()` encapsulates the Gemini→Gemini HD→Tesseract retry chain for one page, returning exactly one result. `_stitch_page_results()` merges all page results in order with sequential IDs. The main function becomes a simple loop + stitch.

**Tech Stack:** Python 3.11+, PyMuPDF (fitz), google-genai, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/tools/scanned_page_ocr.py` | Modify | Add `PageOCRResult`, `_process_single_page()`, `_stitch_page_results()`, rewrite `process_scanned_pages()` |
| `tests/test_scanned_ocr.py` | Modify | Add tests for new functions |

---

### Task 1: Add `PageOCRResult` dataclass and `_process_single_page()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write tests for `_process_single_page()`**

Add to `tests/test_scanned_ocr.py`. Add `PageOCRResult` and `_process_single_page` to imports from `src.tools.scanned_page_ocr`:

```python
from src.tools.scanned_page_ocr import (
    PageOCRResult,
    ScannedPageResult,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _process_single_page,
    _regions_to_model_objects,
    _relative_to_pt,
    _rescue_missed_tables,
    _sort_regions_by_column,
)


class TestProcessSinglePage:
    """Tests for per-page OCR with retry chain."""

    def test_gemini_success_returns_result(self):
        """Gemini succeeds on first try — returns gemini result."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "pages": [{
                "page_number": 1,
                "page_type": "text_dominant",
                "regions": [
                    {"type": "paragraph", "text": "Hello world.", "reading_order": 1},
                ],
            }]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        result = _process_single_page(
            mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
        )

        assert result.page_number == 0
        assert result.source == "gemini"
        assert len(result.paragraphs) >= 1
        assert result.paragraphs[0].text == "Hello world."

    def test_gemini_none_falls_to_tesseract(self):
        """Gemini returns None (RECITATION) — falls to Tesseract."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        with patch("src.tools.scanned_page_ocr._tesseract_fallback") as mock_tess:
            mock_tess.return_value = [
                ParagraphInfo(id="ocr_p_0", text="Tesseract text", style_name="Normal", page_number=0),
            ]
            result = _process_single_page(
                mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
            )

        assert result.source == "tesseract"
        assert len(result.paragraphs) == 1
        assert result.paragraphs[0].text == "Tesseract text"

    def test_all_fail_returns_empty(self):
        """Both Gemini and Tesseract fail — returns empty result with 'failed' source."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        with patch("src.tools.scanned_page_ocr._tesseract_fallback") as mock_tess:
            mock_tess.return_value = []
            result = _process_single_page(
                mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
            )

        assert result.source == "failed"
        assert len(result.paragraphs) == 0

    def test_result_dataclass_fields(self):
        result = PageOCRResult(page_number=3)
        assert result.page_number == 3
        assert result.paragraphs == []
        assert result.tables == []
        assert result.figures == []
        assert result.api_usage == []
        assert result.source == "failed"
        assert result.warnings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestProcessSinglePage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `PageOCRResult` and `_process_single_page()`**

Add `PageOCRResult` after `ScannedPageResult` (around line 58) in `src/tools/scanned_page_ocr.py`:

```python
@dataclass
class PageOCRResult:
    """Result of OCR processing for a single page."""
    page_number: int
    paragraphs: list[ParagraphInfo] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)
    figures: list[ImageInfo] = field(default_factory=list)
    api_usage: list[ApiUsage] = field(default_factory=list)
    source: str = "failed"  # "gemini", "gemini_hd", "tesseract", "failed"
    warnings: list[str] = field(default_factory=list)
```

Add `_process_single_page()` after `_integrate_page_data()` (around line 530). This function encapsulates the full retry chain for one page:

```python
DELAY_BETWEEN_PAGES = 5  # seconds between pages for rate limiting


def _process_single_page(
    client,
    model: str,
    doc: fitz.Document,
    page_number: int,
    prompt: str,
) -> PageOCRResult:
    """Process a single scanned page through Gemini OCR with retry chain.

    Tries: Gemini at 200 DPI → Gemini at 300 DPI (if garbled) → Tesseract.
    Returns exactly one result — never duplicates content.

    Args:
        client: google.genai.Client instance.
        model: Gemini model ID.
        doc: Open PyMuPDF document.
        page_number: 0-based page number.
        prompt: OCR prompt string.

    Returns:
        PageOCRResult with paragraphs, tables, figures from the best source.
    """
    result = PageOCRResult(page_number=page_number)

    # ── Try 1: Gemini at standard DPI ───────────────────────────
    try:
        batch_result = _process_ocr_batch(client, model, doc, [page_number], prompt, dpi=PAGE_DPI)

        if batch_result is not None:
            page_data_list, usage = batch_result
            if usage:
                result.api_usage.append(usage)

            if page_data_list:
                paras, tables, figures = _regions_to_model_objects(
                    page_data_list[0],
                    page_number=page_number,
                    para_offset=0,
                    table_offset=0,
                    img_offset=0,
                    pdf_doc=doc,
                )

                # Check for garbled output
                if paras and not _find_garbled_pages(paras):
                    result.paragraphs = paras
                    result.tables = tables
                    result.figures = figures
                    result.source = "gemini"
                    logger.debug("Page %d: Gemini success (%d paras, %d tables)",
                                 page_number + 1, len(paras), len(tables))
                    return result
                elif paras:
                    logger.warning("Page %d: garbled at %d DPI, retrying at %d DPI",
                                   page_number + 1, PAGE_DPI, PAGE_DPI_RETRY)
                # else: empty response, fall through
        else:
            logger.warning("Page %d: Gemini returned empty (likely RECITATION)",
                           page_number + 1)
    except Exception as e:
        logger.warning("Page %d: Gemini failed (%s)", page_number + 1, e)

    # ── Try 2: Gemini at high DPI ───────────────────────────────
    try:
        time.sleep(3)  # brief pause before retry
        batch_result = _process_ocr_batch(client, model, doc, [page_number], prompt, dpi=PAGE_DPI_RETRY)

        if batch_result is not None:
            page_data_list, usage = batch_result
            if usage:
                result.api_usage.append(usage)

            if page_data_list:
                paras, tables, figures = _regions_to_model_objects(
                    page_data_list[0],
                    page_number=page_number,
                    para_offset=0,
                    table_offset=0,
                    img_offset=0,
                    pdf_doc=doc,
                )

                if paras and not _find_garbled_pages(paras):
                    result.paragraphs = paras
                    result.tables = tables
                    result.figures = figures
                    result.source = "gemini_hd"
                    logger.debug("Page %d: Gemini HD success (%d paras, %d tables)",
                                 page_number + 1, len(paras), len(tables))
                    return result
                elif paras:
                    logger.warning("Page %d: still garbled at %d DPI", page_number + 1, PAGE_DPI_RETRY)
        else:
            logger.warning("Page %d: Gemini HD returned empty", page_number + 1)
    except Exception as e:
        logger.warning("Page %d: Gemini HD failed (%s)", page_number + 1, e)

    # ── Try 3: Tesseract fallback ───────────────────────────────
    logger.info("Page %d: falling back to Tesseract", page_number + 1)
    tess_paras = _tesseract_fallback(doc, page_number, 0)
    if tess_paras:
        result.paragraphs = tess_paras
        result.source = "tesseract"
        logger.info("Page %d: Tesseract extracted %d paragraphs", page_number + 1, len(tess_paras))
        return result

    # ── All failed ──────────────────────────────────────────────
    result.warnings.append(f"Page {page_number + 1}: all OCR methods failed")
    logger.warning("Page %d: all OCR methods failed", page_number + 1)
    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_scanned_ocr.py::TestProcessSinglePage -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Add PageOCRResult and _process_single_page() with retry chain"
```

---

### Task 2: Add `_stitch_page_results()` and rewrite `process_scanned_pages()`

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write tests for `_stitch_page_results()`**

Add `_stitch_page_results` to imports and add test class:

```python
from src.tools.scanned_page_ocr import (
    PageOCRResult,
    ScannedPageResult,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _process_single_page,
    _regions_to_model_objects,
    _relative_to_pt,
    _rescue_missed_tables,
    _sort_regions_by_column,
    _stitch_page_results,
)


class TestStitchPageResults:
    """Tests for merging per-page OCR results into a unified document."""

    def test_stitches_two_pages_in_order(self):
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Page 1 text", style_name="Normal", page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="gemini")
        page1.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Page 2 text", style_name="Normal", page_number=1),
        ]

        paras, tables, figures = _stitch_page_results([page0, page1])

        assert len(paras) == 2
        assert paras[0].text == "Page 1 text"
        assert paras[1].text == "Page 2 text"
        # IDs should be sequential
        assert paras[0].id == "ocr_p_0"
        assert paras[1].id == "ocr_p_1"

    def test_stitches_with_tables(self):
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.tables = [
            TableInfo(id="ocr_tbl_0", rows=[], row_count=0, col_count=0, page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="gemini")
        page1.tables = [
            TableInfo(id="ocr_tbl_0", rows=[], row_count=0, col_count=0, page_number=1),
        ]

        paras, tables, figures = _stitch_page_results([page0, page1])

        assert len(tables) == 2
        assert tables[0].id == "ocr_tbl_0"
        assert tables[1].id == "ocr_tbl_1"

    def test_empty_page_result_included(self):
        """Failed pages produce no content but don't break stitching."""
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Content", style_name="Normal", page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="failed")
        # Empty — no paragraphs, no tables

        paras, tables, figures = _stitch_page_results([page0, page1])
        assert len(paras) == 1

    def test_empty_list(self):
        paras, tables, figures = _stitch_page_results([])
        assert paras == []
        assert tables == []
        assert figures == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestStitchPageResults -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_stitch_page_results()`**

Add after `_process_single_page()` in `src/tools/scanned_page_ocr.py`:

```python
def _stitch_page_results(
    page_results: list[PageOCRResult],
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]:
    """Merge per-page OCR results into unified lists with sequential IDs.

    Each page's paragraphs/tables/figures get their IDs reassigned to be
    globally sequential: ocr_p_0, ocr_p_1, ..., ocr_tbl_0, ocr_tbl_1, etc.

    Args:
        page_results: List of PageOCRResult, one per page, in page order.

    Returns:
        (all_paragraphs, all_tables, all_figures) with sequential IDs.
    """
    all_paragraphs: list[ParagraphInfo] = []
    all_tables: list[TableInfo] = []
    all_figures: list[ImageInfo] = []

    para_idx = 0
    tbl_idx = 0
    fig_idx = 0

    for page_result in page_results:
        for para in page_result.paragraphs:
            all_paragraphs.append(para.model_copy(update={"id": f"ocr_p_{para_idx}"}))
            para_idx += 1

        for table in page_result.tables:
            all_tables.append(table.model_copy(update={"id": f"ocr_tbl_{tbl_idx}"}))
            tbl_idx += 1

        for figure in page_result.figures:
            all_figures.append(figure.model_copy(update={"id": f"ocr_img_{fig_idx}"}))
            fig_idx += 1

    return all_paragraphs, all_tables, all_figures
```

- [ ] **Step 4: Run stitch tests**

Run: `pytest tests/test_scanned_ocr.py::TestStitchPageResults -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Rewrite `process_scanned_pages()`**

Replace the entire function body (lines 158-430) with the clean per-page version. Keep the same function signature. The new implementation:

```python
def process_scanned_pages(
    pdf_path: str,
    scanned_page_numbers: list[int],
    course_context: str = "",
    model: str = "gemini-2.5-flash",
    on_progress: Callable[[str], None] | None = None,
) -> ScannedPageResult:
    """Process scanned PDF pages through Gemini for OCR + layout analysis.

    Processes each page individually with a clean retry chain:
    Gemini (200 DPI) → Gemini (300 DPI if garbled) → Tesseract fallback.
    Each page gets exactly one result — no duplication from retries.

    Args:
        pdf_path: Path to the original PDF file.
        scanned_page_numbers: 0-based page numbers identified as scanned.
        course_context: Course context string for the prompt.
        model: Gemini model ID.
        on_progress: Optional callback for progress updates.

    Returns:
        ScannedPageResult with extracted paragraphs, tables, and figure images.
    """
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return ScannedPageResult(success=False, error="GEMINI_API_KEY not set")

    if not scanned_page_numbers:
        return ScannedPageResult(success=True)

    if not Path(pdf_path).exists():
        return ScannedPageResult(success=False, error=f"PDF not found: {pdf_path}")

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return ScannedPageResult(success=False, error=f"Failed to init Gemini: {e}")

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return ScannedPageResult(success=False, error=f"Failed to open PDF: {e}")

    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{course_context}", course_context or "Not specified")

    # ── Process each page individually ──────────────────────────
    total_pages = len(scanned_page_numbers)
    page_results: list[PageOCRResult] = []

    for i, page_num in enumerate(scanned_page_numbers):
        if on_progress:
            on_progress(f"OCR page {i + 1}/{total_pages}: page {page_num + 1}")
        logger.info("OCR page %d/%d: page %d", i + 1, total_pages, page_num + 1)

        page_result = _process_single_page(client, model, doc, page_num, prompt)
        page_results.append(page_result)

        logger.info(
            "Page %d: %s → %d paragraphs, %d tables",
            page_num + 1, page_result.source,
            len(page_result.paragraphs), len(page_result.tables),
        )

        # Rate limit between pages
        if i + 1 < total_pages:
            time.sleep(DELAY_BETWEEN_PAGES)

    # ── Stitch page results ─────────────────────────────────────
    all_paragraphs, all_tables, all_figures = _stitch_page_results(page_results)

    # ── Table rescue on stitched result ─────────────────────────
    if all_paragraphs:
        rescued_paras, rescued_tables, rescue_usage = _rescue_missed_tables(
            all_paragraphs, all_tables, doc, client, model,
        )
        if len(rescued_tables) > len(all_tables):
            logger.info(
                "Table rescue: %d paragraphs removed, %d tables added",
                len(all_paragraphs) - len(rescued_paras),
                len(rescued_tables) - len(all_tables),
            )
            all_paragraphs = rescued_paras
            all_tables = rescued_tables

    doc.close()

    # ── Collect usage and warnings ──────────────────────────────
    all_usage: list[ApiUsage] = []
    all_warnings: list[str] = []
    pages_processed: list[int] = []

    for pr in page_results:
        all_usage.extend(pr.api_usage)
        all_warnings.extend(pr.warnings)
        if pr.source != "failed":
            pages_processed.append(pr.page_number)

    logger.info(
        "OCR complete: %d pages → %d paragraphs, %d tables, %d figures",
        len(pages_processed), len(all_paragraphs),
        len(all_tables), len(all_figures),
    )

    return ScannedPageResult(
        success=True,
        paragraphs=all_paragraphs,
        tables=all_tables,
        figures=all_figures,
        pages_processed=pages_processed,
        api_usage=all_usage,
        warnings=all_warnings,
    )
```

- [ ] **Step 6: Remove old batch constants**

Find and remove `PAGES_PER_BATCH = 2` (line 39) and `DELAY_BETWEEN_BATCHES = 10` (line 40). The new `DELAY_BETWEEN_PAGES = 5` was added in Task 1 alongside `_process_single_page`.

Keep `MAX_RETRIES` and `INITIAL_BACKOFF` — they're used by `_process_ocr_batch()` which still exists.

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All tests PASS

Run: `pytest tests/ -v --timeout=60`
Expected: All 891+ tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "Rewrite process_scanned_pages: per-page pipeline with clean stitching"
```

---

### Task 3: End-to-end validation

Requires Gemini API key and Mayer test document.

- [ ] **Step 1: Run Mayer document**

```bash
python3 scripts/test_batch.py --doc "7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional.pdf"
```

- [ ] **Step 2: Check for duplicated content**

```bash
# Count occurrences of the abstract — should be exactly 1
grep -c "This essay examines the role" "testdocs/output/7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional_accessible.html"
```

Expected: 1 (was 2 before the fix)

- [ ] **Step 3: Check visual QA findings**

```bash
cat testdocs/output/visual_qa_findings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Pages checked: {d[\"pages_checked\"]}')
print(f'Findings: {len(d[\"findings\"])}')
for f in d['findings']:
    print(f'  [{f[\"severity\"]}] p{f[\"page\"]}: {f[\"type\"]} - {f[\"description\"][:80]}')
"
```

Verify improvement over previous run.

- [ ] **Step 4: Check table count**

```bash
grep -c "<table>" "testdocs/output/7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional_accessible.html"
```

Should be 4-5 tables (Tables 1-4 plus possibly Table of Legacies).

- [ ] **Step 5: Open and visually inspect**

```bash
open "testdocs/output/7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional_accessible.html"
```

Check: no duplicated paragraphs, tables rendered as tables, all pages present.

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "Validate page-by-page OCR on Mayer document"
```
