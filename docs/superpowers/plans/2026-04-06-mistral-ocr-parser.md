# Mistral OCR Markdown Parser + Experimental Comparison Mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse Mistral OCR 3 markdown output into DocumentModel objects and run it in parallel with the hybrid OCR pipeline so reports include side-by-side quality comparison.

**Architecture:** New `mistral_ocr.py` handles API calls and markdown parsing via markdown-it-py. The orchestrator runs both OCR paths concurrently using ThreadPoolExecutor. The report generator adds a comparison section when Mistral results are available.

**Tech Stack:** markdown-it-py (token-based parser), mistralai SDK (already installed), concurrent.futures (stdlib)

---

### Task 1: Markdown-to-ParagraphInfo Parser (Core)

**Files:**
- Create: `src/tools/mistral_ocr.py`
- Create: `tests/test_mistral_parser.py`

This is the heart of the feature — converting markdown-it-py tokens into `ParagraphInfo` with proper `RunInfo` objects for inline formatting.

- [ ] **Step 1: Write failing tests for heading parsing**

```python
# tests/test_mistral_parser.py
"""Tests for Mistral OCR markdown parser."""
import pytest
from src.tools.mistral_ocr import parse_page_markdown


class TestHeadingParsing:
    def test_h1_heading(self):
        md = "# Main Title"
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 1
        assert paras[0].heading_level == 1
        assert paras[0].text == "Main Title"
        assert paras[0].id == "ocr_p_0"

    def test_h2_heading(self):
        md = "## Section Heading"
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].heading_level == 2
        assert paras[0].text == "Section Heading"

    def test_multiple_headings(self):
        md = "# Title\n\n## Section 1\n\n## Section 2"
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 3
        assert paras[0].heading_level == 1
        assert paras[1].heading_level == 2
        assert paras[2].heading_level == 2
        assert paras[2].id == "ocr_p_2"

    def test_heading_with_bold(self):
        md = "## **Bold Heading**"
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].heading_level == 2
        assert paras[0].text == "Bold Heading"
        assert paras[0].runs[0].bold is True
```

- [ ] **Step 2: Write failing tests for paragraph + inline formatting**

```python
# append to tests/test_mistral_parser.py

class TestParagraphParsing:
    def test_plain_paragraph(self):
        md = "Hello world."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "Hello world."
        assert paras[0].heading_level is None
        assert len(paras[0].runs) == 1
        assert paras[0].runs[0].bold is None
        assert paras[0].runs[0].italic is None

    def test_bold_text(self):
        md = "Some **bold** text."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].text == "Some bold text."
        assert len(paras[0].runs) == 3
        assert paras[0].runs[0].text == "Some "
        assert paras[0].runs[0].bold is None
        assert paras[0].runs[1].text == "bold"
        assert paras[0].runs[1].bold is True
        assert paras[0].runs[2].text == " text."

    def test_italic_text(self):
        md = "An *italic* word."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].runs[1].text == "italic"
        assert paras[0].runs[1].italic is True

    def test_nested_bold_italic(self):
        md = "Normal **bold *bold-italic* bold** end."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].text == "Normal bold bold-italic bold end."
        # Find the bold-italic run
        bi_run = [r for r in paras[0].runs if r.bold and r.italic]
        assert len(bi_run) == 1
        assert bi_run[0].text == "bold-italic"

    def test_multiple_paragraphs(self):
        md = "First paragraph.\n\nSecond paragraph."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 2
        assert paras[0].text == "First paragraph."
        assert paras[1].text == "Second paragraph."

    def test_blockquote(self):
        md = "> This is a quote."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "This is a quote."
```

- [ ] **Step 3: Write failing tests for link parsing**

```python
# append to tests/test_mistral_parser.py

class TestLinkParsing:
    def test_inline_link(self):
        md = "Click [here](http://example.com) now."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert paras[0].text == "Click here now."
        assert len(links) == 1
        assert links[0].text == "here"
        assert links[0].url == "http://example.com"
        assert links[0].id == "ocr_link_0"
        assert links[0].paragraph_id == "ocr_p_0"

    def test_multiple_links(self):
        md = "[A](http://a.com) and [B](http://b.com)"
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(links) == 2
        assert links[0].id == "ocr_link_0"
        assert links[1].id == "ocr_link_1"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_mistral_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.mistral_ocr'`

- [ ] **Step 5: Implement `parse_page_markdown`**

