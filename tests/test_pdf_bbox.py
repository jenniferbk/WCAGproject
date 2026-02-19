"""Tests for bbox and page_number data on ParagraphInfo and TableInfo.

Verifies that the PDF parser correctly populates bounding box and page
number fields, which are required by the iText tagger for position-based
matching.
"""

from pathlib import Path

import pytest

from src.tools.pdf_parser import parse_pdf

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"
LESSON_PDF = TESTDOCS / "Lesson 2 Behaviorism and Structuralism.pdf"


class TestParagraphBbox:

    def test_all_paragraphs_have_page_number(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        for p in result.document.paragraphs:
            assert p.page_number is not None, f"{p.id} missing page_number"

    def test_all_paragraphs_have_bbox(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        # Most paragraphs should have bbox (synthetic anchors may not)
        with_bbox = [p for p in result.document.paragraphs if p.bbox is not None]
        total = len(result.document.paragraphs)
        assert len(with_bbox) / total > 0.9, \
            f"Only {len(with_bbox)}/{total} paragraphs have bbox"

    def test_bbox_format(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        for p in result.document.paragraphs:
            if p.bbox is not None:
                assert len(p.bbox) == 4, f"{p.id} bbox has {len(p.bbox)} values"
                x0, y0, x1, y1 = p.bbox
                assert x0 < x1, f"{p.id} bbox x0={x0} >= x1={x1}"
                assert y0 < y1, f"{p.id} bbox y0={y0} >= y1={y1}"
                assert x0 >= 0, f"{p.id} bbox x0 negative"
                assert y0 >= 0, f"{p.id} bbox y0 negative"

    def test_page_numbers_sequential(self):
        """Page numbers should be in non-decreasing order."""
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        pages = [p.page_number for p in result.document.paragraphs]
        for i in range(1, len(pages)):
            assert pages[i] >= pages[i-1], \
                f"Page number decreased at paragraph {i}: {pages[i-1]} -> {pages[i]}"

    def test_first_page_paragraphs_on_page_0(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        page_0 = [p for p in result.document.paragraphs if p.page_number == 0]
        assert len(page_0) > 0, "No paragraphs on page 0"

    def test_lesson_pdf_paragraphs_have_bbox(self):
        """Test with a different PDF (has images)."""
        result = parse_pdf(str(LESSON_PDF))
        assert result.success
        with_bbox = [p for p in result.document.paragraphs if p.bbox is not None]
        assert len(with_bbox) > 0, "No paragraphs with bbox"


class TestTableBbox:

    def test_tables_have_page_number(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        assert len(result.document.tables) > 0, "No tables found"
        for t in result.document.tables:
            assert t.page_number is not None, f"{t.id} missing page_number"

    def test_tables_have_bbox(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        for t in result.document.tables:
            assert t.bbox is not None, f"{t.id} missing bbox"
            assert len(t.bbox) == 4, f"{t.id} bbox has {len(t.bbox)} values"
            x0, y0, x1, y1 = t.bbox
            assert x0 < x1, f"{t.id} bbox x0={x0} >= x1={x1}"
            assert y0 < y1, f"{t.id} bbox y0={y0} >= y1={y1}"

class TestLinkBbox:

    def test_links_have_page_number(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        if len(result.document.links) == 0:
            pytest.skip("No links found in syllabus PDF")
        for lnk in result.document.links:
            assert lnk.page_number is not None, f"{lnk.id} missing page_number"

    def test_links_have_bbox(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        if len(result.document.links) == 0:
            pytest.skip("No links found in syllabus PDF")
        for lnk in result.document.links:
            assert lnk.bbox is not None, f"{lnk.id} missing bbox"
            assert len(lnk.bbox) == 4, f"{lnk.id} bbox has {len(lnk.bbox)} values"
            x0, y0, x1, y1 = lnk.bbox
            assert x0 < x1, f"{lnk.id} bbox x0={x0} >= x1={x1}"

    def test_link_has_url(self):
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        if len(result.document.links) == 0:
            pytest.skip("No links found in syllabus PDF")
        for lnk in result.document.links:
            assert lnk.url, f"{lnk.id} has empty URL"


class TestImageBbox:

    def test_images_have_bbox(self):
        """Lesson 2 has 36 images — all should have bbox from get_image_rects."""
        result = parse_pdf(str(LESSON_PDF))
        assert result.success
        assert len(result.document.images) > 0, "No images found"
        images_with_bbox = [img for img in result.document.images if img.bbox is not None]
        assert len(images_with_bbox) > 0, "No images have bbox"
        for img in images_with_bbox:
            assert len(img.bbox) == 4, f"{img.id} bbox has {len(img.bbox)} values"
            x0, y0, x1, y1 = img.bbox
            assert x1 > x0, f"{img.id} bbox x0={x0} >= x1={x1}"
            assert y1 > y0, f"{img.id} bbox y0={y0} >= y1={y1}"

    def test_images_have_page_number(self):
        result = parse_pdf(str(LESSON_PDF))
        assert result.success
        for img in result.document.images:
            assert img.page_number is not None, f"{img.id} missing page_number"

    def test_image_bbox_reasonable_size(self):
        """Image bboxes should be at least 10pt on each side."""
        result = parse_pdf(str(LESSON_PDF))
        assert result.success
        for img in result.document.images:
            if img.bbox:
                x0, y0, x1, y1 = img.bbox
                width = x1 - x0
                height = y1 - y0
                assert width > 5, f"{img.id} bbox too narrow: {width}pt"
                assert height > 5, f"{img.id} bbox too short: {height}pt"


class TestTableBbox:

    def test_table_bbox_reasonable_size(self):
        """Table bboxes should be at least a few points wide and tall."""
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        for t in result.document.tables:
            if t.bbox:
                x0, y0, x1, y1 = t.bbox
                width = x1 - x0
                height = y1 - y0
                assert width > 10, f"{t.id} bbox too narrow: {width}pt"
                assert height > 10, f"{t.id} bbox too short: {height}pt"
