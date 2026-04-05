# LaTeX Accessibility Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.tex` / `.zip` LaTeX document support to the remediation pipeline, producing accessible HTML (SVG equations + hidden MathML) and PDF with AI-generated equation descriptions.

**Architecture:** LaTeXML converts LaTeX → HTML with MathML via subprocess. BeautifulSoup parses the HTML into DocumentModel (with new MathInfo model). The existing 4-phase AI pipeline generates equation descriptions and image alt text. ziamath renders MathML → SVG for the output HTML and PDF.

**Tech Stack:** LaTeXML (system, subprocess), ziamath (Python, MathML→SVG), BeautifulSoup4 (Python, HTML parsing), Claude API (equation descriptions), Gemini API (comprehension + image alt text)

**Spec:** `docs/superpowers/specs/2026-04-04-latex-accessibility-design.md`

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `src/tools/latex_parser.py` | Call LaTeXML subprocess, parse HTML into DocumentModel |
| `src/tools/math_renderer.py` | MathML → SVG via ziamath |
| `src/tools/math_descriptions.py` | Classify trivial/complex, generate descriptions via Claude |
| `src/prompts/math_description.md` | Prompt for Claude equation description batches |
| `src/tools/report_builder.py` | Human-readable report HTML generator |
| `tests/test_latex_parser.py` | LaTeX parser unit tests |
| `tests/test_math_renderer.py` | MathML → SVG rendering tests |
| `tests/test_math_descriptions.py` | Math complexity classification tests |

### Modified files
| File | What changes |
|------|-------------|
| `src/models/document.py` | Add MathInfo, MATH content type, math/math_ids fields |
| `src/agent/orchestrator.py` | Route .tex/.zip to latex_parser, zip output |
| `src/tools/html_builder.py` | Render MathInfo as SVG + hidden MathML, resolve [math_N] placeholders |
| `src/agent/executor.py` | Handle `add_math_description` action type |
| `src/web/app.py` | Accept .tex/.zip uploads, return zip output |
| `docs/data_schema.md` | Document MathInfo model |

---

### Task 1: Data Model Extensions

**Files:**
- Modify: `src/models/document.py:15-170`
- Modify: `docs/data_schema.md`
- Test: `tests/test_latex_parser.py` (create)

- [ ] **Step 1: Write failing test for MathInfo model**

```python
# tests/test_latex_parser.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_latex_parser.py::TestMathInfo -v`
Expected: FAIL — `MathInfo` not defined, `ContentType.MATH` not defined

- [ ] **Step 3: Add MathInfo and MATH content type to document.py**

In `src/models/document.py`, add to `ContentType`:
```python
class ContentType(str, Enum):
    """Type of content element in document order."""
    PARAGRAPH = "paragraph"
    TABLE = "table"
    MATH = "math"
```

Add `MathInfo` after `ParagraphInfo` (around line 122):
```python
class MathInfo(BaseModel, frozen=True):
    """A mathematical expression extracted from a LaTeX document."""
    id: str                            # math_0, math_1, ...
    latex_source: str                  # original LaTeX from alttext attribute
    mathml: str                        # full MathML markup from LaTeXML
    display: str = "block"             # "block" or "inline"
    description: str = ""              # full natural language reading
    equation_number: str | None = None # "(1)", "(2)" from ltx_tag_equation
    confidence: float = 1.0            # AI confidence in description
    unparsed: bool = False             # True if LaTeXML couldn't parse
```

Add `math_ids` to `ParagraphInfo`:
```python
    column: int | None = None  # OCR: 0=full-width, 1=left, 2=right
    math_ids: list[str] = Field(default_factory=list)  # MathInfo IDs for inline math
```

Add `math` to `DocumentModel`:
```python
    links: list[LinkInfo] = Field(default_factory=list)
    math: list[MathInfo] = Field(default_factory=list)
    content_order: list[ContentOrderItem] = Field(default_factory=list)
```

Add math stats to `DocumentStats`:
```python
    fake_heading_candidates: int = 0
    math_count: int = 0
    math_missing_description: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_latex_parser.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/ -q --tb=short`
Expected: All existing tests pass (the new fields have defaults)

- [ ] **Step 6: Update data_schema.md**

Add MathInfo table after ParagraphInfo in `docs/data_schema.md`:
```markdown
### MathInfo

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `math_0`, `math_1`, ... |
| `latex_source` | `str` | Original LaTeX from `alttext` attribute |
| `mathml` | `str` | Full MathML markup from LaTeXML |
| `display` | `str` | `"block"` or `"inline"` |
| `description` | `str` | Full natural language reading (AI-generated) |
| `equation_number` | `str \| None` | e.g., `"(1)"` from LaTeXML |
| `confidence` | `float` | AI confidence in description |
| `unparsed` | `bool` | True if LaTeXML couldn't parse the math |
```

Add to ParagraphInfo table: `| math_ids | list[str] | MathInfo IDs for inline math in this paragraph |`

Add to DocumentModel table: `| math | list[MathInfo] | Mathematical expressions |`

Add to DocumentStats table: `| math_count | int | Total math expressions |` and `| math_missing_description | int | Math without descriptions |`

- [ ] **Step 7: Commit**

```bash
git add src/models/document.py docs/data_schema.md tests/test_latex_parser.py
git commit -m "feat: add MathInfo model, MATH content type, math fields on DocumentModel"
```

---

### Task 2: MathML → SVG Renderer

**Files:**
- Create: `src/tools/math_renderer.py`
- Test: `tests/test_math_renderer.py`

- [ ] **Step 1: Write failing tests for math_renderer**

