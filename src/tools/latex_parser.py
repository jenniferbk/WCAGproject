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
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from src.models.document import (
    CellInfo, ContentOrderItem, ContentType, DocumentModel, DocumentStats,
    ImageInfo, LinkInfo, MathInfo, MetadataInfo, ParagraphInfo, RunInfo, TableInfo,
)
from src.tools.docx_parser import ParseResult

logger = logging.getLogger(__name__)

LATEXML_TIMEOUT = 120  # seconds

# ──────────────────────────────────────────────────────────────────────────────
# Algorithmic / algorithm package command formatter
# ──────────────────────────────────────────────────────────────────────────────

# Mapping from LaTeX math symbols to Unicode equivalents used in pseudocode.
_LATEX_SYMBOLS: list[tuple[str, str]] = [
    (r"\gets", "←"),
    (r"\leftarrow", "←"),
    (r"\rightarrow", "→"),
    (r"\geq", "≥"),
    (r"\leq", "≤"),
    (r"\neq", "≠"),
    (r"\ne", "≠"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\ldots", "…"),
    (r"\infty", "∞"),
    (r"\in", "∈"),
    (r"\notin", "∉"),
    (r"\subseteq", "⊆"),
    (r"\cup", "∪"),
    (r"\cap", "∩"),
    (r"\forall", "∀"),
    (r"\exists", "∃"),
    (r"\neg", "¬"),
    (r"\land", "∧"),
    (r"\lor", "∨"),
    (r"\to", "→"),
    (r"\Rightarrow", "⇒"),
    (r"\Leftarrow", "⇐"),
    (r"\Leftrightarrow", "⇔"),
    (r"\alpha", "α"),
    (r"\beta", "β"),
    (r"\gamma", "γ"),
    (r"\delta", "δ"),
    (r"\epsilon", "ε"),
    (r"\theta", "θ"),
    (r"\lambda", "λ"),
    (r"\mu", "μ"),
    (r"\pi", "π"),
    (r"\sigma", "σ"),
    (r"\tau", "τ"),
    (r"\phi", "φ"),
    (r"\psi", "ψ"),
    (r"\omega", "ω"),
]

# Commands that begin an indented block.
_BLOCK_OPEN = frozenset(["\\Function", "\\Procedure", "\\If", "\\ElsIf", "\\Else",
                          "\\For", "\\While"])
# Commands that close (dedent) a block.
_BLOCK_CLOSE = frozenset(["\\EndFunction", "\\EndProcedure", "\\EndIf",
                           "\\EndFor", "\\EndWhile"])
# Commands with a single {arg} argument.
_BLOCK_CLOSE_NOARG = _BLOCK_CLOSE | {"\\Else"}

# Detection heuristic patterns.
# A text is algorithmic if it satisfies one of:
#  (A) contains a block-opener (Function/If/While/…) AND a structural token (State/EndXxx/…)
#  (B) contains \State or \Return alongside \Call  (body-only fragments)
#  (C) contains \Require or \Ensure (preamble-only fragments with body)
_DETECTION_BLOCK_STARTERS = re.compile(
    r"\\(Function|Procedure|If|While|For)\b"
)
_DETECTION_STRUCTURAL = re.compile(
    r"\\(State|EndIf|EndFunction|EndProcedure|EndFor|EndWhile)\b"
)
_DETECTION_CALL = re.compile(r"\\Call\b")
_DETECTION_STATE_OR_RETURN = re.compile(r"\\(State|Return)\b")
_DETECTION_REQUIRE_ENSURE = re.compile(r"\\(Require|Ensure)\b")


def _is_algorithmic(text: str) -> bool:
    """Return True if *text* appears to contain algorithmic package commands."""
    # (A) block structure
    if _DETECTION_BLOCK_STARTERS.search(text) and _DETECTION_STRUCTURAL.search(text):
        return True
    # (B) \State or \Return with \Call
    if _DETECTION_STATE_OR_RETURN.search(text) and _DETECTION_CALL.search(text):
        return True
    # (C) \Require/\Ensure with at least one \State
    if _DETECTION_REQUIRE_ENSURE.search(text) and _DETECTION_STATE_OR_RETURN.search(text):
        return True
    return False


def _substitute_latex_symbols(text: str) -> str:
    """Replace known LaTeX math symbols with Unicode equivalents."""
    for latex, unicode_char in _LATEX_SYMBOLS:
        text = text.replace(latex, unicode_char)
    # Strip any remaining unknown \commands that are lone words (e.g. \foo → foo)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    return text


def _tokenize_algorithmic(text: str) -> list[str]:
    r"""Split raw algorithmic text into a list of tokens.

    Tokens are either:
    - A backslash-command like ``\Function``
    - A brace-delimited group like ``{QuickSort}``
    - A bare word / symbol run between commands
    """
    tokens: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\":
            # Read command name
            j = i + 1
            while j < len(text) and (text[j].isalpha() or text[j] == "*"):
                j += 1
            tokens.append(text[i:j])
            i = j
        elif text[i] == "{":
            # Read balanced braces
            depth = 1
            j = i + 1
            while j < len(text) and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            tokens.append(text[i:j])  # includes braces
            i = j
        elif text[i].isspace():
            i += 1  # skip whitespace between tokens
        else:
            # Bare text until next \ or {
            j = i
            while j < len(text) and text[j] not in ("\\", "{") and text[j] != "\n":
                j += 1
            chunk = text[i:j].strip()
            if chunk:
                tokens.append(chunk)
            i = j
    return tokens


def _strip_braces(token: str) -> str:
    """Remove surrounding braces from a token like ``{foo}`` → ``foo``."""
    token = token.strip()
    if token.startswith("{") and token.endswith("}"):
        return token[1:-1].strip()
    return token


def _html_escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_algorithmic_block(text: str) -> str | None:
    """Convert raw algorithmic package commands to formatted pseudocode HTML.

    Takes a string that may contain LaTeX ``algorithmic``/``algpseudocode``
    commands and returns an HTML ``<pre class="algorithm">`` block with
    keyword bolding and indentation, or ``None`` if the text does not appear
    to contain algorithmic commands.
    """
    # Detection: must look like an algorithmic block.
    if not _is_algorithmic(text):
        return None

    tokens = _tokenize_algorithmic(text)

    indent = 0
    INDENT_SPACES = 4
    lines: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # ── Block openers ──────────────────────────────────────────────────
        if tok == "\\Function" or tok == "\\Procedure":
            keyword = "Function" if tok == "\\Function" else "Procedure"
            name = _strip_braces(tokens[i + 1]) if i + 1 < len(tokens) and tokens[i + 1].startswith("{") else ""
            params = _strip_braces(tokens[i + 2]) if i + 2 < len(tokens) and tokens[i + 2].startswith("{") else ""
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>{_html_escape(keyword)}</strong> {_html_escape(name)}({_html_escape(params)}):")
            indent += 1
            skip = 1 + (1 if name else 0) + (1 if params else 0)
            i += skip

        elif tok == "\\If":
            condition_parts: list[str] = []
            j = i + 1
            while j < len(tokens) and tokens[j].startswith("{"):
                condition_parts.append(_strip_braces(tokens[j]))
                j += 1
            condition = " ".join(condition_parts) if condition_parts else ""
            condition = _substitute_latex_symbols(condition)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>If</strong> {_html_escape(condition)}:")
            indent += 1
            i = j

        elif tok == "\\ElsIf":
            indent = max(0, indent - 1)
            condition_parts = []
            j = i + 1
            while j < len(tokens) and tokens[j].startswith("{"):
                condition_parts.append(_strip_braces(tokens[j]))
                j += 1
            condition = " ".join(condition_parts) if condition_parts else ""
            condition = _substitute_latex_symbols(condition)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>Else If</strong> {_html_escape(condition)}:")
            indent += 1
            i = j

        elif tok == "\\Else":
            indent = max(0, indent - 1)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>Else</strong>:")
            indent += 1
            i += 1

        elif tok in ("\\For",):
            condition_parts = []
            j = i + 1
            while j < len(tokens) and tokens[j].startswith("{"):
                condition_parts.append(_strip_braces(tokens[j]))
                j += 1
            condition = " ".join(condition_parts) if condition_parts else ""
            condition = _substitute_latex_symbols(condition)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>For</strong> {_html_escape(condition)}:")
            indent += 1
            i = j

        elif tok == "\\While":
            condition_parts = []
            j = i + 1
            while j < len(tokens) and tokens[j].startswith("{"):
                condition_parts.append(_strip_braces(tokens[j]))
                j += 1
            condition = " ".join(condition_parts) if condition_parts else ""
            condition = _substitute_latex_symbols(condition)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}<strong>While</strong> {_html_escape(condition)}:")
            indent += 1
            i = j

        # ── Block closers ──────────────────────────────────────────────────
        elif tok in _BLOCK_CLOSE:
            indent = max(0, indent - 1)
            i += 1

        # ── \State — gather rest of line until next command ────────────────
        elif tok == "\\State":
            # Collect tokens until the next backslash-command
            j = i + 1
            parts: list[str] = []
            while j < len(tokens) and not tokens[j].startswith("\\"):
                parts.append(tokens[j])
                j += 1
            # Resolve \Call inline within gathered parts
            stmt = _resolve_calls_in_parts(parts)
            stmt = _substitute_latex_symbols(stmt)
            stmt = stmt.strip()
            prefix = " " * (indent * INDENT_SPACES)
            if stmt:
                lines.append(f"{prefix}{_html_escape(stmt)}")
            i = j

        # ── \Return ────────────────────────────────────────────────────────
        elif tok == "\\Return":
            j = i + 1
            parts = []
            while j < len(tokens) and not tokens[j].startswith("\\"):
                parts.append(tokens[j])
                j += 1
            expr = _resolve_calls_in_parts(parts)
            expr = _substitute_latex_symbols(expr).strip()
            prefix = " " * (indent * INDENT_SPACES)
            if expr:
                lines.append(f"{prefix}<strong>Return</strong> {_html_escape(expr)}")
            else:
                lines.append(f"{prefix}<strong>Return</strong>")
            i = j

        # ── \Call{Name}{args} ─────────────────────────────────────────────
        elif tok == "\\Call":
            name = _strip_braces(tokens[i + 1]) if i + 1 < len(tokens) else ""
            args = _strip_braces(tokens[i + 2]) if i + 2 < len(tokens) else ""
            args = _substitute_latex_symbols(args)
            prefix = " " * (indent * INDENT_SPACES)
            lines.append(f"{prefix}{_html_escape(name)}({_html_escape(args)})")
            skip = 1 + (1 if name else 0) + (1 if args else 0)
            i += skip

        # ── \Require / \Ensure ────────────────────────────────────────────
        elif tok in ("\\Require", "\\Ensure"):
            keyword = "Require" if tok == "\\Require" else "Ensure"
            j = i + 1
            parts = []
            while j < len(tokens) and not tokens[j].startswith("\\"):
                parts.append(tokens[j])
                j += 1
            stmt = _resolve_calls_in_parts(parts)
            stmt = _substitute_latex_symbols(stmt).strip()
            prefix = " " * (indent * INDENT_SPACES)
            if stmt:
                lines.append(f"{prefix}<strong>{keyword}:</strong> {_html_escape(stmt)}")
            else:
                lines.append(f"{prefix}<strong>{keyword}:</strong>")
            i = j

        # ── Unknown / bare text — skip ────────────────────────────────────
        else:
            i += 1

    if not lines:
        return None

    inner = "\n".join(lines)
    return f'<pre class="algorithm">\n{inner}\n</pre>'