```python
# src/tools/mistral_ocr.py
"""Mistral OCR 3 client and markdown-to-DocumentModel parser.

Converts Mistral's markdown OCR output into ParagraphInfo, TableInfo,
and LinkInfo objects matching the same models used by the hybrid pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    ImageInfo,
    LinkInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.models.pipeline import ApiUsage

logger = logging.getLogger(__name__)

# Page header pattern: "152 MAYER" at top of scanned pages
_PAGE_HEADER_RE = re.compile(r"^\d+\s+[A-Z][A-Z\s]+$")

# Mistral table reference: [tbl-0.md](tbl-0.md)
_TABLE_REF_RE = re.compile(r"\[tbl-\d+\.md\]\(tbl-\d+\.md\)")


def parse_page_markdown(
    markdown: str,
    page_index: int,
    tables: list | None = None,
    para_offset: int = 0,
    table_offset: int = 0,
    link_offset: int = 0,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[LinkInfo], list[ContentOrderItem]]:
    """Parse a single page's markdown into model objects.

    Args:
        markdown: Raw markdown string from Mistral OCR.
        page_index: 0-based page number.
        tables: Mistral TableObject list for this page (structured table data).
        para_offset: Starting index for paragraph IDs.
        table_offset: Starting index for table IDs.
        link_offset: Starting index for link IDs.

    Returns:
        Tuple of (paragraphs, tables, links, content_order).
    """
    md_parser = MarkdownIt("commonmark").enable("table")
    tokens = md_parser.parse(markdown)

    result_paras: list[ParagraphInfo] = []
    result_tables: list[TableInfo] = []
    result_links: list[LinkInfo] = []
    content_order: list[ContentOrderItem] = []

    p_idx = para_offset
    t_idx = table_offset
    l_idx = link_offset
    mistral_table_idx = 0  # index into the tables list from Mistral

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # ── Headings ──────────────────────────────────────────
        if tok.type == "heading_open":
            level = int(tok.tag[1])  # h1 -> 1, h2 -> 2, etc.
            # Next token is inline with children
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            runs, para_links = _extract_runs_and_links(
                inline_tok, f"ocr_p_{p_idx}", l_idx, page_index,
            )
            text = "".join(r.text for r in runs)
            para = ParagraphInfo(
                id=f"ocr_p_{p_idx}",
                text=text,
                heading_level=level,
                runs=runs,
                links=para_links,
                page_number=page_index,
            )
            result_paras.append(para)
            result_links.extend(para_links)
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=f"ocr_p_{p_idx}",
            ))
            l_idx += len(para_links)
            p_idx += 1
            i += 3  # heading_open, inline, heading_close
            continue

        # ── Paragraphs ────────────────────────────────────────
        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            # Check if this paragraph is just a table reference
            if inline_tok and inline_tok.content and _TABLE_REF_RE.fullmatch(inline_tok.content.strip()):
                # This is a table reference — emit a TableInfo instead
                table_info = _parse_mistral_table(
                    tables, mistral_table_idx, f"ocr_tbl_{t_idx}", page_index,
                )
                if table_info:
                    result_tables.append(table_info)
                    content_order.append(ContentOrderItem(
                        content_type=ContentType.TABLE, id=f"ocr_tbl_{t_idx}",
                    ))
                    t_idx += 1
                    mistral_table_idx += 1
                i += 3  # paragraph_open, inline, paragraph_close
                continue

            # Check for page header (e.g. "152 MAYER") — skip it
            if inline_tok and inline_tok.content and _PAGE_HEADER_RE.match(inline_tok.content.strip()):
                i += 3
                continue

            runs, para_links = _extract_runs_and_links(
                inline_tok, f"ocr_p_{p_idx}", l_idx, page_index,
            )
            text = "".join(r.text for r in runs)
            if not text.strip():
                i += 3
                continue
            para = ParagraphInfo(
                id=f"ocr_p_{p_idx}",
                text=text,
                runs=runs,
                links=para_links,
                page_number=page_index,
            )
            result_paras.append(para)
            result_links.extend(para_links)
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=f"ocr_p_{p_idx}",
            ))
            l_idx += len(para_links)
            p_idx += 1
            i += 3  # paragraph_open, inline, paragraph_close
            continue

        # ── Blockquotes ───────────────────────────────────────
        if tok.type == "blockquote_open":
            # Collect all paragraphs inside the blockquote
            i += 1
            while i < len(tokens) and tokens[i].type != "blockquote_close":
                if tokens[i].type == "paragraph_open":
                    inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                    runs, para_links = _extract_runs_and_links(
                        inline_tok, f"ocr_p_{p_idx}", l_idx, page_index,
                    )
                    text = "".join(r.text for r in runs)
                    if text.strip():
                        para = ParagraphInfo(
                            id=f"ocr_p_{p_idx}",
                            text=text,
                            runs=runs,
                            links=para_links,
                            page_number=page_index,
                        )
                        result_paras.append(para)
                        result_links.extend(para_links)
                        content_order.append(ContentOrderItem(
                            content_type=ContentType.PARAGRAPH, id=f"ocr_p_{p_idx}",
                        ))
                        l_idx += len(para_links)
                        p_idx += 1
                    i += 3  # paragraph_open, inline, paragraph_close
                else:
                    i += 1
            i += 1  # skip blockquote_close
            continue

        # ── Tables (inline markdown tables, not Mistral TableObject) ──
        if tok.type == "table_open":
            table_info, end_i = _parse_inline_table(
                tokens, i, f"ocr_tbl_{t_idx}", page_index,
            )
            if table_info:
                result_tables.append(table_info)
                content_order.append(ContentOrderItem(
                    content_type=ContentType.TABLE, id=f"ocr_tbl_{t_idx}",
                ))
                t_idx += 1
            i = end_i + 1
            continue

        i += 1

    return result_paras, result_tables, result_links, content_order


def _extract_runs_and_links(
    inline_tok,
    paragraph_id: str,
    link_offset: int,
    page_number: int,
) -> tuple[list[RunInfo], list[LinkInfo]]:
    """Walk inline token children and build RunInfo + LinkInfo lists.

    Handles nested formatting: **bold *bold-italic* bold** produces
    three RunInfo objects with appropriate flags.
    """
    if not inline_tok or not inline_tok.children:
        if inline_tok and inline_tok.content:
            return [RunInfo(text=inline_tok.content)], []
        return [], []

    runs: list[RunInfo] = []
    links: list[LinkInfo] = []
    bold_depth = 0
    italic_depth = 0
    link_href: str | None = None
    link_text_parts: list[str] = []
    l_idx = link_offset

    for child in inline_tok.children:
        if child.type == "strong_open":
            bold_depth += 1
        elif child.type == "strong_close":
            bold_depth -= 1
        elif child.type == "em_open":
            italic_depth += 1
        elif child.type == "em_close":
            italic_depth -= 1
        elif child.type == "link_open":
            link_href = child.attrs.get("href", "") if child.attrs else ""
            link_text_parts = []
        elif child.type == "link_close":
            link_text = "".join(link_text_parts)
            if link_href is not None:
                links.append(LinkInfo(
                    id=f"ocr_link_{l_idx}",
                    text=link_text,
                    url=link_href,
                    paragraph_id=paragraph_id,
                    page_number=page_number,
                ))
                l_idx += 1
            link_href = None
            link_text_parts = []
        elif child.type in ("text", "code_inline"):
            text = child.content
            if not text:
                continue
            run = RunInfo(
                text=text,
                bold=True if bold_depth > 0 else None,
                italic=True if italic_depth > 0 else None,
            )
            runs.append(run)
            if link_href is not None:
                link_text_parts.append(text)
        elif child.type == "softbreak":
            runs.append(RunInfo(text=" "))
            if link_href is not None:
                link_text_parts.append(" ")
        elif child.type == "hardbreak":
            runs.append(RunInfo(text="\n"))

    return runs, links


def _parse_mistral_table(
    tables: list | None,
    table_idx: int,
    table_id: str,
    page_number: int,
) -> TableInfo | None:
    """Convert a Mistral TableObject to TableInfo by parsing its markdown content."""
    if not tables or table_idx >= len(tables):
        return None

    table_obj = tables[table_idx]
    content = table_obj.get("content", "") if isinstance(table_obj, dict) else getattr(table_obj, "content", "")

    return _parse_markdown_table_string(content, table_id, page_number)


def _parse_markdown_table_string(
    md_table: str,
    table_id: str,
    page_number: int,
) -> TableInfo | None:
    """Parse a markdown table string into a TableInfo.

    Format:
    | Header1 | Header2 |
    | --- | --- |
    | cell | cell |
    """
    lines = [l.strip() for l in md_table.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return None

    def parse_row(line: str) -> list[str]:
        # Split on |, strip whitespace, drop empty first/last from leading/trailing |
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        return cells

    # First line is header
    headers = parse_row(lines[0])

    # Second line is separator (| --- | --- |) — skip it
    # Remaining lines are data rows
    rows: list[list[CellInfo]] = []
    # Header row
    rows.append([CellInfo(text=h) for h in headers])

    for line in lines[2:]:
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue  # skip extra separator lines
        cells = parse_row(line)
        rows.append([CellInfo(text=c) for c in cells])

    return TableInfo(
        id=table_id,
        rows=rows,
        header_row_count=1,
        row_count=len(rows),
        col_count=len(headers),
        page_number=page_number,
    )


def _parse_inline_table(
    tokens: list,
    start: int,
    table_id: str,
    page_number: int,
) -> tuple[TableInfo | None, int]:
    """Parse a table from markdown-it tokens (table_open ... table_close).

    Returns the TableInfo and the index of the table_close token.
    """
    rows: list[list[CellInfo]] = []
    current_row: list[CellInfo] = []
    header_row_count = 0
    in_thead = False
    i = start + 1  # skip table_open

    while i < len(tokens) and tokens[i].type != "table_close":
        tok = tokens[i]
        if tok.type == "thead_open":
            in_thead = True
        elif tok.type == "thead_close":
            in_thead = False
        elif tok.type == "tr_open":
            current_row = []
        elif tok.type == "tr_close":
            rows.append(current_row)
            if in_thead:
                header_row_count += 1
        elif tok.type in ("th_open", "td_open"):
            # Next token is inline with cell content
            cell_inline = tokens[i + 1] if i + 1 < len(tokens) else None
            cell_text = cell_inline.content if cell_inline else ""
            current_row.append(CellInfo(text=cell_text))
            i += 2  # skip inline + th_close/td_close
            continue
        i += 1

    col_count = max((len(r) for r in rows), default=0)
    return TableInfo(
        id=table_id,
        rows=rows,
        header_row_count=header_row_count,
        row_count=len(rows),
        col_count=col_count,
        page_number=page_number,
    ), i
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_mistral_parser.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/mistral_ocr.py tests/test_mistral_parser.py
git commit -m "feat: markdown-to-DocumentModel parser for Mistral OCR output"
```

