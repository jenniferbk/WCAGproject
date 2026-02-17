"""Tests for the docx parser."""

from pathlib import Path

import pytest

from src.tools.docx_parser import parse_docx


class TestParseSimpleDocx:
    def test_parse_succeeds(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        assert result.success
        assert result.document is not None

    def test_metadata_extracted(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        assert doc.metadata.title == "Test Document"
        assert doc.metadata.author == "Test Author"

    def test_source_format(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        assert result.document.source_format == "docx"

    def test_heading_levels(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        headings = [p for p in doc.paragraphs if p.heading_level is not None]
        assert len(headings) == 3
        assert headings[0].heading_level == 1
        assert headings[0].text == "Main Title"
        assert headings[1].heading_level == 2
        assert headings[2].heading_level == 3

    def test_paragraph_count(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        # 3 headings + 3 body paragraphs = 6
        assert doc.stats.paragraph_count == 6
        assert doc.stats.heading_count == 3

    def test_content_order(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        # All items should be paragraphs (no tables in simple doc)
        assert len(doc.content_order) == 6
        assert all(item.content_type == "paragraph" for item in doc.content_order)
        assert doc.content_order[0].id == "p_0"

    def test_paragraph_ids_sequential(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        for i, para in enumerate(doc.paragraphs):
            assert para.id == f"p_{i}"

    def test_run_formatting(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        doc = result.document
        # Body paragraph should have runs
        body_para = doc.paragraphs[1]  # "This is the first body paragraph..."
        assert len(body_para.runs) > 0
        assert body_para.runs[0].text != ""


class TestParseFakeHeadings:
    def test_fake_heading_detected(self, fake_headings_docx: Path):
        result = parse_docx(fake_headings_docx)
        doc = result.document

        # Find paragraphs with fake heading signals
        candidates = [
            p for p in doc.paragraphs
            if p.fake_heading_signals is not None
        ]
        assert len(candidates) >= 1

    def test_fake_heading_score(self, fake_headings_docx: Path):
        result = parse_docx(fake_headings_docx)
        doc = result.document

        # "Fake Section Title" — bold, 16pt, short, followed by normal text
        fake = next(
            (p for p in doc.paragraphs if "Fake Section Title" in p.text), None
        )
        assert fake is not None
        assert fake.fake_heading_signals is not None
        assert fake.fake_heading_signals.all_runs_bold is True
        assert fake.fake_heading_signals.is_short is True
        assert fake.fake_heading_signals.score >= 0.5

    def test_long_bold_not_fake_heading(self, fake_headings_docx: Path):
        result = parse_docx(fake_headings_docx)
        doc = result.document

        # Long bold paragraph should not score highly
        long_para = next(
            (p for p in doc.paragraphs if "way too many words" in p.text), None
        )
        assert long_para is not None
        if long_para.fake_heading_signals is not None:
            assert long_para.fake_heading_signals.is_short is False

    def test_real_heading_has_no_fake_signals(self, fake_headings_docx: Path):
        result = parse_docx(fake_headings_docx)
        doc = result.document

        real_heading = next(
            (p for p in doc.paragraphs if p.heading_level is not None), None
        )
        assert real_heading is not None
        assert real_heading.fake_heading_signals is None


class TestParseTable:
    def test_table_parsed(self, table_docx: Path):
        result = parse_docx(table_docx)
        doc = result.document
        assert doc.stats.table_count == 1

    def test_table_content(self, table_docx: Path):
        result = parse_docx(table_docx)
        doc = result.document
        table = doc.tables[0]
        assert table.row_count == 3
        assert table.col_count == 3
        assert table.rows[0][0].text == "Name"
        assert table.rows[1][0].text == "Alice"

    def test_header_row_detected(self, table_docx: Path):
        result = parse_docx(table_docx)
        doc = result.document
        table = doc.tables[0]
        assert table.header_row_count >= 1

    def test_content_order_includes_table(self, table_docx: Path):
        result = parse_docx(table_docx)
        doc = result.document
        table_items = [
            item for item in doc.content_order if item.content_type == "table"
        ]
        assert len(table_items) == 1
        assert table_items[0].id == "tbl_0"


class TestParseErrors:
    def test_nonexistent_file(self, tmp_path: Path):
        result = parse_docx(tmp_path / "does_not_exist.docx")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_wrong_extension(self, tmp_path: Path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake pdf")
        result = parse_docx(pdf_file)
        assert not result.success
        assert "not a .docx" in result.error.lower()

    def test_corrupt_file(self, tmp_path: Path):
        bad_file = tmp_path / "corrupt.docx"
        bad_file.write_bytes(b"not a zip file")
        result = parse_docx(bad_file)
        assert not result.success
        assert result.error != ""
