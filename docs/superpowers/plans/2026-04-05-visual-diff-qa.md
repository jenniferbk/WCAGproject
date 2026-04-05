# Visual Diff QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect content gaps in scanned PDF remediation by comparing original page images against rendered HTML output via Gemini, and surface findings with side-by-side thumbnails in the compliance report.

**Architecture:** After execution (Phase 3.5), render original PDF pages and companion HTML (via WeasyPrint→PDF→PyMuPDF→PNG) to images, send batches to Gemini for content comparison, save findings with thumbnails for the report's "Visual Quality Check" section.

**Tech Stack:** Python 3.11+, PyMuPDF (fitz), WeasyPrint, google-genai (Gemini API), pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models/pipeline.py` | Modify | Add `VisualQAFinding` and `VisualQAResult` models |
| `src/prompts/visual_qa.md` | Create | Gemini comparison prompt |
| `src/tools/visual_qa.py` | Create | Render pages, call Gemini, collect findings, save PNGs |
| `src/tools/report_generator.py` | Modify | Add "Visual Quality Check" section with thumbnails |
| `src/agent/orchestrator.py` | Modify | Call visual QA after Phase 3, pass findings to report |
| `scripts/test_batch.py` | Modify | Aggregate visual QA findings across batch runs |
| `tests/test_visual_qa.py` | Create | Unit tests for rendering, comparison, and integration |

---

### Task 1: Add data models

**Files:**
- Modify: `src/models/pipeline.py` (after line 191, after `RemediationResult`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_visual_qa.py`:

```python
"""Tests for visual diff QA."""

import pytest

from src.models.pipeline import VisualQAFinding, VisualQAResult, ApiUsage


class TestVisualQAModels:
    def test_finding_creation(self):
        finding = VisualQAFinding(
            original_page=4,
            rendered_page=3,
            finding_type="missing_table",
            description="Table 2 appears truncated",
            severity="high",
        )
        assert finding.original_page == 4
        assert finding.rendered_page == 3
        assert finding.finding_type == "missing_table"
        assert finding.severity == "high"

    def test_finding_no_rendered_page(self):
        finding = VisualQAFinding(
            original_page=7,
            rendered_page=None,
            finding_type="dropped_image",
            description="Figure on page 8 not found in rendered output",
            severity="medium",
        )
        assert finding.rendered_page is None

    def test_result_defaults(self):
        result = VisualQAResult()
        assert result.findings == []
        assert result.pages_checked == 0
        assert result.api_usage == []

    def test_result_with_findings(self):
        finding = VisualQAFinding(
            original_page=0,
            rendered_page=0,
            finding_type="truncated_text",
            description="Paragraph cut off",
            severity="medium",
        )
        result = VisualQAResult(
            findings=[finding],
            pages_checked=5,
        )
        assert len(result.findings) == 1
        assert result.pages_checked == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_visual_qa.py::TestVisualQAModels -v`