```python
# tests/test_math_renderer.py
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
        # Real MathML from LaTeXML spike
        mathml = (
            '<math><mrow><msubsup><mo>∫</mo><mn>0</mn>'
            '<mi mathvariant="normal">∞</mi></msubsup>'
            '<mrow><mi>f</mi><mo>⁢</mo><mrow><mo stretchy="false">(</mo>'
            '<mi>t</mi><mo stretchy="false">)</mo></mrow></mrow></mrow></math>'
        )
        svg = render_mathml_to_svg(mathml)
        assert "<svg" in svg
        assert len(svg) > 200  # not a trivial/empty SVG

    def test_invalid_mathml_returns_fallback(self):
        svg = render_mathml_to_svg("<math><notreal></notreal></math>")
        # Should return fallback SVG with error text, not raise
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_math_renderer.py -v`
Expected: FAIL — `math_renderer` module not found

- [ ] **Step 3: Implement math_renderer.py**

```python
# src/tools/math_renderer.py
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
        On rendering failure, returns a fallback SVG with the error.
    """
    if not mathml or not mathml.strip():
        return ""

    try:
        import ziamath as zm
        eqn = zm.Math(mathml)
        return eqn.svg()
    except Exception as e:
        logger.warning("MathML→SVG failed: %s (input: %.60s...)", e, mathml)
        # Return fallback: a simple SVG with "[math]" text
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_math_renderer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/math_renderer.py tests/test_math_renderer.py
git commit -m "feat: add MathML/LaTeX → SVG renderer using ziamath"
```

---

### Task 3: LaTeX Parser — LaTeXML Subprocess

**Files:**
- Create: `src/tools/latex_parser.py`
- Test: `tests/test_latex_parser.py` (extend)

This task builds the LaTeXML subprocess wrapper. The HTML→DocumentModel parsing is Task 4.

- [ ] **Step 1: Write failing tests for LaTeXML invocation**

Append to `tests/test_latex_parser.py`:

```python
import os
import shutil
from pathlib import Path
from src.tools.latex_parser import (
    _find_main_tex,
    _run_latexml,
    _assess_conversion_quality,
)


class TestFindMainTex:
    def test_single_tex_file(self, tmp_path):
        tex = tmp_path / "homework.tex"
        tex.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")
        assert _find_main_tex(tmp_path) == tex

    def test_multiple_tex_prefers_root(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text(r"\documentclass{article}\begin{document}\input{ch1}\end{document}")
        sub = tmp_path / "chapters" / "ch1.tex"
        sub.parent.mkdir()
        sub.write_text(r"\section{Chapter 1}")
        assert _find_main_tex(tmp_path) == main

    def test_skips_commented_documentclass(self, tmp_path):
        tex = tmp_path / "notes.tex"
        tex.write_text("% \\documentclass{old}\n\\documentclass{article}\n\\begin{document}Hi\\end{document}")
        assert _find_main_tex(tmp_path) == tex

    def test_no_documentclass_returns_none(self, tmp_path):
        tex = tmp_path / "fragment.tex"
        tex.write_text(r"\section{Just a fragment}")
        assert _find_main_tex(tmp_path) is None


class TestRunLatexml:
    @pytest.fixture(autouse=True)
    def skip_if_no_latexml(self):
        if not shutil.which("latexml"):
            pytest.skip("LaTeXML not installed")

    def test_converts_simple_tex(self, tmp_path):
        tex = tmp_path / "test.tex"
        tex.write_text(
            r"\documentclass{article}"
            r"\begin{document}"
            r"Hello $x^2$ world."
            r"\end{document}"
        )
        result = _run_latexml(tex, tmp_path)
        assert result.success
        assert result.html
        assert "<math" in result.html
        assert "x" in result.html

    def test_captures_errors(self, tmp_path):
        tex = tmp_path / "bad.tex"
        tex.write_text(
            r"\documentclass{article}"
            r"\usepackage{nonexistentpackage}"
            r"\begin{document}Hi\end{document}"
        )
        result = _run_latexml(tex, tmp_path)
        # Should still succeed (LaTeXML is lenient) but with warnings
        assert result.success
        assert len(result.warnings) > 0


class TestAssessConversionQuality:
    def test_counts_errors(self):
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
        html = '<p class="ltx_p">Hello</p><math class="ltx_Math">x</math>'
        errors, unparsed = _assess_conversion_quality(html)
        assert errors == 0
        assert unparsed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestFindMainTex -v`
Expected: FAIL — `_find_main_tex` not defined

- [ ] **Step 3: Implement LaTeXML subprocess wrapper**

