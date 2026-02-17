"""Tests for html_builder: DocumentModel → accessible HTML."""

from __future__ import annotations

import pytest

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    DocumentModel,
    DocumentStats,
    ImageInfo,
    LinkInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.tools.html_builder import HtmlBuildResult, build_html


def _make_doc(
    paragraphs: list[ParagraphInfo] | None = None,
    tables: list[TableInfo] | None = None,
    images: list[ImageInfo] | None = None,
    content_order: list[ContentOrderItem] | None = None,
    title: str = "Test Document",
    language: str = "en",
) -> DocumentModel:
    """Helper to build a minimal DocumentModel for testing."""
    paras = paragraphs or []
    tbls = tables or []
    imgs = images or []
    order = content_order or [
        ContentOrderItem(content_type=ContentType.PARAGRAPH, id=p.id) for p in paras
    ] + [
        ContentOrderItem(content_type=ContentType.TABLE, id=t.id) for t in tbls
    ]
    return DocumentModel(
        source_format="docx",
        source_path="/test.docx",
        metadata=MetadataInfo(title=title, language=language),
        paragraphs=paras,
        tables=tbls,
        images=imgs,
        links=[],
        content_order=order,
        contrast_issues=[],
        stats=DocumentStats(
            paragraph_count=len(paras),
            table_count=len(tbls),
            image_count=len(imgs),
        ),
    )


class TestBuildHtmlBasic:
    def test_empty_document(self):
        doc = _make_doc()
        result = build_html(doc)
        assert result.success
        assert '<!DOCTYPE html>' in result.html
        assert 'lang="en"' in result.html
        assert '<title>Test Document</title>' in result.html

    def test_language_attribute(self):
        doc = _make_doc(language="fr")
        result = build_html(doc)
        assert 'lang="fr"' in result.html

    def test_title_in_head(self):
        doc = _make_doc(title="My Accessible Doc")
        result = build_html(doc)
        assert '<title>My Accessible Doc</title>' in result.html

    def test_default_title(self):
        doc = _make_doc(title="")
        result = build_html(doc)
        assert '<title>Untitled Document</title>' in result.html

    def test_custom_css(self):
        doc = _make_doc()
        result = build_html(doc, css=".custom { color: red; }")
        assert ".custom { color: red; }" in result.html


class TestParagraphRendering:
    def test_simple_paragraph(self):
        para = ParagraphInfo(id="p_0", text="Hello world")
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<p>Hello world</p>" in result.html

    def test_heading(self):
        para = ParagraphInfo(id="p_0", text="Main Title", heading_level=1)
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<h1>Main Title</h1>" in result.html

    def test_heading_levels(self):
        paras = [
            ParagraphInfo(id="p_0", text="H1", heading_level=1),
            ParagraphInfo(id="p_1", text="H2", heading_level=2),
            ParagraphInfo(id="p_2", text="H3", heading_level=3),
        ]
        doc = _make_doc(paragraphs=paras)
        result = build_html(doc)
        assert "<h1>H1</h1>" in result.html
        assert "<h2>H2</h2>" in result.html
        assert "<h3>H3</h3>" in result.html

    def test_heading_level_clamped_to_6(self):
        para = ParagraphInfo(id="p_0", text="Deep", heading_level=9)
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<h6>Deep</h6>" in result.html

    def test_empty_paragraph_skipped(self):
        paras = [
            ParagraphInfo(id="p_0", text="Before"),
            ParagraphInfo(id="p_1", text=""),
            ParagraphInfo(id="p_2", text="After"),
        ]
        doc = _make_doc(paragraphs=paras)
        result = build_html(doc)
        assert "<p>Before</p>" in result.html
        assert "<p>After</p>" in result.html
        # Empty paragraph should not produce <p></p>
        assert result.html.count("<p>") == 2

    def test_list_item(self):
        para = ParagraphInfo(id="p_0", text="List item text", is_list_item=True)
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<li>" in result.html

    def test_html_escaping(self):
        para = ParagraphInfo(id="p_0", text='Text with <tags> & "quotes"')
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "&lt;tags&gt;" in result.html
        assert "&amp;" in result.html


