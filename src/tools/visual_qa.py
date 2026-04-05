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
