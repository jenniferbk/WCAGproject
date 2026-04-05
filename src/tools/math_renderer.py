"""Render MathML and LaTeX math to SVG using ziamath.

Pure Python, no system dependencies. Used for:
- LMS-safe HTML output (SVG equations work without JavaScript)
- PDF generation via WeasyPrint (which can't render MathML)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def render_mathml_to_svg(mathml: str) -> str:
    """Convert MathML markup to an SVG string.

    Args:
        mathml: MathML markup (e.g., from LaTeXML output).

    Returns:
        SVG string, or empty string if input is empty.
        On rendering failure, returns a fallback SVG with error indicator.
    """
    if not mathml or not mathml.strip():
        return ""

    try:
        import ziamath as zm
        eqn = zm.Math(mathml)
        return eqn.svg()
    except Exception as e:
        logger.warning("MathML→SVG failed: %s (input: %.60s...)", e, mathml)
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="20">'
            '<text x="5" y="15" font-size="12" fill="red">[math]</text>'
            '</svg>'
        )


def render_latex_to_svg(latex: str) -> str:
    """Convert LaTeX math string to an SVG string.

    Args:
        latex: LaTeX math (e.g., r"\\frac{a}{b}"). Do not include $ delimiters.

    Returns:
        SVG string, or empty string if input is empty.
    """
    if not latex or not latex.strip():
        return ""

    try:
        import ziamath as zm
        eqn = zm.Latex(latex)
        return eqn.svg()
    except Exception as e:
        logger.warning("LaTeX→SVG failed: %s (input: %.60s...)", e, latex)
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="20">'
            '<text x="5" y="15" font-size="12" fill="red">[math]</text>'
            '</svg>'
        )