---

### Task 2: Page Header Stripping + Content Order + ID Offsets

**Files:**
- Modify: `tests/test_mistral_parser.py`
- Modify: `src/tools/mistral_ocr.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_mistral_parser.py

class TestPageHeaderStripping:
    def test_strips_page_header(self):
        md = "152 MAYER\n\nActual content here."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "Actual content here."

    def test_keeps_non_header_numbers(self):
        md = "152 items were found in the study."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(paras) == 1
        assert "152 items" in paras[0].text

    def test_strips_header_various_formats(self):
        md = "LEARNERS AS INFORMATION PROCESSORS 153\n\nContent."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        # This has text before the number — should NOT be stripped
        # (the regex expects number first)
        assert len(paras) == 2


class TestContentOrder:
    def test_paragraph_table_paragraph(self):
        md = "Intro text.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter table."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(order) == 3
        assert order[0].content_type == ContentType.PARAGRAPH
        assert order[1].content_type == ContentType.TABLE
        assert order[2].content_type == ContentType.PARAGRAPH

    def test_heading_paragraph_order(self):
        md = "# Title\n\nBody text."
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        assert len(order) == 2
        assert order[0].id == "ocr_p_0"
        assert order[1].id == "ocr_p_1"


class TestIDOffsets:
    def test_para_offset(self):
        md = "First paragraph."
        paras, tables, links, order = parse_page_markdown(
            md, page_index=1, para_offset=5,
        )
        assert paras[0].id == "ocr_p_5"

    def test_table_offset(self):
        md = "| A |\n|---|\n| 1 |"
        paras, tables, links, order = parse_page_markdown(
            md, page_index=0, table_offset=3,
        )
        assert tables[0].id == "ocr_tbl_3"

    def test_link_offset(self):
        md = "A [link](http://x.com)."
        paras, tables, links, order = parse_page_markdown(
            md, page_index=0, link_offset=7,
        )
        assert links[0].id == "ocr_link_7"
```

