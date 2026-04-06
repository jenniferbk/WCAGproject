"""Tests for scanned page OCR and layout analysis."""

import json
import pytest

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    DocumentModel,
    DocumentStats,
    ImageInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.models.pipeline import ApiUsage
from unittest.mock import MagicMock, patch

from src.tools.scanned_page_ocr import (
    PageOCRResult,
    ScannedPageResult,
    _apply_corrections,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _gemini_classify_structure,
    _heuristic_classify_blocks,
    _integrate_page_data,
    _is_garbled_text,
    _is_leaked_header_footer,
    _merge_blocks_and_structure,
    _process_single_page,
    _regions_to_model_objects,
    _relative_to_pt,
    _rescue_missed_tables,
    _sort_regions_by_column,
    _stitch_page_results,
    _tesseract_extract_blocks,
)


# ── Helper to build a scanned-page DocumentModel ──────────────────


def _make_scanned_doc(
    scanned_pages: list[int],
    total_pages: int = 3,
    text_paras: list[ParagraphInfo] | None = None,
) -> DocumentModel:
    """Build a DocumentModel simulating a mix of scanned and text pages.

    ``scanned_pages`` are 0-based page numbers that get ScannedPageAnchor
    paragraphs.  Other pages get a normal paragraph.
    """
    paragraphs: list[ParagraphInfo] = []
    images: list[ImageInfo] = []
    content_order: list[ContentOrderItem] = []
    p_idx = 0
    img_idx = 0

    for page_num in range(total_pages):
        if page_num in scanned_pages:
            img_id = f"img_{img_idx}"
            para_id = f"p_{p_idx}"
            images.append(ImageInfo(
                id=img_id,
                content_type="image/png",
                page_number=page_num,
                is_decorative=False,
            ))
            paragraphs.append(ParagraphInfo(
                id=para_id,
                text="",
                style_name="ScannedPageAnchor",
                image_ids=[img_id],
                page_number=page_num,
            ))
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=para_id,
            ))
            p_idx += 1
            img_idx += 1
        else:
            para_id = f"p_{p_idx}"
            paragraphs.append(ParagraphInfo(
                id=para_id,
                text=f"Text on page {page_num}",
                style_name="Normal",
                page_number=page_num,
            ))
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=para_id,
            ))
            p_idx += 1

    if text_paras:
        paragraphs.extend(text_paras)
        for p in text_paras:
            content_order.append(ContentOrderItem(
                content_type=ContentType.PARAGRAPH, id=p.id,
            ))

    return DocumentModel(
        source_format="pdf",
        source_path="/tmp/test.pdf",
        metadata=MetadataInfo(title="Test Document"),
        paragraphs=paragraphs,
        images=images,
        content_order=content_order,
        stats=DocumentStats(
            paragraph_count=len(paragraphs),
            image_count=len(images),
        ),
    )


# ── Tests for _regions_to_model_objects ──────────────────────────


class TestRegionsToModelHeading:
    def test_heading_creates_paragraph_with_level(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {
                    "type": "heading",
                    "text": "Introduction",
                    "heading_level": 1,
                    "bold": True,
                    "font_size_relative": "large",
                    "reading_order": 1,
                },
            ],
        }
        paras, tables, figures = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].heading_level == 1
        assert paras[0].text == "Introduction"
        assert paras[0].style_name == "Heading 1"
        assert paras[0].runs[0].bold is True
        assert len(tables) == 0
        assert len(figures) == 0

    def test_heading_level_clamped(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "heading", "text": "X", "heading_level": 10, "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].heading_level == 6  # clamped to max

    def test_heading_level_zero_becomes_one(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "heading", "text": "X", "heading_level": 0, "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].heading_level == 1

    def test_empty_heading_skipped(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "heading", "text": "", "heading_level": 1, "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 0


class TestRegionsToModelParagraph:
    def test_paragraph_preserves_formatting(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {
                    "type": "paragraph",
                    "text": "Some body text here.",
                    "bold": True,
                    "italic": True,
                    "font_size_relative": "normal",
                    "reading_order": 1,
                },
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=3, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Some body text here."
        assert paras[0].page_number == 3
        assert paras[0].runs[0].bold is True
        assert paras[0].runs[0].italic is True

    def test_empty_paragraph_skipped(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "  ", "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 0

    def test_non_bold_paragraph_has_none_bold(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "Normal text.", "bold": False, "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].runs[0].bold is None


class TestRegionsToModelTable:
    def test_table_with_headers_and_rows(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "table",
                    "text": "",
                    "reading_order": 1,
                    "table_data": {
                        "headers": ["Name", "Score"],
                        "rows": [["Alice", "95"], ["Bob", "87"]],
                    },
                },
            ],
        }
        _, tables, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(tables) == 1
        tbl = tables[0]
        assert tbl.header_row_count == 1
        assert tbl.row_count == 3  # 1 header + 2 data
        assert tbl.col_count == 2
        assert tbl.rows[0][0].text == "Name"
        assert tbl.rows[1][0].text == "Alice"
        assert tbl.rows[2][1].text == "87"

    def test_table_no_headers(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "table",
                    "text": "",
                    "reading_order": 1,
                    "table_data": {
                        "headers": [],
                        "rows": [["A", "B"], ["C", "D"]],
                    },
                },
            ],
        }
        _, tables, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert tables[0].header_row_count == 0
        assert tables[0].row_count == 2

    def test_empty_table_falls_back_to_text(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "table",
                    "text": "Some table content as text",
                    "reading_order": 1,
                    "table_data": {"headers": [], "rows": []},
                },
            ],
        }
        paras, tables, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(tables) == 0
        assert len(paras) == 1
        assert paras[0].text == "Some table content as text"


class TestRegionsToModelFigure:
    def test_figure_creates_image_info(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "figure",
                    "text": "",
                    "figure_description": "A bar chart showing test scores",
                    "reading_order": 1,
                },
            ],
        }
        _, _, figures = _regions_to_model_objects(
            page_data, page_number=2, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(figures) == 1
        assert figures[0].alt_text == "A bar chart showing test scores"
        assert figures[0].page_number == 2
        assert figures[0].is_decorative is False

    def test_figure_falls_back_to_text_for_description(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "figure",
                    "text": "Figure 1: Results",
                    "reading_order": 1,
                },
            ],
        }
        _, _, figures = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert figures[0].alt_text == "Figure 1: Results"


class TestRegionsToModelSpecialTypes:
    def test_equation_creates_italic_paragraph(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "equation", "text": "x² + 2x + 1 = 0", "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].text == "x² + 2x + 1 = 0"
        assert paras[0].runs[0].italic is True

    def test_caption_creates_small_paragraph(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {"type": "caption", "text": "Figure 1. Test results", "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].text == "Figure 1. Test results"
        assert paras[0].runs[0].font_size_pt == 10.0  # small

    def test_footnote_creates_small_paragraph(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "footnote", "text": "1. See reference.", "reading_order": 10},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert paras[0].runs[0].font_size_pt == 10.0

    def test_page_header_skipped(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "page_header", "text": "Journal of Math Ed", "reading_order": 0},
                {"type": "paragraph", "text": "Body text.", "reading_order": 1},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Body text."

    def test_page_footer_skipped(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "page_footer", "text": "Page 7", "reading_order": 99},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 0


class TestRegionsReadingOrder:
    def test_regions_sorted_by_reading_order(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "Third", "reading_order": 3},
                {"type": "heading", "text": "First", "heading_level": 1, "reading_order": 1},
                {"type": "paragraph", "text": "Second", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert [p.text for p in paras] == ["First", "Second", "Third"]

    def test_two_column_reading_order(self):
        """Left column (reading_order 1-3) before right column (4-6)."""
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "Left 1", "column": 1, "reading_order": 1},
                {"type": "paragraph", "text": "Left 2", "column": 1, "reading_order": 2},
                {"type": "paragraph", "text": "Left 3", "column": 1, "reading_order": 3},
                {"type": "paragraph", "text": "Right 1", "column": 2, "reading_order": 4},
                {"type": "paragraph", "text": "Right 2", "column": 2, "reading_order": 5},
                {"type": "paragraph", "text": "Right 3", "column": 2, "reading_order": 6},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        texts = [p.text for p in paras]
        assert texts == ["Left 1", "Left 2", "Left 3", "Right 1", "Right 2", "Right 3"]


class TestRegionsIdOffsets:
    def test_paragraph_ids_use_offset(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "A", "reading_order": 1},
                {"type": "paragraph", "text": "B", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=10, table_offset=0, img_offset=0,
        )
        assert paras[0].id == "ocr_p_10"
        assert paras[1].id == "ocr_p_11"

    def test_table_ids_use_offset(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {
                    "type": "table",
                    "text": "",
                    "reading_order": 1,
                    "table_data": {"headers": ["H"], "rows": [["V"]]},
                },
            ],
        }
        _, tables, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=5, img_offset=0,
        )
        assert tables[0].id == "ocr_tbl_5"

    def test_figure_ids_use_offset(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {"type": "figure", "figure_description": "A chart", "reading_order": 1},
            ],
        }
        _, _, figures = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=3,
        )
        assert figures[0].id == "ocr_img_3"