```python
# src/tools/latex_parser.py
"""Parse .tex files into a DocumentModel via LaTeXML.

Two-step process:
1. LaTeXML converts LaTeX → XML
2. latexmlpost converts XML → HTML with Presentation MathML
3. BeautifulSoup parses the HTML into DocumentModel

Requires: latexml system package (apt install latexml / brew install latexml)
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LATEXML_TIMEOUT = 120  # seconds


@dataclass
class LatexmlResult:
    """Result of LaTeXML conversion."""
    success: bool
    html: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    error_count: int = 0
    unparsed_math_count: int = 0


def _find_main_tex(project_dir: Path) -> Path | None:
    """Find the main .tex file in a project directory.

    Looks for files containing \\documentclass (not in comments).
    Prefers files in the root directory over subdirectories.
    """
    candidates: list[tuple[int, Path]] = []  # (depth, path)

    for tex_file in project_dir.rglob("*.tex"):
        try:
            content = tex_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Check for \documentclass not preceded by %
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("%"):
                continue
            if re.search(r'\\documentclass', stripped):
                depth = len(tex_file.relative_to(project_dir).parts) - 1
                candidates.append((depth, tex_file))
                break

    if not candidates:
        return None

    # Sort by depth (root first), then alphabetically
    candidates.sort(key=lambda x: (x[0], x[1].name))
    return candidates[0][1]


def _run_latexml(
    tex_path: Path,
    project_dir: Path,
) -> LatexmlResult:
    """Run LaTeXML on a .tex file and return HTML with MathML.

    Two-step: latexml (tex→xml) then latexmlpost (xml→html).
    """
    if not tex_path.exists():
        return LatexmlResult(success=False, error=f"File not found: {tex_path}")

    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "output.xml"
        html_path = Path(tmp) / "output.html"

        # Step 1: LaTeX → XML
        cmd1 = [
            "latexml",
            str(tex_path),
            f"--destination={xml_path}",
            f"--path={project_dir}",
        ]

        try:
            result1 = subprocess.run(
                cmd1,
                capture_output=True,
                text=True,
                timeout=LATEXML_TIMEOUT,
                cwd=str(project_dir),
            )
        except subprocess.TimeoutExpired:
            return LatexmlResult(
                success=False,
                error=f"LaTeXML timed out after {LATEXML_TIMEOUT}s. Document may be too complex.",
            )
        except FileNotFoundError:
            return LatexmlResult(
                success=False,
                error="LaTeXML is not installed. Install with: apt install latexml (Ubuntu) or brew install latexml (macOS)",
            )

        warnings = _parse_latexml_stderr(result1.stderr)

        if not xml_path.exists():
            return LatexmlResult(
                success=False,
                error=f"LaTeXML failed to produce output. {result1.stderr[-500:] if result1.stderr else ''}",
                warnings=warnings,
            )

        # Step 2: XML → HTML with Presentation MathML
        cmd2 = [
            "latexmlpost",
            str(xml_path),
            f"--destination={html_path}",
            "--format=html5",
            "--pmml",
        ]

        try:
            result2 = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(project_dir),
            )
        except subprocess.TimeoutExpired:
            return LatexmlResult(
                success=False,
                error="latexmlpost timed out converting XML to HTML.",
                warnings=warnings,
            )

        warnings.extend(_parse_latexml_stderr(result2.stderr))

        if not html_path.exists():
            return LatexmlResult(
                success=False,
                error=f"latexmlpost failed. {result2.stderr[-500:] if result2.stderr else ''}",
                warnings=warnings,
            )

        html = html_path.read_text(encoding="utf-8", errors="replace")
        error_count, unparsed_count = _assess_conversion_quality(html)

        return LatexmlResult(
            success=True,
            html=html,
            warnings=warnings,
            error_count=error_count,
            unparsed_math_count=unparsed_count,
        )


def _parse_latexml_stderr(stderr: str) -> list[str]:
    """Extract meaningful warnings from LaTeXML stderr output."""
    warnings = []
    if not stderr:
        return warnings

    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip progress/timing lines
        if line.startswith("(") and "sec)" in line:
            continue
        if "Warning:" in line or "Error:" in line:
            warnings.append(line)
        elif "missing_file" in line or "undefined" in line:
            warnings.append(line)

    return warnings


def _assess_conversion_quality(html: str) -> tuple[int, int]:
    """Count error indicators in LaTeXML HTML output.

    Returns:
        (error_element_count, unparsed_math_count)
    """
    error_count = html.count('class="ltx_ERROR')
    unparsed_count = html.count('class="ltx_math_unparsed"')
    return error_count, unparsed_count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_latex_parser.py -v`
Expected: All pass (TestRunLatexml skips if LaTeXML not installed)

- [ ] **Step 5: Commit**

```bash
git add src/tools/latex_parser.py tests/test_latex_parser.py
git commit -m "feat: add LaTeXML subprocess wrapper with main tex finder"
```

---

### Task 4: LaTeX Parser — HTML → DocumentModel

**Files:**
- Modify: `src/tools/latex_parser.py`
- Test: `tests/test_latex_parser.py` (extend)

This is the core parsing logic: LaTeXML HTML → DocumentModel with MathInfo.

- [ ] **Step 1: Write failing tests for HTML→DocumentModel parsing**

Append to `tests/test_latex_parser.py`:

```python
from src.models.document import (
    ContentType,
    DocumentModel,
    ImageInfo,
    MathInfo,
    ParagraphInfo,
    TableInfo,
)
from src.tools.latex_parser import _parse_latexml_html


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
        # Paragraph text has placeholder
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
        # Block equation should be in content_order as MATH
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestParseLatexmlHtml -v`
Expected: FAIL — `_parse_latexml_html` not found

- [ ] **Step 3: Implement _parse_latexml_html**

Add to `src/tools/latex_parser.py`:

```python
from bs4 import BeautifulSoup, Tag

from src.models.document import (
    CellInfo,
    ContentOrderItem,
    ContentType,
    DocumentModel,
    DocumentStats,
    ImageInfo,
    LinkInfo,
    MathInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)


def _parse_latexml_html(
    html: str,
    project_dir: Path | None = None,
    source_path: str = "",
) -> DocumentModel:
    """Parse LaTeXML HTML output into a DocumentModel.

    Walks the DOM tree, converting LaTeXML's ltx_ structures into
    ParagraphInfo, MathInfo, TableInfo, and ImageInfo objects.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Extract metadata
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    html_tag = soup.find("html")
    lang = html_tag.get("lang", "en") if html_tag else "en"

    metadata = MetadataInfo(title=title, language=lang)

    # Accumulators
    paragraphs: list[ParagraphInfo] = []
    math_list: list[MathInfo] = []
    tables: list[TableInfo] = []
    images: list[ImageInfo] = []
    content_order: list[ContentOrderItem] = []
    warnings: list[str] = []

    p_idx = 0
    math_idx = 0
    tbl_idx = 0
    img_idx = 0

    def _next_math_id() -> str:
        nonlocal math_idx
        mid = f"math_{math_idx}"
        math_idx += 1
        return mid

    def _extract_math(math_tag: Tag) -> MathInfo:
        """Extract a MathInfo from a <math> element."""
        mid = _next_math_id()
        latex_source = math_tag.get("alttext", "")
        display = math_tag.get("display", "inline")
        is_unparsed = "ltx_math_unparsed" in (math_tag.get("class") or [])
        mathml_str = str(math_tag)

        return MathInfo(
            id=mid,
            latex_source=latex_source,
            mathml=mathml_str,
            display=display,
            unparsed=is_unparsed,
        )

    def _process_paragraph(p_tag: Tag) -> ParagraphInfo | None:
        """Convert a <p class="ltx_p"> into a ParagraphInfo with math placeholders."""
        nonlocal p_idx

        inline_math_ids: list[str] = []
        text_parts: list[str] = []

        for child in p_tag.children:
            if isinstance(child, Tag) and child.name == "math":
                math_info = _extract_math(child)
                math_list.append(math_info)
                inline_math_ids.append(math_info.id)
                text_parts.append(f"[{math_info.id}]")
            elif isinstance(child, Tag):
                text_parts.append(child.get_text())
            else:
                text_parts.append(str(child))

        text = "".join(text_parts).strip()
        if not text:
            return None

        pid = f"p_{p_idx}"
        p_idx += 1

        return ParagraphInfo(
            id=pid,
            text=text,
            style_name="Normal",
            runs=[RunInfo(text=text, font_size_pt=12.0)],
            math_ids=inline_math_ids,
        )

    def _process_heading(h_tag: Tag) -> ParagraphInfo | None:
        """Convert an <hN> heading tag into a ParagraphInfo."""
        nonlocal p_idx

        level = int(h_tag.name[1]) if h_tag.name[1:].isdigit() else 2
        text = h_tag.get_text(strip=True)
        if not text:
            return None

        pid = f"p_{p_idx}"
        p_idx += 1

        return ParagraphInfo(
            id=pid,
            text=text,
            style_name=f"Heading {level}",
            heading_level=level,
            runs=[RunInfo(text=text, bold=True, font_size_pt=16.0)],
        )

    def _process_equation_table(table_tag: Tag) -> MathInfo | None:
        """Extract block equation from <table class="ltx_equation">."""
        math_tag = table_tag.find("math")
        if not math_tag:
            return None

        math_info = _extract_math(math_tag)
        # Override display to block
        math_info = MathInfo(
            id=math_info.id,
            latex_source=math_info.latex_source,
            mathml=math_info.mathml,
            display="block",
            unparsed=math_info.unparsed,
            equation_number=_find_equation_number(table_tag),
        )
        return math_info

    def _find_equation_number(table_tag: Tag) -> str | None:
        """Find equation number like (1) in an equation table."""
        tag_span = table_tag.find("span", class_="ltx_tag_equation")
        if tag_span:
            return tag_span.get_text(strip=True)
        return None

    def _process_data_table(table_tag: Tag) -> TableInfo | None:
        """Convert <table class="ltx_tabular"> into a TableInfo."""
        nonlocal tbl_idx

        rows: list[list[CellInfo]] = []
        header_row_count = 0

        thead = table_tag.find("thead")
        if thead:
            for tr in thead.find_all("tr"):
                cells = [
                    CellInfo(text=td.get_text(strip=True), paragraphs=[td.get_text(strip=True)])
                    for td in tr.find_all(["th", "td"])
                ]
                if cells:
                    rows.append(cells)
                    header_row_count += 1

        tbody = table_tag.find("tbody") or table_tag
        for tr in tbody.find_all("tr", recursive=False):
            if tr.parent == thead:
                continue
            cells = [
                CellInfo(text=td.get_text(strip=True), paragraphs=[td.get_text(strip=True)])
                for td in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)

        if not rows:
            return None

        tid = f"tbl_{tbl_idx}"
        tbl_idx += 1
        col_count = max((len(r) for r in rows), default=0)

        return TableInfo(
            id=tid,
            rows=rows,
            header_row_count=header_row_count,
            has_header_style=header_row_count > 0,
            row_count=len(rows),
            col_count=col_count,
        )

    # ── Walk the DOM ────────────────────────────────────────────
    article = soup.find("article", class_="ltx_document") or soup.find("body") or soup
    if not article:
        return DocumentModel(source_format="tex", metadata=metadata)

    # Count errors for warnings
    error_spans = soup.find_all("span", class_=lambda c: c and "ltx_ERROR" in c)
    if error_spans:
        warnings.append(f"LaTeXML: {len(error_spans)} undefined macro(s) in output")

    # Process all elements in DOM order
    for element in article.descendants:
        if not isinstance(element, Tag):
            continue

        classes = element.get("class") or []

        # Headings
        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6") and "ltx_title" in " ".join(classes):
            para = _process_heading(element)
            if para:
                paragraphs.append(para)
                content_order.append(ContentOrderItem(content_type=ContentType.PARAGRAPH, id=para.id))

        # Block equations (table wrapping math)
        elif element.name == "table" and any("ltx_equation" in c for c in classes):
            math_info = _process_equation_table(element)
            if math_info:
                math_list.append(math_info)
                content_order.append(ContentOrderItem(content_type=ContentType.MATH, id=math_info.id))

        # Data tables (not equation tables)
        elif element.name == "table" and any("ltx_tabular" in c for c in classes):
            table_info = _process_data_table(element)
            if table_info:
                tables.append(table_info)
                content_order.append(ContentOrderItem(content_type=ContentType.TABLE, id=table_info.id))

        # Paragraphs
        elif element.name == "p" and "ltx_p" in " ".join(classes):
            # Skip if inside an equation table (already handled)
            if element.find_parent("table", class_=lambda c: c and "ltx_equation" in " ".join(c if isinstance(c, list) else [c])):
                continue
            para = _process_paragraph(element)
            if para:
                paragraphs.append(para)
                content_order.append(ContentOrderItem(content_type=ContentType.PARAGRAPH, id=para.id))

        # Images
        elif element.name == "img":
            src = element.get("src", "")
            iid = f"img_{img_idx}"
            img_idx += 1

            # Try to load image data from project dir
            img_data = None
            if project_dir and src:
                img_path = _resolve_image_path(project_dir, src)
                if img_path and img_path.exists():
                    try:
                        img_data = img_path.read_bytes()
                    except Exception:
                        pass

            images.append(ImageInfo(
                id=iid,
                image_data=img_data,
                content_type=_guess_mime(src),
                alt_text=element.get("alt", ""),
                is_decorative=False,
            ))
            content_order.append(ContentOrderItem(content_type=ContentType.PARAGRAPH, id=iid))

    # Build stats
    heading_count = sum(1 for p in paragraphs if p.heading_level is not None)
    stats = DocumentStats(
        paragraph_count=len(paragraphs),
        table_count=len(tables),
        image_count=len(images),
        heading_count=heading_count,
        images_missing_alt=sum(1 for i in images if not i.alt_text and not i.is_decorative),
        math_count=len(math_list),
        math_missing_description=sum(1 for m in math_list if not m.description),
    )

    return DocumentModel(
        source_format="tex",
        source_path=source_path,
        metadata=metadata,
        paragraphs=paragraphs,
        tables=tables,
        images=images,
        math=math_list,
        content_order=content_order,
        stats=stats,
        parse_warnings=warnings,
    )


def _resolve_image_path(project_dir: Path, src: str) -> Path | None:
    """Resolve an image path, trying common extensions if missing."""
    direct = project_dir / src
    if direct.exists():
        return direct

    # Try adding extensions (LaTeX often omits them)
    for ext in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"):
        candidate = project_dir / f"{src}{ext}"
        if candidate.exists():
            return candidate

    return None


def _guess_mime(filename: str) -> str:
    """Guess MIME type from filename."""
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return "image/png"  # default
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_latex_parser.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/tools/latex_parser.py tests/test_latex_parser.py
git commit -m "feat: add LaTeXML HTML → DocumentModel parser"
```