- [ ] **Step 2: Run tests to verify they pass (these should already pass from Task 1)**

Run: `pytest tests/test_mistral_parser.py -v`
Expected: All PASS. If any fail, fix the implementation.

- [ ] **Step 3: Commit**

```bash
git add tests/test_mistral_parser.py
git commit -m "test: page header stripping, content order, and ID offset tests"
```

---

### Task 3: Multi-Page Stitching + Mistral API Client

**Files:**
- Modify: `src/tools/mistral_ocr.py`
- Modify: `tests/test_mistral_parser.py`

- [ ] **Step 1: Write failing tests for multi-page stitching**

```python
# append to tests/test_mistral_parser.py
from src.tools.mistral_ocr import stitch_pages


class TestMultiPageStitching:
    def test_ids_sequential_across_pages(self):
        pages_md = [
            "# Title\n\nParagraph one.",
            "## Section\n\nParagraph two.",
        ]
        paras, tables, links, order = stitch_pages(pages_md, page_tables=[None, None])
        ids = [p.id for p in paras]
        assert ids == ["ocr_p_0", "ocr_p_1", "ocr_p_2", "ocr_p_3"]

    def test_page_numbers_correct(self):
        pages_md = ["Text on page 0.", "Text on page 1."]
        paras, tables, links, order = stitch_pages(pages_md, page_tables=[None, None])
        assert paras[0].page_number == 0
        assert paras[1].page_number == 1

    def test_table_ids_sequential_across_pages(self):
        pages_md = [
            "| A |\n|---|\n| 1 |",
            "| B |\n|---|\n| 2 |",
        ]
        paras, tables, links, order = stitch_pages(pages_md, page_tables=[None, None])
        assert tables[0].id == "ocr_tbl_0"
        assert tables[1].id == "ocr_tbl_1"

    def test_empty_page_skipped(self):
        pages_md = ["Content.", "", "More content."]
        paras, tables, links, order = stitch_pages(
            pages_md, page_tables=[None, None, None],
        )
        assert len(paras) == 2
```

