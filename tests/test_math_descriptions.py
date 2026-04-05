"""Tests for math description generation."""

import pytest
from src.models.document import MathInfo
from src.tools.math_descriptions import classify_math, trivial_description


class TestClassifyMath:
    def test_single_variable_is_trivial(self):
        m = MathInfo(id="m0", latex_source="x", mathml="<math><mi>x</mi></math>", display="inline")
        assert classify_math(m) == "trivial"

    def test_greek_letter_is_trivial(self):
        m = MathInfo(id="m0", latex_source=r"\alpha", mathml="<math><mi>α</mi></math>", display="inline")
        assert classify_math(m) == "trivial"

    def test_subscript_is_trivial(self):
        m = MathInfo(id="m0", latex_source="x_i", mathml="<math><msub><mi>x</mi><mi>i</mi></msub></math>", display="inline")
        assert classify_math(m) == "trivial"

    def test_fraction_is_complex(self):
        m = MathInfo(id="m0", latex_source=r"\frac{a}{b}", mathml="<math>...</math>", display="block")
        assert classify_math(m) == "complex"

    def test_integral_is_complex(self):
        m = MathInfo(id="m0", latex_source=r"\int_0^\infty f(x)dx", mathml="<math>...</math>", display="block")
        assert classify_math(m) == "complex"

    def test_short_equation_is_complex(self):
        m = MathInfo(id="m0", latex_source="x^2 + y^2 = 1", mathml="<math>...</math>", display="block")
        assert classify_math(m) == "complex"

    def test_number_is_trivial(self):
        m = MathInfo(id="m0", latex_source="42", mathml="<math><mn>42</mn></math>", display="inline")
        assert classify_math(m) == "trivial"

    def test_empty_is_trivial(self):
        m = MathInfo(id="m0", latex_source="", mathml="<math></math>", display="inline")
        assert classify_math(m) == "trivial"


class TestTrivialDescription:
    def test_single_variable(self):
        assert trivial_description("x") == "x"

    def test_greek_alpha(self):
        assert trivial_description(r"\alpha") == "alpha"

    def test_greek_beta(self):
        assert trivial_description(r"\beta") == "beta"

    def test_subscript(self):
        assert trivial_description("x_i") == "x sub i"

    def test_superscript_simple(self):
        assert trivial_description("x^2") == "x squared"

    def test_superscript_cubed(self):
        assert trivial_description("x^3") == "x cubed"

    def test_number(self):
        assert trivial_description("42") == "42"

    def test_n_factorial(self):
        assert trivial_description("n!") == "n factorial"