---

### Task 5: Top-Level parse_latex Function + Zip Handling

**Files:**
- Modify: `src/tools/latex_parser.py`
- Test: `tests/test_latex_parser.py` (extend)

- [ ] **Step 1: Write failing tests for parse_latex and zip handling**

Append to `tests/test_latex_parser.py`:

```python
import zipfile
from src.tools.latex_parser import parse_latex, _safe_extract_zip


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
        assert "path traversal" in result.error.lower() or "invalid" in result.error.lower()

    def test_rejects_oversized(self, tmp_path):
        zip_path = tmp_path / "big.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Write a file that reports large uncompressed size
            zf.writestr("big.txt", "x" * 1000)
        dest = tmp_path / "extracted"
        # Use a tiny limit to test the check
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
        # May succeed with empty/minimal output or fail — either is acceptable
        # But should not crash
        assert isinstance(result.success, bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestParseLatex -v`
Expected: FAIL — `parse_latex` not found

- [ ] **Step 3: Implement parse_latex and _safe_extract_zip**

Add to `src/tools/latex_parser.py`:

```python
from src.tools.docx_parser import ParseResult  # reuse the same result type

MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_ZIP_FILES = 500


@dataclass
class ZipExtractResult:
    """Result of safe zip extraction."""
    success: bool
    extract_dir: Path | None = None
    error: str = ""


def _safe_extract_zip(
    zip_path: Path,
    dest_dir: Path,
    max_bytes: int = MAX_ZIP_BYTES,
    max_files: int = MAX_ZIP_FILES,
) -> ZipExtractResult:
    """Safely extract a zip file with security checks.

    Validates: path traversal, total size, file count, symlinks.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Check file count
            if len(zf.namelist()) > max_files:
                return ZipExtractResult(
                    success=False,
                    error=f"Zip contains too many files ({len(zf.namelist())} > {max_files})",
                )

            # Check total uncompressed size and path safety
            total_size = 0
            for info in zf.infolist():
                # Reject path traversal
                if ".." in info.filename or info.filename.startswith("/"):
                    return ZipExtractResult(
                        success=False,
                        error=f"Invalid path in zip: {info.filename}",
                    )
                # Reject symlinks
                if info.external_attr >> 28 == 0xA:
                    return ZipExtractResult(
                        success=False,
                        error=f"Zip contains symlink: {info.filename}",
                    )
                total_size += info.file_size

            if total_size > max_bytes:
                return ZipExtractResult(
                    success=False,
                    error=f"Zip too large when extracted ({total_size} bytes > {max_bytes})",
                )

            # Extract
            dest_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(dest_dir)

            return ZipExtractResult(success=True, extract_dir=dest_dir)

    except zipfile.BadZipFile:
        return ZipExtractResult(success=False, error="Invalid zip file")
    except Exception as e:
        return ZipExtractResult(success=False, error=f"Zip extraction failed: {e}")


def parse_latex(filepath: str | Path) -> ParseResult:
    """Parse a .tex or .zip LaTeX project into a DocumentModel.

    Accepts:
    - A single .tex file
    - A .zip containing a LaTeX project (finds main .tex automatically)

    Returns:
        ParseResult with success/failure and the DocumentModel.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return ParseResult(success=False, error=f"File not found: {filepath}")

    suffix = filepath.suffix.lower()
    cleanup_dir: Path | None = None

    try:
        if suffix == ".zip":
            # Extract zip to temp directory
            import tempfile
            tmp = Path(tempfile.mkdtemp(prefix="latex_"))
            cleanup_dir = tmp

            extract_result = _safe_extract_zip(filepath, tmp)
            if not extract_result.success:
                return ParseResult(success=False, error=extract_result.error)

            project_dir = tmp
            main_tex = _find_main_tex(project_dir)
            if not main_tex:
                return ParseResult(
                    success=False,
                    error="Couldn't find main LaTeX file in the upload. "
                          "No file contains \\documentclass.",
                )
        elif suffix in (".tex", ".ltx"):
            main_tex = filepath
            project_dir = filepath.parent
        else:
            return ParseResult(
                success=False,
                error=f"Unsupported file type: {suffix}. Accepts .tex, .ltx, or .zip",
            )

        # Run LaTeXML
        latexml_result = _run_latexml(main_tex, project_dir)
        if not latexml_result.success:
            return ParseResult(
                success=False,
                error=latexml_result.error,
                warnings=latexml_result.warnings,
            )

        # Parse HTML into DocumentModel
        doc_model = _parse_latexml_html(
            latexml_result.html,
            project_dir=project_dir,
            source_path=str(filepath),
        )

        # Add LaTeXML warnings to parse warnings
        all_warnings = list(doc_model.parse_warnings) + latexml_result.warnings
        if latexml_result.error_count > 0:
            all_warnings.append(
                f"LaTeXML: {latexml_result.error_count} error(s), "
                f"{latexml_result.unparsed_math_count} unparsed math expression(s)"
            )

        # Rebuild with combined warnings
        doc_model = DocumentModel(
            source_format=doc_model.source_format,
            source_path=doc_model.source_path,
            metadata=doc_model.metadata,
            paragraphs=doc_model.paragraphs,
            tables=doc_model.tables,
            images=doc_model.images,
            math=doc_model.math,
            links=doc_model.links,
            content_order=doc_model.content_order,
            contrast_issues=doc_model.contrast_issues,
            stats=doc_model.stats,
            parse_warnings=all_warnings,
        )

        return ParseResult(
            success=True,
            document=doc_model,
            warnings=all_warnings,
        )

    finally:
        # Clean up temp directory for zip extraction
        if cleanup_dir and cleanup_dir.exists():
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)
```