- [ ] **Step 2: Implement `stitch_pages`**

```python
# add to src/tools/mistral_ocr.py

def stitch_pages(
    pages_md: list[str],
    page_tables: list[list | None],
) -> tuple[list[ParagraphInfo], list[TableInfo], list[LinkInfo], list[ContentOrderItem]]:
    """Stitch multiple pages of parsed markdown into a single set of model objects.

    IDs are sequential across all pages.
    """
    all_paras: list[ParagraphInfo] = []
    all_tables: list[TableInfo] = []
    all_links: list[LinkInfo] = []
    all_order: list[ContentOrderItem] = []

    p_offset = 0
    t_offset = 0
    l_offset = 0

    for page_idx, md in enumerate(pages_md):
        tables = page_tables[page_idx] if page_tables else None
        paras, tbls, links, order = parse_page_markdown(
            md,
            page_index=page_idx,
            tables=tables,
            para_offset=p_offset,
            table_offset=t_offset,
            link_offset=l_offset,
        )
        all_paras.extend(paras)
        all_tables.extend(tbls)
        all_links.extend(links)
        all_order.extend(order)

        p_offset += len(paras)
        t_offset += len(tbls)
        l_offset += len(links)

    return all_paras, all_tables, all_links, all_order
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_mistral_parser.py -v`
Expected: All PASS

- [ ] **Step 4: Implement `process_scanned_pages_mistral` (API client)**

```python
# add to src/tools/mistral_ocr.py
import os
import time
from src.tools.scanned_page_ocr import ScannedPageResult


def process_scanned_pages_mistral(
    pdf_path: Path,
    scanned_page_numbers: list[int],
    api_key: str | None = None,
) -> ScannedPageResult:
    """Process scanned PDF pages through Mistral OCR 3.

    Args:
        pdf_path: Path to the PDF file.
        scanned_page_numbers: 0-based page numbers to process.
        api_key: Mistral API key. Falls back to MISTRAL_API_KEY env var.

    Returns:
        ScannedPageResult matching the hybrid pipeline's return type.
    """
    key = api_key or os.environ.get("MISTRAL_API_KEY")
    if not key:
        return ScannedPageResult(
            success=False,
            error="MISTRAL_API_KEY not set",
        )

    try:
        from mistralai.client import Mistral
        from mistralai.client.models.ocrrequest import FileChunk

        client = Mistral(api_key=key)

        # Upload PDF
        t0 = time.time()
        uploaded = client.files.upload(
            file={
                "file_name": Path(pdf_path).name,
                "content": Path(pdf_path).read_bytes(),
            },
            purpose="ocr",
        )
        upload_time = time.time() - t0
        logger.info("Mistral: uploaded %s in %.1fs", pdf_path.name, upload_time)

        # Run OCR (Mistral uses 0-based page indices)
        t0 = time.time()
        result = client.ocr.process(
            model="mistral-ocr-latest",
            document=FileChunk(file_id=uploaded.id, type="file"),
            pages=scanned_page_numbers,
            include_image_base64=False,
            table_format="markdown",
        )
        ocr_time = time.time() - t0
        logger.info(
            "Mistral: OCR completed in %.1fs, %d pages",
            ocr_time, result.usage_info.pages_processed,
        )

        # Parse each page's markdown
        pages_md = [page.markdown for page in result.pages]
        page_tables = []
        for page in result.pages:
            if page.tables:
                page_tables.append([
                    {"content": t.content, "id": t.id}
                    for t in page.tables
                ])
            else:
                page_tables.append(None)

        paras, tables, links, order = stitch_pages(pages_md, page_tables)

        # Cleanup uploaded file
        try:
            client.files.delete(file_id=uploaded.id)
        except Exception:
            pass

        # Build usage record
        pages_processed = result.usage_info.pages_processed
        usage = ApiUsage(
            phase="ocr_mistral",
            model="mistral-ocr-latest",
            input_tokens=0,  # Mistral charges per page, not tokens
            output_tokens=0,
        )

        return ScannedPageResult(
            success=True,
            paragraphs=paras,
            tables=tables,
            figures=[],  # Mistral doesn't extract figures from scanned pages
            pages_processed=scanned_page_numbers,
            api_usage=[usage],
            warnings=[],
        )

    except Exception as e:
        logger.warning("Mistral OCR failed: %s", e)
        return ScannedPageResult(
            success=False,
            error=f"Mistral OCR error: {e}",
        )
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_mistral_parser.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/tools/mistral_ocr.py tests/test_mistral_parser.py
git commit -m "feat: multi-page stitching and Mistral API client"
```

