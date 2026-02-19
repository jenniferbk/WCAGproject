"""Generate semantic HTML from PDF pages using Gemini's multimodal vision.

Renders each PDF page to a high-resolution image and sends it to Gemini
with a prompt requesting accessible, semantic HTML. The result is a
properly structured HTML document that can be converted to PDF/UA.

Pages are processed in batches to respect API limits.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from src.models.document import DocumentModel, ImageInfo
from src.models.pipeline import ApiUsage, RemediationStrategy

logger = logging.getLogger(__name__)

# Batching and rate limit settings
PAGES_PER_BATCH = 5
DELAY_BETWEEN_BATCHES = 15  # seconds
MAX_RETRIES = 3
INITIAL_BACKOFF = 30  # seconds, doubles each retry
PAGE_DPI = 200  # render resolution for Gemini vision


@dataclass
class GeminiHtmlResult:
    """Result of generating HTML from PDF pages via Gemini."""
    success: bool
    html: str = ""
    html_path: str = ""
    pages_processed: int = 0
    warnings: list[str] = field(default_factory=list)
    api_usage: list[ApiUsage] = field(default_factory=list)
    error: str = ""


# Prompt template for Gemini page-to-HTML conversion
PAGE_HTML_PROMPT = """You are an accessibility specialist converting PDF pages to semantic HTML.

## Context
- Document title: {title}
- Document language: {language}
- Course: {course_context}

## Instructions

Convert the following PDF page image(s) to clean, semantic HTML. The HTML must be:

1. **Structurally correct:** Use proper heading hierarchy (h1-h6), paragraph tags, lists (ul/ol/li), tables with th/td
2. **Accessible:** Every image gets descriptive alt text, tables have proper scope attributes on headers
3. **Semantic:** Use the right elements — don't use bold/styling where a heading belongs
4. **Complete:** Capture ALL text content visible on the page, including footnotes, headers/footers, sidebars
5. **Clean:** No inline styles unless needed for essential meaning (e.g., color). No CSS classes.

## Specific Rules

- Headings: Use the heading level that matches the document's visual hierarchy
- Tables: Use `<th scope="col">` for column headers, `<th scope="row">` for row headers
- Images: Describe what the image shows in the alt text. If decorative, use `alt=""`
- Lists: Convert bulleted items to `<ul><li>`, numbered items to `<ol><li>`
- Links: Preserve URLs as `<a href="...">`
- Math: Use plain text approximation (e.g., "x² + 2x + 1") — MathML is not needed
- Page breaks: Do NOT include any page break indicators. Merge content naturally.

## Remediation Data

The following elements have been analyzed and should be tagged as specified:
{remediation_hints}

## Output Format

