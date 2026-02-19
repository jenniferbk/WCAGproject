"""Render accessible HTML to PDF/UA-1 via WeasyPrint.

Wraps WeasyPrint's HTML-to-PDF conversion with error handling and
the pdf_variant='pdf/ua-1' flag for PDF/UA compliance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PdfOutputResult:
    """Result of rendering HTML to PDF."""
    success: bool
    output_path: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def render_pdf(
    html_string: str,
    output_path: str | Path,
    css: str = "",
) -> PdfOutputResult:
    """Render an HTML string to a PDF/UA-1 file via WeasyPrint.

    Args:
        html_string: Complete HTML document string.
        output_path: Path where the PDF will be written.
        css: Optional additional CSS stylesheet string.

    Returns:
        PdfOutputResult with success/failure and output path.
    """
    output_path = Path(output_path)
    warnings: list[str] = []

    if not html_string or not html_string.strip():
        return PdfOutputResult(
            success=False,
            error="Empty HTML string provided",
            warnings=warnings,
        )

    try:
        from weasyprint import HTML, CSS

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html_doc = HTML(string=html_string)

        stylesheets = []
        if css:
            stylesheets.append(CSS(string=css))

        html_doc.write_pdf(
            str(output_path),
            stylesheets=stylesheets or None,
            pdf_variant="pdf/ua-1",
        )

        if not output_path.exists():
            return PdfOutputResult(
                success=False,
                error="PDF file was not created",
                warnings=warnings,
            )

        logger.info("PDF rendered: %s (%d bytes)", output_path, output_path.stat().st_size)

        return PdfOutputResult(
            success=True,
            output_path=str(output_path),
            warnings=warnings,
        )

    except ImportError as e:
        return PdfOutputResult(
            success=False,
            error=f"WeasyPrint not available: {e}",
            warnings=warnings,
        )
    except Exception as e:
        logger.exception("PDF rendering failed")
        return PdfOutputResult(
            success=False,
            error=f"PDF rendering failed: {e}",
            warnings=warnings,
        )
