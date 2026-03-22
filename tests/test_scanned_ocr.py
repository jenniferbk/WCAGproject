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
    _regions_to_model_objects,
    _relative_to_pt,
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
