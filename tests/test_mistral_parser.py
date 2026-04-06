"""Tests for Mistral OCR markdown-to-DocumentModel parser."""

import pytest

from src.tools.mistral_ocr import parse_page_markdown
from src.models.document import ContentType


class TestHeadingParsing:
    def test_h1_heading(self):
        paras, tables, links, order = parse_page_markdown("# Main Title", page_index=0)
        assert len(paras) == 1
        assert paras[0].heading_level == 1
        assert paras[0].text == "Main Title"
        assert paras[0].id == "ocr_p_0"

    def test_h2_heading(self):
        paras, _, _, _ = parse_page_markdown("## Section", page_index=0)
        assert paras[0].heading_level == 2

    def test_multiple_headings(self):
        paras, _, _, _ = parse_page_markdown("# Title\n\n## Section 1\n\n## Section 2", page_index=0)
        assert len(paras) == 3
        assert [p.heading_level for p in paras] == [1, 2, 2]
        assert paras[2].id == "ocr_p_2"

    def test_heading_with_bold(self):
        paras, _, _, _ = parse_page_markdown("## **Bold Heading**", page_index=0)
        assert paras[0].heading_level == 2
        assert paras[0].text == "Bold Heading"
        assert paras[0].runs[0].bold is True


class TestParagraphParsing:
    def test_plain_paragraph(self):
        paras, _, _, _ = parse_page_markdown("Hello world.", page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "Hello world."
        assert paras[0].heading_level is None
        assert len(paras[0].runs) == 1
        assert paras[0].runs[0].bold is None

    def test_bold_text(self):
        paras, _, _, _ = parse_page_markdown("Some **bold** text.", page_index=0)
        assert paras[0].text == "Some bold text."
        assert len(paras[0].runs) == 3
        assert paras[0].runs[1].text == "bold"
        assert paras[0].runs[1].bold is True

    def test_italic_text(self):
        paras, _, _, _ = parse_page_markdown("An *italic* word.", page_index=0)
        assert paras[0].runs[1].italic is True

    def test_nested_bold_italic(self):
        paras, _, _, _ = parse_page_markdown("Normal **bold *bold-italic* bold** end.", page_index=0)
        assert paras[0].text == "Normal bold bold-italic bold end."
        bi_run = [r for r in paras[0].runs if r.bold and r.italic]
        assert len(bi_run) == 1
        assert bi_run[0].text == "bold-italic"

    def test_multiple_paragraphs(self):
        paras, _, _, _ = parse_page_markdown("First.\n\nSecond.", page_index=0)
        assert len(paras) == 2

    def test_blockquote(self):
        paras, _, _, _ = parse_page_markdown("> This is a quote.", page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "This is a quote."


class TestLinkParsing:
    def test_inline_link(self):
        paras, _, links, _ = parse_page_markdown("Click [here](http://example.com) now.", page_index=0)
        assert paras[0].text == "Click here now."
        assert len(links) == 1
        assert links[0].text == "here"
        assert links[0].url == "http://example.com"
        assert links[0].id == "ocr_link_0"
        assert links[0].paragraph_id == "ocr_p_0"

    def test_multiple_links(self):
        _, _, links, _ = parse_page_markdown("[A](http://a.com) and [B](http://b.com)", page_index=0)
        assert len(links) == 2
        assert links[1].id == "ocr_link_1"


class TestTableParsing:
    def test_inline_markdown_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        _, tables, _, _ = parse_page_markdown(md, page_index=0)
        assert len(tables) == 1
        assert tables[0].id == "ocr_tbl_0"
        assert tables[0].header_row_count == 1
        assert tables[0].col_count == 2
        assert tables[0].rows[0][0].text == "A"  # header
        assert tables[0].rows[1][0].text == "1"  # data

    def test_table_reference_with_structured_data(self):
        md = "[tbl-0.md](tbl-0.md)"
        table_data = [{"content": "| X | Y |\n| --- | --- |\n| 1 | 2 |", "id": "tbl-0.md"}]
        _, tables, _, _ = parse_page_markdown(md, page_index=0, tables=table_data)
        assert len(tables) == 1
        assert tables[0].rows[0][0].text == "X"


class TestPageHeaderStripping:
    def test_strips_page_header(self):
        paras, _, _, _ = parse_page_markdown("152 MAYER\n\nActual content.", page_index=0)
        assert len(paras) == 1
        assert paras[0].text == "Actual content."

    def test_keeps_non_header_numbers(self):
        paras, _, _, _ = parse_page_markdown("152 items were found in the study.", page_index=0)
        assert len(paras) == 1
        assert "152 items" in paras[0].text


class TestContentOrder:
    def test_paragraph_table_paragraph(self):
        md = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter."
        _, _, _, order = parse_page_markdown(md, page_index=0)
        assert len(order) == 3
        assert order[0].content_type == ContentType.PARAGRAPH
        assert order[1].content_type == ContentType.TABLE
        assert order[2].content_type == ContentType.PARAGRAPH


class TestIDOffsets:
    def test_para_offset(self):
        paras, _, _, _ = parse_page_markdown("Text.", page_index=1, para_offset=5)
        assert paras[0].id == "ocr_p_5"

    def test_table_offset(self):
        _, tables, _, _ = parse_page_markdown("| A |\n|---|\n| 1 |", page_index=0, table_offset=3)
        assert tables[0].id == "ocr_tbl_3"

    def test_link_offset(self):
        _, _, links, _ = parse_page_markdown("A [link](http://x.com).", page_index=0, link_offset=7)
        assert links[0].id == "ocr_link_7"
