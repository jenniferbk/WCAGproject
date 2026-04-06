"""OCR and layout analysis for scanned PDF pages using Gemini vision.

Detects scanned pages (full-page images with no text layer), segments them
into content regions (headings, paragraphs, tables, figures, equations),
and converts the results into DocumentModel objects so the rest of the
remediation pipeline can work on real text instead of image descriptions.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import re

import fitz  # PyMuPDF
from anthropic import Anthropic

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
MAX_RETRIES = 3
INITIAL_BACKOFF = 30  # seconds, doubles each retry
PAGE_DPI = 300  # render resolution for Gemini vision (300 DPI recommended for OCR)
PAGE_DPI_RETRY = 400  # higher resolution for retry on garbled output
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

    Processes each page individually with a clean retry chain:
    Gemini (200 DPI) → Gemini (300 DPI if garbled) → Tesseract fallback.
    Each page gets exactly one result — no duplication from retries.
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

    # ── Process each page individually ──────────────────────────
    total_pages = len(scanned_page_numbers)
    page_results: list[PageOCRResult] = []

    for i, page_num in enumerate(scanned_page_numbers):
        if on_progress:
            on_progress(f"OCR page {i + 1}/{total_pages}: page {page_num + 1}")
        logger.info("OCR page %d/%d: page %d", i + 1, total_pages, page_num + 1)

        page_result = _process_single_page(client, model, doc, page_num)
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


def _process_single_page(
    client,
    model: str,
    doc: fitz.Document,
    page_number: int,
) -> PageOCRResult:
    """Process a single scanned page using hybrid OCR.

    1. Tesseract extracts raw text blocks (always runs first)
    2. Gemini classifies structure, Haiku corrects text
    3. Merge results

    Falls back gracefully at each level.
    """
    result = PageOCRResult(page_number=page_number)

    # Step 1: Tesseract text extraction
    blocks = _tesseract_extract_blocks(doc, page_number)
    if not blocks:
        result.warnings.append(f"Page {page_number + 1}: Tesseract extracted no text")
        return result

    logger.info("Page %d: Tesseract extracted %d blocks", page_number + 1, len(blocks))

    # Step 2: Gemini structure + Haiku correction
    structure = None
    corrections: dict[int, str] = {}

    if client is not None:
        structure = _gemini_classify_structure(client, model, doc, page_number, blocks)
        if structure:
            logger.info("Page %d: Gemini structure classification succeeded", page_number + 1)
        else:
            logger.warning("Page %d: Gemini structure classification failed — using heuristics", page_number + 1)

    corrections = _haiku_correct_text(blocks, doc, page_number)
    if corrections:
        logger.info("Page %d: Haiku corrected %d/%d blocks", page_number + 1, len(corrections), len(blocks))

    # Step 3: Apply corrections
    corrected_blocks = _apply_corrections(blocks, corrections)

    # Step 4: Merge into model objects
    if structure:
        paras, tables, figures = _merge_blocks_and_structure(
            corrected_blocks, structure, page_number,
            para_offset=0, table_offset=0, img_offset=0, pdf_doc=doc,
        )
        # Correct table cell OCR errors via Haiku
        if tables:
            tables = _haiku_correct_table_cells(tables, doc, page_number)

        result.paragraphs = paras
        result.tables = tables
        result.figures = figures
        result.source = "hybrid"
    else:
        result.paragraphs = _heuristic_classify_blocks(corrected_blocks, page_number, para_offset=0)
        result.source = "hybrid_fallback"

    logger.info("Page %d: %s → %d paragraphs, %d tables, %d figures",
                page_number + 1, result.source,
                len(result.paragraphs), len(result.tables), len(result.figures))

    return result


def _stitch_page_results(
    page_results: list[PageOCRResult],
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]:
    """Merge per-page OCR results into unified lists with sequential IDs.

    Each page's paragraphs/tables/figures get their IDs reassigned to be
    globally sequential: ocr_p_0, ocr_p_1, ..., ocr_tbl_0, ocr_tbl_1, etc.
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