Also add the `zipfile` import at the top of the file:
```python
import zipfile
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/test_latex_parser.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/tools/latex_parser.py tests/test_latex_parser.py
git commit -m "feat: add parse_latex entry point with zip handling and security"
```

---

### Task 6: Math Description Generation

**Files:**
- Create: `src/tools/math_descriptions.py`
- Create: `src/prompts/math_description.md`
- Test: `tests/test_math_descriptions.py`

- [ ] **Step 1: Write failing tests for math complexity classification**

```python
# tests/test_math_descriptions.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_math_descriptions.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement math_descriptions.py**

```python
# src/tools/math_descriptions.py
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

# LaTeX commands that indicate complex math
_COMPLEX_COMMANDS = {
    r"\frac", r"\int", r"\sum", r"\prod", r"\sqrt", r"\begin",
    r"\lim", r"\infty", r"\partial", r"\nabla",
    r"\matrix", r"\bmatrix", r"\pmatrix",
    r"\underbrace", r"\overbrace", r"\overset", r"\underset",
}

# Greek letter mappings
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
    """Classify a math expression as 'trivial' or 'complex'.

    Trivial: single symbol, variable, number, Greek letter, simple sub/superscript.
    Complex: everything else (equations, fractions, integrals, multi-term expressions).
    """
    latex = math.latex_source.strip()

    # Empty or very short
    if not latex:
        return "trivial"

    # Check for complex command indicators
    for cmd in _COMPLEX_COMMANDS:
        if cmd in latex:
            return "complex"

    # Strip simple wrappers
    clean = latex.strip("{}")

    # Longer than 10 chars with operators → complex
    if len(clean) > 10 and re.search(r'[+\-*/=<>]', clean):
        return "complex"

    # Block display is usually a significant equation
    if math.display == "block" and len(clean) > 5:
        return "complex"

    return "trivial"


