"""Tests for the PDF parser.

Tests against real PDF files in testdocs/ directory.
"""

import pytest
from pathlib import Path

from src.tools.pdf_parser import (
    parse_pdf,
    ParseResult,
    _get_base_font_name,
    _should_split_before_line,
    _split_block_into_sub_paragraphs,
)

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"
LESSON_PDF = TESTDOCS / "Lesson 2 Behaviorism and Structuralism.pdf"


class TestParsePdfBasic:
    """Basic parsing tests."""

    def test_parse_success(self):
        result = parse_pdf(SYLLABUS_PDF)
        assert result.success is True
        assert result.document is not None
        assert result.error == ""

    def test_source_format(self):
        result = parse_pdf(SYLLABUS_PDF)
        assert result.document.source_format == "pdf"

    def test_source_path(self):
        result = parse_pdf(SYLLABUS_PDF)
        assert result.document.source_path == str(SYLLABUS_PDF)

    def test_file_not_found(self):
        result = parse_pdf("/nonexistent/file.pdf")
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_wrong_extension(self):
        result = parse_pdf(TESTDOCS / "Assignment 1.docx")
        assert result.success is False
        assert "not a .pdf" in result.error.lower()


class TestPdfParagraphs:
    """Test paragraph extraction."""

    def test_paragraphs_extracted(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.paragraph_count > 0

    def test_paragraph_ids_sequential(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for i, p in enumerate(doc.paragraphs):
            assert p.id == f"p_{i}"

    def test_paragraph_text_content(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # The syllabus should contain "EMAT 8030" in the first paragraph
        texts = [p.text for p in doc.paragraphs]
        assert any("EMAT 8030" in t for t in texts)

    def test_paragraph_runs_have_font_info(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # At least some paragraphs should have runs with font sizes
        has_font = False
        for p in doc.paragraphs:
            for r in p.runs:
                if r.font_size_pt is not None:
                    has_font = True
                    break
            if has_font:
                break
        assert has_font

    def test_paragraph_runs_detect_bold(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # The syllabus has bold text like "Course Description"
        has_bold = any(
            r.bold is True
            for p in doc.paragraphs
            for r in p.runs
        )
        assert has_bold


class TestPdfMetadata:
    """Test metadata extraction."""

    def test_metadata_extracted(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.metadata is not None

    def test_lesson_has_title(self):
        """The lesson PDF (exported from PowerPoint) has a title."""
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        assert doc.metadata.title != ""


class TestPdfImages:
    """Test image extraction."""

    def test_no_images_in_syllabus(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.image_count == 0

    def test_images_in_lesson(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        assert doc.stats.image_count > 0

    def test_image_has_page_number(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        for img in doc.images:
            assert img.page_number is not None
            assert img.page_number >= 0

    def test_image_ids_sequential(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        for i, img in enumerate(doc.images):
            assert img.id == f"img_{i}"

    def test_image_has_data(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        for img in doc.images[:3]:
            assert img.image_data is not None
            assert len(img.image_data) > 0

    def test_image_has_dimensions(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        for img in doc.images[:3]:
            assert img.width_px > 0
            assert img.height_px > 0

    def test_image_has_content_type(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        for img in doc.images[:3]:
            assert img.content_type.startswith("image/")

    def test_images_missing_alt(self):
        result = parse_pdf(LESSON_PDF)
        doc = result.document
        assert doc.stats.images_missing_alt > 0


class TestPdfTables:
    """Test table extraction."""

    def test_tables_in_syllabus(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.table_count > 0

    def test_table_ids_sequential(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for i, t in enumerate(doc.tables):
            assert t.id == f"tbl_{i}"

    def test_table_has_rows(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for t in doc.tables:
            assert t.row_count > 0
            assert len(t.rows) == t.row_count

    def test_table_has_columns(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for t in doc.tables:
            assert t.col_count > 0

    def test_table_cells_have_text(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # At least one table should have cells with text
        has_text = any(
            cell.text.strip()
            for t in doc.tables
            for row in t.rows
            for cell in row
        )
        assert has_text


class TestPdfContentOrder:
    """Test content order extraction."""

    def test_content_order_populated(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert len(doc.content_order) > 0

    def test_content_order_has_paragraphs_and_tables(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        types = {item.content_type for item in doc.content_order}
        assert "paragraph" in types
        assert "table" in types


class TestPdfFakeHeadings:
    """Test fake heading detection."""

    def test_fake_heading_candidates_detected(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # The syllabus has bold section headers like "Course Description"
        assert doc.stats.fake_heading_candidates >= 1

    def test_fake_heading_has_signals(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        candidates = [
            p for p in doc.paragraphs
            if p.fake_heading_signals and p.fake_heading_signals.score >= 0.5
        ]
        assert len(candidates) >= 1
        for c in candidates:
            # Candidates must have at least one strong signal
            assert (
                c.fake_heading_signals.all_runs_bold is True
                or c.fake_heading_signals.distinct_font is True
                or c.fake_heading_signals.font_size_above_avg is True
            )


class TestPdfLinks:
    """Test link extraction."""

    def test_links_extracted(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.link_count > 0

    def test_link_has_url(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for link in doc.links:
            assert link.url != ""

    def test_link_ids_sequential(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for i, link in enumerate(doc.links):
            assert link.id == f"link_{i}"


class TestPdfBlockSplitting:
    """Test that formatting transitions in PDF blocks produce separate paragraphs."""

    def test_bold_headings_split_from_body(self):
        """Bold section headings like 'Course Description' should be in their
        own paragraph, not merged with surrounding non-bold body text."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # Find "Course Description" as its own paragraph
        course_desc_paras = [
            p for p in doc.paragraphs
            if "Course Description" in p.text and len(p.text) < 50
        ]
        assert len(course_desc_paras) >= 1, (
            "Expected 'Course Description' to be its own short paragraph "
            "after block splitting"
        )

    def test_course_title_split_from_body(self):
        """Course title 'EMAT 8030...' should be in its own paragraph."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        title_paras = [
            p for p in doc.paragraphs
            if "EMAT 8030" in p.text
            and "Advanced Study" in p.text
            and len(p.text) < 100
        ]
        assert len(title_paras) >= 1

    def test_more_paragraphs_after_splitting(self):
        """Block splitting should produce more paragraphs than blocks."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # With splitting, we should have many more paragraphs than before
        # (previously ~20-30 from raw blocks, now should be higher)
        assert doc.stats.paragraph_count > 30

    def test_paragraph_ids_still_sequential(self):
        """Even after splitting, paragraph IDs should remain sequential."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for i, p in enumerate(doc.paragraphs):
            assert p.id == f"p_{i}"


class TestPdfDistinctFontHeading:
    """Test distinct-font heading detection."""

    def test_course_title_detected_as_heading_candidate(self):
        """The course title uses 'Century' font (different from body
        'TimesNewRoman'), so it should be a fake heading candidate."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        title_paras = [
            p for p in doc.paragraphs
            if "EMAT 8030" in p.text
            and p.fake_heading_signals is not None
        ]
        assert len(title_paras) >= 1
        title = title_paras[0]
        assert title.fake_heading_signals.distinct_font is True
        assert title.fake_heading_signals.score >= 0.4

    def test_bold_headings_detected(self):
        """Bold section headings should be detected as fake heading candidates
        after block splitting separates them from body text."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        candidates = [
            p for p in doc.paragraphs
            if p.fake_heading_signals and p.fake_heading_signals.score >= 0.5
        ]
        # Should detect multiple headings now (title + section headings)
        assert len(candidates) >= 3

    def test_body_text_not_flagged(self):
        """Regular body text in the dominant font should not get distinct_font."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # Find a paragraph that's clearly body text (long, not bold)
        body_paras = [
            p for p in doc.paragraphs
            if len(p.text) > 100
            and p.fake_heading_signals is None
        ]
        assert len(body_paras) > 0


class TestBlockSplittingHelpers:
    """Unit tests for block splitting helper functions."""

    def test_get_base_font_name_bold_suffix(self):
        assert _get_base_font_name("TimesNewRomanPS-BoldMT") == "TimesNewRomanPS"

    def test_get_base_font_name_italic_suffix(self):
        assert _get_base_font_name("TimesNewRomanPS-ItalicMT") == "TimesNewRomanPS"

    def test_get_base_font_name_psmt_suffix(self):
        assert _get_base_font_name("TimesNewRomanPSMT") == "TimesNewRomanPS"

    def test_get_base_font_name_no_suffix(self):
        assert _get_base_font_name("Century") == "Century"

    def test_should_split_on_bold_transition(self):
        """Lines transitioning from bold to non-bold should split."""
        bold_line = {"spans": [{"text": "Heading", "font": "Arial-Bold", "size": 12.0, "flags": 16}]}
        normal_line = {"spans": [{"text": "Body text", "font": "Arial", "size": 12.0, "flags": 0}]}
        assert _should_split_before_line(bold_line, normal_line) is True

    def test_no_split_same_formatting(self):
        """Lines with same formatting should not split."""
        line1 = {"spans": [{"text": "Line 1", "font": "Arial", "size": 12.0, "flags": 0}]}
        line2 = {"spans": [{"text": "Line 2", "font": "Arial", "size": 12.0, "flags": 0}]}
        assert _should_split_before_line(line1, line2) is False

    def test_should_split_on_font_size_change(self):
        """Lines with >1.5pt size difference should split."""
        big_line = {"spans": [{"text": "Title", "font": "Arial", "size": 16.0, "flags": 0}]}
        normal_line = {"spans": [{"text": "Body", "font": "Arial", "size": 12.0, "flags": 0}]}
        assert _should_split_before_line(big_line, normal_line) is True

    def test_should_split_on_font_family_change(self):
        """Lines with different font families should split."""
        line1 = {"spans": [{"text": "Title", "font": "Century", "size": 12.0, "flags": 0}]}
        line2 = {"spans": [{"text": "Body", "font": "TimesNewRomanPSMT", "size": 12.0, "flags": 0}]}
        assert _should_split_before_line(line1, line2) is True

    def test_split_block_basic(self):
        """A block with bold heading + normal body should split into 2 groups."""
        block = {
            "lines": [
                {"spans": [{"text": "Heading", "font": "Arial-Bold", "size": 12.0, "flags": 16}]},
                {"spans": [{"text": "Body line 1", "font": "Arial", "size": 12.0, "flags": 0}]},
                {"spans": [{"text": "Body line 2", "font": "Arial", "size": 12.0, "flags": 0}]},
            ]
        }
        groups = _split_block_into_sub_paragraphs(block)
        assert len(groups) == 2
        assert len(groups[0]) == 1  # heading line
        assert len(groups[1]) == 2  # body lines

    def test_split_block_no_split_needed(self):
        """A block with uniform formatting should produce one group."""
        block = {
            "lines": [
                {"spans": [{"text": "Line 1", "font": "Arial", "size": 12.0, "flags": 0}]},
                {"spans": [{"text": "Line 2", "font": "Arial", "size": 12.0, "flags": 0}]},
            ]
        }
        groups = _split_block_into_sub_paragraphs(block)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_split_block_empty(self):
        """An empty block should produce no groups."""
        block = {"lines": []}
        groups = _split_block_into_sub_paragraphs(block)
        assert len(groups) == 0

    def test_split_block_multiple_transitions(self):
        """Multiple formatting transitions should produce multiple groups."""
        block = {
            "lines": [
                {"spans": [{"text": "Title", "font": "Century", "size": 14.0, "flags": 16}]},
                {"spans": [{"text": "Body 1", "font": "Arial", "size": 12.0, "flags": 0}]},
                {"spans": [{"text": "Body 2", "font": "Arial", "size": 12.0, "flags": 0}]},
                {"spans": [{"text": "Subhead", "font": "Arial-Bold", "size": 12.0, "flags": 16}]},
                {"spans": [{"text": "More body", "font": "Arial", "size": 12.0, "flags": 0}]},
            ]
        }
        groups = _split_block_into_sub_paragraphs(block)
        assert len(groups) == 4  # Title, Body1+2, Subhead, More body


class TestPdfStats:
    """Test document stats."""

    def test_stats_populated(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.paragraph_count > 0
        assert doc.stats.table_count > 0
