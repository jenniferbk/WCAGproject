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
        Dict mapping page_number to PNG bytes.
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

    Two-step: HTML -> PDF via WeasyPrint, then PDF -> per-page PNG via PyMuPDF.

    Args:
        html_path: Path to the companion HTML file.

    Returns:
        List of PNG bytes, one per rendered page. Empty list on failure.
    """
    if not Path(html_path).exists():
        logger.warning("HTML file not found: %s", html_path)
        return []

    tmp_pdf_path = None
    try:
        from weasyprint import HTML

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_pdf_path = tmp.name

        HTML(filename=html_path).write_pdf(tmp_pdf_path)

        pages: list[bytes] = []
        doc = fitz.open(tmp_pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=RENDER_DPI)
            pages.append(pix.tobytes("png"))
        doc.close()

        logger.info("Rendered HTML to %d page PNGs", len(pages))
        return pages

    except Exception as e:
        logger.warning("Failed to render HTML to PNGs: %s", e)
        return []
    finally:
        if tmp_pdf_path:
            Path(tmp_pdf_path).unlink(missing_ok=True)
