"""Tests for the PDF parser.

Tests against real PDF files in testdocs/ directory.
"""

import pytest
from pathlib import Path

from src.tools.pdf_parser import (
    parse_pdf,
    ParseResult,
    _get_base_font_name,
    _is_likely_data_table,
    _is_citation_like,
    _apply_cluster_penalty,
    _should_split_before_line,
    _split_block_into_sub_paragraphs,
)
from src.models.document import FakeHeadingSignals, ParagraphInfo, RunInfo

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


class TestTableFiltering:
    """Test _is_likely_data_table() false positive filtering."""

    def test_single_column_rejected(self):
        data = [["row 1"], ["row 2"], ["row 3"]]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "single-column" in reason

    def test_single_row_rejected(self):
        data = [["col A", "col B", "col C"]]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "single-row" in reason

    def test_very_small_table_rejected(self):
        data = [["a", "b"], ["c", "d"]]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "very small" in reason

    def test_high_empty_ratio_rejected(self):
        # 3x3 with 7 of 9 cells empty = 78% empty
        data = [
            ["text", None, None],
            [None, None, None],
            [None, "text", None],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "empty" in reason

    def test_long_text_narrow_table_rejected(self):
        # 2-column table with very long cell text (bibliography pattern)
        long_text = "Ginsburg, H. P., & Opper, S. (1988). " * 5  # ~190 chars
        data = [
            [long_text, ""],
            [long_text, ""],
            [long_text, ""],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        # May be caught by dominant column or per-column long text heuristic
        assert "dominates" in reason or "long text" in reason

    def test_real_schedule_table_accepted(self):
        # Typical course schedule: 3+ cols, varied data
        data = [
            ["Date", "Topic", "Readings"],
            ["Jan 12", "Introduction", "Chapter 1"],
            ["Jan 19", "Theory", "Chapter 2"],
            ["Jan 26", "Methods", "Chapter 3"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True
        assert reason == ""

    def test_real_grading_table_accepted(self):
        # Grading rubric: 4 cols, no empty cells
        data = [
            ["Component", "Weight", "Due Date", "Notes"],
            ["Midterm", "30%", "March 5", "In class"],
            ["Final", "40%", "May 10", "Cumulative"],
            ["Homework", "30%", "Weekly", "Drop lowest"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True

    def test_empty_data_rejected(self):
        is_table, reason = _is_likely_data_table([])
        assert is_table is False
        assert "empty" in reason

    def test_three_col_with_some_empty_passes(self):
        # 3-column table with ~22% empty (like schedule continuation) should pass
        data = [
            ["", "", "Reading continuation text here"],
            ["Feb 9", "Topic name", "Read for today: article reference"],
            ["Feb 16", "Another topic", "More readings and assignments"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True

    def test_just_over_60_pct_empty_rejected(self):
        # 10 of 15 cells empty = 66.7% → over 60% threshold → rejected
        data = [
            ["a", None, None, None, "b"],
            [None, None, "c", None, None],
            ["d", None, None, None, "e"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "empty" in reason

    def test_exactly_60_pct_empty_passes(self):
        # Exactly 60% should pass (threshold is strictly >60%)
        # 3x5 with 9 of 15 empty = 60.0%
        data = [
            ["a", None, "b", None, "c"],
            [None, "d", None, "e", None],
            ["f", None, "g", None, None],
        ]
        # Count: a, b, c, d, e, f, g = 7 non-empty, 8 empty out of 15 = 53%
        # Need exactly 60%: 3x5 with 9 empty = 60%
        data = [
            ["a", None, None, None, "b"],
            [None, None, "c", None, None],
            [None, None, None, None, None],
        ]
        # 3 non-empty, 12 empty out of 15 = 80% — too much
        # Let me use 5x3 with 9 of 15 empty:
        data = [
            ["a", "b", "c"],
            [None, None, None],
            ["d", None, "e"],
            [None, "f", None],
            [None, None, None],
        ]
        # 6 non-empty, 9 empty out of 15 = 60% — exactly at threshold
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True  # 60% is NOT >60%


class TestTableFilteringIntegration:
    """Integration tests: table filtering on real syllabus PDF."""

    def test_syllabus_has_tables(self):
        """The course schedule should still be detected as a table."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.table_count >= 1

    def test_syllabus_fewer_tables_than_unfiltered(self):
        """Filtering should reduce the table count significantly.

        The syllabus has a course schedule (~4 real tables spanning pages 5-9)
        plus many bibliography/reading list entries that PyMuPDF misidentifies.
        After filtering, we should have fewer tables.
        """
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # Before filtering: ~24 tables. After: should be significantly fewer.
        assert doc.stats.table_count < 20

    def test_schedule_table_preserved(self):
        """The course schedule table (Date/Topic/Readings) should survive filtering."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # Find a table with "Date" or "Topic" in first row
        schedule_tables = [
            t for t in doc.tables
            if t.rows and any("Date" in c.text or "Topic" in c.text for c in t.rows[0])
        ]
        assert len(schedule_tables) >= 1

    def test_table_ids_sequential_after_filtering(self):
        """Table IDs should remain sequential even after filtering removes some."""
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        for i, t in enumerate(doc.tables):
            assert t.id == f"tbl_{i}"

    def test_paragraph_count_stable_or_increased(self):
        """Filtering false tables should recover text as paragraphs.

        Text inside rejected tables is no longer excluded from paragraph
        extraction, so paragraph count should be >= before.
        """
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        # With filtering, text from rejected table rects flows into paragraphs
        assert doc.stats.paragraph_count >= 100


class TestTableFilteringBulletColumn:
    """Test bullet/number first column heuristic."""

    def test_bullet_first_column_rejected(self):
        """Table with bullet characters in first column = formatted list."""
        data = [
            ["\u2022", "", "Smith, J. (2019). Title of article."],
            ["\u2022", "", "Jones, K. (2020). Another reference."],
            ["\u2022", "", "Brown, A. (2021). Yet another paper."],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "bullet" in reason

    def test_dash_first_column_rejected(self):
        data = [
            ["-", "Item one description here"],
            ["-", "Item two description here"],
            ["-", "Item three description here"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "bullet" in reason

    def test_number_first_column_rejected(self):
        data = [
            ["1.", "", "First reading assignment"],
            ["2.", "", "Second reading assignment"],
            ["3.", "", "Third reading assignment"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "bullet" in reason or "number" in reason

    def test_non_bullet_first_column_passes(self):
        """Real data in first column should not trigger bullet filter."""
        data = [
            ["Date", "Topic", "Reading"],
            ["Jan 12", "Intro", "Chapter 1"],
            ["Jan 19", "Methods", "Chapter 2"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True


class TestTableFilteringDominantColumn:
    """Test dominant column heuristic."""

    def test_one_column_dominates_text_rejected(self):
        """If one column has >85% of all text, it's a list with formatting."""
        data = [
            ["", "Smith, J. (2019). A very long reference text that describes an important article in detail."],
            ["", "Jones, K. (2020). Another lengthy reference with a long title and full bibliographic info."],
            ["", "Brown, A. (2021). Yet another paper with extensive description and publication details."],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "dominates" in reason

    def test_balanced_columns_pass(self):
        """Columns with balanced text distribution should pass."""
        data = [
            ["Assignment", "Due Date", "Points"],
            ["Homework 1", "Feb 1", "100"],
            ["Homework 2", "Feb 15", "100"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True


class TestTableFilteringPerColumnLongText:
    """Test per-column long text heuristic."""

    def test_wide_table_long_column_rejected(self):
        """3-column table where one column has long text = references."""
        # Use enough text in other columns to avoid the dominant-column check
        # (need the long column to be <=85% of total), but still >120 avg
        long_ref = "x" * 150  # 150 chars average per cell
        other_text = "y" * 40  # enough to keep dominant col under 85%
        data = [
            [other_text, other_text, long_ref],
            [other_text, other_text, long_ref],
            [other_text, other_text, long_ref],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is False
        assert "long text" in reason

    def test_wide_table_short_columns_pass(self):
        """3-column table with short cell text should pass."""
        data = [
            ["Week 1", "Introduction", "Ch. 1"],
            ["Week 2", "Literature Review", "Ch. 2"],
            ["Week 3", "Methods", "Ch. 3"],
        ]
        is_table, reason = _is_likely_data_table(data)
        assert is_table is True

    def test_long_column_needs_3_cells(self):
        """Per-column long text needs >=3 non-empty cells to trigger."""
        # Use 3 columns with enough text in other cols to avoid dominant-col
        # check, but only 2 non-empty cells in the long column
        long_ref = "x" * 150
        other = "y" * 50
        data = [
            [other, other, long_ref],
            [other, other, long_ref],
            [other, other, ""],  # empty — only 2 non-empty in col 2
        ]
        is_table, reason = _is_likely_data_table(data)
        # With only 2 non-empty cells in the long column, shouldn't trigger
        assert is_table is True


class TestCitationDetection:
    """Test _is_citation_like() function."""

    def test_parenthesized_year(self):
        assert _is_citation_like("Smith, J. (2019). Some title.") is True

    def test_et_al(self):
        assert _is_citation_like("Johnson et al. published a study") is True

    def test_many_commas(self):
        assert _is_citation_like("Smith, Jones, Brown, and Davis wrote") is True

    def test_normal_heading(self):
        assert _is_citation_like("Course Schedule") is False

    def test_short_heading(self):
        assert _is_citation_like("Introduction") is False

    def test_two_commas_not_enough(self):
        assert _is_citation_like("First, second, and third") is False


class TestClusterPenalty:
    """Test _apply_cluster_penalty() function."""

    def _make_para(self, text: str, score: float) -> ParagraphInfo:
        signals = FakeHeadingSignals(
            all_runs_bold=True,
            is_short=True,
            score=score,
        )
        return ParagraphInfo(
            id=f"p_{id(text)}",
            text=text,
            runs=[RunInfo(text=text, bold=True)],
            fake_heading_signals=signals,
        )

    def _make_non_candidate(self, text: str) -> ParagraphInfo:
        return ParagraphInfo(
            id=f"p_{id(text)}",
            text=text,
            runs=[RunInfo(text=text)],
        )

    def test_cluster_of_5_gets_penalty(self):
        """5 consecutive candidates should get cluster penalty."""
        paras = [self._make_para(f"Author {i}", 0.65) for i in range(5)]
        result = _apply_cluster_penalty(paras)
        for p in result:
            assert p.fake_heading_signals.score < 0.65

    def test_cluster_of_4_no_penalty(self):
        """4 consecutive candidates should NOT get cluster penalty."""
        paras = [self._make_para(f"Author {i}", 0.65) for i in range(4)]
        result = _apply_cluster_penalty(paras)
        for p in result:
            assert p.fake_heading_signals.score == 0.65

    def test_sparse_candidates_no_penalty(self):
        """Candidates separated by non-candidates should not get penalty."""
        paras = []
        for i in range(10):
            paras.append(self._make_para(f"Heading {i}", 0.65))
            paras.append(self._make_non_candidate(f"Body text {i}"))
        result = _apply_cluster_penalty(paras)
        for p in result:
            if p.fake_heading_signals is not None:
                assert p.fake_heading_signals.score == 0.65

    def test_non_candidates_unaffected(self):
        """Non-candidate paragraphs should not be modified."""
        paras = [self._make_non_candidate("Body")] * 3
        paras.extend(self._make_para(f"A {i}", 0.65) for i in range(6))
        result = _apply_cluster_penalty(paras)
        # First 3 should be unchanged
        for p in result[:3]:
            assert p.fake_heading_signals is None


class TestPdfStats:
    """Test document stats."""

    def test_stats_populated(self):
        result = parse_pdf(SYLLABUS_PDF)
        doc = result.document
        assert doc.stats.paragraph_count > 0
        assert doc.stats.table_count > 0