Expected: FAIL with `ImportError` (models don't exist yet)

- [ ] **Step 3: Add models to pipeline.py**

Add after line 191 (after `RemediationResult` class) in `src/models/pipeline.py`:

```python
class VisualQAFinding(BaseModel, frozen=True):
    """A content gap detected by visual diff QA."""
    original_page: int              # 0-based original page number
    rendered_page: int | None       # 0-based rendered page (closest match), None if no match
    finding_type: str               # missing_table, truncated_text, dropped_image, garbled_equation, other
    description: str                # Human-readable description
    severity: str                   # high, medium, low


class VisualQAResult(BaseModel):
    """Result of visual diff QA phase."""
    findings: list[VisualQAFinding] = Field(default_factory=list)
    pages_checked: int = 0
    api_usage: list[ApiUsage] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visual_qa.py::TestVisualQAModels -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/models/pipeline.py tests/test_visual_qa.py
git commit -m "Add VisualQAFinding and VisualQAResult models"
```

---

### Task 2: Create the Gemini comparison prompt

**Files:**
- Create: `src/prompts/visual_qa.md`

- [ ] **Step 1: Write the prompt file**

```markdown
# Visual Quality Check — Accessibility Remediation

You are verifying that no educational content was lost during accessibility remediation of a scanned document.

## What You're Looking At

- **Original pages**: Scanned images from the original PDF document. These are labeled "Original page N".
- **Rendered pages**: Pages from the accessible HTML rendering, converted to PDF for comparison. These are labeled "Rendered page N". The rendered version may have a DIFFERENT number of pages than the original — that is expected.

## Your Task

Compare the original pages against the rendered pages. Identify any educational content present in the originals that is MISSING, TRUNCATED, GARBLED, or INCORRECTLY REPRESENTED in the rendered version.

## What to Ignore (Expected Differences)

These differences are intentional and should NOT be reported:
- Different layout, fonts, colors, spacing, margins
- Different page count or page breaks
- Different header/footer content
- Reordered content (if all content is present)
- Styling differences (bold, italic variations)

## What to Report

- **missing_table**: A table visible in the original is completely absent from the rendered version
- **truncated_text**: Text content from the original is cut off or incomplete in the rendered version
- **dropped_image**: A figure, chart, or diagram in the original has no corresponding content in the rendered version
- **garbled_equation**: Mathematical notation is incorrectly rendered or unreadable
- **other**: Any other educational content loss not covered above

## Severity Guide

- **high**: Entire content blocks missing (full table, full paragraph, figure with no description)
- **medium**: Partial content loss (truncated table rows, incomplete text, degraded equation)
- **low**: Minor differences that don't significantly impact comprehension

## Response Format

Return JSON:
```json
{
  "findings": [
    {
      "original_page": 5,
      "rendered_page": 4,
      "type": "missing_table",
      "description": "Table 2 (Three Themes) with 3 columns and 4 rows is not present in the rendered output",
      "severity": "high"
    }
  ]
}
```

If no content issues are found, return: `{"findings": []}`

For each finding, `original_page` is the 1-based page number from the original document. `rendered_page` is the 1-based page number in the rendered version where the content should appear (or the closest page). Set `rendered_page` to null if you cannot determine which rendered page corresponds.
```

- [ ] **Step 2: Commit**

```bash
git add src/prompts/visual_qa.md
git commit -m "Add Gemini prompt for visual diff QA comparison"
```

---

### Task 3: Implement page rendering functions

**Files:**
- Create: `src/tools/visual_qa.py`
- Modify: `tests/test_visual_qa.py`

- [ ] **Step 1: Write tests for render functions**

Add to `tests/test_visual_qa.py`:

```python
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.tools.visual_qa import render_original_pages, render_html_to_page_pngs


class TestRenderOriginalPages:
    def test_renders_specified_pages(self):
        """Test rendering specific pages from a PDF."""
        # Use a real test PDF if available, otherwise mock
        test_pdf = Path("tests/test_docs/simple_test.pdf")
        if not test_pdf.exists():
            pytest.skip("No test PDF available")

        result = render_original_pages(str(test_pdf), [0])
        assert 0 in result
        assert len(result[0]) > 100  # PNG bytes should be non-trivial
        # Verify it's a PNG (magic bytes)
        assert result[0][:4] == b'\x89PNG'

    def test_empty_page_list(self):
        result = render_original_pages("dummy.pdf", [])
        assert result == {}

    def test_renders_mock_pdf(self):
        """Test with a mocked PyMuPDF document."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b'\x89PNG\r\n\x1a\nfake_png_data'
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        with patch("src.tools.visual_qa.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc
            result = render_original_pages("test.pdf", [0, 2, 5])

        assert len(result) == 3
        assert 0 in result and 2 in result and 5 in result


class TestRenderHtmlToPagePngs:
    def test_renders_simple_html(self):
        """Test rendering a simple HTML file to page PNGs."""
        html_content = """<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body><h1>Test Document</h1><p>Hello world.</p></body>
</html>"""
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write(html_content)
            html_path = f.name

        try:
            result = render_html_to_page_pngs(html_path)
            assert len(result) >= 1
            assert result[0][:4] == b'\x89PNG'
        finally:
            os.unlink(html_path)

    def test_nonexistent_html_returns_empty(self):
        result = render_html_to_page_pngs("/nonexistent/path.html")
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visual_qa.py::TestRenderOriginalPages tests/test_visual_qa.py::TestRenderHtmlToPagePngs -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the render functions**

Create `src/tools/visual_qa.py`:

```python
"""Visual diff QA for scanned PDF remediation.

Compares original scanned PDF pages against the rendered HTML output
to detect content gaps (missing tables, dropped images, truncated text).
Uses Gemini vision for intelligent content comparison.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import fitz  # PyMuPDF

from src.models.pipeline import ApiUsage, VisualQAFinding, VisualQAResult
from src.utils.json_repair import parse_json_lenient

logger = logging.getLogger(__name__)

# Rendering resolution for page comparison
RENDER_DPI = 200


def render_original_pages(
    pdf_path: str,
    page_numbers: list[int],
) -> dict[int, bytes]:
    """Render specified pages from the original PDF to PNG.

    Args:
        pdf_path: Path to the original PDF file.
        page_numbers: 0-based page numbers to render.

    Returns:
        Dict mapping page_number → PNG bytes.
    """
    if not page_numbers:
        return {}

    result: dict[int, bytes] = {}
    try:
        doc = fitz.open(pdf_path)
        for page_num in page_numbers:
            if 0 <= page_num < len(doc):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=RENDER_DPI)
                result[page_num] = pix.tobytes("png")
        doc.close()
    except Exception as e:
        logger.warning("Failed to render original pages: %s", e)

    return result


def render_html_to_page_pngs(html_path: str) -> list[bytes]:
    """Render companion HTML to per-page PNG images.

    Two-step process using only existing dependencies:
    1. HTML → PDF via WeasyPrint
    2. PDF → per-page PNG via PyMuPDF

    Args:
        html_path: Path to the companion HTML file.

    Returns:
        List of PNG bytes, one per rendered page. Empty list on failure.
    """
    if not Path(html_path).exists():
        logger.warning("HTML file not found: %s", html_path)
        return []

    try:
        from weasyprint import HTML

        # Step 1: HTML → temporary PDF
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_pdf_path = tmp.name

        HTML(filename=html_path).write_pdf(tmp_pdf_path)

        # Step 2: PDF → per-page PNGs
        pages: list[bytes] = []
        doc = fitz.open(tmp_pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=RENDER_DPI)
            pages.append(pix.tobytes("png"))
        doc.close()

        # Clean up temp file
        Path(tmp_pdf_path).unlink(missing_ok=True)

        logger.info("Rendered HTML to %d page PNGs", len(pages))
        return pages

    except Exception as e:
        logger.warning("Failed to render HTML to PNGs: %s", e)
        Path(tmp_pdf_path).unlink(missing_ok=True) if 'tmp_pdf_path' in dir() else None
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visual_qa.py::TestRenderOriginalPages tests/test_visual_qa.py::TestRenderHtmlToPagePngs -v`
Expected: All 5 tests PASS (1 may skip if no test PDF)

- [ ] **Step 5: Commit**

```bash
git add src/tools/visual_qa.py tests/test_visual_qa.py
git commit -m "Implement page rendering for visual diff QA"
```

---

### Task 4: Implement Gemini comparison function

**Files:**
- Modify: `src/tools/visual_qa.py`
- Modify: `tests/test_visual_qa.py`

- [ ] **Step 1: Write tests for compare_pages**

Add to `tests/test_visual_qa.py`:

```python
from src.tools.visual_qa import compare_pages


class TestComparePages:
    def test_returns_findings_on_issues(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "findings": [
                {
                    "original_page": 5,
                    "rendered_page": 4,
                    "type": "missing_table",
                    "description": "Table 2 not found in rendered output",
                    "severity": "high",
                }
            ]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        original_pngs = {4: b"fake_png_page5"}
        rendered_pngs = [b"fake_png_rendered1", b"fake_png_rendered2"]

        findings, usage = compare_pages(
            original_pngs, rendered_pngs, mock_client, "gemini-2.5-flash",
        )

        assert len(findings) == 1
        assert findings[0].original_page == 4  # converted from 1-based to 0-based
        assert findings[0].rendered_page == 3  # converted from 1-based to 0-based
        assert findings[0].finding_type == "missing_table"
        assert findings[0].severity == "high"

    def test_returns_empty_on_no_issues(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"findings": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []

    def test_handles_gemini_exception(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []
        assert usage is None

    def test_handles_none_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []
```

Add `import json` to the top of the test file if not already there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visual_qa.py::TestComparePages -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement compare_pages**

Add to `src/tools/visual_qa.py` after the render functions:

```python
def _load_visual_qa_prompt() -> str:
    """Load the visual QA prompt from the prompts directory."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "visual_qa.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "Compare original scanned pages against rendered pages. "
        "Identify educational content that is missing, truncated, or garbled. "
        "Return JSON with 'findings' array."
    )


