"""Tests for LaTeX parsing and MathInfo model."""

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