class TestInlineFormatting:
    def test_bold(self):
        para = ParagraphInfo(
            id="p_0", text="Bold text",
            runs=[RunInfo(text="Bold text", bold=True)],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<strong>Bold text</strong>" in result.html

    def test_italic(self):
        para = ParagraphInfo(
            id="p_0", text="Italic text",
            runs=[RunInfo(text="Italic text", italic=True)],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<em>Italic text</em>" in result.html

    def test_underline(self):
        para = ParagraphInfo(
            id="p_0", text="Underlined",
            runs=[RunInfo(text="Underlined", underline=True)],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "<u>Underlined</u>" in result.html

    def test_color_inline_style(self):
        para = ParagraphInfo(
            id="p_0", text="Red text",
            runs=[RunInfo(text="Red text", color="#FF0000")],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert 'color: #FF0000' in result.html

    def test_font_size_inline_style(self):
        para = ParagraphInfo(
            id="p_0", text="Big",
            runs=[RunInfo(text="Big", font_size_pt=24.0)],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "font-size: 24.0pt" in result.html

    def test_multiple_runs(self):
        para = ParagraphInfo(
            id="p_0", text="Normal Bold Italic",
            runs=[
                RunInfo(text="Normal "),
                RunInfo(text="Bold ", bold=True),
                RunInfo(text="Italic", italic=True),
            ],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert "Normal " in result.html
        assert "<strong>Bold </strong>" in result.html
        assert "<em>Italic</em>" in result.html

    def test_link_rendering(self):
        para = ParagraphInfo(
            id="p_0", text="Click here for info",
            runs=[RunInfo(text="Click here for info")],
            links=[LinkInfo(id="link_0", text="here", url="https://example.com", paragraph_id="p_0")],
        )
        doc = _make_doc(paragraphs=[para])
        result = build_html(doc)
        assert 'href="https://example.com"' in result.html
        assert ">here</a>" in result.html


class TestImageRendering:
    def test_image_with_alt_text(self):
        img = ImageInfo(
            id="img_0", alt_text="A chart showing growth",
            content_type="image/png", paragraph_id="p_0",
        )
        para = ParagraphInfo(id="p_0", text="", image_ids=["img_0"])
        doc = _make_doc(paragraphs=[para], images=[img])
        result = build_html(doc)
        assert 'alt="A chart showing growth"' in result.html

    def test_image_placeholder_src(self):
        img = ImageInfo(id="img_0", alt_text="Test", content_type="image/png", paragraph_id="p_0")
        para = ParagraphInfo(id="p_0", text="", image_ids=["img_0"])
        doc = _make_doc(paragraphs=[para], images=[img])
        result = build_html(doc)
        assert 'src="images/img_0.png"' in result.html

    def test_image_embedded_base64(self):
        img = ImageInfo(
            id="img_0", alt_text="Test",
            content_type="image/png", paragraph_id="p_0",
            image_data=b"\x89PNG\r\n\x1a\n",  # fake PNG header
        )
        para = ParagraphInfo(id="p_0", text="", image_ids=["img_0"])
        doc = _make_doc(paragraphs=[para], images=[img])
        result = build_html(doc, embed_images=True)
        assert "data:image/png;base64," in result.html

    def test_image_dimensions(self):
        img = ImageInfo(
            id="img_0", alt_text="Test",
            content_type="image/png", paragraph_id="p_0",
            width_px=200, height_px=100,
        )
        para = ParagraphInfo(id="p_0", text="", image_ids=["img_0"])
        doc = _make_doc(paragraphs=[para], images=[img])
        result = build_html(doc)
        assert 'width="200"' in result.html
        assert 'height="100"' in result.html

    def test_missing_alt_text_warning(self):
        img = ImageInfo(
            id="img_0", alt_text="", is_decorative=False,
            content_type="image/png", paragraph_id="p_0",
        )
        para = ParagraphInfo(id="p_0", text="", image_ids=["img_0"])
        doc = _make_doc(paragraphs=[para], images=[img])
        result = build_html(doc)
        assert any("no alt text" in w for w in result.warnings)


class TestTableRendering:
    def test_simple_table(self):
        table = TableInfo(
            id="tbl_0",
            rows=[
                [CellInfo(text="Name"), CellInfo(text="Age")],
                [CellInfo(text="Alice"), CellInfo(text="30")],
            ],
            header_row_count=1,
            row_count=2, col_count=2,
        )
        doc = _make_doc(tables=[table])
        result = build_html(doc)
        assert "<thead>" in result.html
        assert "<tbody>" in result.html
        assert '<th scope="col">' in result.html
        assert "<td>" in result.html

    def test_table_header_scope(self):
        table = TableInfo(
            id="tbl_0",
            rows=[
                [CellInfo(text="Col1"), CellInfo(text="Col2")],
                [CellInfo(text="A"), CellInfo(text="B")],
            ],
            header_row_count=1,
            row_count=2, col_count=2,
        )
        doc = _make_doc(tables=[table])
        result = build_html(doc)
        assert 'scope="col"' in result.html

    def test_table_colspan(self):
        table = TableInfo(
            id="tbl_0",
            rows=[
                [CellInfo(text="Merged", grid_span=2)],
                [CellInfo(text="A"), CellInfo(text="B")],
            ],
            header_row_count=0,
            row_count=2, col_count=2,
        )
        doc = _make_doc(tables=[table])
        result = build_html(doc)
        assert 'colspan="2"' in result.html

    def test_table_no_headers(self):
        table = TableInfo(
            id="tbl_0",
            rows=[
                [CellInfo(text="A"), CellInfo(text="B")],
                [CellInfo(text="C"), CellInfo(text="D")],
            ],
            header_row_count=0,
            row_count=2, col_count=2,
        )
        doc = _make_doc(tables=[table])
        result = build_html(doc)
        assert "<thead>" not in result.html
        assert "<th" not in result.html


class TestContentOrder:
    def test_paragraphs_and_tables_interleaved(self):
        para1 = ParagraphInfo(id="p_0", text="Before table")
        table = TableInfo(
            id="tbl_0",
            rows=[[CellInfo(text="Cell")]],
            header_row_count=0, row_count=1, col_count=1,
        )
        para2 = ParagraphInfo(id="p_1", text="After table")

        order = [
            ContentOrderItem(content_type=ContentType.PARAGRAPH, id="p_0"),
            ContentOrderItem(content_type=ContentType.TABLE, id="tbl_0"),
            ContentOrderItem(content_type=ContentType.PARAGRAPH, id="p_1"),
        ]
        doc = _make_doc(paragraphs=[para1, para2], tables=[table], content_order=order)
        result = build_html(doc)

        # Verify order: para1 before table before para2
        p1_pos = result.html.index("Before table")
        tbl_pos = result.html.index("<table>")
        p2_pos = result.html.index("After table")
        assert p1_pos < tbl_pos < p2_pos


class TestErrorHandling:
    def test_build_result_dataclass(self):
        result = HtmlBuildResult(success=True, html="<html></html>")
        assert result.success
        assert result.html == "<html></html>"
        assert result.warnings == []
        assert result.error == ""
