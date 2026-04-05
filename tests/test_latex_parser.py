"""Tests for LaTeX parsing and MathInfo model."""

import os
import shutil
from pathlib import Path

import pytest
from src.models.document import MathInfo, ContentType


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
