"""OCR and layout analysis for scanned PDF pages using Gemini vision.

Detects scanned pages (full-page images with no text layer), segments them
into content regions (headings, paragraphs, tables, figures, equations),
and converts the results into DocumentModel objects so the rest of the
remediation pipeline can work on real text instead of image descriptions.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import re

import fitz  # PyMuPDF

from src.utils.json_repair import parse_json_lenient

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    ImageInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.models.pipeline import ApiUsage

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────
PAGES_PER_BATCH = 2  # small batches — dense scanned pages produce large OCR output
DELAY_BETWEEN_BATCHES = 10  # seconds
MAX_RETRIES = 3
INITIAL_BACKOFF = 30  # seconds, doubles each retry
PAGE_DPI = 200  # render resolution for Gemini vision
PAGE_DPI_RETRY = 300  # higher resolution for retry on garbled output
DELAY_BETWEEN_PAGES = 5  # seconds between pages for rate limiting


@dataclass
class ScannedPageResult:
    """Result of OCR processing for scanned pages."""
    success: bool
    paragraphs: list[ParagraphInfo] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)
    figures: list[ImageInfo] = field(default_factory=list)
    pages_processed: list[int] = field(default_factory=list)  # 0-based page numbers
    api_usage: list[ApiUsage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""


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


# ── Gemini structured output schema ──────────────────────────────

OCR_PAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "pages": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "page_number": {"type": "INTEGER"},
                    "page_type": {
                        "type": "STRING",
                        "enum": ["text_dominant", "mixed", "purely_visual"],
                    },
                    "regions": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "type": {
                                    "type": "STRING",
                                    "enum": [
                                        "heading",
                                        "paragraph",
                                        "table",
                                        "figure",
                                        "equation",
                                        "caption",
                                        "page_header",
                                        "page_footer",
                                        "footnote",
                                    ],
                                },
                                "text": {"type": "STRING"},
                                "heading_level": {"type": "INTEGER"},
                                "bold": {"type": "BOOLEAN"},
                                "italic": {"type": "BOOLEAN"},
                                "font_size_relative": {
                                    "type": "STRING",
                                    "enum": ["large", "normal", "small"],
                                },
                                "column": {"type": "INTEGER"},
                                "table_data": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "headers": {
                                            "type": "ARRAY",
                                            "items": {"type": "STRING"},
                                        },
                                        "rows": {
                                            "type": "ARRAY",
                                            "items": {
                                                "type": "ARRAY",
                                                "items": {"type": "STRING"},
                                            },
                                        },
                                    },
                                },
                                "figure_description": {"type": "STRING"},
                                "reading_order": {"type": "INTEGER"},
                            },
                            "required": ["type", "reading_order"],
                        },
                    },
                },
                "required": ["page_number", "page_type", "regions"],
            },
        },
    },
    "required": ["pages"],
}


def _load_prompt() -> str:
    """Load the scanned OCR prompt from the prompts directory."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "scanned_ocr.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback minimal prompt
    return (
        "Perform OCR on these scanned PDF pages. Extract ALL text exactly as it "
        "appears. Identify headings, paragraphs, tables, figures, equations, and "
        "footnotes. Return structured JSON."
    )


