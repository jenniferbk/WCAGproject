"""Tests for MathML → SVG rendering via ziamath."""

import pytest
from src.tools.math_renderer import render_mathml_to_svg, render_latex_to_svg


class TestRenderMathmlToSvg:
    def test_simple_fraction(self):
        mathml = "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
        svg = render_mathml_to_svg(mathml)
        assert svg.startswith("<svg")
        assert "</svg>" in svg

    def test_inline_variable(self):
        mathml = "<math><mi>x</mi></math>"
        svg = render_mathml_to_svg(mathml)
        assert "<svg" in svg

    def test_complex_integral(self):
        mathml = (
            '<math><mrow><msubsup><mo>∫</mo><mn>0</mn>'
            '<mi mathvariant="normal">∞</mi></msubsup>'
            '<mrow><mi>f</mi><mo>⁢</mo><mrow><mo stretchy="false">(</mo>'
            '<mi>t</mi><mo stretchy="false">)</mo></mrow></mrow></mrow></math>'
        )
        svg = render_mathml_to_svg(mathml)
        assert "<svg" in svg
        assert len(svg) > 200

    def test_invalid_mathml_returns_fallback(self):
        svg = render_mathml_to_svg("<math><notreal></notreal></math>")
        assert svg is not None

    def test_empty_mathml_returns_empty(self):
        svg = render_mathml_to_svg("")
        assert svg == ""


class TestRenderLatexToSvg:
    def test_simple_expression(self):
        svg = render_latex_to_svg(r"\frac{a}{b}")
        assert "<svg" in svg

    def test_greek_letters(self):
        svg = render_latex_to_svg(r"\alpha + \beta")
        assert "<svg" in svg
