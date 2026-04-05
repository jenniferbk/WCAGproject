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