def _load_table_rescue_prompt() -> str:
    """Load the table rescue prompt from the prompts directory."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "table_rescue.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "Extract the table with caption '{caption}' from this page image. "
        "Return JSON with 'headers' (array of strings) and 'rows' (array of arrays of strings)."
    )


def process_scanned_pages(
    pdf_path: str,
    scanned_page_numbers: list[int],
    course_context: str = "",
    model: str = "gemini-2.5-flash",
    on_progress: Callable[[str], None] | None = None,
) -> ScannedPageResult:
    """Process scanned PDF pages through Gemini for OCR + layout analysis.

    Renders each scanned page to a high-res PNG, sends to Gemini in batches,
    and converts the structured response into DocumentModel objects.

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

    all_paragraphs: list[ParagraphInfo] = []
    all_tables: list[TableInfo] = []
    all_figures: list[ImageInfo] = []
    all_usage: list[ApiUsage] = []
    all_warnings: list[str] = []
    pages_processed: list[int] = []

    # Track offsets for unique IDs across batches
    para_offset = 0
    table_offset = 0
    img_offset = 0

    # Process in batches
    total_batches = (len(scanned_page_numbers) + PAGES_PER_BATCH - 1) // PAGES_PER_BATCH

    for batch_idx in range(0, len(scanned_page_numbers), PAGES_PER_BATCH):
        batch_pages = scanned_page_numbers[batch_idx:batch_idx + PAGES_PER_BATCH]
        batch_num = batch_idx // PAGES_PER_BATCH + 1

        para_offset_before_batch = len(all_paragraphs)

        if on_progress:
            on_progress(
                f"OCR batch {batch_num}/{total_batches}: "
                f"pages {', '.join(str(p + 1) for p in batch_pages)}"
            )
        logger.info(
            "OCR batch %d/%d: pages %s",
            batch_num, total_batches,
            [p + 1 for p in batch_pages],
        )

        try:
            batch_result = _process_ocr_batch(
                client, model, doc, batch_pages, prompt,
            )

            if batch_result is None:
                all_warnings.append(f"OCR batch {batch_num} returned empty")
                # Fall through to single-page retry below
                raise ValueError("Empty batch result")

            page_data_list, usage = batch_result
            if usage:
                all_usage.append(usage)

            _integrate_page_data(
                page_data_list, doc,
                all_paragraphs, all_tables, all_figures,
                pages_processed, para_offset, table_offset, img_offset,
                known_page_numbers=batch_pages,
                gemini_client=client,
                gemini_model=model,
            )
            # Recalculate offsets from actual lists
            para_offset = len(all_paragraphs)
            table_offset = len(all_tables)
            img_offset = len(all_figures)

        except Exception as e:
            # Batch failed — retry each page individually
            if len(batch_pages) > 1:
                logger.warning(
                    "OCR batch %d failed (%s), retrying %d pages individually",
                    batch_num, e, len(batch_pages),
                )
                for single_page in batch_pages:
                    try:
                        if on_progress:
                            on_progress(f"OCR retry: page {single_page + 1}")
                        time.sleep(5)  # brief pause between retries
                        single_result = _process_ocr_batch(
                            client, model, doc, [single_page], prompt,
                        )
                        if single_result is not None:
                            page_data_list, usage = single_result
                            if usage:
                                all_usage.append(usage)
                            _integrate_page_data(
                                page_data_list, doc,
                                all_paragraphs, all_tables, all_figures,
                                pages_processed, len(all_paragraphs),
                                len(all_tables), len(all_figures),
                                known_page_numbers=[single_page],
                                gemini_client=client,
                                gemini_model=model,
                            )
                        else:
                            # Gemini refused (likely RECITATION) — try Tesseract
                            logger.info(
                                "Gemini refused page %d, trying Tesseract fallback",
                                single_page + 1,
                            )
                            tess_paras = _tesseract_fallback(doc, single_page, len(all_paragraphs))
                            if tess_paras:
                                all_paragraphs.extend(tess_paras)
                                pages_processed.append(single_page)
                                logger.info(
                                    "Tesseract extracted %d paragraphs from page %d",
                                    len(tess_paras), single_page + 1,
                                )
                            else:
                                w = f"OCR page {single_page + 1}: both Gemini and Tesseract failed"
                                logger.warning(w)
                                all_warnings.append(w)
                    except Exception as e2:
                        w = f"OCR page {single_page + 1} failed: {e2}"
                        logger.warning(w)
                        all_warnings.append(w)
                # Update offsets after retry loop so next batch uses correct IDs
                para_offset = len(all_paragraphs)
                table_offset = len(all_tables)
                img_offset = len(all_figures)
            else:
                # Single-page batch failed — try Tesseract
                single_page = batch_pages[0]
                logger.info(
                    "Gemini failed for page %d (%s), trying Tesseract fallback",
                    single_page + 1, e,
                )
                tess_paras = _tesseract_fallback(doc, single_page, len(all_paragraphs))
                if tess_paras:
                    all_paragraphs.extend(tess_paras)
                    pages_processed.append(single_page)
                    logger.info(
                        "Tesseract extracted %d paragraphs from page %d",
                        len(tess_paras), single_page + 1,
                    )
                else:
                    warning = f"OCR page {single_page + 1}: both Gemini and Tesseract failed"
                    logger.warning(warning)
                    all_warnings.append(warning)
                # Update offsets after single-page fallback
                para_offset = len(all_paragraphs)
                table_offset = len(all_tables)
                img_offset = len(all_figures)

        # ── Quality check: detect garbled pages and retry ──────────
        # Check paragraphs added in this batch for garbled text
        new_para_count = len(all_paragraphs) - para_offset_before_batch
        if new_para_count > 0:
            garbled_pages = _find_garbled_pages(
                all_paragraphs[para_offset_before_batch:],
            )
            if garbled_pages:
                logger.warning(
                    "Garbled OCR detected on pages %s, retrying at %d DPI",
                    [p + 1 for p in garbled_pages], PAGE_DPI_RETRY,
                )
                # Remove garbled paragraphs for those pages
                garbled_set = set(garbled_pages)
                all_paragraphs[para_offset_before_batch:] = [
                    p for p in all_paragraphs[para_offset_before_batch:]
                    if p.page_number not in garbled_set
                ]
                # Also remove from pages_processed so they get re-added
                for gp in garbled_pages:
                    if gp in pages_processed:
                        pages_processed.remove(gp)

                # Retry each garbled page individually at higher DPI
                for gp in garbled_pages:
                    if on_progress:
                        on_progress(f"OCR quality retry: page {gp + 1} at {PAGE_DPI_RETRY} DPI")
                    time.sleep(5)
                    try:
                        retry_result = _process_ocr_batch(
                            client, model, doc, [gp], prompt,
                            dpi=PAGE_DPI_RETRY,
                        )
                        if retry_result is not None:
                            retry_pages, retry_usage = retry_result
                            if retry_usage:
                                all_usage.append(retry_usage)
                            _integrate_page_data(
                                retry_pages, doc,
                                all_paragraphs, all_tables, all_figures,
                                pages_processed, len(all_paragraphs),
                                len(all_tables), len(all_figures),
                                known_page_numbers=[gp],
                                gemini_client=client,
                                gemini_model=model,
                            )
                        else:
                            # Gemini refused — Tesseract fallback
                            tess_paras = _tesseract_fallback(doc, gp, len(all_paragraphs))
                            if tess_paras:
                                all_paragraphs.extend(tess_paras)
                                pages_processed.append(gp)
                            else:
                                all_warnings.append(
                                    f"OCR page {gp + 1}: garbled output, retry failed"
                                )
                    except Exception as e_retry:
                        all_warnings.append(
                            f"OCR page {gp + 1}: garble retry failed: {e_retry}"
                        )

                # Update offsets after quality retry
                para_offset = len(all_paragraphs)
                table_offset = len(all_tables)
                img_offset = len(all_figures)

        # Rate limit pause between batches
        if batch_idx + PAGES_PER_BATCH < len(scanned_page_numbers):
            logger.info("Waiting %ds before next OCR batch...", DELAY_BETWEEN_BATCHES)
            time.sleep(DELAY_BETWEEN_BATCHES)

    doc.close()

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
    """Convert page data from Gemini and append to accumulator lists.

    Args:
        known_page_numbers: 0-based page numbers we KNOW were sent to Gemini.
            Used to override Gemini's self-reported page_number which can be
            unreliable (e.g., returning 1 instead of 11 for a single-page batch).
        gemini_client: Optional Gemini client for table rescue. When provided,
            paragraphs that look like missed table captions are re-sent to Gemini
            for structured re-extraction.
        gemini_model: Gemini model name to use for table rescue.
    """
    p_off = para_offset
    t_off = table_offset
    i_off = img_offset

    for i, page_data in enumerate(page_data_list):
        # Determine the correct 0-based page number.
        # Strategy: use Gemini's self-reported page_number to find the
        # matching known page, falling back to positional index.
        gemini_page = page_data.get("page_number", 0)
        if known_page_numbers:
            if len(known_page_numbers) == 1:
                # Single-page batch — always use the known page
                page_num = known_page_numbers[0]
            elif gemini_page > 0 and (gemini_page - 1) in known_page_numbers:
                # Gemini's 1-based page matches one of our known pages
                page_num = gemini_page - 1
            elif i < len(known_page_numbers):
                # Positional fallback
                page_num = known_page_numbers[i]
            else:
                # More page_data entries than known pages — use Gemini's
                page_num = gemini_page - 1 if gemini_page > 0 else 0
        else:
            page_num = gemini_page
            if page_num > 0:
                page_num -= 1

        logger.debug(
            "Page data %d: gemini_page=%d → assigned page=%d (known=%s)",
            i, gemini_page, page_num, known_page_numbers,
        )

        paras, tables, figures = _regions_to_model_objects(
            page_data,
            page_number=page_num,
            para_offset=p_off,
            table_offset=t_off,
            img_offset=i_off,
            pdf_doc=pdf_doc,
        )

        all_paragraphs.extend(paras)
        all_tables.extend(tables)
        all_figures.extend(figures)
        pages_processed.append(page_num)

        p_off += len(paras)
        t_off += len(tables)
        i_off += len(figures)

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
        else:
            logger.warning("Page %d: Gemini returned empty (likely RECITATION)",
                           page_number + 1)
    except Exception as e:
        logger.warning("Page %d: Gemini failed (%s)", page_number + 1, e)

    # ── Try 2: Gemini at high DPI ───────────────────────────────
    try:
        time.sleep(3)
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


