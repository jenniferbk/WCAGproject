"""Mistral OCR markdown-to-DocumentModel parser.

Converts markdown (as returned by Mistral OCR) into the project's
DocumentModel types: ParagraphInfo, TableInfo, LinkInfo, ContentOrderItem.

Uses markdown-it-py for tokenization.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    LinkInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.models.pipeline import ApiUsage

logger = logging.getLogger(__name__)

# Regex for scanned page headers like "152 MAYER", "23 SMITH AND JONES"
_PAGE_HEADER_RE = re.compile(r"^\d+\s+[A-Z][A-Z\s]+$")


def parse_page_markdown(
    markdown: str,
    page_index: int,
    tables: list[dict[str, Any]] | None = None,
    para_offset: int = 0,
    table_offset: int = 0,
    link_offset: int = 0,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[LinkInfo], list[ContentOrderItem]]:
    """Parse a page of markdown into DocumentModel objects.

    Args:
        markdown: Markdown text for one page.
        page_index: 0-based page number.
        tables: Optional list of structured table dicts from Mistral
                (each has 'content' with markdown table string, 'id' like 'tbl-0.md').
        para_offset: Starting index for paragraph IDs.
        table_offset: Starting index for table IDs.
        link_offset: Starting index for link IDs.

    Returns:
        Tuple of (paragraphs, tables, links, content_order).
    """
    # Strip page headers from the markdown before parsing
    markdown = _strip_page_headers(markdown)

    md_parser = MarkdownIt("commonmark").enable("table")
    tokens = md_parser.parse(markdown)

    paragraphs: list[ParagraphInfo] = []
    out_tables: list[TableInfo] = []
    links: list[LinkInfo] = []
    content_order: list[ContentOrderItem] = []

    para_idx = para_offset
    tbl_idx = table_offset
    lnk_idx = link_offset

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # --- Headings ---
        if tok.type == "heading_open":
            level = int(tok.tag[1])  # h1 -> 1, h2 -> 2, etc.
            # Next token is the inline content
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            para_id = f"ocr_p_{para_idx}"

            runs, para_links, lnk_idx = _extract_runs_and_links(
                inline_tok, para_id, lnk_idx, page_index
            )
            text = "".join(r.text for r in runs)

            para = ParagraphInfo(
                id=para_id,
                text=text,
                heading_level=level,
                runs=runs,
                links=para_links,
                page_number=page_index,
                style_name=f"Heading{level}",
            )
            paragraphs.append(para)
            links.extend(para_links)
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=para_id
            ))
            para_idx += 1
            # Skip to heading_close
            i += 3  # heading_open, inline, heading_close
            continue

        # --- Paragraphs ---
        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None

            # Check for table reference [tbl-N.md](tbl-N.md)
            table_ref = _check_table_reference(inline_tok, tables)
            if table_ref is not None:
                tbl_id = f"ocr_tbl_{tbl_idx}"
                tbl = _parse_markdown_table_string(
                    table_ref, tbl_id, page_index
                )
                if tbl is not None:
                    out_tables.append(tbl)
                    content_order.append(ContentOrderItem(
                        content_type=ContentType.TABLE, id=tbl_id
                    ))
                    tbl_idx += 1
                i += 3  # paragraph_open, inline, paragraph_close
                continue

            para_id = f"ocr_p_{para_idx}"
            runs, para_links, lnk_idx = _extract_runs_and_links(
                inline_tok, para_id, lnk_idx, page_index
            )
            text = "".join(r.text for r in runs)

            if text.strip():
                para = ParagraphInfo(
                    id=para_id,
                    text=text,
                    runs=runs,
                    links=para_links,
                    page_number=page_index,
                )
                paragraphs.append(para)
                links.extend(para_links)
                content_order.append(ContentOrderItem(
                    content_type=ContentType.PARAGRAPH, id=para_id
                ))
                para_idx += 1
            i += 3  # paragraph_open, inline, paragraph_close
            continue

        # --- Blockquotes ---
        if tok.type == "blockquote_open":
            # Find content inside blockquote (paragraph_open/inline/paragraph_close)
            j = i + 1
            while j < len(tokens) and tokens[j].type != "blockquote_close":
                if tokens[j].type == "paragraph_open" and j + 1 < len(tokens):
                    inline_tok = tokens[j + 1]
                    para_id = f"ocr_p_{para_idx}"
                    runs, para_links, lnk_idx = _extract_runs_and_links(
                        inline_tok, para_id, lnk_idx, page_index
                    )
                    text = "".join(r.text for r in runs)
                    if text.strip():
                        para = ParagraphInfo(
                            id=para_id,
                            text=text,
                            runs=runs,
                            links=para_links,
                            page_number=page_index,
                        )
                        paragraphs.append(para)
                        links.extend(para_links)
                        content_order.append(ContentOrderItem(
                            content_type=ContentType.PARAGRAPH, id=para_id
                        ))
                        para_idx += 1
                j += 1
            i = j + 1  # skip past blockquote_close
            continue

        # --- Tables (inline markdown tables) ---
        if tok.type == "table_open":
            tbl_id = f"ocr_tbl_{tbl_idx}"
            tbl, end_idx = _parse_inline_table(tokens, i, tbl_id, page_index)
            if tbl is not None:
                out_tables.append(tbl)
                content_order.append(ContentOrderItem(
                    content_type=ContentType.TABLE, id=tbl_id
                ))
                tbl_idx += 1
            i = end_idx + 1
            continue

        i += 1

    return paragraphs, out_tables, links, content_order


def _strip_page_headers(markdown: str) -> str:
    """Remove scanned page headers (e.g. '152 MAYER') from start of markdown."""
    lines = markdown.split("\n")
    if lines and _PAGE_HEADER_RE.match(lines[0].strip()):
        lines = lines[1:]
        # Also strip leading blank line after header
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines)


def _extract_runs_and_links(
    inline_tok: Any | None,
    paragraph_id: str,
    link_offset: int,
    page_number: int,
) -> tuple[list[RunInfo], list[LinkInfo], int]:
    """Walk inline token children and extract runs + links.

    Returns (runs, links, updated_link_offset).
    """
    if inline_tok is None or not inline_tok.children:
        return [], [], link_offset

    runs: list[RunInfo] = []
    links: list[LinkInfo] = []
    lnk_idx = link_offset

    bold = False
    italic = False
    in_link = False
    link_href = ""
    link_text_parts: list[str] = []

    for child in inline_tok.children:
        if child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type == "link_open":
            in_link = True
            link_href = (child.attrs or {}).get("href", "")
            link_text_parts = []
        elif child.type == "link_close":
            link_text = "".join(link_text_parts)
            if link_text:
                link = LinkInfo(
                    id=f"ocr_link_{lnk_idx}",
                    text=link_text,
                    url=link_href,
                    paragraph_id=paragraph_id,
                    page_number=page_number,
                )
                links.append(link)
                lnk_idx += 1
            in_link = False
            link_href = ""
            link_text_parts = []
        elif child.type == "text" or child.type == "softbreak":
            text = child.content if child.type == "text" else " "
            if text:
                run = RunInfo(
                    text=text,
                    bold=True if bold else None,
                    italic=True if italic else None,
                )
                runs.append(run)
                if in_link:
                    link_text_parts.append(text)

    return runs, links, lnk_idx


def _check_table_reference(
    inline_tok: Any | None,
    tables: list[dict[str, Any]] | None,
) -> str | None:
    """Check if an inline token is a table reference like [tbl-0.md](tbl-0.md).

    Returns the table's markdown content string if found, None otherwise.
    """
    if inline_tok is None or not inline_tok.children or not tables:
        return None

    # Look for link_open with href matching tbl-N.md
    for child in inline_tok.children:
        if child.type == "link_open":
            href = (child.attrs or {}).get("href", "")
            if re.match(r"^tbl-\d+\.md$", href):
                # Find matching table in the tables list
                for tbl_data in tables:
                    if tbl_data.get("id") == href:
                        return tbl_data.get("content", "")
    return None


def _parse_markdown_table_string(
    md_table: str,
    table_id: str,
    page_number: int,
) -> TableInfo | None:
    """Parse a markdown table string into TableInfo.

    Handles format like:
        | H1 | H2 |
        |---|---|
        | c1 | c2 |
    """
    lines = [line.strip() for line in md_table.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return None

    # Find separator line (the |---|---| line)
    sep_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^\|[\s\-:|]+\|$", line):
            sep_idx = idx
            break

    if sep_idx is None or sep_idx == 0:
        return None

    header_lines = lines[:sep_idx]
    data_lines = lines[sep_idx + 1:]

    def parse_row(line: str) -> list[CellInfo]:
        # Strip leading/trailing pipes and split
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        cells = [c.strip() for c in line.split("|")]
        return [CellInfo(text=c) for c in cells]

    rows: list[list[CellInfo]] = []
    for hl in header_lines:
        rows.append(parse_row(hl))
    for dl in data_lines:
        rows.append(parse_row(dl))

    col_count = max(len(r) for r in rows) if rows else 0
    header_row_count = len(header_lines)

    return TableInfo(
        id=table_id,
        rows=rows,
        header_row_count=header_row_count,
        has_header_style=header_row_count > 0,
        row_count=len(rows),
        col_count=col_count,
        page_number=page_number,
    )


def _parse_inline_table(
    tokens: list[Any],
    start: int,
    table_id: str,
    page_number: int,
) -> tuple[TableInfo | None, int]:
    """Parse a table from markdown-it tokens starting at table_open.

    Returns (TableInfo, index_of_table_close).
    """
    rows: list[list[CellInfo]] = []
    header_row_count = 0
    in_thead = False
    current_row: list[CellInfo] = []

    i = start + 1  # skip table_open
    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "table_close":
            break
        elif tok.type == "thead_open":
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
            # Next token should be inline with cell content
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                cell_text = tokens[i + 1].content or ""
                current_row.append(CellInfo(text=cell_text.strip()))
            else:
                current_row.append(CellInfo(text=""))
        i += 1

    col_count = max(len(r) for r in rows) if rows else 0

    tbl = TableInfo(
        id=table_id,
        rows=rows,
        header_row_count=header_row_count,
        has_header_style=header_row_count > 0,
        row_count=len(rows),
        col_count=col_count,
        page_number=page_number,
    )
    return tbl, i


def _parse_mistral_table(
    tables: list[dict[str, Any]],
    table_idx: int,
    table_id: str,
    page_number: int,
) -> TableInfo | None:
    """Convert a Mistral table object to TableInfo.

    Args:
        tables: List of table dicts from Mistral (each has 'content' key).
        table_idx: Index into the tables list.
        table_id: ID to assign to the TableInfo.
        page_number: 0-based page number.
    """
    if table_idx >= len(tables):
        return None

    tbl_data = tables[table_idx]
    content = tbl_data.get("content", "")
    if not content:
        return None

    return _parse_markdown_table_string(content, table_id, page_number)


# ── Multi-page stitching ────────────────────────────────────────────


def stitch_pages(
    pages_md: list[str],
    page_tables: list[list | None],
) -> tuple[list[ParagraphInfo], list[TableInfo], list[LinkInfo], list[ContentOrderItem]]:
    """Stitch multiple pages of markdown into sequential DocumentModel objects.

    Calls ``parse_page_markdown()`` for each page with incrementing offsets
    so IDs are globally sequential (e.g. ocr_p_0 … ocr_p_N across all pages).

    Args:
        pages_md: List of markdown strings, one per page.
        page_tables: Parallel list; each entry is either a list of structured
                     table dicts (from Mistral) or None.

    Returns:
        Tuple of (paragraphs, tables, links, content_order) across all pages.
    """
    all_paras: list[ParagraphInfo] = []
    all_tables: list[TableInfo] = []
    all_links: list[LinkInfo] = []
    all_order: list[ContentOrderItem] = []

    para_offset = 0
    table_offset = 0
    link_offset = 0

    for page_idx, md in enumerate(pages_md):
        if not md or not md.strip():
            continue

        tables_for_page = page_tables[page_idx] if page_idx < len(page_tables) else None

        paras, tables, links, order = parse_page_markdown(
            markdown=md,
            page_index=page_idx,
            tables=tables_for_page,
            para_offset=para_offset,
            table_offset=table_offset,
            link_offset=link_offset,
        )

        all_paras.extend(paras)
        all_tables.extend(tables)
        all_links.extend(links)
        all_order.extend(order)

        para_offset += len(paras)
        table_offset += len(tables)
        link_offset += len(links)

    return all_paras, all_tables, all_links, all_order


# ── Mistral OCR API client ──────────────────────────────────────────


def process_scanned_pages_mistral(
    pdf_path: Path,
    scanned_page_numbers: list[int],
    api_key: str | None = None,
) -> "ScannedPageResult":
    """Run Mistral OCR on specific pages of a PDF.

    Uploads the PDF, runs OCR on the requested pages, parses the markdown
    output via ``stitch_pages()``, then cleans up the uploaded file.

    Args:
        pdf_path: Path to the PDF file.
        scanned_page_numbers: 0-based page numbers to OCR.
        api_key: Mistral API key.  Falls back to ``MISTRAL_API_KEY`` env var.

    Returns:
        ``ScannedPageResult`` with parsed paragraphs, tables, and metadata.
        On any failure, returns a result with ``success=False`` and an error
        message.
    """
    # Lazy import to avoid hard dependency when not using Mistral
    from src.tools.scanned_page_ocr import ScannedPageResult

    key = api_key or os.environ.get("MISTRAL_API_KEY")
    if not key:
        return ScannedPageResult(
            success=False,
            error="No Mistral API key provided (parameter or MISTRAL_API_KEY env var)",
        )

    try:
        from mistralai.client import Mistral
        from mistralai.client import models as mistral_models
    except ImportError:
        return ScannedPageResult(
            success=False,
            error="mistralai package is not installed",
        )

    file_id: str | None = None
    try:
        client = Mistral(api_key=key)

        # 1. Upload PDF
        pdf_bytes = pdf_path.read_bytes()
        upload_resp = client.files.upload(
            file=mistral_models.File(
                fileName=pdf_path.name,
                content=pdf_bytes,
            ),
            purpose="ocr",
        )
        file_id = upload_resp.id
        logger.info("Uploaded %s as file_id=%s", pdf_path.name, file_id)

        # 2. Run OCR  (Mistral pages are 0-based)
        ocr_resp = client.ocr.process(
            model="mistral-ocr-latest",
            document=mistral_models.FileChunk(file_id=file_id),
            pages=scanned_page_numbers,
            include_image_base64=False,
            table_format="markdown",
        )

        # 3. Collect per-page markdown + tables
        pages_md: list[str] = []
        page_tables: list[list | None] = []

        for page_obj in ocr_resp.pages:
            pages_md.append(page_obj.markdown or "")
            if page_obj.tables:
                page_tables.append([
                    {"id": t.id, "content": t.content}
                    for t in page_obj.tables
                ])
            else:
                page_tables.append(None)

        # 4. Stitch into DocumentModel objects
        paragraphs, tables, links, order = stitch_pages(pages_md, page_tables)

        # 5. Build usage info
        usage = ocr_resp.usage_info
        api_usage = [
            ApiUsage(
                phase="ocr",
                model="mistral-ocr-latest",
                input_tokens=usage.pages_processed if usage else 0,
                output_tokens=0,
            )
        ]

        return ScannedPageResult(
            success=True,
            paragraphs=paragraphs,
            tables=tables,
            figures=[],
            pages_processed=scanned_page_numbers,
            api_usage=api_usage,
        )

    except Exception as exc:
        logger.exception("Mistral OCR failed for %s", pdf_path.name)
        return ScannedPageResult(
            success=False,
            error=f"Mistral OCR error: {exc}",
        )
    finally:
        # 6. Clean up uploaded file
        if file_id is not None:
            try:
                client.files.delete(file_id=file_id)  # type: ignore[possibly-undefined]
                logger.info("Deleted uploaded file %s", file_id)
            except Exception:
                logger.warning("Failed to delete uploaded file %s", file_id, exc_info=True)