def _resolve_calls_in_parts(parts: list[str]) -> str:
    r"""Inline-resolve ``\Call{Name}{args}`` within a list of token parts.

    Handles the case where ``\Call`` appears inside a ``\State`` body.
    Since we have already tokenized, ``\Call`` will appear as a bare token
    followed by ``{Name}`` and ``{args}`` brace-tokens in ``parts``.
    """
    result: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p == "\\Call":
            name = _strip_braces(parts[i + 1]) if i + 1 < len(parts) else ""
            args = _strip_braces(parts[i + 2]) if i + 2 < len(parts) else ""
            args = _substitute_latex_symbols(args)
            result.append(f"{name}({args})")
            skip = 1 + (1 if name else 0) + (1 if args else 0)
            i += skip
        elif p.startswith("{"):
            result.append(_strip_braces(p))
            i += 1
        else:
            result.append(p)
            i += 1
    return " ".join(result)


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


def _parse_latexml_html(
    html: str,
    project_dir: Path | None = None,
    source_path: str = "",
) -> DocumentModel:
    """Parse LaTeXML HTML output into a DocumentModel."""
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

    # Track processed elements to avoid duplicates
    processed_ids: set[str] = set()

    def _next_math_id() -> str:
        nonlocal math_idx
        mid = f"math_{math_idx}"
        math_idx += 1
        return mid

    def _extract_math(math_tag: Tag) -> MathInfo:
        mid = _next_math_id()
        latex_source = math_tag.get("alttext", "")
        display = math_tag.get("display", "inline")
        classes = math_tag.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        is_unparsed = "ltx_math_unparsed" in classes
        mathml_str = str(math_tag)
        return MathInfo(
            id=mid, latex_source=latex_source, mathml=mathml_str,
            display=display, unparsed=is_unparsed,
        )

    def _process_paragraph(p_tag: Tag) -> ParagraphInfo | None:
        nonlocal p_idx

        # Collect all ltx_ERROR span texts from this paragraph to check for
        # algorithmic commands before doing general paragraph processing.
        error_spans_in_p = p_tag.find_all("span", class_=lambda c: c and "ltx_ERROR" in str(c))
        if error_spans_in_p:
            error_text = " ".join(s.get_text(strip=True) for s in error_spans_in_p)
            algo_html = format_algorithmic_block(error_text)
            if algo_html is not None:
                pid = f"p_{p_idx}"
                p_idx += 1
                return ParagraphInfo(
                    id=pid, text=algo_html, style_name="Normal",
                    runs=[RunInfo(text=algo_html, font_size_pt=12.0)],
                )

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
            id=pid, text=text, style_name="Normal",
            runs=[RunInfo(text=text, font_size_pt=12.0)],
            math_ids=inline_math_ids,
        )

    def _process_heading(h_tag: Tag) -> ParagraphInfo | None:
        nonlocal p_idx
        level = int(h_tag.name[1]) if h_tag.name[1:].isdigit() else 2
        text = h_tag.get_text(strip=True)
        if not text:
            return None
        pid = f"p_{p_idx}"
        p_idx += 1
        return ParagraphInfo(
            id=pid, text=text, style_name=f"Heading {level}",
            heading_level=level,
            runs=[RunInfo(text=text, bold=True, font_size_pt=16.0)],
        )

    def _process_equation_table(table_tag: Tag) -> MathInfo | None:
        math_tag = table_tag.find("math")
        if not math_tag:
            return None
        math_info = _extract_math(math_tag)
        # Override to block display and find equation number
        eq_num = None
        tag_span = table_tag.find("span", class_=lambda c: c and "ltx_tag_equation" in c)
        if tag_span:
            eq_num = tag_span.get_text(strip=True)
        return MathInfo(
            id=math_info.id, latex_source=math_info.latex_source,
            mathml=math_info.mathml, display="block",
            unparsed=math_info.unparsed, equation_number=eq_num,
        )

    def _process_data_table(table_tag: Tag) -> TableInfo | None:
        nonlocal tbl_idx
        rows: list[list[CellInfo]] = []
        header_row_count = 0
        thead = table_tag.find("thead")
        if thead:
            for tr in thead.find_all("tr"):
                cells = [
                    CellInfo(
                        text=td.get_text(strip=True),
                        paragraphs=[td.get_text(strip=True)],
                    )
                    for td in tr.find_all(["th", "td"])
                ]
                if cells:
                    rows.append(cells)
                    header_row_count += 1
        tbody = table_tag.find("tbody") or table_tag
        for tr in tbody.find_all("tr", recursive=False):
            if thead and tr.parent == thead:
                continue
            cells = [
                CellInfo(
                    text=td.get_text(strip=True),
                    paragraphs=[td.get_text(strip=True)],
                )
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
            id=tid, rows=rows, header_row_count=header_row_count,
            has_header_style=header_row_count > 0, row_count=len(rows), col_count=col_count,
        )

    # Walk the DOM
    article = soup.find("article", class_="ltx_document") or soup.find("body") or soup

    # Count errors
    error_spans = article.find_all("span", class_=lambda c: c and "ltx_ERROR" in str(c))
    if error_spans:
        warnings.append(f"LaTeXML: {len(error_spans)} undefined macro(s) in output")

    # Process elements in DOM order
    for element in article.descendants:
        if not isinstance(element, Tag):
            continue

        el_id = element.get("id", "")
        if el_id and el_id in processed_ids:
            continue

        classes = element.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        class_str = " ".join(classes)

        # Headings
        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6") and "ltx_title" in class_str:
            para = _process_heading(element)
            if para:
                paragraphs.append(para)
                content_order.append(ContentOrderItem(content_type=ContentType.PARAGRAPH, id=para.id))
                if el_id:
                    processed_ids.add(el_id)

        # Block equations
        elif element.name == "table" and "ltx_equation" in class_str:
            math_info = _process_equation_table(element)
            if math_info:
                math_list.append(math_info)
                content_order.append(ContentOrderItem(content_type=ContentType.MATH, id=math_info.id))
                if el_id:
                    processed_ids.add(el_id)

        # Data tables (not equations)
        elif element.name == "table" and "ltx_tabular" in class_str:
            table_info = _process_data_table(element)
            if table_info:
                tables.append(table_info)
                content_order.append(ContentOrderItem(content_type=ContentType.TABLE, id=table_info.id))
                if el_id:
                    processed_ids.add(el_id)

        # Paragraphs
        elif element.name == "p" and "ltx_p" in class_str:
            # Skip paragraphs inside equation tables
            parent_table = element.find_parent("table")
            if parent_table:
                parent_classes = parent_table.get("class") or []
                if isinstance(parent_classes, str):
                    parent_classes = parent_classes.split()
                if any("ltx_equation" in c for c in parent_classes):
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
            img_data = None
            if project_dir and src:
                img_path = _resolve_image_path(project_dir, src)
                if img_path and img_path.exists():
                    try:
                        img_data = img_path.read_bytes()
                    except Exception:
                        pass
            images.append(ImageInfo(
                id=iid, image_data=img_data,
                content_type=_guess_mime(src),
                alt_text=element.get("alt", ""),
                is_decorative=False,
            ))

    # Stats
    heading_count = sum(1 for p in paragraphs if p.heading_level is not None)
    stats = DocumentStats(
        paragraph_count=len(paragraphs), table_count=len(tables),
        image_count=len(images), heading_count=heading_count,
        images_missing_alt=sum(1 for i in images if not i.alt_text and not i.is_decorative),
        math_count=len(math_list),
        math_missing_description=sum(1 for m in math_list if not m.description),
    )

    return DocumentModel(
        source_format="tex", source_path=source_path,
        metadata=metadata, paragraphs=paragraphs, tables=tables,
        images=images, math=math_list, content_order=content_order,
        stats=stats, parse_warnings=warnings,
    )