def _process_ocr_batch(
    client,
    model: str,
    doc: fitz.Document,
    page_numbers: list[int],
    prompt: str,
    dpi: int = PAGE_DPI,
) -> tuple[list[dict], ApiUsage | None] | None:
    """Send a batch of page images to Gemini for OCR.

    Returns (list of page data dicts, ApiUsage) or None on failure.
    """
    from google.genai import types

    content_parts: list = [prompt]

    for page_num in page_numbers:
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")

        content_parts.append(
            types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        )
        content_parts.append(
            f"Above is page {page_num + 1} of {len(doc)}. "
            f"Extract all content from this scanned page."
        )

    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=content_parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=OCR_PAGE_SCHEMA,
                    temperature=0.1,  # low temperature for accurate transcription
                ),
            )
            resp_text = response.text
            if resp_text is None:
                # Log finish reason for debugging
                finish_reason = ""
                try:
                    if response.candidates:
                        finish_reason = str(response.candidates[0].finish_reason)
                except Exception:
                    pass
                logger.warning(
                    "Gemini returned empty for OCR pages %s (finish_reason=%s)",
                    [p + 1 for p in page_numbers], finish_reason or "unknown",
                )
                return None

            try:
                data = json.loads(resp_text)
            except json.JSONDecodeError:
                logger.warning(
                    "OCR JSON parse failed for pages %s (len=%d), trying lenient parser",
                    [p + 1 for p in page_numbers], len(resp_text),
                )
                logger.debug("Raw OCR response (first 500 chars): %s", resp_text[:500])
                data = parse_json_lenient(resp_text)
            usage = _extract_usage(response, model)
            return data.get("pages", []), usage

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
            phase="ocr",
            model=model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        )
    except Exception:
        return None