def _tesseract_extract_blocks(
    doc: fitz.Document,
    page_number: int,
    dpi: int = PAGE_DPI,
) -> list[dict]:
    """Run Tesseract on a page and return raw block dicts.

    Groups words by ``block_num``, filters noise, and returns one dict per
    surviving block in the order Tesseract produced them.

    Each returned dict has the shape::

        {
            "id":   int,            # sequential 0-based index
            "text": str,            # joined word text for the block
            "bbox": [x, y, w, h],  # left, top, width, height in pixels
        }

    Filtering applied:
    - Words with conf < 20 are excluded (low-confidence OCR noise).
    - Blocks whose assembled text is < 3 chars are excluded (single chars /
      punctuation fragments).
    - Blocks matching :func:`_is_leaked_header_footer` are excluded.

    Args:
        doc: Open PyMuPDF document.
        page_number: 0-based page index.
        dpi: Render resolution; defaults to ``PAGE_DPI`` (300).

    Returns:
        List of block dicts, possibly empty on failure or blank page.
    """
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        logger.warning("pytesseract or Pillow not installed — cannot use Tesseract extraction")
        return []

    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))

        data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)

        if not data or not data.get("text"):
            return []

        # Group high-confidence words into blocks keyed by block_num.
        # Track bounding box extents per block.
        raw_blocks: dict[int, dict] = {}

        for i in range(len(data["text"])):
            word = data["text"][i].strip()
            conf_raw = data["conf"][i]
            conf = int(conf_raw) if conf_raw != "-1" else 0
            if not word or conf < 20:
                continue

            block_num = data["block_num"][i]
            left = data["left"][i]
            top = data["top"][i]
            width = data["width"][i]
            height = data["height"][i]

            if block_num not in raw_blocks:
                raw_blocks[block_num] = {
                    "words": [],
                    "min_left": left,
                    "min_top": top,
                    "max_right": left + width,
                    "max_bottom": top + height,
                }
            b = raw_blocks[block_num]
            b["words"].append(word)
            b["min_left"] = min(b["min_left"], left)
            b["min_top"] = min(b["min_top"], top)
            b["max_right"] = max(b["max_right"], left + width)
            b["max_bottom"] = max(b["max_bottom"], top + height)

        if not raw_blocks:
            return []

        result: list[dict] = []
        idx = 0
        for _block_num, b in sorted(raw_blocks.items()):
            text = " ".join(b["words"])

            # Filter short fragments (page numbers, stray punctuation, artefacts)
            if len(text) < 3:
                continue

            # Filter leaked headers/footers
            if _is_leaked_header_footer(text):
                continue

            x = b["min_left"]
            y = b["min_top"]
            w = b["max_right"] - b["min_left"]
            h = b["max_bottom"] - b["min_top"]

            result.append({"id": idx, "text": text, "bbox": [x, y, w, h]})
            idx += 1

        return result

    except Exception as exc:  # noqa: BLE001
        logger.warning("_tesseract_extract_blocks failed on page %d: %s", page_number, exc)
        return []


HYBRID_STRUCTURE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "regions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "block_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "type": {"type": "STRING", "enum": ["heading", "paragraph", "table", "figure", "equation", "caption", "page_header", "page_footer", "footnote"]},
                    "heading_level": {"type": "INTEGER"},
                    "column": {"type": "INTEGER"},
                    "reading_order": {"type": "INTEGER"},
                    "bold": {"type": "BOOLEAN"},
                    "italic": {"type": "BOOLEAN"},
                    "font_size_relative": {"type": "STRING", "enum": ["large", "normal", "small"]},
                    "table_data": {
                        "type": "OBJECT",
                        "properties": {
                            "headers": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}},
                        },
                    },
                    "figure_description": {"type": "STRING"},
                },
                "required": ["block_ids", "type", "reading_order"],
            },
        },
    },
    "required": ["regions"],
}


