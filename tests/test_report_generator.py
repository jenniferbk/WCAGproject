"""Tests for report_generator._build_math_review_section."""

from __future__ import annotations

import pytest


class TestBuildMathReviewSection:
    def test_generates_section_with_equations(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="math_0", latex_source=r"x^2 + y^2 = r^2",
                     mathml="<math><mi>x</mi></math>", display="block",
                     description="x squared plus y squared equals r squared"),
            MathInfo(id="math_1", latex_source=r"\alpha",
                     mathml="<math><mi>α</mi></math>", display="inline",
                     description="alpha"),
            MathInfo(id="math_2", latex_source=r"\int_0^1 f(x) dx",
                     mathml="<math><mi>f</mi></math>", display="block",
                     description=""),
        ]

        html = _build_math_review_section(math_list)

        assert "Math Review" in html
        assert "3 equations" in html or "3 equation" in html
        assert "2 block" in html
        assert "1 inline" in html
        assert r"x^2 + y^2 = r^2" in html or "x^2" in html
        assert "x squared plus y squared" in html
        assert "Missing" in html
        assert "<details" in html

    def test_empty_math_list_returns_empty(self):
        from src.tools.report_generator import _build_math_review_section
        html = _build_math_review_section([])
        assert html == ""

    def test_status_badges_present(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source="x", mathml="<math><mi>x</mi></math>",
                     display="inline", description="x"),
            MathInfo(id="m1", latex_source=r"\frac{a}{b}",
                     mathml="<math><mi>a</mi></math>", display="block",
                     description="a over b"),
            MathInfo(id="m2", latex_source=r"\sum_{i=1}^n",
                     mathml="<math><mi>n</mi></math>", display="block",
                     description=""),
        ]

        html = _build_math_review_section(math_list)

        assert "status-auto" in html
        assert "status-ai" in html
        assert "status-missing" in html

    def test_block_equations_before_inline(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"\alpha", mathml="<math><mi>α</mi></math>",
                     display="inline", description="alpha"),
            MathInfo(id="m1", latex_source=r"\frac{a}{b}", mathml="<math><mi>a</mi></math>",
                     display="block", description="a over b"),
        ]

        html = _build_math_review_section(math_list)

        block_pos = html.find("Block Equations")
        inline_pos = html.find("Inline Equations")
        assert block_pos != -1
        assert inline_pos != -1
        assert block_pos < inline_pos

    def test_only_block_no_inline_table(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"\frac{a}{b}", mathml="<math><mi>a</mi></math>",
                     display="block", description="a over b"),
        ]

        html = _build_math_review_section(math_list)

        assert "Block Equations" in html
        assert "Inline Equations" not in html

    def test_only_inline_no_block_table(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"\alpha", mathml="<math><mi>α</mi></math>",
                     display="inline", description="alpha"),
        ]

        html = _build_math_review_section(math_list)

        assert "Block Equations" not in html
        assert "Inline Equations" in html

    def test_tikz_shows_ai_generated(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"\begin{tikzpicture}...\end{tikzpicture}",
                     mathml="", display="block",
                     description="A diagram with nodes",
                     tikz_source=r"\begin{tikzpicture}\node{A};\end{tikzpicture}"),
        ]

        html = _build_math_review_section(math_list)

        assert "status-ai" in html
        assert "[TikZ Diagram]" in html

    def test_no_description_shows_missing(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"\frac{d}{dx}", mathml="<math><mi>d</mi></math>",
                     display="block", description=""),
        ]

        html = _build_math_review_section(math_list)

        assert "status-missing" in html
        assert "Missing" in html
        assert "No description" in html

    def test_details_closed_by_default(self):
        """The <details> element should not have 'open' attribute."""
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source="x", mathml="<math><mi>x</mi></math>",
                     display="inline", description="x"),
        ]

        html = _build_math_review_section(math_list)

        # Should have <details class="math-review-section"> but NOT <details open
        assert '<details class="math-review-section">' in html
        assert "<details open" not in html

    def test_singular_equation_grammar(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source="x", mathml="<math><mi>x</mi></math>",
                     display="inline", description="x"),
        ]

        html = _build_math_review_section(math_list)

        assert "1 equation" in html

    def test_html_escaping_in_latex_source(self):
        """LaTeX source with < > & should be escaped in HTML output."""
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="m0", latex_source=r"a < b & c > d",
                     mathml="<math><mi>a</mi></math>",
                     display="inline", description="a less than b"),
        ]

        html = _build_math_review_section(math_list)

        assert "&lt;" in html
        assert "&amp;" in html
        assert "&gt;" in html