---

### Task 4: Real Mayer Output End-to-End Tests

**Files:**
- Modify: `tests/test_mistral_parser.py`

Uses the saved markdown files from `testdocs/output/mistral_ocr_eval/` — no API calls needed.

- [ ] **Step 1: Write end-to-end tests against real Mayer output**

```python
# append to tests/test_mistral_parser.py
import json
from pathlib import Path

EVAL_DIR = Path(__file__).parent.parent / "testdocs" / "output" / "mistral_ocr_eval"


@pytest.mark.skipif(
    not (EVAL_DIR / "page_1.md").exists(),
    reason="Mistral eval output not available",
)
class TestMayerEndToEnd:
    def test_page1_title_and_abstract(self):
        md = (EVAL_DIR / "page_1.md").read_text()
        paras, tables, links, order = parse_page_markdown(md, page_index=0)
        # Should have title heading
        headings = [p for p in paras if p.heading_level is not None]
        assert len(headings) >= 1
        assert headings[0].heading_level == 1
        assert "Learners as Information Processors" in headings[0].text
        # Should have multiple paragraphs (abstract + body)
        assert len(paras) >= 3

    def test_page6_table3_found(self):
        """Table 3 is the one the hybrid pipeline misses."""
        md = (EVAL_DIR / "page_6.md").read_text()
        with open(EVAL_DIR / "full_response.json") as f:
            data = json.load(f)
        # Page 6 is index 5
        page_data = data["pages"][5]
        tables_data = page_data.get("tables") or []
        paras, tables, links, order = parse_page_markdown(
            md, page_index=5, tables=[
                {"content": t["content"], "id": t["id"]} for t in tables_data
            ],
        )
        assert len(tables) >= 1
        # Table should have headers
        assert tables[0].header_row_count == 1
        assert tables[0].col_count >= 3  # View | Content | Activity | Learner

    def test_page6_headings(self):
        md = (EVAL_DIR / "page_6.md").read_text()
        paras, tables, links, order = parse_page_markdown(md, page_index=5)
        headings = [p for p in paras if p.heading_level is not None]
        heading_texts = [h.text for h in headings]
        assert "Literal Interpretation of Information Processing" in heading_texts
        assert "Constructivist Interpretation of Information Processing" in heading_texts

    def test_page10_no_recitation_loss(self):
        """Page 10 triggers RECITATION in Gemini — Mistral should get full text."""
        md = (EVAL_DIR / "page_10.md").read_text()
        paras, tables, links, order = parse_page_markdown(md, page_index=9)
        total_chars = sum(len(p.text) for p in paras)
        assert total_chars > 3000  # full page of text
        headings = [p for p in paras if p.heading_level is not None]
        heading_texts = [h.text for h in headings]
        assert "The Critical Path" in heading_texts
        assert "ACKNOWLEDGMENTS" in heading_texts
        assert "REFERENCES" in heading_texts

    def test_all_11_pages_stitched(self):
        """Parse all 11 pages and verify sequential IDs."""
        with open(EVAL_DIR / "full_response.json") as f:
            data = json.load(f)
        pages_md = [p["markdown"] for p in data["pages"]]
        page_tables = []
        for p in data["pages"]:
            tbls = p.get("tables") or []
            if tbls:
                page_tables.append([
                    {"content": t["content"], "id": t["id"]} for t in tbls
                ])
            else:
                page_tables.append(None)

        paras, tables, links, order = stitch_pages(pages_md, page_tables)

        # Should have substantial content
        assert len(paras) >= 50
        assert len(tables) >= 4  # 4 tables in the paper

        # IDs should be sequential
        para_ids = [int(p.id.split("_")[-1]) for p in paras]
        assert para_ids == list(range(len(paras)))

        table_ids = [int(t.id.split("_")[-1]) for t in tables]
        assert table_ids == list(range(len(tables)))

        # Content order should cover everything
        para_in_order = [o for o in order if o.content_type == ContentType.PARAGRAPH]
        table_in_order = [o for o in order if o.content_type == ContentType.TABLE]
        assert len(para_in_order) == len(paras)
        assert len(table_in_order) == len(tables)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_mistral_parser.py::TestMayerEndToEnd -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_mistral_parser.py
git commit -m "test: end-to-end Mayer paper tests against real Mistral output"
```