def _is_leaked_header_footer(text: str) -> bool:
    """Detect page headers/footers that Gemini failed to classify.

    Common academic paper patterns:
    - "TITLE IN ALL CAPS 157"
    - "158 AUTHOR LAST NAME"
    - "Journal Name, Vol(Issue), pp-pp" style lines
    """
    text = text.strip()
    words = text.split()
    if not words or len(words) > 12:
        return False

    # Pattern: ALL CAPS words followed by a page number
    # e.g. "LEARNERS AS INFORMATION PROCESSORS 157"
    if re.match(r'^[A-Z][A-Z\s\'\u2019:,\-]+\d{1,4}\s*$', text):
        return True

    # Pattern: page number followed by ALL CAPS author name
    # e.g. "158 MAYER"
    if re.match(r'^\d{1,4}\s+[A-Z]{2,}(\s+[A-Z]{2,})*\s*$', text):
        return True

    # Pattern: just a page number (1-3 digits, possibly with surrounding whitespace)
    if re.match(r'^\d{1,3}$', text):
        return True

    return False


def _is_garbled_text(text: str, threshold: float = 0.15) -> bool:
    """Detect garbled OCR output that should be retried or discarded.

    Checks for:
    - Words with no vowels (longer than 3 chars)
    - Unusual consonant clusters (3+ consonants not in common English patterns)
    - Excessive accented/special characters in English text
    - Digit-letter mixtures like "hk2"

    Args:
        text: The OCR text to check.
        threshold: Fraction of garbled words to trigger detection (default 20%).

    Returns:
        True if the text appears garbled.
    """
    # Common English 3+ consonant clusters (lowercase)
    _COMMON_CLUSTERS = frozenset({
        "str", "thr", "scr", "spr", "spl", "shr", "ght", "ngs", "nts",
        "sts", "rds", "nds", "rts", "lts", "mps", "nks", "lps", "lks",
        "rks", "nch", "nth", "lds", "lms", "rms", "rns", "wns",
        "cts", "pts", "ngth", "ckl", "sch", "chr", "phr", "rst",
        "nst", "mbl", "ndl", "ngl", "ntl", "tch", "dth",
    })

    words = text.split()
    if len(words) < 4:
        return False  # too short to judge

    garbled_count = 0
    checked = 0
    for word in words:
        clean = word.strip('.,;:!?()[]{}"\'-–—')
        if not clean or len(clean) < 3:
            continue
        checked += 1
        lower = clean.lower()

        # No vowels in a word > 3 chars
        if len(clean) > 3 and not re.search(r'[aeiouAEIOU]', clean):
            garbled_count += 1
            continue

        # Unusual consonant clusters: 3+ consecutive consonants
        # not matching common English patterns
        clusters = re.findall(r'[bcdfghjklmnpqrstvwxyz]{3,}', lower)
        has_unusual = False
        for cluster in clusters:
            # Check if ANY common cluster is a substring
            if not any(cc in cluster for cc in _COMMON_CLUSTERS):
                has_unusual = True
                break
        if has_unusual and len(clean) > 3:
            garbled_count += 1
            continue

        # Non-ASCII chars outside Latin Extended range are likely OCR artifacts.
        # Common accented Latin chars (U+00C0-U+024F) are legitimate in
        # academic text (author names, French/Spanish/German citations).
        non_latin_exotic = sum(
            1 for c in clean
            if ord(c) > 127 and not (0x00C0 <= ord(c) <= 0x024F)
        )
        if non_latin_exotic >= 1 and len(clean) > 3:
            garbled_count += 1
            continue

        # Digit-letter mix (e.g., "hk2", "8s" is ok because it's short)
        if len(clean) > 2 and re.match(r'^[a-z]+\d[a-z]+$', clean, re.IGNORECASE):
            garbled_count += 1
            continue

    if checked == 0:
        return False
    return garbled_count / checked >= threshold


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

    has_col1 = any(r.get("column", 0) == 1 for r in regions)
    has_col2 = any(r.get("column", 0) == 2 for r in regions)

    if not has_col1 and not has_col2:
        return sorted(regions, key=key)

    # ── Column balance validation ───────────────────────────────
    _SKIP_TYPES = ("heading", "page_header", "page_footer")

    if has_col2 and not has_col1:
        first_col2_order = min(
            r.get("reading_order", 0)
            for r in regions if r.get("column", 0) == 2
        )
        reassigned = 0
        for r in regions:
            col = r.get("column", 0) or 0
            if col == 0 and r.get("reading_order", 0) < first_col2_order:
                if r.get("type") not in _SKIP_TYPES:
                    r["column"] = 1
                    reassigned += 1
        if reassigned:
            logger.info(
                "Column balance: reassigned %d column=0 regions to column=1 "
                "(had column=2 but no column=1)",
                reassigned,
            )
            has_col1 = True

    if has_col1 and not has_col2:
        last_col1_order = max(
            r.get("reading_order", 0)
            for r in regions if r.get("column", 0) == 1
        )
        reassigned = 0
        for r in regions:
            col = r.get("column", 0) or 0
            if col == 0 and r.get("reading_order", 0) > last_col1_order:
                if r.get("type") not in _SKIP_TYPES:
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