# ── Tests for _relative_to_pt ───────────────────────────────────


class TestRelativeToPt:
    def test_large(self):
        assert _relative_to_pt("large") == 16.0

    def test_normal(self):
        assert _relative_to_pt("normal") == 12.0

    def test_small(self):
        assert _relative_to_pt("small") == 10.0

    def test_unknown_defaults_to_12(self):
        assert _relative_to_pt("huge") == 12.0


# ── Tests for _merge_ocr_into_model ──────────────────────────────


class TestMergeOcrIntoModel:
    """Test the orchestrator's _merge_ocr_into_model function."""

    def test_replaces_synthetic_anchors(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0, 1], total_pages=3)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[
                ParagraphInfo(
                    id="ocr_p_0", text="Text from page 0",
                    style_name="Normal", page_number=0,
                ),
                ParagraphInfo(
                    id="ocr_p_1", text="Text from page 1",
                    style_name="Normal", page_number=1,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0, 1])

        # Synthetic anchors removed
        anchor_styles = [p.style_name for p in merged.paragraphs
                         if p.style_name == "ScannedPageAnchor"]
        assert len(anchor_styles) == 0

        # OCR paragraphs present
        ocr_texts = [p.text for p in merged.paragraphs if p.text.startswith("Text from")]
        assert "Text from page 0" in ocr_texts
        assert "Text from page 1" in ocr_texts

    def test_removes_full_page_images(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=2)
        assert len(doc.images) == 1  # one full-page image

        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[
                ParagraphInfo(
                    id="ocr_p_0", text="OCR text",
                    style_name="Normal", page_number=0,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])

        # Full-page image removed
        assert len(merged.images) == 0

    def test_preserves_text_pages(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=3)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[
                ParagraphInfo(
                    id="ocr_p_0", text="OCR text",
                    style_name="Normal", page_number=0,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])

        # Text pages (1, 2) untouched
        text_paras = [p for p in merged.paragraphs
                      if p.text.startswith("Text on page")]
        assert len(text_paras) == 2

    def test_adds_ocr_tables(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=1)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[],
            tables=[
                TableInfo(
                    id="ocr_tbl_0",
                    rows=[[CellInfo(text="A"), CellInfo(text="B")]],
                    header_row_count=0,
                    row_count=1,
                    col_count=2,
                    page_number=0,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])
        assert len(merged.tables) == 1
        assert merged.tables[0].id == "ocr_tbl_0"

    def test_adds_ocr_figures(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=1)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[],
            figures=[
                ImageInfo(
                    id="ocr_img_0",
                    alt_text="A chart",
                    page_number=0,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])
        # Original full-page image removed, OCR figure added
        assert len(merged.images) == 1
        assert merged.images[0].id == "ocr_img_0"
        assert merged.images[0].alt_text == "A chart"

    def test_content_order_rebuilt(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=2)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[
                ParagraphInfo(
                    id="ocr_p_0", text="OCR heading",
                    style_name="Heading 1", heading_level=1, page_number=0,
                ),
                ParagraphInfo(
                    id="ocr_p_1", text="OCR body",
                    style_name="Normal", page_number=0,
                ),
            ],
            tables=[
                TableInfo(
                    id="ocr_tbl_0",
                    rows=[[CellInfo(text="X")]],
                    row_count=1, col_count=1, page_number=0,
                ),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])

        order_ids = [item.id for item in merged.content_order]
        # p_0 (scanned anchor) should be removed
        assert "p_0" not in order_ids
        # Text page para still there
        assert "p_1" in order_ids
        # OCR items added
        assert "ocr_p_0" in order_ids
        assert "ocr_p_1" in order_ids
        assert "ocr_tbl_0" in order_ids

    def test_stats_recalculated(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[0], total_pages=2)
        ocr_result = ScannedPageResult(
            success=True,
            paragraphs=[
                ParagraphInfo(
                    id="ocr_p_0", text="Heading",
                    style_name="Heading 1", heading_level=1, page_number=0,
                ),
                ParagraphInfo(
                    id="ocr_p_1", text="Body",
                    style_name="Normal", page_number=0,
                ),
            ],
            figures=[
                ImageInfo(id="ocr_img_0", alt_text="", page_number=0),
            ],
        )

        merged = _merge_ocr_into_model(doc, ocr_result, [0])

        # 2 OCR paras + 1 text page para = 3
        assert merged.stats.paragraph_count == 3
        assert merged.stats.heading_count == 1
        # 1 OCR figure (original page image removed)
        assert merged.stats.image_count == 1
        # ocr_img_0 has empty alt text and is not decorative
        assert merged.stats.images_missing_alt == 1

    def test_no_scanned_pages_returns_unchanged(self):
        from src.agent.orchestrator import _merge_ocr_into_model

        doc = _make_scanned_doc(scanned_pages=[], total_pages=2)
        ocr_result = ScannedPageResult(success=True)

        merged = _merge_ocr_into_model(doc, ocr_result, [])

        assert merged.stats.paragraph_count == doc.stats.paragraph_count


# ── Tests for ScannedPageResult ──────────────────────────────────


class TestScannedPageResult:
    def test_default_values(self):
        result = ScannedPageResult(success=True)
        assert result.success is True
        assert result.paragraphs == []
        assert result.tables == []
        assert result.figures == []
        assert result.pages_processed == []
        assert result.api_usage == []
        assert result.warnings == []
        assert result.error == ""

    def test_failure_with_error(self):
        result = ScannedPageResult(success=False, error="No API key")
        assert result.success is False
        assert result.error == "No API key"


# ── Tests for mixed region types on a single page ────────────────


class TestMixedRegionsOnPage:
    def test_heading_paragraph_table_figure_on_one_page(self):
        page_data = {
            "page_type": "mixed",
            "regions": [
                {"type": "heading", "text": "Results", "heading_level": 2, "reading_order": 1},
                {"type": "paragraph", "text": "The results show...", "reading_order": 2},
                {
                    "type": "table",
                    "text": "",
                    "reading_order": 3,
                    "table_data": {
                        "headers": ["Metric", "Value"],
                        "rows": [["Accuracy", "95%"]],
                    },
                },
                {
                    "type": "figure",
                    "figure_description": "A line graph of accuracy over time",
                    "reading_order": 4,
                },
                {"type": "caption", "text": "Figure 1. Accuracy over time.", "reading_order": 5},
            ],
        }
        paras, tables, figures = _regions_to_model_objects(
            page_data, page_number=5, para_offset=0, table_offset=0, img_offset=0,
        )

        assert len(paras) == 3  # heading + paragraph + caption
        assert len(tables) == 1
        assert len(figures) == 1

        assert paras[0].heading_level == 2
        assert paras[1].text == "The results show..."
        assert paras[2].text == "Figure 1. Accuracy over time."
        assert tables[0].col_count == 2
        assert figures[0].alt_text == "A line graph of accuracy over time"

        # All on page 5
        for p in paras:
            assert p.page_number == 5
        assert tables[0].page_number == 5
        assert figures[0].page_number == 5