def _extract_usage(response, model: str) -> ApiUsage | None:
    """Extract token usage from a Gemini response."""
    try:
        meta = response.usage_metadata
        return ApiUsage(
            phase="visual_qa",
            model=model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        )
    except Exception:
        return None


def compare_pages(
    original_pngs: dict[int, bytes],
    rendered_pngs: list[bytes],
    client,
    model: str,
) -> tuple[list[VisualQAFinding], ApiUsage | None]:
    """Send original and rendered page images to Gemini for content comparison.

    Args:
        original_pngs: Dict of {0-based page_number: PNG bytes} for originals.
        rendered_pngs: List of PNG bytes for rendered pages.
        client: google.genai.Client instance.
        model: Gemini model ID.

    Returns:
        (list of findings, ApiUsage or None). Findings have 0-based page numbers.
    """
    from google.genai import types

    prompt = _load_visual_qa_prompt()

    # Build content parts: prompt + labeled original pages + labeled rendered pages
    content_parts: list = [prompt]

    for page_num in sorted(original_pngs.keys()):
        content_parts.append(
            types.Part.from_bytes(data=original_pngs[page_num], mime_type="image/png")
        )
        content_parts.append(f"Original page {page_num + 1}")

    for i, png in enumerate(rendered_pngs):
        content_parts.append(
            types.Part.from_bytes(data=png, mime_type="image/png")
        )
        content_parts.append(f"Rendered page {i + 1}")

    try:
        response = client.models.generate_content(
            model=model,
            contents=content_parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        resp_text = response.text
        if resp_text is None:
            logger.warning("Visual QA: Gemini returned empty response")
            return [], None

        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError:
            data = parse_json_lenient(resp_text)

        usage = _extract_usage(response, model)

        findings: list[VisualQAFinding] = []
        for f in data.get("findings", []):
            # Convert from 1-based (Gemini) to 0-based (internal)
            orig_page = f.get("original_page", 1) - 1
            rend_page = f.get("rendered_page")
            if rend_page is not None:
                rend_page = rend_page - 1

            findings.append(VisualQAFinding(
                original_page=orig_page,
                rendered_page=rend_page,
                finding_type=f.get("type", "other"),
                description=f.get("description", ""),
                severity=f.get("severity", "medium"),
            ))

        return findings, usage

    except Exception as e:
        logger.warning("Visual QA comparison failed: %s", e)
        return [], None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visual_qa.py::TestComparePages -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/visual_qa.py tests/test_visual_qa.py
git commit -m "Implement Gemini comparison for visual diff QA"
```

---

### Task 5: Implement run_visual_qa orchestrator

**Files:**
- Modify: `src/tools/visual_qa.py`
- Modify: `tests/test_visual_qa.py`

- [ ] **Step 1: Write tests for run_visual_qa**

Add to `tests/test_visual_qa.py`:

```python
from src.tools.visual_qa import run_visual_qa


class TestRunVisualQA:
    def test_full_flow_with_mocks(self):
        """Test the full orchestration with mocked rendering and Gemini."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "findings": [{
                "original_page": 3,
                "rendered_page": 2,
                "type": "truncated_text",
                "description": "Paragraph cut off at bottom of page",
                "severity": "medium",
            }]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.tools.visual_qa.render_original_pages") as mock_render_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_render_html:

            mock_render_orig.return_value = {
                0: b"fake_page_1",
                1: b"fake_page_2",
                2: b"fake_page_3",
            }
            mock_render_html.return_value = [b"fake_rendered_1", b"fake_rendered_2"]

            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0, 1, 2],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

                assert result.pages_checked == 3
                assert len(result.findings) == 1
                assert result.findings[0].finding_type == "truncated_text"

                # Check that PNGs were saved
                qa_dir = Path(tmpdir) / "visual_qa"
                assert qa_dir.exists()

    def test_skips_when_no_rendered_pages(self):
        with patch("src.tools.visual_qa.render_original_pages") as mock_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_html:
            mock_orig.return_value = {0: b"fake"}
            mock_html.return_value = []  # HTML rendering failed

            mock_client = MagicMock()

            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

            assert result.pages_checked == 0
            assert result.findings == []
            mock_client.models.generate_content.assert_not_called()

    def test_saves_findings_json(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"findings": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.tools.visual_qa.render_original_pages") as mock_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_html:
            mock_orig.return_value = {0: b"fake"}
            mock_html.return_value = [b"fake"]

            with tempfile.TemporaryDirectory() as tmpdir:
                run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

                findings_path = Path(tmpdir) / "visual_qa_findings.json"
                assert findings_path.exists()
                data = json.loads(findings_path.read_text())
                assert "findings" in data
                assert "pages_checked" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visual_qa.py::TestRunVisualQA -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement run_visual_qa**

Add to `src/tools/visual_qa.py`:

```python
# Maximum pages per Gemini call for visual QA
PAGES_PER_BATCH = 4


def _save_page_pngs(
    original_pngs: dict[int, bytes],
    rendered_pngs: list[bytes],
    output_dir: str,
) -> Path:
    """Save page PNGs to output directory for report thumbnail embedding.

    Returns path to the visual_qa subdirectory.
    """
    qa_dir = Path(output_dir) / "visual_qa"
    qa_dir.mkdir(exist_ok=True)

    for page_num, png_bytes in original_pngs.items():
        (qa_dir / f"original_page_{page_num}.png").write_bytes(png_bytes)

    for i, png_bytes in enumerate(rendered_pngs):
        (qa_dir / f"rendered_page_{i}.png").write_bytes(png_bytes)

    return qa_dir


def run_visual_qa(
    pdf_path: str,
    html_path: str,
    scanned_page_numbers: list[int],
    client,
    model: str,
    output_dir: str,
) -> VisualQAResult:
    """Run visual diff QA comparing original pages against rendered output.

    Args:
        pdf_path: Path to the original PDF file.
        html_path: Path to the companion HTML file.
        scanned_page_numbers: 0-based page numbers that were scanned/OCR'd.
        client: google.genai.Client instance.
        model: Gemini model ID.
        output_dir: Directory to save PNGs and findings JSON.

    Returns:
        VisualQAResult with findings and API usage.
    """
    logger.info("Visual QA: rendering %d original pages", len(scanned_page_numbers))

    # Step 1: Render original pages
    original_pngs = render_original_pages(pdf_path, scanned_page_numbers)
    if not original_pngs:
        logger.warning("Visual QA: no original pages rendered")
        return VisualQAResult()

    # Step 2: Render HTML to page PNGs
    rendered_pngs = render_html_to_page_pngs(html_path)
    if not rendered_pngs:
        logger.warning("Visual QA: HTML rendering failed, skipping comparison")
        return VisualQAResult()

    logger.info(
        "Visual QA: %d original pages, %d rendered pages",
        len(original_pngs), len(rendered_pngs),
    )

    # Step 3: Save PNGs for report thumbnails
    _save_page_pngs(original_pngs, rendered_pngs, output_dir)

    # Step 4: Compare in batches
    # Split original pages into batches, send all rendered pages with each batch
    all_findings: list[VisualQAFinding] = []
    all_usage: list[ApiUsage] = []
    sorted_pages = sorted(original_pngs.keys())

    for batch_start in range(0, len(sorted_pages), PAGES_PER_BATCH):
        batch_page_nums = sorted_pages[batch_start:batch_start + PAGES_PER_BATCH]
        batch_pngs = {p: original_pngs[p] for p in batch_page_nums}

        logger.info(
            "Visual QA: comparing original pages %s against %d rendered pages",
            [p + 1 for p in batch_page_nums], len(rendered_pngs),
        )

        findings, usage = compare_pages(batch_pngs, rendered_pngs, client, model)
        all_findings.extend(findings)
        if usage:
            all_usage.append(usage)

    # Step 5: Save findings JSON
    findings_data = {
        "document": Path(pdf_path).name,
        "pages_checked": len(original_pngs),
        "original_page_count": len(original_pngs),
        "rendered_page_count": len(rendered_pngs),
        "findings": [
            {
                "page": f.original_page + 1,  # 1-based for JSON output
                "rendered_page": (f.rendered_page + 1) if f.rendered_page is not None else None,
                "type": f.finding_type,
                "description": f.description,
                "severity": f.severity,
            }
            for f in all_findings
        ],
    }

    findings_path = Path(output_dir) / "visual_qa_findings.json"
    findings_path.write_text(json.dumps(findings_data, indent=2), encoding="utf-8")
    logger.info("Visual QA: saved findings to %s", findings_path)

    result = VisualQAResult(
        findings=all_findings,
        pages_checked=len(original_pngs),
        api_usage=all_usage,
    )

    if all_findings:
        high_medium = [f for f in all_findings if f.severity in ("high", "medium")]
        logger.info(
            "Visual QA: %d findings (%d high/medium)",
            len(all_findings), len(high_medium),
        )
    else:
        logger.info("Visual QA: no content gaps detected")

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visual_qa.py::TestRunVisualQA -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run full test file**

Run: `pytest tests/test_visual_qa.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/tools/visual_qa.py tests/test_visual_qa.py
git commit -m "Implement run_visual_qa orchestrator with batching and findings persistence"
```

---

### Task 6: Add "Visual Quality Check" section to report

**Files:**
- Modify: `src/tools/report_generator.py` (insert between "What We Did" and "What Needs Attention", around line 790)

- [ ] **Step 1: Write the test**

Add to `tests/test_visual_qa.py`:

```python
from src.tools.report_generator import _build_visual_qa_section


class TestBuildVisualQASection:
    def test_returns_empty_for_no_findings(self):
        result = _build_visual_qa_section([], "")
        assert result == ""

    def test_returns_empty_for_low_only(self):
        findings = [
            VisualQAFinding(
                original_page=0, rendered_page=0,
                finding_type="other", description="Minor", severity="low",
            ),
        ]
        result = _build_visual_qa_section(findings, "")
        assert result == ""

    def test_renders_high_findings(self):
        findings = [
            VisualQAFinding(
                original_page=4, rendered_page=3,
                finding_type="missing_table",
                description="Table 2 is missing from rendered output",
                severity="high",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        assert "Visual Quality Check" in result
        assert "Table 2 is missing" in result
        assert "Page 5" in result  # 0-based → 1-based display

    def test_renders_medium_findings(self):
        findings = [
            VisualQAFinding(
                original_page=1, rendered_page=1,
                finding_type="truncated_text",
                description="Paragraph appears cut off",
                severity="medium",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        assert "Visual Quality Check" in result
        assert "Paragraph appears cut off" in result

    def test_summary_line(self):
        findings = [
            VisualQAFinding(
                original_page=0, rendered_page=0,
                finding_type="missing_table", description="A", severity="high",
            ),
            VisualQAFinding(
                original_page=2, rendered_page=1,
                finding_type="truncated_text", description="B", severity="medium",
            ),
            VisualQAFinding(
                original_page=3, rendered_page=2,
                finding_type="other", description="C", severity="low",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        assert "2" in result  # 2 high/medium findings shown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visual_qa.py::TestBuildVisualQASection -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement _build_visual_qa_section**

Add to `src/tools/report_generator.py`. Find the imports at the top of the file and add:

```python
from src.models.pipeline import VisualQAFinding
```

Add the helper function before `generate_report_html()`:

```python
def _build_visual_qa_section(
    findings: list[VisualQAFinding],
    output_dir: str,
) -> str:
    """Build the 'Visual Quality Check' HTML section for the report.

    Only shows high and medium severity findings. Returns empty string
    if there are no high/medium findings (section is omitted from report).

    Args:
        findings: List of VisualQAFinding objects.
        output_dir: Path to output directory containing visual_qa/ PNGs.

    Returns:
        HTML string for the section, or empty string if nothing to show.
    """
    import base64

    # Filter to high + medium only
    visible = [f for f in findings if f.severity in ("high", "medium")]
    if not visible:
        return ""

    qa_dir = Path(output_dir) / "visual_qa" if output_dir else None

    # Group by original page
    by_page: dict[int, list[VisualQAFinding]] = {}
    for f in visible:
        by_page.setdefault(f.original_page, []).append(f)

    pages_affected = len(by_page)
    total_findings = len(visible)

    html_parts = [
        '<div class="section">',
        '<h2>Visual Quality Check</h2>',
        f'<p>Visual comparison found <strong>{total_findings} content {"issue" if total_findings == 1 else "issues"}</strong> '
        f'across {pages_affected} {"page" if pages_affected == 1 else "pages"} that may need attention.</p>',
    ]

    for page_num in sorted(by_page.keys()):
        page_findings = by_page[page_num]
        html_parts.append(f'<div class="visual-qa-page" style="margin: 1.5em 0; padding: 1em; border: 1px solid #ddd; border-radius: 8px;">')
        html_parts.append(f'<h3>Page {page_num + 1}</h3>')

        # Side-by-side thumbnails
        html_parts.append('<div style="display: flex; gap: 1em; margin: 1em 0;">')

        # Original thumbnail
        orig_path = qa_dir / f"original_page_{page_num}.png" if qa_dir else None
        if orig_path and orig_path.exists():
            orig_b64 = base64.b64encode(orig_path.read_bytes()).decode("ascii")
            html_parts.append(
                f'<div style="flex: 1;"><p style="font-weight: bold; margin-bottom: 0.5em;">Original</p>'
                f'<img src="data:image/png;base64,{orig_b64}" style="max-width: 400px; border: 1px solid #ccc;" '
                f'alt="Original page {page_num + 1}"></div>'
            )

        # Rendered thumbnail (use rendered_page from first finding)
        rendered_page = page_findings[0].rendered_page
        if rendered_page is not None and qa_dir:
            rend_path = qa_dir / f"rendered_page_{rendered_page}.png"
            if rend_path.exists():
                rend_b64 = base64.b64encode(rend_path.read_bytes()).decode("ascii")
                html_parts.append(
                    f'<div style="flex: 1;"><p style="font-weight: bold; margin-bottom: 0.5em;">Rendered</p>'
                    f'<img src="data:image/png;base64,{rend_b64}" style="max-width: 400px; border: 1px solid #ccc;" '
                    f'alt="Rendered page {rendered_page + 1}"></div>'
                )

        html_parts.append('</div>')  # close flex container

        # Finding descriptions
        for f in page_findings:
            severity_color = "#d32f2f" if f.severity == "high" else "#f57c00"
            severity_label = f.severity.upper()
            html_parts.append(
                f'<p style="margin: 0.5em 0;"><span style="color: {severity_color}; font-weight: bold;">'
                f'[{severity_label}]</span> {f.description}</p>'
            )

        html_parts.append('</div>')  # close visual-qa-page

    html_parts.append('</div>')  # close section
    return "\n".join(html_parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visual_qa.py::TestBuildVisualQASection -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Wire into generate_report_html**

In `src/tools/report_generator.py`, find the `generate_report_html()` function. Update its signature to accept optional visual QA findings and output_dir:

Find the function signature (around line 414):
```python
def generate_report_html(result: RemediationResult) -> str:
```

Change to:
```python
def generate_report_html(
    result: RemediationResult,
    visual_qa_findings: list[VisualQAFinding] | None = None,
    output_dir: str = "",
) -> str:
```

Then find where the "What We Did" and "What Needs Your Attention" sections are built (around line 788-790). Insert the visual QA section between them. Find the pattern where sections are concatenated and add:

```python
    visual_qa_html = ""
    if visual_qa_findings:
        visual_qa_html = _build_visual_qa_section(visual_qa_findings, output_dir)
```

Then insert `{visual_qa_html}` in the HTML template between the "What We Did" div and the "What Needs Your Attention" div.

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: All 855+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/report_generator.py tests/test_visual_qa.py
git commit -m "Add Visual Quality Check section to compliance report with thumbnails"
```

---

### Task 7: Integrate into orchestrator pipeline

**Files:**
- Modify: `src/agent/orchestrator.py` (between Phase 3 ~line 488 and Phase 4 ~line 490)

- [ ] **Step 1: Write integration test**

Add to `tests/test_visual_qa.py`:

```python
class TestOrchestratorIntegration:
    """Smoke test that orchestrator imports and calls visual QA correctly."""

    def test_visual_qa_import(self):
        """Verify the visual QA module can be imported from orchestrator context."""
        from src.tools.visual_qa import run_visual_qa
        assert callable(run_visual_qa)

    def test_visual_qa_result_in_pipeline_models(self):
        """Verify VisualQAResult can be used alongside existing pipeline models."""
        from src.models.pipeline import RemediationResult, VisualQAResult
        result = VisualQAResult(pages_checked=5)
        assert result.pages_checked == 5
```

- [ ] **Step 2: Add visual QA call to orchestrator**

In `src/agent/orchestrator.py`, add the import near the top of the file with other tool imports:

```python
from src.tools.visual_qa import run_visual_qa
```

Find the gap between Phase 3 and Phase 4 (after `exec_result.companion_html_path` logging around line 488, before `# ── Phase 4: Review` comment around line 490). Insert:

```python
    # ── Phase 3.5: Visual Diff QA (scanned PDFs only) ──────────
    visual_qa_result = None
    if (
        suffix == ".pdf"
        and hasattr(parse_result, "scanned_page_numbers")
        and parse_result.scanned_page_numbers
        and exec_result.companion_html_path
    ):
        if on_phase:
            on_phase("visual_qa", "Visual quality check")
        logger.info("Phase 3.5: Visual Diff QA (%d scanned pages)", len(parse_result.scanned_page_numbers))

        try:
            from google import genai
            from dotenv import load_dotenv
            load_dotenv()
            gemini_key = os.environ.get("GEMINI_API_KEY")
            if gemini_key:
                gemini_client = genai.Client(api_key=gemini_key)
                visual_qa_result = run_visual_qa(
                    pdf_path=request.file_path,
                    html_path=exec_result.companion_html_path,
                    scanned_page_numbers=parse_result.scanned_page_numbers,
                    client=gemini_client,
                    model="gemini-2.5-flash",
                    output_dir=str(output_dir),
                )
                if visual_qa_result.api_usage:
                    all_usage.extend(visual_qa_result.api_usage)
                logger.info(
                    "Visual QA: %d findings (%d pages checked)",
                    len(visual_qa_result.findings), visual_qa_result.pages_checked,
                )
            else:
                logger.warning("Visual QA: GEMINI_API_KEY not set, skipping")
        except Exception as e:
            logger.warning("Visual QA failed: %s", e)
```

**Important:** Check if a Gemini client is already created earlier in the orchestrator (it is — for comprehension). If so, reuse it instead of creating a new one. Look for an existing `genai.Client` variable. If the orchestrator already has a `gemini_client` or `client` variable in scope from the comprehension phase, use that directly:

```python
        visual_qa_result = run_visual_qa(
            pdf_path=request.file_path,
            html_path=exec_result.companion_html_path,
            scanned_page_numbers=parse_result.scanned_page_numbers,
            client=client,  # reuse existing Gemini client
            model="gemini-2.5-flash",
            output_dir=str(output_dir),
        )
```

- [ ] **Step 3: Pass visual QA findings to report generator**

Find the `generate_report_html(final_result)` call (around line 593). Update it to:

```python
    visual_qa_findings = visual_qa_result.findings if visual_qa_result else None
    report_html = generate_report_html(
        final_result,
        visual_qa_findings=visual_qa_findings,
        output_dir=str(output_dir),
    )
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/orchestrator.py tests/test_visual_qa.py
git commit -m "Integrate visual diff QA into orchestrator pipeline (Phase 3.5)"
```

---

### Task 8: Add batch aggregation to test runner

**Files:**
- Modify: `scripts/test_batch.py`

- [ ] **Step 1: Add findings aggregation**

Find the batch summary section in `scripts/test_batch.py` (where `batch_results.md` is written). After the main results loop, add aggregation of visual QA findings:

```python
    # ── Aggregate visual QA findings ──────────────────────────────
    all_visual_findings = []
    for doc_path in docs:
        findings_path = output_dir / doc_path.stem / "visual_qa_findings.json"
        if not findings_path.exists():
            findings_path = output_dir / "visual_qa_findings.json"
        if findings_path.exists():
            try:
                data = json.loads(findings_path.read_text())
                for f in data.get("findings", []):
                    f["document"] = data.get("document", doc_path.name)
                    all_visual_findings.append(f)
            except Exception:
                pass

    if all_visual_findings:
        summary_path = output_dir / "visual_qa_summary.json"
        summary_path.write_text(
            json.dumps({"findings": all_visual_findings}, indent=2),
            encoding="utf-8",
        )
        logger.info("Visual QA summary: %d findings across all documents → %s", len(all_visual_findings), summary_path)

        # Count by type
        from collections import Counter
        type_counts = Counter(f.get("type", "other") for f in all_visual_findings)
        for ftype, count in type_counts.most_common():
            logger.info("  %s: %d", ftype, count)
```

- [ ] **Step 2: Commit**

```bash
git add scripts/test_batch.py
git commit -m "Add visual QA findings aggregation to batch runner"
```

---

### Task 9: End-to-end validation

This task requires the Gemini API key and the Mayer test document. Run manually.

- [ ] **Step 1: Run on Mayer document**

```bash
python3 scripts/test_batch.py --doc "7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional.pdf"
```

Check logs for:
- "Phase 3.5: Visual Diff QA (11 scanned pages)"
- "Visual QA: N original pages, M rendered pages"
- "Visual QA: saved findings to ..."
- Any findings in `testdocs/output/visual_qa_findings.json`

- [ ] **Step 2: Check the compliance report**

Open the generated HTML report and verify:
- If findings exist: "Visual Quality Check" section appears with thumbnails
- If no findings: section is omitted (clean pass)
- Thumbnails load correctly (not broken images)
- Finding descriptions are clear and actionable

- [ ] **Step 3: Check PNG files**

```bash
ls -la testdocs/output/visual_qa/
```

Verify original_page_*.png and rendered_page_*.png files exist.

- [ ] **Step 4: Run full batch**

```bash
python3 scripts/test_batch.py
```

Check `testdocs/output/visual_qa_summary.json` for aggregated findings.

- [ ] **Step 5: Commit any fixes**

```bash
git add -u
git commit -m "Validate visual diff QA on batch test documents"
```
