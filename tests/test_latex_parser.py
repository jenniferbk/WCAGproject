"""Tests for LaTeX parsing and MathInfo model."""

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from src.models.document import (
    ContentType,
    DocumentModel,
    ImageInfo,
    MathInfo,
    MetadataInfo,
    ParagraphInfo,
    TableInfo,
    ContentOrderItem,
)
from src.tools.latex_parser import _parse_latexml_html, parse_latex, _safe_extract_zip
from src.tools.html_builder import build_html


class TestMathInfo:
    def test_basic_creation(self):
        m = MathInfo(
            id="math_0",
            latex_source=r"\frac{1}{2}",
            mathml="<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>",
        )
        assert m.id == "math_0"
        assert m.display == "block"
        assert m.description == ""
        assert m.equation_number is None
        assert m.confidence == 1.0
        assert m.unparsed is False

    def test_inline_display(self):
        m = MathInfo(
            id="math_1",
            latex_source="x",
            mathml="<math><mi>x</mi></math>",
            display="inline",
        )
        assert m.display == "inline"

    def test_with_equation_number(self):
        m = MathInfo(
            id="math_2",
            latex_source=r"\int_0^\infty",
            mathml="<math>...</math>",
            equation_number="(1)",
        )
        assert m.equation_number == "(1)"

    def test_unparsed_flag(self):
        m = MathInfo(
            id="math_3",
            latex_source=r"\badcommand",
            mathml="<math>...</math>",
            unparsed=True,
        )
        assert m.unparsed is True


class TestContentTypeMath:
    def test_math_enum_exists(self):
        assert ContentType.MATH == "math"


class TestFindMainTex:
    def test_single_tex_file(self, tmp_path):
        from src.tools.latex_parser import _find_main_tex
        tex = tmp_path / "homework.tex"
        tex.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")
        assert _find_main_tex(tmp_path) == tex

    def test_multiple_tex_prefers_root(self, tmp_path):
        from src.tools.latex_parser import _find_main_tex
        main = tmp_path / "main.tex"
        main.write_text(r"\documentclass{article}\begin{document}\input{ch1}\end{document}")
        sub = tmp_path / "chapters" / "ch1.tex"
        sub.parent.mkdir()
        sub.write_text(r"\section{Chapter 1}")
        assert _find_main_tex(tmp_path) == main

    def test_skips_commented_documentclass(self, tmp_path):
        from src.tools.latex_parser import _find_main_tex
        tex = tmp_path / "notes.tex"
        tex.write_text("% \\documentclass{old}\n\\documentclass{article}\n\\begin{document}Hi\\end{document}")
        assert _find_main_tex(tmp_path) == tex

    def test_no_documentclass_returns_none(self, tmp_path):
        from src.tools.latex_parser import _find_main_tex
        tex = tmp_path / "fragment.tex"
        tex.write_text(r"\section{Just a fragment}")
        assert _find_main_tex(tmp_path) is None


class TestRunLatexml:
    @pytest.fixture(autouse=True)
    def skip_if_no_latexml(self):
        if not shutil.which("latexml"):
            pytest.skip("LaTeXML not installed")

    def test_converts_simple_tex(self, tmp_path):
        from src.tools.latex_parser import _run_latexml
        tex = tmp_path / "test.tex"
        tex.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello $x^2$ world.\n\\end{document}\n"
        )
        result = _run_latexml(tex, tmp_path)
        assert result.success
        assert result.html
        assert "<math" in result.html
        assert "x" in result.html

    def test_captures_errors(self, tmp_path):
        from src.tools.latex_parser import _run_latexml
        tex = tmp_path / "bad.tex"
        tex.write_text(
            "\\documentclass{article}\n\\usepackage{nonexistentpackage}\n\\begin{document}Hi\\end{document}\n"
        )
        result = _run_latexml(tex, tmp_path)
        assert result.success
        assert len(result.warnings) > 0