def _resolve_image_path(project_dir: Path, src: str) -> Path | None:
    """Resolve a relative image src path from LaTeXML HTML to an absolute path."""
    direct = project_dir / src
    if direct.exists():
        return direct
    for ext in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"):
        candidate = project_dir / f"{src}{ext}"
        if candidate.exists():
            return candidate
    return None


def _guess_mime(filename: str) -> str:
    """Guess MIME type from filename extension."""
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return "image/png"


MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_ZIP_FILES = 500


@dataclass
class ZipExtractResult:
    """Result of a zip extraction attempt."""
    success: bool
    extract_dir: Path | None = None
    error: str = ""


def _safe_extract_zip(
    zip_path: Path,
    dest_dir: Path,
    max_bytes: int = MAX_ZIP_BYTES,
    max_files: int = MAX_ZIP_FILES,
) -> ZipExtractResult:
    """Extract a zip file safely, rejecting path traversal, symlinks, and oversized archives."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if len(zf.namelist()) > max_files:
                return ZipExtractResult(
                    success=False,
                    error=f"Zip contains too many files ({len(zf.namelist())} > {max_files})",
                )
            total_size = 0
            for info in zf.infolist():
                if ".." in info.filename or info.filename.startswith("/"):
                    return ZipExtractResult(success=False, error=f"Invalid path in zip: {info.filename}")
                if info.external_attr >> 28 == 0xA:
                    return ZipExtractResult(success=False, error=f"Zip contains symlink: {info.filename}")
                total_size += info.file_size
            if total_size > max_bytes:
                return ZipExtractResult(
                    success=False,
                    error=f"Zip too large when extracted ({total_size} bytes > {max_bytes})",
                )
            dest_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(dest_dir)
            return ZipExtractResult(success=True, extract_dir=dest_dir)
    except zipfile.BadZipFile:
        return ZipExtractResult(success=False, error="Invalid zip file")
    except Exception as e:
        return ZipExtractResult(success=False, error=f"Zip extraction failed: {e}")


def parse_latex(filepath: str | Path) -> ParseResult:
    """Parse a .tex or .zip LaTeX project into a DocumentModel."""
    filepath = Path(filepath)
    if not filepath.exists():
        return ParseResult(success=False, error=f"File not found: {filepath}")

    suffix = filepath.suffix.lower()
    cleanup_dir: Path | None = None

    try:
        if suffix == ".zip":
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
                    error="Couldn't find main LaTeX file in the upload. No file contains \\documentclass.",
                )
        elif suffix in (".tex", ".ltx"):
            main_tex = filepath
            project_dir = filepath.parent
        else:
            return ParseResult(
                success=False,
                error=f"Unsupported file type: {suffix}. Accepts .tex, .ltx, or .zip",
            )

        latexml_result = _run_latexml(main_tex, project_dir)
        if not latexml_result.success:
            return ParseResult(
                success=False,
                error=latexml_result.error,
                warnings=latexml_result.warnings,
            )

        doc_model = _parse_latexml_html(
            latexml_result.html,
            project_dir=project_dir,
            source_path=str(filepath),
        )

        all_warnings = list(doc_model.parse_warnings) + latexml_result.warnings
        if latexml_result.error_count > 0:
            all_warnings.append(
                f"LaTeXML: {latexml_result.error_count} error(s), "
                f"{latexml_result.unparsed_math_count} unparsed math expression(s)"
            )

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
            stats=doc_model.stats,
            parse_warnings=all_warnings,
        )

        return ParseResult(success=True, document=doc_model, warnings=all_warnings)

    finally:
        if cleanup_dir and cleanup_dir.exists():
            shutil.rmtree(cleanup_dir, ignore_errors=True)