def _regions_to_model_objects(
    page_data: dict,
    page_number: int,
    para_offset: int,
    table_offset: int,
    img_offset: int,
    pdf_doc: fitz.Document | None = None,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]:
    """Convert Gemini OCR regions into DocumentModel objects.

    Args:
        page_data: Dict with 'page_type' and 'regions' from Gemini.
        page_number: 0-based page number.
        para_offset: Starting offset for paragraph IDs (ocr_p_N).
        table_offset: Starting offset for table IDs (ocr_tbl_N).
        img_offset: Starting offset for image IDs (ocr_img_N).
        pdf_doc: Optional PyMuPDF document for extracting figure images.

    Returns:
        Tuple of (paragraphs, tables, figures).
    """
    regions = page_data.get("regions", [])
    # Column-aware sort: left col → right col, not raw reading_order
    regions = _sort_regions_by_column(regions)

    paragraphs: list[ParagraphInfo] = []
    tables: list[TableInfo] = []
    figures: list[ImageInfo] = []

    para_idx = 0
    tbl_idx = 0
    fig_idx = 0

    for region in regions:
        region_type = region.get("type", "")
        text = region.get("text", "").strip()
        region_column = region.get("column", 0) or 0  # 0=full-width, 1=left, 2=right

        # Skip page headers/footers/page numbers — repeated nav elements
        if region_type in ("page_header", "page_footer"):
            continue

        # Catch headers/footers that Gemini misclassified as paragraphs or headings
        if text and _is_leaked_header_footer(text):
            logger.debug("Filtered leaked header/footer: %r", text[:80])
            continue

        if region_type == "heading":
            level = region.get("heading_level", 2)
            level = max(1, min(6, level))  # clamp to 1-6
            if not text:
                continue
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name=f"Heading {level}",
                heading_level=level,
                runs=[RunInfo(
                    text=text,
                    bold=True,
                    font_size_pt=_relative_to_pt(region.get("font_size_relative", "large")),
                )],
                page_number=page_number,
                column=region_column,
            ))
            para_idx += 1

        elif region_type == "paragraph":
            if not text:
                continue
            bold = region.get("bold", False)
            italic = region.get("italic", False)
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Normal",
                runs=[RunInfo(
                    text=text,
                    bold=bold if bold else None,
                    italic=italic if italic else None,
                    font_size_pt=_relative_to_pt(region.get("font_size_relative", "normal")),
                )],
                page_number=page_number,
                column=region_column,
            ))
            para_idx += 1

        elif region_type == "equation":
            if not text:
                continue
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Normal",
                runs=[RunInfo(
                    text=text,
                    italic=True,  # equations typically italic
                    font_size_pt=_relative_to_pt(region.get("font_size_relative", "normal")),
                )],
                page_number=page_number,
                column=region_column,
            ))
            para_idx += 1

        elif region_type == "caption":
            if not text:
                continue
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Normal",
                runs=[RunInfo(
                    text=text,
                    italic=True,
                    font_size_pt=_relative_to_pt("small"),
                )],
                page_number=page_number,
                column=region_column,
            ))
            para_idx += 1

        elif region_type == "footnote":
            if not text:
                continue
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Normal",
                runs=[RunInfo(
                    text=text,
                    font_size_pt=_relative_to_pt("small"),
                )],
                page_number=page_number,
                column=region_column,
            ))
            para_idx += 1

        elif region_type == "table":
            table_data = region.get("table_data", {})
            headers = table_data.get("headers", [])
            rows = table_data.get("rows", [])

            if not headers and not rows:
                # Fall back to rendering as text if no structured data
                if text:
                    paragraphs.append(ParagraphInfo(
                        id=f"ocr_p_{para_offset + para_idx}",
                        text=text,
                        style_name="Normal",
                        page_number=page_number,
                        column=region_column,
                    ))
                    para_idx += 1
                continue

            # Build table rows
            table_rows: list[list[CellInfo]] = []

            # Header row
            if headers:
                table_rows.append([
                    CellInfo(text=h, paragraphs=[h]) for h in headers
                ])

            # Data rows
            for row_cells in rows:
                table_rows.append([
                    CellInfo(text=c, paragraphs=[c]) for c in row_cells
                ])

            col_count = max(
                (len(r) for r in table_rows),
                default=0,
            )

            tables.append(TableInfo(
                id=f"ocr_tbl_{table_offset + tbl_idx}",
                rows=table_rows,
                header_row_count=1 if headers else 0,
                has_header_style=bool(headers),
                row_count=len(table_rows),
                col_count=col_count,
                page_number=page_number,
            ))
            tbl_idx += 1

        elif region_type == "figure":
            desc = region.get("figure_description", "") or text
            # Extract the page image as the figure image
            img_data = None
            width = None
            height = None
            if pdf_doc is not None and 0 <= page_number < len(pdf_doc):
                try:
                    page = pdf_doc[page_number]
                    pix = page.get_pixmap(dpi=150)
                    img_data = pix.tobytes("png")
                    width = pix.width
                    height = pix.height
                except Exception:
                    pass

            figures.append(ImageInfo(
                id=f"ocr_img_{img_offset + fig_idx}",
                image_data=img_data,
                content_type="image/png",
                alt_text=desc,
                width_px=width,
                height_px=height,
                page_number=page_number,
                is_decorative=False,
            ))
            fig_idx += 1

    return paragraphs, tables, figures