---

### Task 5: Orchestrator Parallel Execution

**Files:**
- Modify: `src/agent/orchestrator.py`

- [ ] **Step 1: Add Mistral import and parallel OCR execution**

In `src/agent/orchestrator.py`, add the import near the top with the other tool imports:

```python
from src.tools.mistral_ocr import process_scanned_pages_mistral
```

Then modify the OCR section (around line 446) to run both pipelines concurrently. Find the block that currently reads:

```python
        ocr_result = process_scanned_pages(
            pdf_path=doc_path,
            scanned_page_numbers=parse_result.scanned_page_numbers,
            course_context=course_ctx,
            on_progress=lambda detail: on_phase("ocr", detail) if on_phase else None,
        )
```

Replace with:

```python
        import concurrent.futures

        mistral_result = None
        mistral_api_key = os.environ.get("MISTRAL_API_KEY")

        if mistral_api_key:
            # Run both OCR pipelines concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                hybrid_future = pool.submit(
                    process_scanned_pages,
                    pdf_path=doc_path,
                    scanned_page_numbers=parse_result.scanned_page_numbers,
                    course_context=course_ctx,
                    on_progress=lambda detail: on_phase("ocr", detail) if on_phase else None,
                )
                mistral_future = pool.submit(
                    process_scanned_pages_mistral,
                    pdf_path=doc_path,
                    scanned_page_numbers=parse_result.scanned_page_numbers,
                )

                ocr_result = hybrid_future.result()

                try:
                    mistral_result = mistral_future.result()
                    if mistral_result.success:
                        logger.info(
                            "Mistral OCR: %d paragraphs, %d tables from %d pages",
                            len(mistral_result.paragraphs),
                            len(mistral_result.tables),
                            len(mistral_result.pages_processed),
                        )
                    else:
                        logger.warning("Mistral OCR failed (non-fatal): %s", mistral_result.error)
                        mistral_result = None
                except Exception as e:
                    logger.warning("Mistral OCR failed (non-fatal): %s", e)
                    mistral_result = None
        else:
            ocr_result = process_scanned_pages(
                pdf_path=doc_path,
                scanned_page_numbers=parse_result.scanned_page_numbers,
                course_context=course_ctx,
                on_progress=lambda detail: on_phase("ocr", detail) if on_phase else None,
            )
```

- [ ] **Step 2: Thread both OCR results to the report generator**

Find the `generate_report_html` call (around line 691):

```python
        report_html = generate_report_html(
            final_result,
            visual_qa_findings=visual_qa_findings,
            output_dir=output_dir,
        )
```

Change to:

```python
        report_html = generate_report_html(
            final_result,
            visual_qa_findings=visual_qa_findings,
            output_dir=output_dir,
            hybrid_ocr_result=ocr_result if ocr_result else None,
            mistral_ocr_result=mistral_result,
        )
```

Both `ocr_result` and `mistral_result` need to be initialized to `None` before the scanned page check block and kept in scope. Add these near the top of the `process()` function, before the `if parse_result.scanned_page_numbers:` block:

```python
    ocr_result = None
    mistral_result = None
```

Note: `ocr_result` may already be initialized — just make sure `mistral_result = None` is added.

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `pytest tests/ -v --timeout=60 -x -q 2>&1 | tail -20`
Expected: All existing tests PASS (Mistral path is skipped when no API key)

- [ ] **Step 4: Commit**

```bash
git add src/agent/orchestrator.py
git commit -m "feat: parallel Mistral OCR execution in orchestrator"
```

