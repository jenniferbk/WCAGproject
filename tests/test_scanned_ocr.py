"""Tests for scanned page OCR and layout analysis."""

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
from src.tools.scanned_page_ocr import (
    ScannedPageResult,
    _collect_table_paragraphs,
    _find_garbled_pages,
    _find_table_captions,
    _is_garbled_text,
    _is_leaked_header_footer,
    _regions_to_model_objects,
    _relative_to_pt,
    _sort_regions_by_column,
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