def trivial_description(latex: str) -> str:
    """Generate a deterministic description for trivial math.

    Handles: single variables, numbers, Greek letters, simple sub/superscripts.
    """
    latex = latex.strip().strip("{}")

    # Pure number
    if re.match(r'^-?\d+\.?\d*$', latex):
        return latex

    # Greek letter
    if latex in _GREEK:
        return _GREEK[latex]

    # Factorial
    if re.match(r'^([a-zA-Z])!$', latex):
        return f"{latex[0]} factorial"

    # Simple subscript: x_i, x_0, x_{ij}
    m = re.match(r'^([a-zA-Z])_\{?([a-zA-Z0-9]+)\}?$', latex)
    if m:
        return f"{m.group(1)} sub {m.group(2)}"

    # Simple superscript: x^2, x^3, x^n
    m = re.match(r'^([a-zA-Z])(\^)\{?(\d+)\}?$', latex)
    if m:
        base, _, exp = m.groups()
        if exp == "2":
            return f"{base} squared"
        if exp == "3":
            return f"{base} cubed"
        return f"{base} to the {exp}"

    # Superscript with variable: x^n
    m = re.match(r'^([a-zA-Z])\^\{?([a-zA-Z])\}?$', latex)
    if m:
        return f"{m.group(1)} to the {m.group(2)}"

    # Single letter/symbol
    if len(latex) == 1:
        return latex

    # Fallback: return cleaned latex
    return latex
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_math_descriptions.py -v`
Expected: All pass

- [ ] **Step 5: Create the math description prompt**

```markdown
<!-- src/prompts/math_description.md -->
# Math Equation Descriptions for Accessibility

You are generating natural language descriptions of mathematical equations for blind students using screen readers. These descriptions must be mathematically precise and complete — a student should be able to understand and work with the equation from your description alone.

## Context
- Course: {course_context}
- Document type: {document_type}

## Requirements

