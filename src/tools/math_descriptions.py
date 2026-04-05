"""Classify math complexity and generate descriptions.

Trivial math (single symbols, variables) gets deterministic descriptions.
Complex math (equations, integrals, fractions) gets Claude-generated
natural language descriptions with course context.
"""

from __future__ import annotations

import logging
import re

from src.models.document import MathInfo

logger = logging.getLogger(__name__)

_COMPLEX_COMMANDS = {
    r"\frac", r"\int", r"\sum", r"\prod", r"\sqrt", r"\begin",
    r"\lim", r"\infty", r"\partial", r"\nabla",
    r"\matrix", r"\bmatrix", r"\pmatrix",
    r"\underbrace", r"\overbrace", r"\overset", r"\underset",
}

_GREEK = {
    r"\alpha": "alpha", r"\beta": "beta", r"\gamma": "gamma",
    r"\delta": "delta", r"\epsilon": "epsilon", r"\varepsilon": "varepsilon",
    r"\zeta": "zeta", r"\eta": "eta", r"\theta": "theta",
    r"\iota": "iota", r"\kappa": "kappa", r"\lambda": "lambda",
    r"\mu": "mu", r"\nu": "nu", r"\xi": "xi",
    r"\pi": "pi", r"\rho": "rho", r"\sigma": "sigma",
    r"\tau": "tau", r"\upsilon": "upsilon", r"\phi": "phi",
    r"\chi": "chi", r"\psi": "psi", r"\omega": "omega",
    r"\Gamma": "Gamma", r"\Delta": "Delta", r"\Theta": "Theta",
    r"\Lambda": "Lambda", r"\Xi": "Xi", r"\Pi": "Pi",
    r"\Sigma": "Sigma", r"\Phi": "Phi", r"\Psi": "Psi",
    r"\Omega": "Omega",
}


def classify_math(math: MathInfo) -> str:
    """Classify a math expression as 'trivial' or 'complex'."""
    latex = math.latex_source.strip()

    if not latex:
        return "trivial"

    for cmd in _COMPLEX_COMMANDS:
        if cmd in latex:
            return "complex"

    clean = latex.strip("{}")

    if len(clean) > 10 and re.search(r'[+\-*/=<>]', clean):
        return "complex"

    if math.display == "block" and len(clean) > 5:
        return "complex"

    return "trivial"


def trivial_description(latex: str) -> str:
    """Generate a deterministic description for trivial math."""
    latex = latex.strip().strip("{}")

    if re.match(r'^-?\d+\.?\d*$', latex):
        return latex

    if latex in _GREEK:
        return _GREEK[latex]

    if re.match(r'^([a-zA-Z])!$', latex):
        return f"{latex[0]} factorial"

    m = re.match(r'^([a-zA-Z])_\{?([a-zA-Z0-9]+)\}?$', latex)
    if m:
        return f"{m.group(1)} sub {m.group(2)}"

    m = re.match(r'^([a-zA-Z])(\^)\{?(\d+)\}?$', latex)
    if m:
        base, _, exp = m.groups()
        if exp == "2":
            return f"{base} squared"
        if exp == "3":
            return f"{base} cubed"
        return f"{base} to the {exp}"

    m = re.match(r'^([a-zA-Z])\^\{?([a-zA-Z])\}?$', latex)
    if m:
        return f"{m.group(1)} to the {m.group(2)}"

    if len(latex) == 1:
        return latex

    return latex