Return ONLY the HTML body content (no <html>, <head>, <body> tags — just the inner content).
Return as a JSON object with a single key "html" containing the HTML string.
"""

# Schema for structured output
PAGE_HTML_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "html": {"type": "STRING"},
    },
    "required": ["html"],
}


def generate_gemini_html(
    doc_model: DocumentModel,
    strategy: RemediationStrategy | None = None,
    source_path: str = "",
    model: str = "gemini-2.5-flash",
    course_context: str = "",
) -> GeminiHtmlResult:
    """Generate accessible HTML from a PDF using Gemini's vision capabilities.

    Renders each page to a high-res image and sends to Gemini for conversion
    to semantic HTML. Merges results into a complete HTML document.

    Args:
        doc_model: Parsed document model from the PDF.
        strategy: Remediation strategy (provides heading levels, alt text, etc.).
        source_path: Path to the original PDF (used for page rendering).
        model: Gemini model ID.
        course_context: Course context string for the prompt.

    Returns:
        GeminiHtmlResult with the complete HTML document.
    """
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return GeminiHtmlResult(
            success=False,
            error="GEMINI_API_KEY not set",
        )

    pdf_path = source_path or doc_model.source_path
    if not pdf_path or not Path(pdf_path).exists():
        return GeminiHtmlResult(
            success=False,
            error=f"PDF file not found: {pdf_path}",
        )

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
    except Exception as e:
        return GeminiHtmlResult(success=False, error=f"Failed to init Gemini client: {e}")

    # Open PDF and render pages
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return GeminiHtmlResult(success=False, error=f"Failed to open PDF: {e}")

    warnings: list[str] = []
    page_htmls: list[str] = []
    all_usage: list[ApiUsage] = []
    total_pages = len(doc)

    # Build remediation hints from strategy
    hints = _build_remediation_hints(doc_model, strategy)

    # Determine title and language from strategy/model
    title = doc_model.metadata.title or "Untitled Document"
    language = doc_model.metadata.language or "en"
    if strategy:
        for action in strategy.actions:
            if action.action_type == "set_title":
                title = action.parameters.get("title", title)
            elif action.action_type == "set_language":
                language = action.parameters.get("language", language)

    # Process pages in batches
    total_batches = (total_pages + PAGES_PER_BATCH - 1) // PAGES_PER_BATCH

    for batch_start in range(0, total_pages, PAGES_PER_BATCH):
        batch_end = min(batch_start + PAGES_PER_BATCH, total_pages)
        batch_num = batch_start // PAGES_PER_BATCH + 1

        logger.info(
            "Processing pages %d-%d (batch %d/%d)",
            batch_start + 1, batch_end, batch_num, total_batches,
        )

        try:
            batch_html, batch_usage = _process_page_batch(
                client, model, doc, batch_start, batch_end,
                title=title, language=language,
                course_context=course_context,
                hints=hints,
            )
            page_htmls.append(batch_html)
            if batch_usage:
                all_usage.append(batch_usage)
        except Exception as e:
            warning = f"Batch {batch_num} failed: {e}"
            logger.warning(warning)
            warnings.append(warning)
            # Add placeholder
            page_htmls.append(
                f"<!-- Page batch {batch_start+1}-{batch_end} failed: {e} -->"
            )

        # Rate limit pause between batches
        if batch_end < total_pages:
            logger.info("Waiting %ds before next batch...", DELAY_BETWEEN_BATCHES)
            time.sleep(DELAY_BETWEEN_BATCHES)

    doc.close()

    # Assemble complete HTML document
    body_content = "\n\n".join(page_htmls)
    full_html = _wrap_html(body_content, title=title, language=language, doc_model=doc_model)

    return GeminiHtmlResult(
        success=True,
        html=full_html,
        pages_processed=total_pages,
        warnings=warnings,
        api_usage=all_usage,
    )


def _process_page_batch(
    client,
    model: str,
    doc: fitz.Document,
    start: int,
    end: int,
    title: str = "",
    language: str = "en",
    course_context: str = "",
    hints: str = "",
) -> tuple[str, ApiUsage | None]:
    """Process a batch of PDF pages through Gemini vision.

    Returns tuple of (HTML body content, ApiUsage or None).
    """
    from google.genai import types

    prompt = PAGE_HTML_PROMPT.format(
        title=title,
        language=language,
        course_context=course_context or "Not specified",
        remediation_hints=hints or "None",
    )

    content_parts: list = [prompt]

    for page_idx in range(start, end):
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=PAGE_DPI)
        png_bytes = pix.tobytes("png")

        content_parts.append(
            types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        )
        content_parts.append(f"Above is page {page_idx + 1} of {len(doc)}.")

    # Call Gemini with retry
    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=content_parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=PAGE_HTML_SCHEMA,
                    temperature=0.2,
                ),
            )
            resp_text = response.text
            if resp_text is None:
                # Gemini returned empty content (safety block or empty response)
                logger.warning(
                    "Gemini returned empty text for pages %d-%d (possible safety block)",
                    start_page + 1, end_page,
                )
                return "", _extract_usage(response, model)
            data = json.loads(resp_text)
            usage = _extract_usage(response, model)
            return data.get("html", ""), usage
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < MAX_RETRIES:
                    backoff = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "Rate limited (attempt %d/%d), waiting %ds",
                        attempt + 1, MAX_RETRIES + 1, backoff,
                    )
                    time.sleep(backoff)
                    continue
            raise

    raise last_error  # type: ignore[misc]


def _extract_usage(response, model: str) -> ApiUsage | None:
    """Extract token usage from a Gemini response."""
    try:
        meta = response.usage_metadata
        return ApiUsage(
            phase="gemini_html",
            model=model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        )
    except Exception:
        return None


def _build_remediation_hints(
    doc_model: DocumentModel,
    strategy: RemediationStrategy | None,
) -> str:
    """Build hints string from the remediation strategy for the Gemini prompt."""
    if not strategy:
        return "No remediation strategy available."

    hints: list[str] = []
    para_by_id = {p.id: p for p in doc_model.paragraphs}
    img_by_id = {img.id: img for img in doc_model.images}

    for action in strategy.actions:
        if action.status == "skipped":
            continue

        if action.action_type == "set_heading_level":
            para = para_by_id.get(action.element_id)
            level = action.parameters.get("level", 1)
            if para:
                hints.append(
                    f"- \"{para.text[:80]}\" → Heading {level} (H{level})"
                )

        elif action.action_type == "set_alt_text":
            img = img_by_id.get(action.element_id)
            alt = action.parameters.get("alt_text", "")
            if img and alt:
                hints.append(
                    f"- Image {img.id} (page {(img.page_number or 0) + 1}) → alt=\"{alt[:100]}\""
                )

        elif action.action_type == "mark_header_rows":
            count = action.parameters.get("header_count", 1)
            hints.append(
                f"- Table {action.element_id} → {count} header row(s)"
            )

    return "\n".join(hints) if hints else "No specific remediation hints."


def _wrap_html(
    body_content: str,
    title: str = "",
    language: str = "en",
    doc_model: DocumentModel | None = None,
) -> str:
    """Wrap body content in a complete HTML document with embedded images."""
    import html as html_mod

    # Embed images as base64 data URIs
    image_section = ""
    if doc_model and doc_model.images:
        img_parts = []
        for img in doc_model.images:
            if img.image_data and not img.is_decorative:
                b64 = base64.b64encode(img.image_data).decode("ascii")
                mime = img.content_type or "image/png"
                alt = html_mod.escape(img.alt_text or "Image")
                img_parts.append(
                    f'<!-- Image {img.id} from page {(img.page_number or 0) + 1} -->'
                )
                # Don't embed inline — Gemini's HTML may reference them by description
        image_section = "\n".join(img_parts)

    return f"""<!DOCTYPE html>
<html lang="{html_mod.escape(language)}">
<head>
    <meta charset="utf-8">
    <title>{html_mod.escape(title)}</title>
    <style>
        body {{ font-family: serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
        th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
        th {{ background-color: #f0f0f0; font-weight: bold; }}
        img {{ max-width: 100%; height: auto; }}
        h1 {{ font-size: 1.8em; }}
        h2 {{ font-size: 1.5em; }}
        h3 {{ font-size: 1.3em; }}
    </style>
</head>
<body>
{body_content}
{image_section}
</body>
</html>"""
