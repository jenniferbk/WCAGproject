"""Parse .tex files into a DocumentModel via LaTeXML.

Two-step process:
1. LaTeXML converts LaTeX → XML
2. latexmlpost converts XML → HTML with Presentation MathML
3. BeautifulSoup parses the HTML into DocumentModel (Task 4)

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
    candidates: list[tuple[int, Path]] = []

    for tex_file in project_dir.rglob("*.tex"):
        try:
            content = tex_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

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

    candidates.sort(key=lambda x: (x[0], x[1].name))
    return candidates[0][1]


def _run_latexml(tex_path: Path, project_dir: Path) -> LatexmlResult:
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
