"""Python wrapper for the iText 7 PDF tagger Java CLI.

Writes a tagging plan JSON, invokes the Java CLI, and parses the result.
Uses the same subprocess pattern as verapdf_checker.py.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.models.document import DocumentModel, ImageInfo, LinkInfo, ParagraphInfo, TableInfo
from src.models.pipeline import RemediationAction, RemediationStrategy

logger = logging.getLogger(__name__)

# Default JAR path relative to project root
_DEFAULT_JAR = Path(__file__).parent.parent.parent / "java" / "itext-tagger" / "build" / "libs" / "itext-tagger-all.jar"

# JAVA_HOME for Homebrew OpenJDK 17
_JAVA_HOME = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"


@dataclass
class TaggingResult:
    """Result from the iText tagger CLI."""
    success: bool
    output_path: str = ""
    tags_applied: int = 0
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def tag_pdf(tagging_plan: dict, jar_path: str | None = None) -> TaggingResult:
    """Apply structure tags to a PDF using the iText tagger CLI.

    Args:
        tagging_plan: Dict matching the TaggingPlan JSON schema.
        jar_path: Path to the fat JAR. Defaults to built location.

    Returns:
        TaggingResult with success/failure and change log.
    """
    jar = Path(jar_path) if jar_path else _DEFAULT_JAR
    if not jar.exists():
        return TaggingResult(
            success=False,
            errors=[f"iText tagger JAR not found: {jar}. Run 'gradle fatJar' in java/itext-tagger/"],
        )

    # Find Java executable
    java_bin = _find_java()
    if not java_bin:
        return TaggingResult(
            success=False,
            errors=["Java not found. Install Java 17+: brew install openjdk@17"],
        )

    # Write tagging plan to temp file
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(tagging_plan, f, ensure_ascii=False)
            plan_path = f.name
    except Exception as e:
        return TaggingResult(success=False, errors=[f"Failed to write plan JSON: {e}"])

    try:
        cmd = [java_bin, "-jar", str(jar), plan_path]
        logger.info("Running iText tagger: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse JSON output from stdout
        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()
            return TaggingResult(
                success=False,
                errors=[f"No output from iText tagger. stderr: {stderr[:500]}"],
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            return TaggingResult(
                success=False,
                errors=[f"Invalid JSON from tagger: {e}. stdout: {stdout[:500]}"],
            )

        return TaggingResult(
            success=data.get("success", False),
            output_path=data.get("output_path", ""),
            tags_applied=data.get("tags_applied", 0),
            changes=data.get("changes", []),
            warnings=data.get("warnings", []),
            errors=data.get("errors", []),
        )

    except subprocess.TimeoutExpired:
        return TaggingResult(success=False, errors=["iText tagger timed out (120s)"])
    except Exception as e:
        return TaggingResult(success=False, errors=[f"Failed to run iText tagger: {e}"])
    finally:
        try:
            os.unlink(plan_path)
        except Exception:
            pass


def build_tagging_plan(
    strategy: RemediationStrategy,
    doc_model: DocumentModel,
    input_path: str,
    output_path: str,
) -> dict:
    """Build a tagging plan JSON dict from a remediation strategy and document model.

    Translates RemediationActions into position-based tagging instructions
    using the bbox and page_number data from the parser.

    Args:
        strategy: Remediation strategy with planned actions.
        doc_model: Parsed document model with bbox data.
        input_path: Path to the original PDF.
        output_path: Path for the tagged output PDF.

    Returns:
        Dict matching the TaggingPlan JSON schema.
    """
    # Build lookup maps
    para_by_id: dict[str, ParagraphInfo] = {p.id: p for p in doc_model.paragraphs}
    img_by_id: dict[str, ImageInfo] = {img.id: img for img in doc_model.images}
    tbl_by_id: dict[str, TableInfo] = {t.id: t for t in doc_model.tables}
    link_by_id: dict[str, LinkInfo] = {lnk.id: lnk for lnk in doc_model.links}

    # Extract metadata actions
    title = ""
    language = ""
    elements: list[dict] = []

    for action in strategy.actions:
        if action.status == "skipped":
            continue

        if action.action_type == "set_title":
            title = action.parameters.get("title", "")

        elif action.action_type == "set_language":
            language = action.parameters.get("language", "")

        elif action.action_type == "set_heading_level":
            para = para_by_id.get(action.element_id)
            if para:
                level = action.parameters.get("level", 1)
                elem = {
                    "type": "heading",
                    "level": level,
                    "text": para.text,
                    "page": para.page_number if para.page_number is not None else 0,
                    "bbox": list(para.bbox) if para.bbox else None,
                }
                elements.append(elem)

        elif action.action_type == "set_alt_text":
            img = img_by_id.get(action.element_id)
            if img:
                alt_text = action.parameters.get("alt_text", "")
                elem = {
                    "type": "image_alt",
                    "image_id": img.id,
                    "alt_text": alt_text,
                    "page": img.page_number if img.page_number is not None else 0,
                    "bbox": list(img.bbox) if img.bbox else None,
                    "xref": img.xref if img.xref is not None else 0,
                }
                elements.append(elem)

        elif action.action_type == "set_decorative":
            img = img_by_id.get(action.element_id)
            if img:
                elem = {
                    "type": "image_alt",
                    "image_id": img.id,
                    "alt_text": "",
                    "page": img.page_number if img.page_number is not None else 0,
                    "bbox": list(img.bbox) if img.bbox else None,
                    "xref": img.xref if img.xref is not None else 0,
                }
                elements.append(elem)

        elif action.action_type == "mark_header_rows":
            tbl = tbl_by_id.get(action.element_id)
            if tbl:
                header_rows = action.parameters.get("header_count", 1)
                # Build row/cell data for /TR, /TH, /TD children
                rows_data = []
                for row in tbl.rows:
                    cells_data = []
                    for cell in row:
                        cells_data.append({
                            "text": cell.text,
                            "grid_span": cell.grid_span,
                        })
                    rows_data.append({"cells": cells_data})
                elem = {
                    "type": "table",
                    "table_id": tbl.id,
                    "header_rows": header_rows,
                    "rows": rows_data,
                    "page": tbl.page_number if tbl.page_number is not None else 0,
                    "bbox": list(tbl.bbox) if tbl.bbox else None,
                }
                elements.append(elem)

        elif action.action_type == "set_link_text":
            link = link_by_id.get(action.element_id)
            if link:
                new_text = action.parameters.get("new_text", "")
                elem = {
                    "type": "link",
                    "link_id": link.id,
                    "link_text": new_text,
                    "link_url": link.url,
                    "page": link.page_number if link.page_number is not None else 0,
                    "bbox": list(link.bbox) if link.bbox else None,
                }
                elements.append(elem)

    # ── Auto-add headings from fake heading candidates ──────────────
    # If the strategy didn't generate any heading actions, use the parser's
    # fake heading candidates. This ensures PDFs always get heading structure,
    # even when comprehension fails or strategy doesn't produce heading actions.
    has_headings = any(e["type"] == "heading" for e in elements)
    if not has_headings:
        auto_headings = _auto_detect_headings(doc_model, title)
        if auto_headings:
            elements.extend(auto_headings)
            logger.info(
                "Auto-added %d heading tags from fake heading candidates",
                len(auto_headings),
            )

    return {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "metadata": {
            "title": title,
            "language": language,
        },
        "elements": elements,
    }


def _auto_detect_headings(doc_model: DocumentModel, title: str = "") -> list[dict]:
    """Generate heading elements from paragraphs with high fake heading scores.

    Used as a fallback when the strategy phase doesn't produce heading actions.
    Selects paragraphs with score >= 0.6 and assigns heading levels based on
    font size (largest = H1, others = H2).

    Always ensures at least one heading exists — creates an H1 from the
    document title, metadata title, or filename if no candidates are found.
    """
    candidates = []
    for para in doc_model.paragraphs:
        if (
            para.fake_heading_signals
            and para.fake_heading_signals.score >= 0.6
            and para.bbox is not None
            and para.page_number is not None
            and para.text.strip()
            and len(para.text.strip()) < 200  # skip overly long text
        ):
            candidates.append(para)

    if not candidates:
        # No heading candidates — derive an H1 from available sources
        h1_text = title
        if not h1_text:
            h1_text = doc_model.metadata.title if doc_model.metadata and doc_model.metadata.title else ""
        if not h1_text:
            # Derive from filename as last resort
            source = Path(doc_model.source_path)
            h1_text = source.stem.strip()
            # Clean up common filename patterns: leading numbers, parentheses
            import re
            h1_text = re.sub(r"^\d+\)\s*", "", h1_text)  # "4) Erlwanger..." -> "Erlwanger..."
            h1_text = re.sub(r"_", " ", h1_text)
        if h1_text:
            return [{
                "type": "heading",
                "level": 1,
                "text": h1_text,
                "page": 0,
                "bbox": None,
            }]
        return []

    # Assign heading levels: largest font = H1, next = H2, rest = H3
    font_sizes = set()
    for c in candidates:
        if c.fake_heading_signals and c.fake_heading_signals.font_size_pt:
            font_sizes.add(c.fake_heading_signals.font_size_pt)
    sorted_sizes = sorted(font_sizes, reverse=True)

    def _heading_level(para: ParagraphInfo) -> int:
        fs = para.fake_heading_signals.font_size_pt if para.fake_heading_signals else None
        if not fs or not sorted_sizes:
            return 2
        idx = sorted_sizes.index(fs) if fs in sorted_sizes else len(sorted_sizes)
        if idx == 0:
            return 1
        elif idx == 1:
            return 2
        return 3

    elements = []
    for para in candidates:
        level = _heading_level(para)
        elements.append({
            "type": "heading",
            "level": level,
            "text": para.text,
            "page": para.page_number if para.page_number is not None else 0,
            "bbox": list(para.bbox) if para.bbox else None,
        })

    return elements


def _find_java() -> str | None:
    """Find the Java executable. Checks JAVA_HOME, then PATH."""
    # Check our known Homebrew location first
    java_path = Path(_JAVA_HOME) / "bin" / "java"
    if java_path.exists():
        return str(java_path)

    # Check JAVA_HOME env var
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        java_path = Path(java_home) / "bin" / "java"
        if java_path.exists():
            return str(java_path)

    # Check PATH
    try:
        result = subprocess.run(
            ["which", "java"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return None