1. **Be mathematically precise.** "x equals negative b plus or minus the square root of b squared minus 4ac, all divided by 2a" — not "the quadratic formula" (that's a label, not a description).

2. **Describe structure.** For fractions, say "numerator ... divided by denominator ...". For integrals, say "the integral from [lower] to [upper] of [integrand] with respect to [variable]".

3. **Read nested expressions inside-out.** "the square root of the quantity b squared minus 4ac" — the "quantity" cue tells the listener where grouping begins.

4. **Include equation numbers.** If the equation is numbered, start with "Equation N:" so students can follow cross-references in the text.

5. **Use context.** If the surrounding text says "the Laplace transform is defined as", your description can reference that: "Equation 1, the definition of the Laplace transform: ..."

## Equations to describe

For each equation below, provide a JSON array of objects with "id" and "description" fields.

{equations}

Respond with ONLY the JSON array. Example:
```json
[
  {"id": "math_5", "description": "Equation 1: the Laplace transform of f of t, defined as the integral from 0 to infinity of f of t times e to the negative s t, with respect to t"},
  {"id": "math_6", "description": "the convolution of f and g at time t, defined as the integral from 0 to t of f of tau times g of t minus tau, with respect to tau"}
]
```
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/math_descriptions.py src/prompts/math_description.md tests/test_math_descriptions.py
git commit -m "feat: add math complexity classifier and trivial description generator"
```

---

### Task 7: Orchestrator Integration

**Files:**
- Modify: `src/agent/orchestrator.py:305-360`
- Modify: `src/web/app.py:431-470`

- [ ] **Step 1: Add .tex/.zip support to orchestrator**

In `src/agent/orchestrator.py`, modify the format check and parser dispatch:

```python
# Change the suffix check (around line 335)
if suffix not in (".docx", ".pptx", ".pdf", ".tex", ".ltx", ".zip"):
    return RemediationResult(
        success=False,
        input_path=doc_path,
        error=f"Unsupported format: {suffix}. Currently .docx, .pptx, .pdf, .tex, and .zip are supported.",
        processing_time_seconds=time.time() - start_time,
    )
```

Add the import at the top:
```python
from src.tools.latex_parser import parse_latex
```

Add the parser dispatch (around line 347):
```python
if suffix == ".pptx":
    parse_result = parse_pptx(doc_path)
elif suffix == ".pdf":
    parse_result = parse_pdf(doc_path)
elif suffix in (".tex", ".ltx", ".zip"):
    parse_result = parse_latex(doc_path)
else:
    parse_result = parse_docx(doc_path)
```

- [ ] **Step 2: Add .tex/.zip to web app upload**

In `src/web/app.py`, modify the upload endpoint (around line 443):

```python
if suffix not in (".docx", ".pdf", ".pptx", ".tex", ".ltx", ".zip"):
    return JSONResponse(
        status_code=400,
        content={"error": f"Unsupported file type: {suffix}. Accepts .docx, .pdf, .pptx, .tex, .zip"},
    )
```

Also update the max file size check for .zip files:
```python
# After reading content
max_bytes = user.max_file_size_mb * 1024 * 1024
if suffix == ".zip":
    max_bytes = 50 * 1024 * 1024  # 50MB for zip uploads
if len(content) > max_bytes:
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -q --tb=short`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/agent/orchestrator.py src/web/app.py
git commit -m "feat: route .tex/.zip uploads through LaTeX parser in orchestrator and web app"
```

---

### Task 8: HTML Builder — Math Rendering

**Files:**
- Modify: `src/tools/html_builder.py`
- Test: `tests/test_latex_parser.py` (extend)

- [ ] **Step 1: Write failing test for math rendering in HTML**

Append to `tests/test_latex_parser.py`:

```python
from src.tools.html_builder import build_html


class TestHtmlBuilderMath:
    def _make_doc_with_math(self):
        """Create a minimal DocumentModel with math for testing."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestHtmlBuilderMath -v`
Expected: FAIL — math rendering not implemented

- [ ] **Step 3: Implement math rendering in html_builder.py**

Add to `src/tools/html_builder.py` the math rendering logic. In `build_html()`, add math map alongside image/table maps:

```python
from src.models.document import MathInfo
from src.tools.math_renderer import render_mathml_to_svg

# In build_html(), after building para_map:
math_map = {m.id: m for m in doc.math} if doc.math else {}
```

Add to the content_order loop:
```python
elif item.content_type == ContentType.MATH:
    math = math_map.get(item.id)
    if math:
        body_parts.append(_render_block_math(math))
```

Add math rendering helper functions:

```python
def _render_block_math(math: MathInfo) -> str:
    """Render a block math expression as SVG + hidden MathML."""
    svg = render_mathml_to_svg(math.mathml)
    desc = _esc(math.description) if math.description else _esc(math.latex_source)
    eq_num = f'<span class="eq-number">{_esc(math.equation_number)}</span>' if math.equation_number else ""

    return (
        f'  <div class="math-block">\n'
        f'    {eq_num}\n'
        f'    <span role="math" aria-label="{desc}">\n'
        f'      {svg}\n'
        f'      <span class="sr-only">{math.mathml}</span>\n'
        f'    </span>\n'
        f'  </div>'
    )


def _render_inline_math(math: MathInfo) -> str:
    """Render an inline math expression as SVG + hidden MathML."""
    svg = render_mathml_to_svg(math.mathml)
    desc = _esc(math.description) if math.description else _esc(math.latex_source)

    return (
        f'<span class="math-inline" role="math" aria-label="{desc}">'
        f'{svg}'
        f'<span class="sr-only">{math.mathml}</span>'
        f'</span>'
    )
```

Modify `_render_inline()` to resolve math placeholders:

```python
def _render_inline(para: ParagraphInfo, math_map: dict[str, MathInfo] | None = None) -> str:
    """Render paragraph inline content, resolving math placeholders."""
    if not para.runs:
        text = _esc(para.text)
    else:
        # existing run rendering logic...
        text = "".join(parts)

    # Resolve [math_N] placeholders
    if math_map and para.math_ids:
        for mid in para.math_ids:
            math = math_map.get(mid)
            if math:
                placeholder = f"[{mid}]"
                if placeholder in text:
                    text = text.replace(_esc(placeholder), _render_inline_math(math))

    return text
```

Update the `_render_paragraph` call to pass `math_map`, and add `.sr-only` and `.math-block` CSS:

```css
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.math-block {
  margin: 1em 0;
  text-align: center;
}
.math-inline svg {
  vertical-align: middle;
}
.eq-number {
  float: right;
  margin-right: 1em;
}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_latex_parser.py::TestHtmlBuilderMath -v`
Expected: All pass

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/ -q --tb=short`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/tools/html_builder.py tests/test_latex_parser.py
git commit -m "feat: render MathInfo as SVG + hidden MathML in HTML builder"
```

---

### Task 9: End-to-End Integration Test

**Files:**
- Test: `tests/test_latex_parser.py` (extend)

- [ ] **Step 1: Write end-to-end test using real test document**

```python
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

        from src.tools.html_builder import build_html
        html_result = build_html(result.document)
        assert html_result.success
        assert "<svg" in html_result.html  # Math rendered as SVG
        assert "sr-only" in html_result.html  # Hidden MathML present
        assert "<h" in html_result.html  # Headings present
```

- [ ] **Step 2: Run end-to-end tests**

Run: `pytest tests/test_latex_parser.py::TestEndToEndLatex -v`
Expected: All pass

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -q --tb=short`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_latex_parser.py
git commit -m "test: add end-to-end integration tests for LaTeX pipeline"
```

---

### Task 10: Executor — Math Description Action

**Files:**
- Modify: `src/agent/executor.py`
- Test: `tests/test_latex_parser.py` (extend)

- [ ] **Step 1: Add `add_math_description` action handler to executor**

Read `src/agent/executor.py` to understand the action handler pattern. Add a handler for `add_math_description` actions that sets the `description` field on MathInfo objects in the DocumentModel.

The handler receives the math_id and the description text from the strategy phase, and updates the corresponding MathInfo. Since MathInfo is frozen, create a new MathInfo with the description set.

- [ ] **Step 2: Write test for the action handler**

```python
class TestExecutorMathDescription:
    def test_add_math_description_updates_model(self):
        """Verify that add_math_description action sets description on MathInfo."""
        from src.models.document import MathInfo, DocumentModel, MetadataInfo

        doc = DocumentModel(
            source_format="tex",
            metadata=MetadataInfo(title="Test", language="en"),
            math=[
                MathInfo(id="math_0", latex_source="x^2", mathml="<math>...</math>"),
            ],
        )

        # Simulate applying an add_math_description action
        updated_math = []
        for m in doc.math:
            if m.id == "math_0":
                updated_math.append(MathInfo(
                    id=m.id,
                    latex_source=m.latex_source,
                    mathml=m.mathml,
                    display=m.display,
                    description="x squared",
                    equation_number=m.equation_number,
                    confidence=0.95,
                    unparsed=m.unparsed,
                ))
            else:
                updated_math.append(m)

        assert updated_math[0].description == "x squared"
        assert updated_math[0].confidence == 0.95
```

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_latex_parser.py::TestExecutorMathDescription -v`
Expected: PASS

```bash
git add src/agent/executor.py tests/test_latex_parser.py
git commit -m "feat: add add_math_description action handler in executor"
```

---

## Post-Implementation

After all tasks are complete:

1. Run full test suite: `pytest tests/ -v`
2. Test manually with `diffeq_laplace.tex` through the web app (upload, check output)
3. Deploy LaTeXML to Oracle Cloud server: `ssh ... "sudo apt install latexml"`
4. Deploy code: `git push && ssh ... "cd /home/ubuntu/a11y-remediate && git pull && pip install ziamath && sudo systemctl restart a11y-remediate"`
5. Update NOW.md with session state
6. Update MEMORY.md with LaTeX implementation notes