def _find_garbled_pages(paragraphs: list[ParagraphInfo]) -> list[int]:
    """Check a list of paragraphs for garbled OCR and return affected page numbers.

    Groups paragraphs by page, concatenates their text, and runs the garble
    detector.  Returns sorted list of 0-based page numbers with garbled output.
    """
    by_page: dict[int, list[str]] = {}
    for p in paragraphs:
        pg = p.page_number if p.page_number is not None else 0
        by_page.setdefault(pg, []).append(p.text)

    garbled: list[int] = []
    for pg, texts in sorted(by_page.items()):
        combined = " ".join(texts)
        if _is_garbled_text(combined):
            garbled.append(pg)
    return garbled


def _relative_to_pt(relative: str) -> float:
    """Convert relative font size label to approximate point size."""
    return {
        "large": 16.0,
        "normal": 12.0,
        "small": 10.0,
    }.get(relative, 12.0)


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


# Threshold: a paragraph longer than this is likely body prose, not a table cell.
_MAX_TABLE_CELL_CHARS = 200


_SENTENCE_ENDING_RE = re.compile(r'^.{10,}\.\s*$')


def _collect_table_paragraphs(
    paragraphs: list[ParagraphInfo],
    caption_index: int,
) -> list[int]:
    """Collect indices of paragraphs that likely belong to a missed table.

    Starting from caption_index + 1, collects consecutive paragraphs until
    hitting a stop signal:
      - Another table caption
      - A heading (heading_level > 0)
      - A long prose paragraph (> 200 chars)
      - A sentence-like paragraph (ends with a period, >= 10 chars, >= 2 words)
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

        text = para.text.strip()

        # Stop at long prose (unlikely to be a table cell)
        if len(text) > _MAX_TABLE_CELL_CHARS:
            break

        # Stop at sentence-like paragraphs (ends with period, multiple words):
        # these are body prose accidentally collected after a table, not cells.
        if _SENTENCE_ENDING_RE.match(text) and len(text.split()) >= 2:
            break

        indices.append(i)

    return indices


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


def _rescue_missed_tables(
    paragraphs: list[ParagraphInfo],
    tables: list[TableInfo],
    pdf_doc: fitz.Document,
    client,
    model: str,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ApiUsage]]:
    """Detect table captions in OCR paragraphs and re-extract missed tables.

    Scans paragraphs for table caption patterns (e.g., "TABLE 1"). For each
    caption found, checks whether a corresponding table already exists on
    that page. If not, re-sends the page image to Gemini with a focused
    extraction prompt.

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
            # No cell paragraphs found — likely already extracted as a table
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


