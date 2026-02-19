"""Parse .pdf files into a DocumentModel.

Uses PyMuPDF (fitz) for text, image, and table extraction.
Two-pass approach for fake heading detection (same as docx_parser):
  1. First pass: collect all font sizes and paragraph data
  2. Second pass: score fake heading candidates against the median font size
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    DocumentModel,
    DocumentStats,
    FakeHeadingSignals,
    ImageInfo,
    LinkInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)

logger = logging.getLogger(__name__)

# Fake heading scoring weights (PDF-specific, includes distinct font signal)
_WEIGHT_ALL_BOLD = 0.25
_WEIGHT_FONT_SIZE = 0.20
_WEIGHT_SHORT = 0.20
_WEIGHT_FOLLOWED_BY = 0.10
_WEIGHT_NOT_IN_TABLE = 0.10
_WEIGHT_DISTINCT_FONT = 0.15


@dataclass
class ParseResult:
    """Result of parsing a PDF file."""
    success: bool
    document: DocumentModel | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    scanned_page_numbers: list[int] = field(default_factory=list)


def _read_struct_tree_headings(
    doc: fitz.Document,
    paragraphs: list[ParagraphInfo],
) -> list[ParagraphInfo]:
    """Read heading tags from the PDF StructTree and apply to paragraphs.

    iText writes /H1-/H6 StructElems with ActualText. We match these back
    to parsed paragraphs by page number + text similarity to set heading_level.
    This makes headings applied by iText visible to the validator on re-parse.
    """
    # Build page xref -> page index mapping
    page_idx_by_xref: dict[int, int] = {}
    for i in range(len(doc)):
        page_idx_by_xref[doc[i].xref] = i

    # Scan xrefs for heading StructElems
    heading_tags: list[dict] = []
    for i in range(1, doc.xref_length()):
        keys = doc.xref_get_keys(i)
        if "S" not in keys or "Type" not in keys:
            continue
        s_val = doc.xref_get_key(i, "S")
        tag_name = s_val[1] if s_val[0] == "name" else ""
        if not re.match(r"^/H[1-6]$", tag_name):
            continue

        level = int(tag_name[2:])

        # Get page from K reference
        k_val = doc.xref_get_key(i, "K")
        k_str = k_val[1] if len(k_val) > 1 else ""
        pg_match = re.search(r"/Pg\s+(\d+)\s+0\s+R", k_str)
        page_xref = int(pg_match.group(1)) if pg_match else None
        page_idx = page_idx_by_xref.get(page_xref) if page_xref else None

        # Get ActualText if available
        actual_text = ""
        if "ActualText" in keys:
            at_val = doc.xref_get_key(i, "ActualText")
            at_type = at_val[0] if len(at_val) > 0 else ""
            at_str = at_val[1] if len(at_val) > 1 else ""
            if at_type == "string":
                # PyMuPDF returns decoded string directly
                actual_text = at_str
            elif at_str.startswith("(") and at_str.endswith(")"):
                actual_text = at_str[1:-1]
            elif at_str.startswith("<") and at_str.endswith(">"):
                try:
                    hex_str = at_str[1:-1]
                    if hex_str.upper().startswith("FEFF"):
                        hex_str = hex_str[4:]
                    actual_text = bytes.fromhex(hex_str).decode("utf-16-be", errors="replace")
                except (ValueError, UnicodeDecodeError):
                    actual_text = ""

        heading_tags.append({
            "level": level,
            "page": page_idx,
            "text": actual_text.strip(),
        })

    if not heading_tags:
        return paragraphs

    # Build page -> paragraphs index for efficient matching
    paras_by_page: dict[int, list[int]] = {}
    for idx, p in enumerate(paragraphs):
        pg = p.page_number
        if pg is not None:
            paras_by_page.setdefault(pg, []).append(idx)

    # Match heading tags to paragraphs
    updated = list(paragraphs)
    matched_para_idxs: set[int] = set()

    for htag in heading_tags:
        page = htag["page"]
        if page is None:
            continue

        candidates = paras_by_page.get(page, [])
        best_idx = None
        best_score = 0.0

        for pidx in candidates:
            if pidx in matched_para_idxs:
                continue
            p = paragraphs[pidx]
            p_text = p.text.strip()
            if not p_text:
                continue

            # Score by text similarity
            h_text = htag["text"]
            if h_text and p_text:
                # Exact match or containment
                if p_text == h_text or h_text in p_text or p_text in h_text:
                    score = 1.0
                else:
                    # Partial word overlap
                    p_words = set(p_text.lower().split())
                    h_words = set(h_text.lower().split())
                    if p_words and h_words:
                        overlap = len(p_words & h_words) / max(len(p_words), len(h_words))
                        score = overlap * 0.8
                    else:
                        score = 0.0
            elif not h_text:
                # No ActualText — use fake heading score as tiebreaker
                if p.fake_heading_signals and p.fake_heading_signals.score >= 0.5:
                    score = p.fake_heading_signals.score * 0.5
                else:
                    score = 0.0
            else:
                score = 0.0

            if score > best_score:
                best_score = score
                best_idx = pidx

        if best_idx is not None and best_score > 0.1:
            matched_para_idxs.add(best_idx)
            p = updated[best_idx]
            updated[best_idx] = p.model_copy(
                update={"heading_level": htag["level"]}
            )

    matched = len(matched_para_idxs)
    if matched > 0:
        logger.info("StructTree: matched %d heading tags to paragraphs", matched)

    return updated


def parse_pdf(filepath: str | Path) -> ParseResult:
    """Parse a .pdf file into a DocumentModel.

    Args:
        filepath: Path to the .pdf file.

    Returns:
        ParseResult with success/failure and the DocumentModel.
    """
    filepath = Path(filepath)
    warnings: list[str] = []

    if not filepath.exists():
        return ParseResult(success=False, error=f"File not found: {filepath}")
    if filepath.suffix.lower() != ".pdf":
        return ParseResult(success=False, error=f"Not a .pdf file: {filepath}")

    try:
        doc = fitz.open(str(filepath))
    except Exception as e:
        return ParseResult(success=False, error=f"Failed to open PDF: {e}")

    try:
        # Extract metadata
        metadata = _extract_metadata(doc)

        # Extract content page by page
        paragraphs: list[ParagraphInfo] = []
        tables: list[TableInfo] = []
        images: list[ImageInfo] = []
        links: list[LinkInfo] = []
        content_order: list[ContentOrderItem] = []
        all_font_sizes: list[float] = []

        para_counter = 0
        table_counter = 0
        link_counter = 0
        img_counter = 0

        for page_num in range(len(doc)):
            page = doc[page_num]

            # --- Extract tables first so we can exclude table regions from text ---
            page_tables, table_rects = _extract_tables(
                page, page_num, table_counter, warnings
            )

            # --- Extract text blocks ---
            page_paras, page_links, page_fonts, page_img_refs = _extract_text_blocks(
                page, page_num, para_counter, link_counter, table_rects, warnings
            )

            # --- Extract images ---
            page_images = _extract_images(
                doc, page, page_num, img_counter,
                paragraphs_so_far=paragraphs,
                new_paras=page_paras,
                warnings=warnings,
            )

            # Link images to paragraphs via image_ids
            if page_images:
                if page_paras:
                    # Attach each image to the first paragraph on its page
                    anchor_para = page_paras[0]
                    new_image_ids = list(anchor_para.image_ids) + [
                        img.id for img in page_images
                    ]
                    page_paras[0] = anchor_para.model_copy(
                        update={"image_ids": new_image_ids}
                    )
                else:
                    # No text on page — create a synthetic anchor paragraph
                    anchor_id = f"p_{para_counter}"
                    page_paras.append(ParagraphInfo(
                        id=anchor_id,
                        text="",
                        style_name="Normal",
                        image_ids=[img.id for img in page_images],
                        page_number=page_num,
                    ))
                    para_counter += 1

            # Add content in order: paragraphs, then tables from this page
            for para in page_paras:
                content_order.append(ContentOrderItem(
                    content_type=ContentType.PARAGRAPH, id=para.id
                ))
            for tbl in page_tables:
                content_order.append(ContentOrderItem(
                    content_type=ContentType.TABLE, id=tbl.id
                ))

            paragraphs.extend(page_paras)
            tables.extend(page_tables)
            images.extend(page_images)
            links.extend(page_links)
            all_font_sizes.extend(page_fonts)

            para_counter += len(page_paras)
            table_counter += len(page_tables)
            link_counter += len(page_links)
            img_counter += len(page_images)

        # Detect scanned pages
        scanned_pages = _detect_scanned_pages(doc)

        # Determine dominant (body) font family across all paragraphs
        font_name_counts: Counter[str] = Counter()
        for p in paragraphs:
            for r in p.runs:
                if r.font_name and r.text.strip():
                    base = _get_base_font_name(r.font_name)
                    font_name_counts[base] += len(r.text)
        dominant_font = font_name_counts.most_common(1)[0][0] if font_name_counts else ""

        # Second pass: score fake headings
        median_font = (
            statistics.median(all_font_sizes) if all_font_sizes
            else 12.0
        )
        paragraphs = _score_fake_headings(paragraphs, median_font, dominant_font)

        # Read heading tags from StructTree (if present, e.g. after iText tagging)
        paragraphs = _read_struct_tree_headings(doc, paragraphs)

        # Compute stats
        heading_count = sum(
            1 for p in paragraphs if p.heading_level is not None
        )
        images_missing_alt = sum(1 for img in images if not img.alt_text)
        fake_heading_candidates = sum(
            1 for p in paragraphs
            if p.fake_heading_signals is not None
            and p.fake_heading_signals.score >= 0.5
        )

        stats = DocumentStats(
            paragraph_count=len(paragraphs),
            table_count=len(tables),
            image_count=len(images),
            link_count=len(links),
            heading_count=heading_count,
            images_missing_alt=images_missing_alt,
            fake_heading_candidates=fake_heading_candidates,
        )

        document = DocumentModel(
            source_format="pdf",
            source_path=str(filepath),
            metadata=metadata,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            links=links,
            content_order=content_order,
            stats=stats,
            parse_warnings=warnings,
        )

        return ParseResult(
            success=True, document=document, warnings=warnings,
            scanned_page_numbers=scanned_pages,
        )

    except Exception as e:
        logger.exception("Error parsing PDF: %s", filepath)
        return ParseResult(success=False, error=f"Parse error: {e}", warnings=warnings)
    finally:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass


def _extract_metadata(doc: fitz.Document) -> MetadataInfo:
    """Extract PDF metadata."""
    meta = doc.metadata or {}

    # Try to get language from PDF catalog
    language = ""
    try:
        catalog = doc.pdf_catalog()
        if catalog:
            xref = catalog
            lang_val = doc.xref_get_key(xref, "Lang")
            if lang_val and lang_val[0] == "string":
                language = lang_val[1].strip("()")
    except Exception:
        pass

    return MetadataInfo(
        title=meta.get("title", "") or "",
        author=meta.get("author", "") or "",
        language=language,
        subject=meta.get("subject", "") or "",
        created=meta.get("creationDate", "") or "",
        modified=meta.get("modDate", "") or "",
    )


def _extract_tables(
    page: fitz.Page,
    page_num: int,
    table_offset: int,
    warnings: list[str],
) -> tuple[list[TableInfo], list[fitz.Rect]]:
    """Extract tables from a page using PyMuPDF's built-in table finder.

    Returns:
        Tuple of (list of TableInfo, list of table bounding rects for exclusion).
    """
    tables: list[TableInfo] = []
    table_rects: list[fitz.Rect] = []

    try:
        tab_finder = page.find_tables()
        for i, tab in enumerate(tab_finder.tables):
            tbl_id = f"tbl_{table_offset + i}"
            tab_rect = fitz.Rect(tab.bbox)
            table_rects.append(tab_rect)

            # Extract cell data
            extracted = tab.extract()
            if not extracted:
                continue

            rows: list[list[CellInfo]] = []
            for row_data in extracted:
                cells: list[CellInfo] = []
                for cell_text in row_data:
                    text = cell_text or ""
                    cells.append(CellInfo(
                        text=text,
                        paragraphs=[text] if text else [],
                    ))
                rows.append(cells)

            row_count = len(rows)
            col_count = max((len(r) for r in rows), default=0)

            # Heuristic: first row is header if it looks like labels
            header_row_count = 0

            tables.append(TableInfo(
                id=tbl_id,
                rows=rows,
                header_row_count=header_row_count,
                row_count=row_count,
                col_count=col_count,
                bbox=(tab_rect.x0, tab_rect.y0, tab_rect.x1, tab_rect.y1),
                page_number=page_num,
            ))
    except Exception as e:
        warnings.append(f"Table extraction failed on page: {e}")

    return tables, table_rects


def _is_in_table_rect(
    block_rect: fitz.Rect,
    table_rects: list[fitz.Rect],
) -> bool:
    """Check if a text block overlaps significantly with any table region."""
    for tr in table_rects:
        intersection = block_rect & tr
        if not intersection.is_empty:
            # If >50% of block area is inside the table, consider it table text
            block_area = block_rect.width * block_rect.height
            if block_area > 0:
                overlap = intersection.width * intersection.height
                if overlap / block_area > 0.5:
                    return True
    return False


def _get_base_font_name(font_name: str) -> str:
    """Extract the base font family name, stripping subset prefixes and weight/style suffixes.

    E.g. 'ABCDEF+TimesNewRomanPS-BoldMT' → 'TimesNewRomanPS',
         'TimesNewRomanPSMT' → 'TimesNewRomanPS',
         'GHIJKL+Century' → 'Century'
    """
    import re
    # Step 0: Strip PDF subset font prefix (6 uppercase letters + '+')
    font_name = re.sub(r"^[A-Z]{6}\+", "", font_name)
    # Step 1: Strip trailing 'MT' (Monotype identifier)
    if font_name.endswith("MT") and len(font_name) > 2:
        font_name = font_name[:-2]
    # Step 2: Strip weight/style suffixes (longest first to avoid partial match)
    for suffix in ("-BoldItalic", "-BoldItal", "-Bold", "-Italic", "-Ital"):
        if font_name.endswith(suffix):
            return font_name[: -len(suffix)]
    return font_name


def _line_is_all_bold(line: dict) -> bool:
    """Check if all non-empty spans in a line are bold."""
    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
    if not spans:
        return False
    return all(bool(s.get("flags", 0) & (1 << 4)) for s in spans)


def _line_primary_font(line: dict) -> tuple[str, float]:
    """Get the dominant font name and size of a line (from the longest span)."""
    best_font = ""
    best_size = 0.0
    best_len = 0
    for span in line.get("spans", []):
        text = span.get("text", "")
        if len(text) > best_len:
            best_len = len(text)
            best_font = span.get("font", "")
            best_size = span.get("size", 12.0)
    return best_font, best_size


def _should_split_before_line(prev_line: dict, curr_line: dict) -> bool:
    """Detect formatting transitions between consecutive lines.

    Returns True if the current line should start a new sub-paragraph.
    """
    prev_bold = _line_is_all_bold(prev_line)
    curr_bold = _line_is_all_bold(curr_line)

    # Bold transition
    if prev_bold != curr_bold:
        return True

    prev_font, prev_size = _line_primary_font(prev_line)
    curr_font, curr_size = _line_primary_font(curr_line)

    # Font size change (>1.5pt difference)
    if abs(prev_size - curr_size) > 1.5:
        return True

    # Font family change
    if prev_font and curr_font:
        if _get_base_font_name(prev_font) != _get_base_font_name(curr_font):
            return True

    return False


def _split_block_into_sub_paragraphs(block: dict) -> list[list[dict]]:
    """Split a PyMuPDF block's lines into groups at formatting transitions.

    Each group of lines becomes its own ParagraphInfo. Lines with no
    non-empty spans are skipped.
    """
    lines = block.get("lines", [])
    if not lines:
        return []

    # Filter to lines that have at least one non-empty span
    non_empty_lines = [
        line for line in lines
        if any(s.get("text", "").strip() for s in line.get("spans", []))
    ]
    if not non_empty_lines:
        return []

    groups: list[list[dict]] = [[non_empty_lines[0]]]
    for i in range(1, len(non_empty_lines)):
        if _should_split_before_line(non_empty_lines[i - 1], non_empty_lines[i]):
            groups.append([non_empty_lines[i]])
        else:
            groups[-1].append(non_empty_lines[i])

    return groups


def _extract_text_blocks(
    page: fitz.Page,
    page_num: int,
    para_offset: int,
    link_offset: int,
    table_rects: list[fitz.Rect],
    warnings: list[str],
) -> tuple[list[ParagraphInfo], list[LinkInfo], list[float], list]:
    """Extract text blocks from a page using the dict-based approach.

    Returns:
        (paragraphs, links, font_sizes, image_refs)
    """
    paragraphs: list[ParagraphInfo] = []
    links: list[LinkInfo] = []
    font_sizes: list[float] = []
    image_refs: list = []  # reserved for future use

    try:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    except Exception as e:
        warnings.append(f"Text extraction failed on page {page_num}: {e}")
        return paragraphs, links, font_sizes, image_refs

    # Get page links for URL extraction
    page_links = page.get_links()

    para_counter = 0

    for block in blocks:
        if block["type"] != 0:  # text block
            continue

        block_rect = fitz.Rect(block["bbox"])

        # Skip text that's inside a detected table
        if _is_in_table_rect(block_rect, table_rects):
            continue

        # Split block into sub-paragraphs at formatting transitions
        sub_groups = _split_block_into_sub_paragraphs(block)
        if not sub_groups:
            continue

        for line_group in sub_groups:
            runs: list[RunInfo] = []
            group_text_parts: list[str] = []
            group_links: list[LinkInfo] = []

            # Compute bounding box for this sub-paragraph group
            group_x0 = float("inf")
            group_y0 = float("inf")
            group_x1 = float("-inf")
            group_y1 = float("-inf")

            for line in line_group:
                line_bbox = line.get("bbox")
                if line_bbox:
                    group_x0 = min(group_x0, line_bbox[0])
                    group_y0 = min(group_y0, line_bbox[1])
                    group_x1 = max(group_x1, line_bbox[2])
                    group_y1 = max(group_y1, line_bbox[3])

                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue

                    font_size = span.get("size", 12.0)
                    font_name = span.get("font", "")
                    flags = span.get("flags", 0)
                    color_int = span.get("color", 0)

                    # Decode flags: bit 0 = superscript, bit 1 = italic,
                    # bit 2 = serif, bit 3 = monospace, bit 4 = bold
                    is_bold = bool(flags & (1 << 4))
                    is_italic = bool(flags & (1 << 1))

                    # Convert color int to hex
                    color_hex = None
                    if color_int != 0:
                        r = (color_int >> 16) & 0xFF
                        g = (color_int >> 8) & 0xFF
                        b = color_int & 0xFF
                        color_hex = f"#{r:02X}{g:02X}{b:02X}"

                    font_sizes.append(font_size)

                    runs.append(RunInfo(
                        text=text,
                        bold=is_bold if is_bold else None,
                        italic=is_italic if is_italic else None,
                        font_size_pt=round(font_size, 1),
                        font_name=font_name,
                        color=color_hex,
                    ))
                    group_text_parts.append(text)

            full_text = "".join(group_text_parts).strip()
            if not full_text:
                continue

            para_id = f"p_{para_offset + para_counter}"

            # Build bbox tuple if we got valid coordinates
            para_bbox = None
            if group_x0 != float("inf"):
                para_bbox = (
                    round(group_x0, 2),
                    round(group_y0, 2),
                    round(group_x1, 2),
                    round(group_y1, 2),
                )

            # Check for hyperlinks overlapping this block
            for pl in page_links:
                if "uri" in pl and pl.get("uri"):
                    link_rect = fitz.Rect(pl["from"])
                    if block_rect.intersects(link_rect):
                        link_id = f"link_{link_offset + len(links) + len(group_links)}"
                        # Try to get link text from the overlapping region
                        link_text = page.get_textbox(link_rect).strip()
                        # Only add link if text appears in this sub-paragraph
                        if link_text and link_text in full_text:
                            link_bbox = (
                                round(link_rect.x0, 2),
                                round(link_rect.y0, 2),
                                round(link_rect.x1, 2),
                                round(link_rect.y1, 2),
                            )
                            group_links.append(LinkInfo(
                                id=link_id,
                                text=link_text or pl["uri"],
                                url=pl["uri"],
                                paragraph_id=para_id,
                                bbox=link_bbox,
                                page_number=page_num,
                            ))

            paragraphs.append(ParagraphInfo(
                id=para_id,
                text=full_text,
                style_name="Normal",
                runs=runs,
                links=group_links,
                bbox=para_bbox,
                page_number=page_num,
            ))
            links.extend(group_links)
            para_counter += 1

    return paragraphs, links, font_sizes, image_refs


def _extract_images(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    img_offset: int,
    paragraphs_so_far: list[ParagraphInfo],
    new_paras: list[ParagraphInfo],
    warnings: list[str],
) -> list[ImageInfo]:
    """Extract images from a page."""
    images: list[ImageInfo] = []

    try:
        image_list = page.get_images(full=True)
    except Exception as e:
        warnings.append(f"Image extraction failed on page {page_num}: {e}")
        return images

    seen_xrefs = set()

    for i, img_info in enumerate(image_list):
        xref = img_info[0]

        # Skip duplicate xrefs on same page
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        try:
            img_data = doc.extract_image(xref)
            if not img_data:
                continue

            image_bytes = img_data.get("image", b"")
            if not image_bytes:
                continue

            ext = img_data.get("ext", "png")
            mime_map = {
                "png": "image/png",
                "jpeg": "image/jpeg",
                "jpg": "image/jpeg",
                "bmp": "image/bmp",
                "gif": "image/gif",
                "tiff": "image/tiff",
            }
            content_type = mime_map.get(ext, f"image/{ext}")

            width = img_data.get("width", 0)
            height = img_data.get("height", 0)

            # Skip tiny images (likely decorative dots/lines)
            if width < 10 or height < 10:
                continue

            img_id = f"img_{img_offset + len(images)}"

            # Get image bounding box on the page
            img_bbox = None
            try:
                rects = page.get_image_rects(xref)
                if rects:
                    r = rects[0]  # first occurrence on this page
                    img_bbox = (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))
            except Exception:
                pass  # bbox is optional, continue without it

            # Get surrounding text from paragraphs on this page
            surrounding = ""
            if new_paras:
                surrounding_parts = []
                for p in new_paras[:3]:
                    if p.text.strip():
                        surrounding_parts.append(p.text[:80])
                surrounding = " | ".join(surrounding_parts)[:200]

            # Check for alt text in PDF structure tree
            alt_text = _get_image_alt_text(doc, xref)

            # Find which paragraph is closest (for paragraph_id)
            para_id = ""
            if new_paras:
                para_id = new_paras[0].id  # rough: first para on page

            images.append(ImageInfo(
                id=img_id,
                image_data=image_bytes,
                content_type=content_type,
                alt_text=alt_text,
                width_px=width,
                height_px=height,
                surrounding_text=surrounding,
                paragraph_id=para_id,
                page_number=page_num,
                xref=xref,
                bbox=img_bbox,
            ))

        except Exception as e:
            warnings.append(f"Image extraction failed for xref {xref} on page {page_num}: {e}")

    return images


def _get_image_alt_text(doc: fitz.Document, xref: int) -> str:
    """Try to get alt text from the PDF structure tree for an image.

    Traverses structure elements looking for /Figure entries that match
    the given image xref. Matches via the /A11yXref custom attribute
    written by our iText tagger, falling back to the first /Figure if
    no xref match is found.
    """
    try:
        catalog_xref = doc.pdf_catalog()
        if not catalog_xref:
            return ""
        sroot = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if sroot[0] != "xref":
            return ""
        sroot_xref = int(sroot[1].split()[0])

        kids = doc.xref_get_key(sroot_xref, "K")
        if kids[0] == "null":
            return ""

        struct_xrefs = _collect_struct_xrefs(doc, sroot_xref, max_depth=6)

        first_figure_alt = ""  # fallback for non-iText-tagged PDFs
        for sx in struct_xrefs:
            try:
                stype = doc.xref_get_key(sx, "S")
                if stype[0] != "name" or stype[1] != "/Figure":
                    continue
                alt_val = doc.xref_get_key(sx, "Alt")
                if alt_val[0] != "string":
                    continue
                alt_text = alt_val[1].strip("()")

                # Match by /A11yXref (written by our iText tagger)
                xref_val = doc.xref_get_key(sx, "A11yXref")
                if xref_val[0] in ("int", "real"):
                    fig_xref = int(float(xref_val[1]))
                    if fig_xref == xref:
                        return alt_text
                    continue  # Different image, skip

                # No /A11yXref — remember first /Figure as fallback
                if not first_figure_alt:
                    k_val = doc.xref_get_key(sx, "K")
                    if k_val[0] == "dict" or k_val[0] == "xref":
                        first_figure_alt = alt_text
            except Exception:
                continue

        return first_figure_alt
    except Exception:
        pass
    return ""


def _collect_struct_xrefs(
    doc: fitz.Document, parent_xref: int, max_depth: int = 6
) -> list[int]:
    """Recursively collect struct element xrefs from the structure tree."""
    if max_depth <= 0:
        return []
    result: list[int] = []
    try:
        k_val = doc.xref_get_key(parent_xref, "K")
        if k_val[0] == "xref":
            child_xref = int(k_val[1].split()[0])
            result.append(child_xref)
            result.extend(_collect_struct_xrefs(doc, child_xref, max_depth - 1))
        elif k_val[0] == "array":
            # Parse array of references
            raw = k_val[1].strip("[]")
            parts = raw.split()
            i = 0
            while i < len(parts):
                if i + 2 < len(parts) and parts[i + 1] == "0" and parts[i + 2] == "R":
                    child_xref = int(parts[i])
                    result.append(child_xref)
                    result.extend(
                        _collect_struct_xrefs(doc, child_xref, max_depth - 1)
                    )
                    i += 3
                else:
                    i += 1
    except Exception:
        pass
    return result


def _detect_scanned_pages(doc: fitz.Document) -> list[int]:
    """Detect pages that appear to be scanned (images but <20 chars of text)."""
    scanned: list[int] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            text = page.get_text("text").strip()
            images = page.get_images(full=True)
            if images and len(text) < 20:
                scanned.append(page_num)
        except Exception:
            continue
    return scanned


def _score_fake_headings(
    paragraphs: list[ParagraphInfo],
    median_font_size: float,
    dominant_font: str = "",
) -> list[ParagraphInfo]:
    """Score paragraphs for fake heading likelihood.

    Weighted combination of heuristic signals. PDF-specific version includes
    a distinct-font signal for text using a different font family from the
    document's dominant (body) font.
    """
    result: list[ParagraphInfo] = []

    for i, para in enumerate(paragraphs):
        if para.heading_level is not None or not para.text.strip():
            result.append(para)
            continue

        # Check if all runs are bold
        text_runs = [r for r in para.runs if r.text.strip()]
        if not text_runs:
            result.append(para)
            continue

        all_bold = all(r.bold is True for r in text_runs)

        # Get font size (use max across runs)
        run_sizes = [r.font_size_pt for r in text_runs if r.font_size_pt is not None]
        max_font_size = max(run_sizes) if run_sizes else None
        font_above_avg = (
            max_font_size is not None and max_font_size >= median_font_size + 2.0
        )

        # Short text (< ~10 words)
        word_count = len(para.text.split())
        is_short = word_count < 10

        # Followed by non-bold text
        followed_by_non_bold = False
        for j in range(i + 1, min(i + 3, len(paragraphs))):
            next_para = paragraphs[j]
            if next_para.text.strip():
                next_text_runs = [r for r in next_para.runs if r.text.strip()]
                if next_text_runs:
                    followed_by_non_bold = not all(
                        r.bold is True for r in next_text_runs
                    )
                break

        # Distinct font: paragraph uses a different font family from the body
        has_distinct_font = False
        if dominant_font:
            para_fonts = {
                _get_base_font_name(r.font_name)
                for r in text_runs
                if r.font_name
            }
            if para_fonts and dominant_font not in para_fonts:
                has_distinct_font = True

        # Must have at least bold OR larger font OR distinct font to be a candidate
        if not all_bold and not font_above_avg and not has_distinct_font:
            result.append(para)
            continue

        # Compute weighted score
        score = (
            _WEIGHT_ALL_BOLD * (1.0 if all_bold else 0.0)
            + _WEIGHT_FONT_SIZE * (1.0 if font_above_avg else 0.0)
            + _WEIGHT_SHORT * (1.0 if is_short else 0.0)
            + _WEIGHT_FOLLOWED_BY * (1.0 if followed_by_non_bold else 0.0)
            + _WEIGHT_NOT_IN_TABLE * 1.0  # always True for paragraphs
            + _WEIGHT_DISTINCT_FONT * (1.0 if has_distinct_font else 0.0)
        )

        signals = FakeHeadingSignals(
            all_runs_bold=all_bold,
            font_size_pt=max_font_size,
            font_size_above_avg=font_above_avg,
            is_short=is_short,
            followed_by_non_bold=followed_by_non_bold,
            not_in_table=True,
            distinct_font=has_distinct_font,
            score=round(score, 3),
        )

        updated = para.model_copy(update={"fake_heading_signals": signals})
        result.append(updated)

    return result