def _load_hybrid_structure_prompt() -> str:
    """Load the hybrid OCR structure classification prompt."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "hybrid_ocr_structure.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Minimal fallback
    return (
        "Classify these Tesseract blocks into structural regions "
        "(heading, paragraph, table, figure, etc.). "
        "Return JSON with a 'regions' array. "
        "Blocks JSON: {blocks_json}"
    )


def _gemini_classify_structure(
    client: object,
    model: str,
    doc: fitz.Document,
    page_number: int,
    blocks: list[dict],
    dpi: int = PAGE_DPI,
) -> dict | None:
    """Classify Tesseract blocks into structural regions using Gemini vision.

    Renders ``page_number`` as a PNG at ``dpi`` resolution, then sends the
    image together with the Tesseract block list to Gemini.  Gemini sees the
    page visually to understand layout but does **not** reproduce text (all
    text already lives in ``blocks``).

    Args:
        client: Initialised ``google.genai.Client``.
        model:  Gemini model name (e.g. ``"gemini-2.5-flash"``).
        doc:    Open PyMuPDF document.
        page_number: 0-based page index to classify.
        blocks: Tesseract block list as returned by
                :func:`_tesseract_extract_blocks`.
        dpi:    Render resolution for the page image.

    Returns:
        Parsed dict with a ``"regions"`` key on success, or ``None`` on
        RECITATION, empty response, or any exception.
    """
    try:
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed — cannot run Gemini structure classification")
        return None

    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
    except Exception as exc:  # noqa: BLE001
        logger.warning("_gemini_classify_structure: failed to render page %d: %s", page_number, exc)
        return None

    prompt_template = _load_hybrid_structure_prompt()
    blocks_json = json.dumps(blocks, ensure_ascii=False)
    prompt = prompt_template.replace("{blocks_json}", blocks_json)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=HYBRID_STRUCTURE_SCHEMA,
                temperature=0.1,
            ),
        )

        resp_text = response.text
        if resp_text is None:
            logger.warning(
                "_gemini_classify_structure: empty response for page %d (RECITATION?)",
                page_number,
            )
            return None

        try:
            return json.loads(resp_text)
        except json.JSONDecodeError:
            result = parse_json_lenient(resp_text)
            if result is None:
                logger.warning(
                    "_gemini_classify_structure: could not parse JSON for page %d",
                    page_number,
                )
            return result

    except Exception as exc:  # noqa: BLE001
        logger.warning("_gemini_classify_structure: exception on page %d: %s", page_number, exc)
        return None


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


# ── Haiku OCR text correction ─────────────────────────────────────

_HYBRID_CORRECTION_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "hybrid_ocr_correction.md"


def _load_hybrid_correction_prompt() -> str:
    """Load the Haiku OCR correction prompt template."""
    return _HYBRID_CORRECTION_PROMPT_PATH.read_text()


def _haiku_correct_text(
    blocks: list[dict],
    doc: fitz.Document,
    page_number: int,
    dpi: int = PAGE_DPI,
) -> dict[int, str]:
    """Compare Tesseract blocks against the page image and return corrections.

    Calls Claude Haiku with a vision message containing the rendered page image
    and all Tesseract blocks. Returns a mapping of {block_id: corrected_text}
    for blocks that contain OCR errors. Blocks that are correct are omitted.

    Returns an empty dict on any failure (graceful degradation).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("ANTHROPIC_API_KEY not set — skipping Haiku OCR correction")
        return {}

    try:
        # Render page to PNG and base64-encode it
        page = doc[page_number]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        image_b64 = base64.b64encode(png_bytes).decode("utf-8")

        # Build prompt with blocks JSON
        blocks_json = json.dumps(blocks, ensure_ascii=False)
        prompt_template = _load_hybrid_correction_prompt()
        prompt = prompt_template.replace("{blocks_json}", blocks_json)

        # Call Claude Haiku
        client = Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        # Parse response
        content_block = response.content[0]
        if content_block.type != "text":
            logger.warning("Haiku returned non-text response for page %d", page_number + 1)
            return {}

        raw = content_block.text
        logger.debug(
            "Haiku correction page %d: %d input tokens, %d output tokens",
            page_number + 1,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = parse_json_lenient(raw)

        corrections_list = data.get("corrections", [])
        result: dict[int, str] = {}
        for item in corrections_list:
            block_id = item.get("id")
            corrected = item.get("corrected_text")
            if block_id is not None and corrected is not None:
                result[int(block_id)] = corrected

        logger.info(
            "Haiku correction page %d: %d/%d blocks corrected",
            page_number + 1,
            len(result),
            len(blocks),
        )
        return result

    except Exception as e:
        logger.warning("Haiku OCR correction failed for page %d: %s", page_number + 1, e)
        return {}


def _haiku_correct_table_cells(
    tables: list[TableInfo],
    doc: fitz.Document,
    page_number: int,
    dpi: int = PAGE_DPI,
) -> list[TableInfo]:
    """Send table cell texts to Claude Haiku for OCR correction.

    Gemini extracts table_data directly from the page image, but its OCR
    of cell contents can have errors. This function sends all cell texts
    to Haiku with the page image for correction.

    Returns a new list of TableInfo with corrected cell texts.
    Returns the original tables unchanged on failure.
    """
    if not tables:
        return tables

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return tables

    # Collect all cell texts across all tables
    cell_entries: list[dict] = []
    idx = 0
    for tbl_i, table in enumerate(tables):
        for row_i, row in enumerate(table.rows):
            for col_i, cell in enumerate(row):
                if cell.text.strip():
                    cell_entries.append({
                        "id": idx,
                        "text": cell.text,
                        "table": tbl_i,
                        "row": row_i,
                        "col": col_i,
                    })
                    idx += 1

    if not cell_entries:
        return tables

    try:
        import base64

        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")

        # Build a simple blocks list for the correction prompt
        blocks_for_prompt = [{"id": e["id"], "text": e["text"]} for e in cell_entries]
        blocks_json = json.dumps(blocks_for_prompt, ensure_ascii=False)

        prompt = (
            "You are an OCR correction tool. Below are table cell texts extracted "
            "by OCR from the attached scanned page. Compare each cell against the "
            "actual table visible in the image. Return corrections ONLY for cells "
            "with errors. Common OCR errors: 'ftom'→'from', spurious periods "
            "('to.research'→'to research'), wrong characters.\n\n"
            "Return JSON: {\"corrections\": [{\"id\": 0, \"corrected_text\": \"...\"}]}\n"
            "If all correct: {\"corrections\": []}\n\n"
            f"CELLS:\n{blocks_json}"
        )

        client = Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        raw = response.content[0].text
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = parse_json_lenient(raw)

        corrections: dict[int, str] = {}
        for item in data.get("corrections", []):
            cid = item.get("id")
            corrected = item.get("corrected_text")
            if cid is not None and corrected is not None:
                corrections[int(cid)] = corrected

        if not corrections:
            logger.debug("Haiku: no table cell corrections needed for page %d", page_number + 1)
            return tables

        logger.info(
            "Haiku corrected %d/%d table cells on page %d",
            len(corrections), len(cell_entries), page_number + 1,
        )

        # Rebuild tables with corrected cells
        corrected_tables: list[TableInfo] = []
        for tbl_i, table in enumerate(tables):
            new_rows: list[list[CellInfo]] = []
            for row_i, row in enumerate(table.rows):
                new_cells: list[CellInfo] = []
                for col_i, cell in enumerate(row):
                    # Find the matching entry
                    match = next(
                        (e for e in cell_entries
                         if e["table"] == tbl_i and e["row"] == row_i and e["col"] == col_i),
                        None,
                    )
                    if match and match["id"] in corrections:
                        corrected_text = corrections[match["id"]]
                        new_cells.append(CellInfo(
                            text=corrected_text,
                            paragraphs=[corrected_text],
                            grid_span=cell.grid_span,
                            v_merge=cell.v_merge,
                        ))
                    else:
                        new_cells.append(cell)
                new_rows.append(new_cells)
            corrected_tables.append(table.model_copy(update={"rows": new_rows}))

        return corrected_tables

    except Exception as e:
        logger.warning("Haiku table cell correction failed for page %d: %s", page_number + 1, e)
        return tables


# ── Hybrid block merging ──────────────────────────────────────────


def _apply_corrections(blocks: list[dict], corrections: dict[int, str]) -> list[dict]:
    """Apply Haiku text corrections to Tesseract blocks.

    For each block, if its ``id`` appears in ``corrections``, replace the
    ``text`` field with the corrected version.  All other fields (``id``,
    ``bbox``) are preserved unchanged.

    Args:
        blocks: List of block dicts as returned by :func:`_tesseract_extract_blocks`.
            Each dict has ``id``, ``text``, and ``bbox`` keys.
        corrections: Mapping of ``{block_id: corrected_text}`` as returned by
            :func:`_haiku_correct_text`.  May be empty.

    Returns:
        New list of block dicts with corrected text where available.
        Blocks absent from ``corrections`` are returned unchanged.
    """
    if not corrections:
        return list(blocks)

    result: list[dict] = []
    for block in blocks:
        block_id = block.get("id")
        if block_id is not None and block_id in corrections:
            result.append({**block, "text": corrections[block_id]})
        else:
            result.append(block)
    return result


def _merge_blocks_and_structure(
    blocks: list[dict],
    structure: dict,
    page_number: int,
    para_offset: int,
    table_offset: int,
    img_offset: int,
    pdf_doc: fitz.Document | None = None,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]:
    """Combine corrected Tesseract blocks with Gemini structural annotations.

    Takes the ``structure`` dict returned by :func:`_gemini_classify_structure`
    (which has a ``"regions"`` key), looks up the text for each region's
    ``block_ids`` from the ``blocks`` lookup, then creates
    :class:`ParagraphInfo`, :class:`TableInfo`, or :class:`ImageInfo` objects.

    Processing order:
    1. Build a ``{id: block}`` lookup from ``blocks``.
    2. Sort regions by ``reading_order``.
    3. For each region, assemble text by joining text from referenced
       ``block_ids`` (space-separated).  Skip page_header/page_footer regions
       and text that matches :func:`_is_leaked_header_footer`.
    4. Create the appropriate model object based on region type.

    Args:
        blocks: Corrected block list (output of :func:`_apply_corrections`).
        structure: Dict with a ``"regions"`` key as returned by
            :func:`_gemini_classify_structure`.
        page_number: 0-based page number for the created model objects.
        para_offset: Starting index for paragraph IDs (``ocr_p_{N}``).
        table_offset: Starting index for table IDs (``ocr_tbl_{N}``).
        img_offset: Starting index for image IDs (``ocr_img_{N}``).
        pdf_doc: Optional open PyMuPDF document for figure image extraction.

    Returns:
        Tuple of ``(paragraphs, tables, figures)``.
    """
    blocks_by_id: dict[int, dict] = {b["id"]: b for b in blocks}

    regions = structure.get("regions", [])
    regions = sorted(regions, key=lambda r: r.get("reading_order", 0))

    paragraphs: list[ParagraphInfo] = []
    tables: list[TableInfo] = []
    figures: list[ImageInfo] = []

    para_idx = 0
    tbl_idx = 0
    fig_idx = 0

    for region in regions:
        region_type = region.get("type", "")

        # Skip nav/structural elements we don't want in the document body
        if region_type in ("page_header", "page_footer"):
            continue

        # Assemble text from referenced block IDs
        block_ids = region.get("block_ids", [])
        text_parts = []
        for bid in block_ids:
            b = blocks_by_id.get(bid)
            if b and b.get("text", "").strip():
                text_parts.append(b["text"].strip())
        text = " ".join(text_parts).strip()

        # Filter leaked headers/footers that Gemini misclassified
        if text and region_type not in ("table", "figure") and _is_leaked_header_footer(text):
            logger.debug("_merge_blocks_and_structure: filtered leaked header/footer: %r", text[:80])
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
                    italic=True,
                    font_size_pt=_relative_to_pt(region.get("font_size_relative", "normal")),
                )],
                page_number=page_number,
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
            ))
            para_idx += 1

        elif region_type == "table":
            table_data = region.get("table_data", {})
            headers = table_data.get("headers", []) if table_data else []
            rows = table_data.get("rows", []) if table_data else []

            if not headers and not rows:
                # Fall back to rendering as text if no structured data
                if text:
                    paragraphs.append(ParagraphInfo(
                        id=f"ocr_p_{para_offset + para_idx}",
                        text=text,
                        style_name="Normal",
                        page_number=page_number,
                    ))
                    para_idx += 1
                continue

            table_rows: list[list[CellInfo]] = []
            if headers:
                table_rows.append([CellInfo(text=h, paragraphs=[h]) for h in headers])
            for row_cells in rows:
                table_rows.append([CellInfo(text=c, paragraphs=[c]) for c in row_cells])

            col_count = max((len(r) for r in table_rows), default=0)
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