class TestAssessConversionQuality:
    def test_counts_errors(self):
        from src.tools.latex_parser import _assess_conversion_quality
        html = '''
        <p>Good</p>
        <span class="ltx_ERROR undefined">\\badcmd</span>
        <span class="ltx_ERROR undefined">\\anotherbad</span>
        <math class="ltx_math_unparsed">broken</math>
        '''
        errors, unparsed = _assess_conversion_quality(html)
        assert errors == 2
        assert unparsed == 1

    def test_clean_html(self):
        from src.tools.latex_parser import _assess_conversion_quality
        html = '<p class="ltx_p">Hello</p><math class="ltx_Math">x</math>'
        errors, unparsed = _assess_conversion_quality(html)
        assert errors == 0
        assert unparsed == 0


class TestParseLatexmlHtml:
    def test_extracts_title(self):
        html = '<html lang="en"><head><title>My Homework</title></head><body><article class="ltx_document"></article></body></html>'
        doc = _parse_latexml_html(html, project_dir=None)
        assert doc.metadata.title == "My Homework"
        assert doc.metadata.language == "en"

    def test_extracts_headings(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <section class="ltx_section">
        <h2 class="ltx_title ltx_title_section">Introduction</h2>
        <div class="ltx_para"><p class="ltx_p">Body text.</p></div>
        </section>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        headings = [p for p in doc.paragraphs if p.heading_level is not None]
        assert len(headings) == 1
        assert headings[0].text == "Introduction"
        assert headings[0].heading_level == 2

    def test_extracts_inline_math_as_placeholder(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <div class="ltx_para"><p class="ltx_p">Let
        <math id="m1" class="ltx_Math" alttext="x" display="inline"><mi>x</mi></math>
        be real.</p></div>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        assert len(doc.math) == 1
        assert doc.math[0].latex_source == "x"
        assert doc.math[0].display == "inline"
        para = [p for p in doc.paragraphs if not p.heading_level][0]
        assert "[math_0]" in para.text
        assert "math_0" in para.math_ids

    def test_extracts_block_equation(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <div class="ltx_para"><p class="ltx_p">Consider:</p></div>
        <table id="S0.Ex1" class="ltx_equation ltx_eqn_table">
        <tbody><tr class="ltx_equation ltx_eqn_row ltx_align_baseline">
        <td class="ltx_eqn_cell ltx_align_center">
        <math id="S0.Ex1.m1" class="ltx_Math" alttext="x^2 + y^2 = 1" display="block">
        <mrow><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><msup><mi>y</mi><mn>2</mn></msup><mo>=</mo><mn>1</mn></mrow>
        </math></td>
        </tr></tbody></table>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        block_math = [m for m in doc.math if m.display == "block"]
        assert len(block_math) == 1
        assert block_math[0].latex_source == "x^2 + y^2 = 1"
        math_items = [i for i in doc.content_order if i.content_type == ContentType.MATH]
        assert len(math_items) == 1

    def test_extracts_equation_number(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <table id="S0.E1" class="ltx_equation ltx_eqn_table">
        <tbody><tr class="ltx_equation ltx_eqn_row ltx_align_baseline">
        <td class="ltx_eqn_cell ltx_align_center">
        <math id="S0.E1.m1" class="ltx_Math" alttext="E=mc^2" display="block">
        <mrow><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow>
        </math></td>
        <td class="ltx_eqn_cell ltx_eqn_eqno">
        <span class="ltx_tag ltx_tag_equation ltx_align_right">(1)</span></td>
        </tr></tbody></table>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        assert doc.math[0].equation_number == "(1)"

    def test_extracts_table(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <table class="ltx_tabular">
        <thead><tr><th class="ltx_th">Name</th><th class="ltx_th">Value</th></tr></thead>
        <tbody><tr><td class="ltx_td">x</td><td class="ltx_td">1</td></tr></tbody>
        </table>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        assert len(doc.tables) == 1
        assert doc.tables[0].header_row_count >= 1

    def test_counts_errors(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <span class="ltx_ERROR undefined">\\badcmd</span>
        <div class="ltx_para"><p class="ltx_p">Text</p></div>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        assert len(doc.parse_warnings) > 0

    def test_unparsed_math_flagged(self):
        html = '''<html lang="en"><head><title>T</title></head><body>
        <article class="ltx_document">
        <div class="ltx_para"><p class="ltx_p">
        <math class="ltx_math_unparsed" alttext="\\weird" display="inline">
        <mi>?</mi></math>
        </p></div>
        </article></body></html>'''
        doc = _parse_latexml_html(html, project_dir=None)
        assert doc.math[0].unparsed is True

    def test_source_format_is_tex(self):
        html = '<html lang="en"><head><title>T</title></head><body><article class="ltx_document"></article></body></html>'
        doc = _parse_latexml_html(html, project_dir=None)
        assert doc.source_format == "tex"


class TestSafeExtractZip:
    def test_extracts_valid_zip(self, tmp_path):
        zip_path = tmp_path / "project.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("main.tex", r"\documentclass{article}\begin{document}Hi\end{document}")
            zf.writestr("fig.png", b"fake png data")
        dest = tmp_path / "extracted"
        result = _safe_extract_zip(zip_path, dest)
        assert result.success
        assert (dest / "main.tex").exists()

    def test_rejects_path_traversal(self, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escape.txt", "gotcha")
        dest = tmp_path / "extracted"
        result = _safe_extract_zip(zip_path, dest)
        assert not result.success

    def test_rejects_oversized(self, tmp_path):
        zip_path = tmp_path / "big.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("big.txt", "x" * 1000)
        dest = tmp_path / "extracted"
        result = _safe_extract_zip(zip_path, dest, max_bytes=100)
        assert not result.success

    def test_rejects_too_many_files(self, tmp_path):
        zip_path = tmp_path / "many.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(10):
                zf.writestr(f"file_{i}.txt", "data")
        dest = tmp_path / "extracted"
        result = _safe_extract_zip(zip_path, dest, max_files=5)
        assert not result.success


class TestParseLatex:
    @pytest.fixture(autouse=True)
    def skip_if_no_latexml(self):
        if not shutil.which("latexml"):
            pytest.skip("LaTeXML not installed")

    def test_parse_single_tex(self, tmp_path):
        tex = tmp_path / "test.tex"
        tex.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\section{Hello}\n"
            "Let $x$ be real.\n"
            "\\end{document}\n"
        )
        result = parse_latex(str(tex))
        assert result.success
        assert result.document is not None
        assert result.document.source_format == "tex"
        assert len(result.document.math) >= 1

    def test_parse_zip(self, tmp_path):
        zip_path = tmp_path / "project.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "main.tex",
                "\\documentclass{article}\n\\begin{document}\n$y=mx+b$\n\\end{document}\n",
            )
        result = parse_latex(str(zip_path))
        assert result.success
        assert result.document is not None

    def test_parse_nonsense_file(self, tmp_path):
        bad = tmp_path / "bad.tex"
        bad.write_text("this is not latex at all")
        result = parse_latex(str(bad))
        assert isinstance(result.success, bool)


class TestHtmlBuilderMath:
    def _make_doc_with_math(self):
        from src.models.document import MetadataInfo, ContentOrderItem
        return DocumentModel(
            source_format="tex",
            metadata=MetadataInfo(title="Test", language="en"),
            paragraphs=[
                ParagraphInfo(
                    id="p_0",
                    text="Consider [math_0] where [math_1] is real.",
                    style_name="Normal",
                    math_ids=["math_0", "math_1"],
                ),
            ],
            math=[
                MathInfo(
                    id="math_0",
                    latex_source=r"\frac{1}{2}",
                    mathml='<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>',
                    display="inline",
                    description="one half",
                ),
                MathInfo(
                    id="math_1",
                    latex_source="x",
                    mathml="<math><mi>x</mi></math>",
                    display="inline",
                    description="x",
                ),
            ],
            content_order=[
                ContentOrderItem(content_type=ContentType.PARAGRAPH, id="p_0"),
            ],
        )

    def test_inline_math_renders_svg_with_aria(self):
        doc = self._make_doc_with_math()
        result = build_html(doc)
        assert result.success
        assert "aria-label" in result.html
        assert "one half" in result.html
        assert "<svg" in result.html

    def test_inline_math_has_hidden_mathml(self):
        doc = self._make_doc_with_math()
        result = build_html(doc)
        assert "sr-only" in result.html
        assert "<math" in result.html

    def test_block_math_renders(self):
        from src.models.document import MetadataInfo, ContentOrderItem
        doc = DocumentModel(
            source_format="tex",
            metadata=MetadataInfo(title="Test", language="en"),
            math=[
                MathInfo(
                    id="math_0",
                    latex_source="E=mc^2",
                    mathml='<math><mrow><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math>',
                    display="block",
                    description="E equals m c squared",
                    equation_number="(1)",
                ),
            ],
            content_order=[
                ContentOrderItem(content_type=ContentType.MATH, id="math_0"),
            ],
        )
        result = build_html(doc)
        assert result.success
        assert "E equals m c squared" in result.html
        assert "(1)" in result.html


class TestEndToEndLatex:
    """Integration test using real test documents."""

    @pytest.fixture(autouse=True)
    def skip_if_no_latexml(self):
        if not shutil.which("latexml"):
            pytest.skip("LaTeXML not installed")

    def test_diffeq_laplace_full_pipeline(self):
        """Parse diffeq_laplace.tex through the full pipeline."""
        tex_path = Path(__file__).parent / "test_docs" / "diffeq_laplace.tex"
        if not tex_path.exists():
            pytest.skip("Test document not available")

        result = parse_latex(str(tex_path))
        assert result.success
        doc = result.document
        assert doc is not None

        # Should have math
        assert doc.stats.math_count > 0
        assert len(doc.math) >= 20  # spike showed 74 math elements

        # Should have headings
        headings = [p for p in doc.paragraphs if p.heading_level]
        assert len(headings) >= 2

        # Should have tables (Laplace transform table)
        assert len(doc.tables) >= 1

        # Block equations should have display="block"
        block = [m for m in doc.math if m.display == "block"]
        assert len(block) >= 2

        # Should have equation numbers
        numbered = [m for m in doc.math if m.equation_number]
        assert len(numbered) >= 1

        # Content order should have MATH entries
        math_items = [i for i in doc.content_order if i.content_type == ContentType.MATH]
        assert len(math_items) >= 2

    def test_syllabus_no_math(self):
        """Parse syllabus.tex — basic LaTeX with no math."""
        tex_path = Path(__file__).parent / "test_docs" / "syllabus.tex"
        if not tex_path.exists():
            pytest.skip("Test document not available")

        result = parse_latex(str(tex_path))
        assert result.success
        doc = result.document
        assert doc is not None
        assert doc.stats.math_count == 0
        assert len(doc.paragraphs) > 0

    def test_html_output_from_latex(self):
        """Full pipeline: .tex → DocumentModel → HTML."""
        tex_path = Path(__file__).parent / "test_docs" / "diffeq_laplace.tex"
        if not tex_path.exists():
            pytest.skip("Test document not available")

        result = parse_latex(str(tex_path))
        assert result.success

        html_result = build_html(result.document)
        assert html_result.success
        assert "<svg" in html_result.html  # Math rendered as SVG
        assert "sr-only" in html_result.html  # Hidden MathML present
        assert "<h" in html_result.html  # Headings present