def _tesseract_fallback(
    doc: fitz.Document,
    page_number: int,
    para_offset: int,
) -> list[ParagraphInfo]:
    """Extract text from a scanned page using Tesseract OCR.

    Used as a fallback when Gemini refuses a page (e.g., RECITATION filter).
    Tesseract provides raw text without semantic structure, so all text is
    returned as plain paragraphs without heading detection or formatting.

    Args:
        doc: Open PyMuPDF document.
        page_number: 0-based page number.
        para_offset: Starting offset for paragraph IDs.

    Returns:
        List of ParagraphInfo objects, or empty list on failure.
    """
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        logger.warning("pytesseract or Pillow not installed — cannot use Tesseract fallback")
        return []

    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=300)  # higher DPI for better Tesseract accuracy
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))

        # Run Tesseract OCR
        text = pytesseract.image_to_string(img, lang="eng")
        if not text or not text.strip():
            return []

        # Split into paragraphs on double newlines
        raw_blocks = text.split("\n\n")
        paragraphs: list[ParagraphInfo] = []

        for block in raw_blocks:
            block_text = block.strip()
            if not block_text:
                continue
            # Collapse single newlines within a paragraph to spaces
            block_text = " ".join(block_text.split("\n"))
            # Skip very short fragments (page numbers, artifacts)
            if len(block_text) < 3:
                continue

            # Filter leaked page headers/footers
            if _is_leaked_header_footer(block_text):
                logger.debug("Tesseract: filtered header/footer: %r", block_text[:80])
                continue

            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + len(paragraphs)}",
                text=block_text,
                style_name="Normal",
                runs=[RunInfo(text=block_text, font_size_pt=12.0)],
                page_number=page_number,
            ))

        return paragraphs

    except Exception as e:
        logger.warning("Tesseract fallback failed for page %d: %s", page_number + 1, e)
        return []