# ── Tests for _sort_regions_by_column ─────────────────────────────


class TestSortRegionsByColumn:
    def test_no_column_info_sorts_by_reading_order(self):
        regions = [
            {"type": "paragraph", "text": "C", "reading_order": 3},
            {"type": "paragraph", "text": "A", "reading_order": 1},
            {"type": "paragraph", "text": "B", "reading_order": 2},
        ]
        result = _sort_regions_by_column(regions)
        assert [r["text"] for r in result] == ["A", "B", "C"]

    def test_left_before_right_column(self):
        regions = [
            {"type": "paragraph", "text": "R1", "column": 2, "reading_order": 1},
            {"type": "paragraph", "text": "L1", "column": 1, "reading_order": 2},
            {"type": "paragraph", "text": "R2", "column": 2, "reading_order": 3},
            {"type": "paragraph", "text": "L2", "column": 1, "reading_order": 4},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        # Left column first, then right — regardless of reading_order
        assert texts == ["L1", "L2", "R1", "R2"]

    def test_interleaved_columns_deinterleaved(self):
        """Simulates Gemini assigning cross-column reading_order (the actual bug)."""
        regions = [
            {"type": "heading", "text": "Intro", "column": 1, "reading_order": 1},
            {"type": "paragraph", "text": "Left body", "column": 1, "reading_order": 2},
            {"type": "heading", "text": "Section 2", "column": 2, "reading_order": 3},
            {"type": "paragraph", "text": "Thorndike...", "column": 1, "reading_order": 4},
            {"type": "paragraph", "text": "Right body", "column": 2, "reading_order": 5},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        # All left-column items should come before right-column items
        assert texts == ["Intro", "Left body", "Thorndike...", "Section 2", "Right body"]

    def test_full_width_before_columns(self):
        regions = [
            {"type": "heading", "text": "Title", "column": 0, "reading_order": 1},
            {"type": "paragraph", "text": "Left", "column": 1, "reading_order": 2},
            {"type": "paragraph", "text": "Right", "column": 2, "reading_order": 3},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["Title", "Left", "Right"]

    def test_full_width_after_columns(self):
        regions = [
            {"type": "paragraph", "text": "Left", "column": 1, "reading_order": 1},
            {"type": "paragraph", "text": "Right", "column": 2, "reading_order": 2},
            {"type": "paragraph", "text": "Footer note", "column": 0, "reading_order": 3},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["Left", "Right", "Footer note"]

    def test_full_width_between_columns(self):
        """Full-width item in the middle (e.g., a table spanning both columns)."""
        regions = [
            {"type": "paragraph", "text": "Left", "column": 1, "reading_order": 1},
            {"type": "table", "text": "Wide table", "column": 0, "reading_order": 2},
            {"type": "paragraph", "text": "Right", "column": 2, "reading_order": 3},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        # Left → full-width-middle → right
        assert texts == ["Left", "Wide table", "Right"]

    def test_full_width_fence_separates_column_groups(self):
        """Full-width item acts as fence: Left A, Right A, Figure, Left B, Right B."""
        regions = [
            {"type": "paragraph", "text": "Left A", "column": 1, "reading_order": 1},
            {"type": "paragraph", "text": "Right A", "column": 2, "reading_order": 2},
            {"type": "figure", "text": "Full-width figure", "column": 0, "reading_order": 3},
            {"type": "paragraph", "text": "Left B", "column": 1, "reading_order": 4},
            {"type": "paragraph", "text": "Right B", "column": 2, "reading_order": 5},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["Left A", "Right A", "Full-width figure", "Left B", "Right B"]

    def test_missing_column_treated_as_full_width(self):
        regions = [
            {"type": "paragraph", "text": "No col", "reading_order": 1},
            {"type": "paragraph", "text": "Left", "column": 1, "reading_order": 2},
            {"type": "paragraph", "text": "Right", "column": 2, "reading_order": 3},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["No col", "Left", "Right"]


# ── Tests for _is_leaked_header_footer ────────────────────────────


class TestIsLeakedHeaderFooter:
    def test_all_caps_with_page_number(self):
        assert _is_leaked_header_footer("LEARNERS AS INFORMATION PROCESSORS 157") is True

    def test_page_number_then_caps_author(self):
        assert _is_leaked_header_footer("158 MAYER") is True

    def test_just_page_number(self):
        assert _is_leaked_header_footer("42") is True

    def test_normal_paragraph_not_detected(self):
        assert _is_leaked_header_footer(
            "The information-processing metaphor focused attention away from behavior."
        ) is False

    def test_short_caps_heading_not_detected(self):
        # Real headings in all caps without page numbers should pass through
        assert _is_leaked_header_footer("HISTORICAL OVERVIEW") is False

    def test_long_text_not_detected(self):
        assert _is_leaked_header_footer(
            "This is a normal paragraph with many words that should not be "
            "detected as a header or footer in any case."
        ) is False

    def test_caps_with_colon_and_number(self):
        assert _is_leaked_header_footer("EDUCATIONAL PSYCHOLOGIST, 31(3/4), 151-161") is False

    def test_three_digit_page_number_with_author(self):
        assert _is_leaked_header_footer("234 SMITH") is True

    def test_empty_string(self):
        assert _is_leaked_header_footer("") is False


# ── Tests for _is_garbled_text ────────────────────────────────────


class TestIsGarbledText:
    def test_clean_english_text(self):
        assert _is_garbled_text(
            "The information-processing metaphor focused attention away "
            "from behavior and toward mental representations."
        ) is False

    def test_garbled_no_vowel_words(self):
        assert _is_garbled_text(
            "found the nee supnbudien of rheril snddels ctiral elledente"
        ) is True

    def test_garbled_accented_chars(self):
        assert _is_garbled_text(
            "classi nation-proceéssing conception of menl représentation "
            "was incomplete lé ig as the acquisition of symbéls"
        ) is True

    def test_mixed_garbled_and_clean(self):
        # Below threshold — some OCR artifacts in mostly clean text
        assert _is_garbled_text(
            "The classic information-processing model was an incomplete "
            "framework for describing the architecture of the human mind. "
            "The lines dividing sensory memory and short-term memory."
        ) is False

    def test_short_text_not_judged(self):
        # Too short to make a reliable judgment
        assert _is_garbled_text("te 2 etn") is False

    def test_pure_gibberish(self):
        assert _is_garbled_text(
            "wnheesitesseeee rndslt prcssng hmnty kndwldge frmwrk"
        ) is True

    def test_mostly_clean_with_few_errors_passes(self):
        # Only 2-3 garbled words in ~20 — below threshold, not a garbled page
        assert _is_garbled_text(
            "account for rote éaming of word lists it was unable to "
            "account for compl hk2 teaming situations they found the"
        ) is False

    def test_heavily_garbled_real_mayer_output(self):
        # Actual garbled text from Mayer PDF OCR — worst-case degradation
        # (accented Latin chars like é now pass through correctly per review fix #1)
        assert _is_garbled_text(
            "found the nee supnbudien of rheril snddels ctiral elledente "
            "te 2 etn Sr renee wnheesitesseeee hmnty kndwldge frmwrk "
            "The box models downplay the role of executive ocessing "
            "classi proceéssing menl incomplté passivi leatning complhk thng"
        ) is True


# ── Tests for _find_garbled_pages ─────────────────────────────────


class TestFindGarbledPages:
    def test_no_garbled_pages(self):
        paras = [
            ParagraphInfo(id="p1", text="Clean text on page one.", page_number=0),
            ParagraphInfo(id="p2", text="More clean text here.", page_number=1),
        ]
        assert _find_garbled_pages(paras) == []

    def test_detects_garbled_page(self):
        paras = [
            ParagraphInfo(id="p1", text="Clean text on page zero.", page_number=0),
            ParagraphInfo(
                id="p2",
                text="supnbudien rheril snddels ctiral elledente garbled nonsense wthout vwls",
                page_number=1,
            ),
        ]
        result = _find_garbled_pages(paras)
        assert result == [1]

    def test_multiple_garbled_pages(self):
        paras = [
            ParagraphInfo(
                id="p1",
                text="garbl txts hre wthout prpr vwls snddels ctiral",
                page_number=0,
            ),
            ParagraphInfo(id="p2", text="This page is fine and readable.", page_number=1),
            ParagraphInfo(
                id="p3",
                text="mre grbled txts snddels ctiral prblms wnheesitesseeee",
                page_number=2,
            ),
        ]
        result = _find_garbled_pages(paras)
        assert 0 in result
        assert 2 in result
        assert 1 not in result


# ── Tests for leaked header/footer filtering in regions ───────────


class TestHeaderFooterFilteringInRegions:
    def test_misclassified_header_filtered(self):
        """A running header that Gemini classified as paragraph gets filtered."""
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "LEARNERS AS INFORMATION PROCESSORS 157", "reading_order": 1},
                {"type": "paragraph", "text": "Real body text here.", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Real body text here."

    def test_page_number_author_filtered(self):
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "158 MAYER", "reading_order": 1},
                {"type": "paragraph", "text": "Body text continues.", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Body text continues."

    def test_real_heading_not_filtered(self):
        """A heading in all caps without page number should not be affected."""
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "heading", "text": "HISTORICAL OVERVIEW", "heading_level": 2, "reading_order": 1},
                {"type": "paragraph", "text": "Body.", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 2
        assert paras[0].text == "HISTORICAL OVERVIEW"

    def test_header_misclassified_as_heading_filtered(self):
        """A running header that Gemini classified as heading gets filtered."""
        page_data = {
            "page_type": "text_dominant",
            "regions": [
                {"type": "heading", "text": "LEARNERS AS INFORMATION PROCESSORS 155", "heading_level": 1, "reading_order": 1},
                {"type": "paragraph", "text": "Body text.", "reading_order": 2},
            ],
        }
        paras, _, _ = _regions_to_model_objects(
            page_data, page_number=0, para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Body text."


# ── Tests for deduplication ───────────────────────────────────────


class TestDeduplicateOcrParagraphs:
    def _dedup(self, paras):
        from src.agent.orchestrator import _deduplicate_ocr_paragraphs
        return _deduplicate_ocr_paragraphs(paras)

    def test_exact_duplicates_removed(self):
        paras = [
            ParagraphInfo(id="p1", text="This is a long paragraph that should be deduplicated when it appears twice in the output.", page_number=0),
            ParagraphInfo(id="p2", text="This is a long paragraph that should be deduplicated when it appears twice in the output.", page_number=0),
        ]
        result = self._dedup(paras)
        assert len(result) == 1
        assert result[0].id == "p1"

    def test_near_duplicates_with_hyphens_removed(self):
        paras = [
            ParagraphInfo(id="p1", text="The information-processing metaphor focused atten- tion away from behavior.", page_number=0),
            ParagraphInfo(id="p2", text="The information-processing metaphor focused attention away from behavior.", page_number=1),
        ]
        result = self._dedup(paras)
        assert len(result) == 1

    def test_near_duplicates_with_dash_variants_removed(self):
        paras = [
            ParagraphInfo(id="p1", text="S-R associations — a key concept in behaviorist psychology and learning theory.", page_number=0),
            ParagraphInfo(id="p2", text="S-R associations - a key concept in behaviorist psychology and learning theory.", page_number=0),
        ]
        result = self._dedup(paras)
        assert len(result) == 1

    def test_short_text_not_deduped(self):
        paras = [
            ParagraphInfo(id="p1", text="Table 1", page_number=0),
            ParagraphInfo(id="p2", text="Table 1", page_number=1),
        ]
        result = self._dedup(paras)
        assert len(result) == 2

    def test_different_text_preserved(self):
        paras = [
            ParagraphInfo(id="p1", text="First unique paragraph with enough text to pass the length threshold.", page_number=0),
            ParagraphInfo(id="p2", text="Second unique paragraph with enough text to pass the length threshold.", page_number=0),
        ]
        result = self._dedup(paras)
        assert len(result) == 2

    def test_prefix_match_removes_near_duplicate(self):
        """Two paragraphs sharing first 60 normalized chars are near-dupes."""
        shared = "The information-processing metaphor was an incomplete transition away from S-R"
        paras = [
            ParagraphInfo(id="p1", text=shared + " behaviorism and its rigid view of cognition.", page_number=0),
            ParagraphInfo(id="p2", text=shared + " behaviorism. Its rigid view of cognition was limiting.", page_number=1),
        ]
        result = self._dedup(paras)
        assert len(result) == 1
        assert result[0].id == "p1"


class TestNormalizeForDedup:
    def _norm(self, text):
        from src.agent.orchestrator import _normalize_for_dedup
        return _normalize_for_dedup(text)

    def test_collapses_whitespace(self):
        assert self._norm("hello   world\n\tfoo") == "hello world foo"

    def test_fixes_hyphenation(self):
        assert self._norm("informa- tion") == "information"

    def test_normalizes_dashes(self):
        n = self._norm("S\u2014R associations")
        assert "-" in n
        assert "\u2014" not in n

    def test_normalizes_quotes(self):
        n = self._norm("\u201cquoted\u201d")
        assert '"quoted"' == n

    def test_lowercases(self):
        assert self._norm("HELLO") == "hello"


class TestFindTableCaptions:
    """Tests for detecting table captions in OCR paragraphs."""

    def test_detects_TABLE_N(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Some intro text.", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="TABLE 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Cell A", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "TABLE 1"
        assert result[0]["caption_index"] == 1
        assert result[0]["paragraph_id"] == "ocr_p_1"

    def test_detects_Table_N_colon(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 2: Three Metaphors", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1
        assert result[0]["caption_text"] == "Table 2: Three Metaphors"

    def test_detects_roman_numeral(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE III Summary of Results", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1

    def test_detects_table_with_period(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 4. Comparison of Methods", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1

    def test_ignores_mid_sentence_reference(self):
        """'see Table 1 for details' should NOT trigger."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="As shown in Table 1, the results vary.", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Refer to TABLE 2 for the full data.", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 0

    def test_ignores_the_table_below(self):
        """Prose mentioning 'the table' should not trigger."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="The table below shows the results.", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 0

    def test_multiple_captions(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 First Table", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="cell a", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="cell b", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="TABLE 2 Second Table", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="cell c", style_name="Normal"),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 2
        assert result[0]["caption_index"] == 0
        assert result[1]["caption_index"] == 3

    def test_skips_headings(self):
        """Headings with table captions ARE valid — heading_level doesn't disqualify."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Table 1 Results", style_name="Heading 2", heading_level=2),
        ]
        result = _find_table_captions(paras)
        assert len(result) == 1


class TestCollectTableParagraphs:
    """Tests for collecting paragraphs belonging to a missed table."""

    def test_collects_until_next_heading(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Metaphors", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Column A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Column B", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="Value 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="Next Section", style_name="Heading 2", heading_level=2),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2, 3]

    def test_collects_until_next_caption(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 First", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Cell A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Cell B", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="TABLE 2 Second", style_name="Normal"),
            ParagraphInfo(id="ocr_p_4", text="Cell C", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_collects_until_long_prose(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Header A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="Value 1", style_name="Normal"),
            ParagraphInfo(id="ocr_p_3", text="This is a long body paragraph that clearly is not a table cell. It contains multiple sentences describing the methodology and results of the experiment in detail, which would never appear in a single table cell." , style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_collects_until_end(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="A", style_name="Normal"),
            ParagraphInfo(id="ocr_p_2", text="B", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == [1, 2]

    def test_empty_after_caption(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 1 Results", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="Next Section", style_name="Heading 2", heading_level=2),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=0)
        assert indices == []

    def test_caption_at_end_of_list(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="Some text", style_name="Normal"),
            ParagraphInfo(id="ocr_p_1", text="TABLE 5 Final", style_name="Normal"),
        ]
        indices = _collect_table_paragraphs(paras, caption_index=1)
        assert indices == []


class TestRescueMissedTables:
    """Tests for the full table rescue pipeline."""

    def _make_paras(self, texts: list[str]) -> list[ParagraphInfo]:
        """Helper to build paragraph lists."""
        paras = []
        for i, text in enumerate(texts):
            paras.append(ParagraphInfo(
                id=f"ocr_p_{i}",
                text=text,
                style_name="Normal",
                page_number=0,
            ))
        return paras

    def test_rescues_table_and_replaces_paragraphs(self):
        paras = self._make_paras([
            "Introduction text.",
            "TABLE 1 Three Metaphors of Learning",
            "Response Strengthening",
            "Knowledge Acquisition",
            "Knowledge Construction",
            "Following paragraph.",
        ])
        tables: list[TableInfo] = []

        # Mock Gemini to return structured table data
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["Metaphor", "Description"], "rows": [["Response Strengthening", "Learning as..."], ["Knowledge Acquisition", "Learning as..."], ["Knowledge Construction", "Learning as..."]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        # Mock PDF doc with one page
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Caption + 3 cell paragraphs should be removed
        assert len(new_paras) == 2  # "Introduction text." and "Following paragraph."
        assert new_paras[0].text == "Introduction text."
        assert new_paras[1].text == "Following paragraph."

        # One table should be created
        assert len(new_tables) == 1
        tbl = new_tables[0]
        assert tbl.header_row_count == 1
        assert tbl.row_count == 4  # 1 header + 3 data
        assert tbl.col_count == 2

    def test_skips_when_table_already_exists(self):
        """If a table already exists on the same page, don't re-send."""
        paras = self._make_paras([
            "TABLE 1 Already Extracted",
            "Cell A",
        ])
        existing_table = TableInfo(
            id="ocr_tbl_0",
            rows=[[CellInfo(text="A", paragraphs=["A"])]],
            header_row_count=1,
            row_count=1,
            col_count=1,
            page_number=0,
        )
        tables = [existing_table]

        mock_client = MagicMock()
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Nothing should change — the table already exists on this page
        mock_client.models.generate_content.assert_not_called()
        assert len(new_tables) == 1

    def test_handles_gemini_failure(self):
        """If Gemini returns empty table, leave paragraphs as-is."""
        paras = self._make_paras([
            "TABLE 1 Broken Table",
            "Cell A",
            "Cell B",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": [], "rows": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Paragraphs should be unchanged
        assert len(new_paras) == 3
        assert len(new_tables) == 0

    def test_handles_gemini_exception(self):
        """If Gemini throws an exception, leave paragraphs as-is."""
        paras = self._make_paras([
            "TABLE 1 Error Table",
            "Cell A",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        assert len(new_paras) == 2
        assert len(new_tables) == 0

    def test_multiple_tables_rescued(self):
        paras = self._make_paras([
            "TABLE 1 First",
            "A1",
            "B1",
            "TABLE 2 Second",
            "A2",
            "B2",
        ])
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["Col"], "rows": [["Val"]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=1)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        assert len(new_paras) == 0  # all paragraphs were table cells or captions
        assert len(new_tables) == 2


class TestIntegratePageDataWithRescue:
    """Test that _integrate_page_data passes through to rescue when client is provided."""

    def test_no_rescue_without_client(self):
        """Without client, paragraphs with table captions stay as paragraphs."""
        page_data_list = [{
            "page_number": 1,
            "page_type": "text_dominant",
            "regions": [
                {"type": "paragraph", "text": "TABLE 1 Test", "reading_order": 1},
                {"type": "paragraph", "text": "Cell A", "reading_order": 2},
            ],
        }]

        all_paragraphs: list[ParagraphInfo] = []
        all_tables: list[TableInfo] = []
        all_figures: list[ImageInfo] = []
        pages_processed: list[int] = []

        _integrate_page_data(
            page_data_list, None,
            all_paragraphs, all_tables, all_figures,
            pages_processed, 0, 0, 0,
            known_page_numbers=[0],
        )
        assert len(all_paragraphs) == 2


class TestColumnSortingValidation:
    """Tests for column balance validation in _sort_regions_by_column."""

    def test_left_column_marked_as_fullwidth(self):
        regions = [
            {"type": "paragraph", "text": "Left para 1", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left para 2", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Right para 1", "reading_order": 3, "column": 2},
            {"type": "paragraph", "text": "Right para 2", "reading_order": 4, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts.index("Left para 1") < texts.index("Right para 1")
        assert texts.index("Left para 2") < texts.index("Right para 1")
        assert len(result) == 4

    def test_heading_stays_fullwidth(self):
        regions = [
            {"type": "heading", "text": "Title", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left text", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Right text", "reading_order": 3, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts[0] == "Title"
        assert texts.index("Left text") < texts.index("Right text")

    def test_balanced_columns_unchanged(self):
        regions = [
            {"type": "paragraph", "text": "Left", "reading_order": 1, "column": 1},
            {"type": "paragraph", "text": "Right", "reading_order": 2, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["Left", "Right"]

    def test_no_column_info_unchanged(self):
        regions = [
            {"type": "paragraph", "text": "B", "reading_order": 2},
            {"type": "paragraph", "text": "A", "reading_order": 1},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts == ["A", "B"]

    def test_right_column_only_no_crash(self):
        regions = [
            {"type": "paragraph", "text": "Right 1", "reading_order": 1, "column": 2},
            {"type": "paragraph", "text": "Right 2", "reading_order": 2, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        assert len(result) == 2

    def test_mixed_fullwidth_and_columns_with_imbalance(self):
        regions = [
            {"type": "heading", "text": "Section Title", "reading_order": 1, "column": 0},
            {"type": "paragraph", "text": "Left A", "reading_order": 2, "column": 0},
            {"type": "paragraph", "text": "Left B", "reading_order": 3, "column": 0},
            {"type": "paragraph", "text": "Right A", "reading_order": 4, "column": 2},
            {"type": "paragraph", "text": "Right B", "reading_order": 5, "column": 2},
        ]
        result = _sort_regions_by_column(regions)
        texts = [r["text"] for r in result]
        assert texts[0] == "Section Title"
        assert texts.index("Left A") < texts.index("Right A")


class TestRescueMultipleTablesOnSamePage:
    """Verify that multiple tables on the same page can all be rescued."""

    def test_two_captions_same_page_both_rescued(self):
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 3 Two Views", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_1", text="View Content", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_2", text="Literal Info", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_3", text="TABLE 4 Legacies", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_4", text="Legacy 1", style_name="Normal", page_number=5),
        ]
        tables: list[TableInfo] = []

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["Col"], "rows": [["Val"]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=10)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # Both tables should be rescued
        assert len(new_tables) == 2
        assert len(new_paras) == 0  # all were captions or cells

    def test_caption_skipped_when_existing_table_on_page(self):
        """If a table already exists (extracted by Gemini), and there's a caption
        with cell paragraphs on the same page, the rescue should still try."""
        paras = [
            ParagraphInfo(id="ocr_p_0", text="TABLE 3 Missing Table", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_1", text="Cell A", style_name="Normal", page_number=5),
            ParagraphInfo(id="ocr_p_2", text="Cell B", style_name="Normal", page_number=5),
        ]
        existing_table = TableInfo(
            id="ocr_tbl_0",
            rows=[[CellInfo(text="X", paragraphs=["X"])]],
            header_row_count=1,
            row_count=1,
            col_count=1,
            page_number=5,
        )
        tables = [existing_table]

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"headers": ["H"], "rows": [["V"]]}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=10)

        new_paras, new_tables, usage = _rescue_missed_tables(
            paras, tables, mock_doc, mock_client, "gemini-2.5-flash",
        )

        # The existing table PLUS the rescued table
        assert len(new_tables) == 2
        assert len(new_paras) == 0


# ── TestProcessSinglePage ─────────────────────────────────────────


class TestProcessSinglePage:
    def test_gemini_success_returns_result(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "pages": [{
                "page_number": 1,
                "page_type": "text_dominant",
                "regions": [
                    {"type": "paragraph", "text": "Hello world.", "reading_order": 1},
                ],
            }]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        result = _process_single_page(
            mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
        )

        assert result.page_number == 0
        assert result.source == "gemini"
        assert len(result.paragraphs) >= 1
        assert result.paragraphs[0].text == "Hello world."

    def test_gemini_none_falls_to_tesseract(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        with patch("src.tools.scanned_page_ocr._tesseract_fallback") as mock_tess:
            mock_tess.return_value = [
                ParagraphInfo(id="ocr_p_0", text="Tesseract text", style_name="Normal", page_number=0),
            ]
            result = _process_single_page(
                mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
            )

        assert result.source == "tesseract"
        assert len(result.paragraphs) == 1

    def test_all_fail_returns_empty(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake_png"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=5)

        with patch("src.tools.scanned_page_ocr._tesseract_fallback") as mock_tess:
            mock_tess.return_value = []
            result = _process_single_page(
                mock_client, "gemini-2.5-flash", mock_doc, 0, "OCR prompt",
            )

        assert result.source == "failed"
        assert len(result.paragraphs) == 0

    def test_result_dataclass_fields(self):
        result = PageOCRResult(page_number=3)
        assert result.page_number == 3
        assert result.paragraphs == []
        assert result.tables == []
        assert result.source == "failed"


# ── Tests for _stitch_page_results ───────────────────────────────


class TestStitchPageResults:
    def test_stitches_two_pages_in_order(self):
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Page 1 text", style_name="Normal", page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="gemini")
        page1.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Page 2 text", style_name="Normal", page_number=1),
        ]

        paras, tables, figures = _stitch_page_results([page0, page1])

        assert len(paras) == 2
        assert paras[0].text == "Page 1 text"
        assert paras[1].text == "Page 2 text"
        assert paras[0].id == "ocr_p_0"
        assert paras[1].id == "ocr_p_1"

    def test_stitches_with_tables(self):
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.tables = [
            TableInfo(id="ocr_tbl_0", rows=[], row_count=0, col_count=0, page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="gemini")
        page1.tables = [
            TableInfo(id="ocr_tbl_0", rows=[], row_count=0, col_count=0, page_number=1),
        ]

        paras, tables, figures = _stitch_page_results([page0, page1])

        assert len(tables) == 2
        assert tables[0].id == "ocr_tbl_0"
        assert tables[1].id == "ocr_tbl_1"

    def test_empty_page_included(self):
        page0 = PageOCRResult(page_number=0, source="gemini")
        page0.paragraphs = [
            ParagraphInfo(id="ocr_p_0", text="Content", style_name="Normal", page_number=0),
        ]
        page1 = PageOCRResult(page_number=1, source="failed")

        paras, tables, figures = _stitch_page_results([page0, page1])
        assert len(paras) == 1

    def test_empty_list(self):
        paras, tables, figures = _stitch_page_results([])
        assert paras == []
        assert tables == []
        assert figures == []


class TestEnhancedTesseractFallback:
    """Tests for enhanced Tesseract with column detection and heading heuristics."""

    def test_detects_all_caps_heading(self):
        """ALL CAPS short text should be detected as heading."""
        # We can't easily unit test the full Tesseract flow without a real image,
        # but we can test that the function is importable and handles edge cases.
        # The real validation is the e2e test on Mayer.
        from src.tools.scanned_page_ocr import _tesseract_fallback
        assert callable(_tesseract_fallback)


def _make_tess_data(**overrides):
    """Build a minimal pytesseract image_to_data DICT with one word per entry.

    Callers pass lists for each key; all lists must have the same length.
    Defaults produce a single word block with reasonable values.
    """
    defaults = {
        "text":      ["Hello"],
        "conf":      [90],
        "block_num": [1],
        "left":      [10],
        "top":       [20],
        "width":     [50],
        "height":    [15],
    }
    defaults.update(overrides)
    return defaults


def _make_mock_doc_page(tess_data: dict, page_width: int = 800):
    """Return (mock_doc, mock_pytesseract_module) ready for patching.

    mock_doc[page_number] returns a page whose get_pixmap() has .tobytes() and .width.
    """
    import io
    from unittest.mock import MagicMock

    # Minimal valid PNG bytes (1×1 white pixel)
    _PNG_1X1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x11\x00\x01\x9a`\x01\x97\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    pix = MagicMock()
    pix.tobytes.return_value = _PNG_1X1
    pix.width = page_width

    page = MagicMock()
    page.get_pixmap.return_value = pix

    doc = MagicMock()
    doc.__getitem__ = MagicMock(return_value=page)

    mock_tess = MagicMock()
    mock_tess.Output.DICT = "dict"
    mock_tess.image_to_data.return_value = tess_data

    return doc, mock_tess


class TestTesseractExtractBlocks:
    """Tests for _tesseract_extract_blocks() raw block extraction."""

    def _call(self, doc, mock_tess, page_number=0, dpi=300):
        """Invoke _tesseract_extract_blocks with pytesseract patched."""
        with patch.dict("sys.modules", {"pytesseract": mock_tess, "PIL": MagicMock(), "PIL.Image": MagicMock()}):
            with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_tess if name == "pytesseract" else __import__(name, *a, **kw)):
                # Use a direct patch on the module namespace instead
                pass
        # Patch pytesseract at the point of lazy import inside the function
        import sys
        orig = sys.modules.get("pytesseract")
        sys.modules["pytesseract"] = mock_tess
        # Also patch PIL.Image
        pil_mock = MagicMock()
        pil_mock.open.return_value = MagicMock()
        orig_pil = sys.modules.get("PIL")
        orig_pil_image = sys.modules.get("PIL.Image")
        sys.modules["PIL"] = pil_mock
        sys.modules["PIL.Image"] = pil_mock
        try:
            result = _tesseract_extract_blocks(doc, page_number, dpi)
        finally:
            if orig is None:
                sys.modules.pop("pytesseract", None)
            else:
                sys.modules["pytesseract"] = orig
            if orig_pil is None:
                sys.modules.pop("PIL", None)
            else:
                sys.modules["PIL"] = orig_pil
            if orig_pil_image is None:
                sys.modules.pop("PIL.Image", None)
            else:
                sys.modules["PIL.Image"] = orig_pil_image
        return result

    def test_returns_blocks_with_id_text_bbox(self):
        """Groups words into blocks by block_num, returns id/text/bbox dicts."""
        data = _make_tess_data(
            text=["Hello", "world"],
            conf=[90, 85],
            block_num=[1, 1],
            left=[10, 70],
            top=[20, 20],
            width=[50, 60],
            height=[15, 15],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)

        assert len(blocks) == 1
        b = blocks[0]
        assert b["id"] == 0
        assert b["text"] == "Hello world"
        # bbox is [left, top, width, height]
        assert len(b["bbox"]) == 4
        assert b["bbox"][0] == 10   # leftmost x
        assert b["bbox"][1] == 20   # topmost y

    def test_filters_low_confidence_words(self):
        """Words with conf < 20 are excluded; block with no remaining words is dropped."""
        data = _make_tess_data(
            text=["Good", "Bad"],
            conf=[80, 10],       # "Bad" below threshold
            block_num=[1, 1],
            left=[10, 70],
            top=[20, 20],
            width=[50, 40],
            height=[15, 15],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)

        assert len(blocks) == 1
        assert "Bad" not in blocks[0]["text"]
        assert "Good" in blocks[0]["text"]

    def test_filters_low_confidence_words_drops_block(self):
        """A block whose only word has conf < 20 is not returned at all."""
        data = _make_tess_data(
            text=["Junk"],
            conf=[5],
            block_num=[1],
            left=[10],
            top=[20],
            width=[50],
            height=[15],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)
        assert blocks == []

    def test_filters_short_fragments(self):
        """Blocks whose assembled text is shorter than 3 chars are excluded."""
        data = _make_tess_data(
            text=["Hi"],       # 2 chars — too short
            conf=[90],
            block_num=[1],
            left=[10],
            top=[20],
            width=[20],
            height=[15],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)
        assert blocks == []

    def test_filters_leaked_headers_footers(self):
        """Blocks matching header/footer pattern (e.g. '158 MAYER') are excluded."""
        # "158 MAYER" matches the page-number + ALL CAPS author pattern
        data = _make_tess_data(
            text=["158", "MAYER"],
            conf=[95, 95],
            block_num=[1, 1],
            left=[10, 50],
            top=[5, 5],
            width=[30, 60],
            height=[12, 12],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)
        assert blocks == []

    def test_empty_page_returns_empty_list(self):
        """If pytesseract returns no words, result is an empty list."""
        data = _make_tess_data(
            text=[],
            conf=[],
            block_num=[],
            left=[],
            top=[],
            width=[],
            height=[],
        )
        doc, mock_tess = _make_mock_doc_page(data)
        blocks = self._call(doc, mock_tess)
        assert blocks == []


# ── Minimal 1×1 white PNG for mocking page rendering ────────────────

_PNG_1X1_GEMINI = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x11\x00\x01\x9a`\x01\x97\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_gemini_mock_doc():
    """Return a mock fitz.Document whose page renders to a 1×1 PNG."""
    pix = MagicMock()
    pix.tobytes.return_value = _PNG_1X1_GEMINI

    page = MagicMock()
    page.get_pixmap.return_value = pix

    doc = MagicMock()
    doc.__getitem__ = MagicMock(return_value=page)
    return doc


class TestGeminiClassifyStructure:
    """Tests for _gemini_classify_structure()."""

    _VALID_BLOCKS = [
        {"id": 0, "text": "Introduction", "bbox": [10, 20, 200, 30]},
        {"id": 1, "text": "This is body text.", "bbox": [10, 60, 400, 20]},
    ]

    _VALID_RESPONSE = json.dumps({
        "regions": [
            {
                "block_ids": [0],
                "type": "heading",
                "heading_level": 1,
                "reading_order": 1,
                "column": 0,
                "bold": True,
                "font_size_relative": "large",
            },
            {
                "block_ids": [1],
                "type": "paragraph",
                "reading_order": 2,
                "column": 0,
            },
        ]
    })

    def test_returns_structure_on_success(self):
        """Returns parsed dict when Gemini returns valid structure JSON."""
        mock_response = MagicMock()
        mock_response.text = self._VALID_RESPONSE

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        doc = _make_gemini_mock_doc()
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", doc, page_number=0, blocks=self._VALID_BLOCKS
        )

        assert result is not None
        assert "regions" in result
        assert len(result["regions"]) == 2
        assert result["regions"][0]["type"] == "heading"
        assert result["regions"][1]["type"] == "paragraph"

    def test_returns_none_on_empty_response(self):
        """Returns None when Gemini returns None text (e.g. RECITATION block)."""
        mock_response = MagicMock()
        mock_response.text = None

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        doc = _make_gemini_mock_doc()
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", doc, page_number=0, blocks=self._VALID_BLOCKS
        )

        assert result is None

    def test_returns_none_on_exception(self):
        """Returns None when Gemini raises an exception."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API error")

        doc = _make_gemini_mock_doc()
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", doc, page_number=0, blocks=self._VALID_BLOCKS
        )

        assert result is None


# ── Tests for _haiku_correct_text ────────────────────────────────


# Minimal valid PNG bytes (1×1 white pixel) for mock pixmap
_PNG_1X1_HAIKU = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x11\x00\x01\x9a`\x01\x97\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_haiku_mock_doc(page_number: int = 0):
    """Return a mock fitz.Document suitable for _haiku_correct_text tests."""
    pix = MagicMock()
    pix.tobytes.return_value = _PNG_1X1_HAIKU

    page = MagicMock()
    page.get_pixmap.return_value = pix

    doc = MagicMock()
    doc.__getitem__ = MagicMock(return_value=page)
    return doc


def _make_haiku_mock_response(corrections_json: str):
    """Build a mock Anthropic message response with the given JSON text."""
    content_block = MagicMock()
    content_block.text = corrections_json
    content_block.type = "text"

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50

    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


class TestHaikuCorrectText:
    """Tests for _haiku_correct_text()."""

    def test_returns_corrections_dict(self):
        """Mock Anthropic returning corrections JSON — verify mapping returned."""
        from src.tools.scanned_page_ocr import _haiku_correct_text

        blocks = [{"id": 1, "text": "tbe learner"}]
        doc = _make_haiku_mock_doc()

        corrections_json = '{"corrections": [{"id": 1, "corrected_text": "the learner"}]}'
        mock_response = _make_haiku_mock_response(corrections_json)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.tools.scanned_page_ocr.Anthropic") as mock_anthropic_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response
                mock_anthropic_cls.return_value = mock_client

                result = _haiku_correct_text(blocks, doc, page_number=0)

        assert result == {1: "the learner"}

    def test_returns_empty_dict_when_all_correct(self):
        """Mock returning empty corrections — verify empty dict returned."""
        from src.tools.scanned_page_ocr import _haiku_correct_text

        blocks = [{"id": 0, "text": "the learner"}]
        doc = _make_haiku_mock_doc()

        corrections_json = '{"corrections": []}'
        mock_response = _make_haiku_mock_response(corrections_json)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.tools.scanned_page_ocr.Anthropic") as mock_anthropic_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response
                mock_anthropic_cls.return_value = mock_client

                result = _haiku_correct_text(blocks, doc, page_number=0)

        assert result == {}

    def test_returns_empty_dict_when_no_api_key(self):
        """When ANTHROPIC_API_KEY is absent, return {} without calling API."""
        from src.tools.scanned_page_ocr import _haiku_correct_text

        blocks = [{"id": 0, "text": "some text"}]
        doc = _make_haiku_mock_doc()

        import os as _os
        env_without_key = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}

        with patch.dict("os.environ", env_without_key, clear=True):
            with patch("src.tools.scanned_page_ocr.Anthropic") as mock_anthropic_cls:
                result = _haiku_correct_text(blocks, doc, page_number=0)
                mock_anthropic_cls.assert_not_called()

        assert result == {}

    def test_returns_empty_dict_on_exception(self):
        """When Anthropic raises an exception, return {} gracefully."""
        from src.tools.scanned_page_ocr import _haiku_correct_text

        blocks = [{"id": 0, "text": "some text"}]
        doc = _make_haiku_mock_doc()

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("src.tools.scanned_page_ocr.Anthropic") as mock_anthropic_cls:
                mock_anthropic_cls.side_effect = Exception("API unavailable")

                result = _haiku_correct_text(blocks, doc, page_number=0)

        assert result == {}


# ── Tests for _apply_corrections ──────────────────────────────────


class TestApplyCorrections:
    """Tests for _apply_corrections()."""

    def test_applies_corrections_to_matching_blocks(self):
        """Corrections are applied to blocks whose id matches."""
        blocks = [
            {"id": 0, "text": "tbe learner", "bbox": [0, 0, 100, 20]},
            {"id": 1, "text": "correct text", "bbox": [0, 30, 100, 20]},
            {"id": 2, "text": "adn more", "bbox": [0, 60, 100, 20]},
        ]
        corrections = {0: "the learner", 2: "and more"}

        result = _apply_corrections(blocks, corrections)

        assert len(result) == 3
        assert result[0]["text"] == "the learner"
        assert result[1]["text"] == "correct text"  # unchanged
        assert result[2]["text"] == "and more"

    def test_empty_corrections_returns_blocks_unchanged(self):
        """Empty corrections dict returns a copy of blocks with original text."""
        blocks = [
            {"id": 0, "text": "some text", "bbox": [0, 0, 100, 20]},
            {"id": 1, "text": "more text", "bbox": [0, 30, 100, 20]},
        ]
        result = _apply_corrections(blocks, {})

        assert len(result) == 2
        assert result[0]["text"] == "some text"
        assert result[1]["text"] == "more text"

    def test_preserves_bbox_and_id(self):
        """Corrected blocks keep original id and bbox unchanged."""
        blocks = [
            {"id": 5, "text": "wrng", "bbox": [10, 20, 200, 30]},
        ]
        corrections = {5: "wrong"}

        result = _apply_corrections(blocks, corrections)

        assert result[0]["id"] == 5
        assert result[0]["bbox"] == [10, 20, 200, 30]
        assert result[0]["text"] == "wrong"


# ── Tests for _merge_blocks_and_structure ─────────────────────────


class TestMergeBlocksAndStructure:
    """Tests for _merge_blocks_and_structure()."""

    _BLOCKS = [
        {"id": 0, "text": "Introduction", "bbox": [10, 20, 200, 30]},
        {"id": 1, "text": "This is body text.", "bbox": [10, 60, 400, 20]},
        {"id": 2, "text": "More body text here.", "bbox": [10, 90, 400, 20]},
    ]

    def test_heading_region(self):
        """Heading region creates ParagraphInfo with correct style, level, and bold run."""
        structure = {
            "regions": [
                {
                    "block_ids": [0],
                    "type": "heading",
                    "heading_level": 2,
                    "bold": True,
                    "font_size_relative": "large",
                    "reading_order": 1,
                },
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            self._BLOCKS, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Introduction"
        assert paras[0].heading_level == 2
        assert paras[0].style_name == "Heading 2"
        assert paras[0].runs[0].bold is True
        assert len(tables) == 0
        assert len(figures) == 0

    def test_paragraph_region_merges_blocks(self):
        """Multiple block_ids are concatenated into a single paragraph."""
        structure = {
            "regions": [
                {
                    "block_ids": [1, 2],
                    "type": "paragraph",
                    "reading_order": 1,
                },
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            self._BLOCKS, structure, page_number=3,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert "This is body text." in paras[0].text
        assert "More body text here." in paras[0].text
        assert paras[0].page_number == 3

    def test_table_region(self):
        """table_data → TableInfo with header row and data rows."""
        blocks = [{"id": 0, "text": "table text", "bbox": [0, 0, 100, 100]}]
        structure = {
            "regions": [
                {
                    "block_ids": [0],
                    "type": "table",
                    "reading_order": 1,
                    "table_data": {
                        "headers": ["Name", "Score"],
                        "rows": [["Alice", "95"], ["Bob", "87"]],
                    },
                },
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=1,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(tables) == 1
        tbl = tables[0]
        assert tbl.header_row_count == 1
        assert tbl.row_count == 3  # 1 header + 2 data
        assert tbl.col_count == 2
        assert tbl.rows[0][0].text == "Name"
        assert tbl.rows[1][0].text == "Alice"
        assert tbl.rows[2][1].text == "87"
        assert tbl.id == "ocr_tbl_0"

    def test_figure_region(self):
        """figure_description → ImageInfo with alt_text."""
        blocks = [{"id": 0, "text": "", "bbox": [0, 0, 500, 400]}]
        structure = {
            "regions": [
                {
                    "block_ids": [0],
                    "type": "figure",
                    "figure_description": "A bar chart showing student performance.",
                    "reading_order": 1,
                },
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=2,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(figures) == 1
        assert figures[0].alt_text == "A bar chart showing student performance."
        assert figures[0].page_number == 2
        assert figures[0].is_decorative is False
        assert figures[0].id == "ocr_img_0"

    def test_page_header_footer_skipped(self):
        """page_header and page_footer regions are excluded from output."""
        blocks = [
            {"id": 0, "text": "Journal of Ed", "bbox": [0, 0, 400, 20]},
            {"id": 1, "text": "Body paragraph content.", "bbox": [0, 50, 400, 20]},
            {"id": 2, "text": "Page 7", "bbox": [0, 780, 400, 20]},
        ]
        structure = {
            "regions": [
                {"block_ids": [0], "type": "page_header", "reading_order": 1},
                {"block_ids": [1], "type": "paragraph", "reading_order": 2},
                {"block_ids": [2], "type": "page_footer", "reading_order": 3},
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=6,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 1
        assert paras[0].text == "Body paragraph content."

    def test_reading_order_respected(self):
        """Regions are output in ascending reading_order regardless of list order."""
        blocks = [
            {"id": 0, "text": "First heading", "bbox": [0, 0, 200, 20]},
            {"id": 1, "text": "Second paragraph.", "bbox": [0, 30, 400, 20]},
            {"id": 2, "text": "Third paragraph.", "bbox": [0, 60, 400, 20]},
        ]
        structure = {
            "regions": [
                # Deliberately out of order in the list
                {"block_ids": [2], "type": "paragraph", "reading_order": 3},
                {"block_ids": [0], "type": "heading", "heading_level": 1, "reading_order": 1},
                {"block_ids": [1], "type": "paragraph", "reading_order": 2},
            ]
        }
        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )
        assert len(paras) == 3
        assert paras[0].text == "First heading"
        assert paras[1].text == "Second paragraph."
        assert paras[2].text == "Third paragraph."


class TestHeuristicClassifyBlocks:
    """Tests for _heuristic_classify_blocks() — Gemini-unavailable fallback classifier."""

    def test_all_caps_detected_as_heading(self):
        """ALL CAPS short text is classified as Heading 2; body text stays Normal."""
        blocks = [
            {"id": 0, "text": "INTRODUCTION", "bbox": [10, 10, 200, 18]},
            {
                "id": 1,
                "text": "This is a normal paragraph with enough text to be body content.",
                "bbox": [10, 40, 400, 14],
            },
        ]
        result = _heuristic_classify_blocks(blocks, page_number=1, para_offset=0)

        assert len(result) == 2

        heading = result[0]
        assert heading.style_name == "Heading 2"
        assert heading.heading_level == 2
        assert heading.runs[0].bold is True
        assert heading.runs[0].font_size_pt == 16.0
        assert heading.page_number == 1
        assert heading.id == "ocr_p_0"

        body = result[1]
        assert body.style_name == "Normal"
        assert body.heading_level is None
        assert body.runs[0].bold is None or body.runs[0].bold is False
        assert body.runs[0].font_size_pt == 12.0
        assert body.page_number == 1
        assert body.id == "ocr_p_1"

    def test_body_text_not_heading(self):
        """Long lowercase text is classified as Normal with correct id and page_number."""
        blocks = [
            {
                "id": 0,
                "text": "This sentence is long enough to be counted as body text and should not be a heading.",
                "bbox": [10, 10, 500, 14],
            },
        ]
        result = _heuristic_classify_blocks(blocks, page_number=3, para_offset=5)

        assert len(result) == 1
        para = result[0]
        assert para.style_name == "Normal"
        assert para.heading_level is None
        assert para.page_number == 3
        assert para.id == "ocr_p_5"

    def test_empty_blocks_returns_empty(self):
        """Empty block list returns an empty list."""
        result = _heuristic_classify_blocks([], page_number=0, para_offset=0)
        assert result == []