---

### Task 6: Report Comparison Section

**Files:**
- Modify: `src/tools/report_generator.py`

- [ ] **Step 1: Add `mistral_ocr_result` parameter to `generate_report_html`**

Find the function signature:

```python
def generate_report_html(
    result: RemediationResult,
    visual_qa_findings: list[VisualQAFinding] | None = None,
    output_dir: str = "",
) -> str:
```

Change to:

```python
def generate_report_html(
    result: RemediationResult,
    visual_qa_findings: list[VisualQAFinding] | None = None,
    output_dir: str = "",
    hybrid_ocr_result: ScannedPageResult | None = None,
    mistral_ocr_result: ScannedPageResult | None = None,
) -> str:
```

Add the import at the top of the file:

```python
from src.tools.scanned_page_ocr import ScannedPageResult
```

- [ ] **Step 2: Add comparison section generation**

Add a helper function before `generate_report_html`:

```python
def _generate_ocr_comparison_html(
    hybrid_result: ScannedPageResult,
    mistral_result: ScannedPageResult,
) -> str:
    """Generate HTML for the OCR engine comparison section."""
    hybrid_paras = hybrid_result.paragraphs
    hybrid_headings = [p for p in hybrid_paras if p.heading_level is not None]
    hybrid_tables = hybrid_result.tables

    mistral_headings = [p for p in mistral_result.paragraphs if p.heading_level is not None]

    # Find differences
    diffs = []
    h_diff = len(mistral_headings) - len(hybrid_headings)
    if h_diff > 0:
        diffs.append(f"Mistral found {h_diff} additional heading(s) not detected by the standard engine")
    elif h_diff < 0:
        diffs.append(f"Standard engine found {-h_diff} additional heading(s) not detected by Mistral")

    t_diff = len(mistral_result.tables) - len(hybrid_tables)
    if t_diff > 0:
        diffs.append(f"Mistral found {t_diff} additional table(s) not detected by the standard engine")
    elif t_diff < 0:
        diffs.append(f"Standard engine found {-t_diff} additional table(s) not detected by Mistral")

    diff_html = ""
    if diffs:
        diff_items = "".join(f"<li>{d}</li>" for d in diffs)
        diff_html = f"""
        <h4>Differences Found</h4>
        <ul>{diff_items}</ul>
        """

    return f"""
    <div class="ocr-comparison" style="margin: 1.5em 0; padding: 1em; border: 2px solid #e0e0e0; border-radius: 8px; background: #fafafa;">
        <h3 style="margin-top: 0;">&#x1f50d; OCR Engine Comparison (Experimental)</h3>
        <p>This document was processed through two OCR engines for quality comparison.
        Only the standard engine's output was used for remediation.</p>
        <table style="border-collapse: collapse; width: 100%; margin: 1em 0;">
            <thead>
                <tr style="background: #f0f0f0;">
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Metric</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Standard (Hybrid)</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Experimental (Mistral)</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Paragraphs</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_paras)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_result.paragraphs)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Headings</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_headings)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_headings)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Tables</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_tables)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_result.tables)}</td>
                </tr>
            </tbody>
        </table>
        {diff_html}
    </div>
    """
```

- [ ] **Step 3: Insert the comparison section into the report**

In `generate_report_html`, after the visual QA section and before the WCAG technical details, add:

```python
    # OCR comparison section (experimental)
    ocr_comparison_html = ""
    if hybrid_ocr_result and mistral_ocr_result and mistral_ocr_result.success:
        ocr_comparison_html = _generate_ocr_comparison_html(hybrid_ocr_result, mistral_ocr_result)
```

Then insert `{ocr_comparison_html}` into the HTML template string at the appropriate location (after the visual QA section, before the technical details `<details>` block).

- [ ] **Step 4: Run existing tests**

Run: `pytest tests/ -v --timeout=60 -x -q 2>&1 | tail -20`
Expected: All PASS (comparison section is only generated when `mistral_ocr_result` is provided)

- [ ] **Step 5: Commit**

```bash
git add src/tools/report_generator.py
git commit -m "feat: OCR comparison section in report when Mistral results available"
```

---

### Task 7: pyproject.toml + Existing Test Suite Verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

Add to the `dependencies` list in `pyproject.toml`:

```toml
    "markdown-it-py>=3.0",
    "mistralai>=2.3",
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v --timeout=60 -q 2>&1 | tail -20`
Expected: All tests PASS including new Mistral parser tests. Existing tests unaffected.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add markdown-it-py and mistralai to project dependencies"
```