def _heuristic_classify_blocks(
    blocks: list[dict],
    page_number: int,
    para_offset: int,
) -> list[ParagraphInfo]:
    """Fallback classifier: convert raw Tesseract blocks to ParagraphInfo objects.

    Used when both Gemini structure classification and other models are
    unavailable.  Applies simple heuristics to detect headings:
    - Short (≤ 6 words) ALL CAPS text → Heading 2 (bold, 16pt)
    - Everything else → Normal paragraph

    Args:
        blocks: Block list as returned by :func:`_tesseract_extract_blocks`.
        page_number: 0-based page number for created model objects.
        para_offset: Starting index for paragraph IDs (``ocr_p_{N}``).

    Returns:
        List of :class:`ParagraphInfo` objects (no tables or figures).
    """
    paragraphs: list[ParagraphInfo] = []
    para_idx = 0

    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue
        if _is_leaked_header_footer(text):
            continue

        words = text.split()
        is_heading = (
            len(words) <= 6
            and text == text.upper()
            and any(c.isalpha() for c in text)
        )

        if is_heading:
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Heading 2",
                heading_level=2,
                runs=[RunInfo(text=text, bold=True, font_size_pt=16.0)],
                page_number=page_number,
            ))
        else:
            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name="Normal",
                runs=[RunInfo(text=text, font_size_pt=12.0)],
                page_number=page_number,
            ))
        para_idx += 1

    return paragraphs
