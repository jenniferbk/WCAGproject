"""Detect fake headings and set heading levels in .docx files.

Targets:
- 1.3.1: Heading hierarchy uses semantic markup
- 2.4.1: Headings provide bypass mechanism
- 2.4.6: Headings describe topic/purpose
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from docx.document import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from src.models.document import ParagraphInfo

logger = logging.getLogger(__name__)


@dataclass
class HeadingIssue:
    """A heading hierarchy issue."""
    paragraph_id: str
    text_preview: str
    issue_type: str  # "skipped_level", "no_h1", "multiple_h1", "fake_heading"
    detail: str
    suggested_level: int | None = None


@dataclass
class HeadingResult:
    """Result of a heading operation."""
    success: bool
    changes: list[str] = field(default_factory=list)
    error: str = ""


def validate_heading_hierarchy(paragraphs: list[ParagraphInfo]) -> list[HeadingIssue]:
    """Check heading hierarchy for issues.

    Checks for:
    - No H1 present
    - Multiple H1s
    - Skipped heading levels (e.g. H1 -> H3 with no H2)

    Args:
        paragraphs: Parsed paragraphs from DocumentModel.

    Returns:
        List of heading issues found.
    """
    issues: list[HeadingIssue] = []
    headings = [(p, p.heading_level) for p in paragraphs if p.heading_level is not None]

    if not headings:
        return issues

    # Check for H1
    h1s = [p for p, level in headings if level == 1]
    if not h1s:
        issues.append(HeadingIssue(
            paragraph_id=headings[0][0].id,
            text_preview=headings[0][0].text[:50],
            issue_type="no_h1",
            detail="Document has no Heading 1. First heading is level "
                   f"{headings[0][1]}: {headings[0][0].text[:50]!r}",
            suggested_level=1,
        ))

    if len(h1s) > 1:
        for h1 in h1s[1:]:
            issues.append(HeadingIssue(
                paragraph_id=h1.id,
                text_preview=h1.text[:50],
                issue_type="multiple_h1",
                detail=f"Multiple H1s found. Consider demoting: {h1.text[:50]!r}",
                suggested_level=2,
            ))

    # Check for skipped levels
    prev_level = 0
    for para, level in headings:
        if level > prev_level + 1 and prev_level > 0:
            issues.append(HeadingIssue(
                paragraph_id=para.id,
                text_preview=para.text[:50],
                issue_type="skipped_level",
                detail=f"Heading level skips from {prev_level} to {level}: {para.text[:50]!r}",
                suggested_level=prev_level + 1,
            ))
        prev_level = level

    return issues


def get_fake_heading_candidates(
    paragraphs: list[ParagraphInfo],
    min_score: float = 0.5,
) -> list[tuple[ParagraphInfo, float]]:
    """Get paragraphs that are likely fake headings based on their signals.

    Args:
        paragraphs: Parsed paragraphs from DocumentModel.
        min_score: Minimum fake heading score to include (0-1).

    Returns:
        List of (paragraph, score) tuples, sorted by score descending.
    """
    candidates = []
    for para in paragraphs:
        if (
            para.fake_heading_signals is not None
            and para.fake_heading_signals.score >= min_score
        ):
            candidates.append((para, para.fake_heading_signals.score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates


def _ensure_heading_style(doc: Document, level: int) -> None:
    """Ensure a heading style exists in the document.

    Some document templates don't include heading styles. This creates
    them with proper outline levels (needed for navigation and PDF bookmarks).
    """
    style_name = f"Heading {level}"
    try:
        doc.styles[style_name]
        return  # already exists
    except KeyError:
        pass

    # Create the heading style via XML
    style_id = f"Heading{level}"
    style_elem = OxmlElement("w:style")
    style_elem.set(qn("w:type"), "paragraph")
    style_elem.set(qn("w:styleId"), style_id)

    name_elem = OxmlElement("w:name")
    name_elem.set(qn("w:val"), style_name)
    style_elem.append(name_elem)

    based_on = OxmlElement("w:basedOn")
    based_on.set(qn("w:val"), "Normal")
    style_elem.append(based_on)

    next_elem = OxmlElement("w:next")
    next_elem.set(qn("w:val"), "Normal")
    style_elem.append(next_elem)

    # Outline level — critical for navigation and PDF bookmarks
    pPr = OxmlElement("w:pPr")
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), str(level - 1))
    pPr.append(outline)
    style_elem.append(pPr)

    # Run formatting — bold + appropriate font size (in half-points)
    rPr = OxmlElement("w:rPr")
    bold_elem = OxmlElement("w:b")
    rPr.append(bold_elem)

    sizes_half_pt = {1: 32, 2: 26, 3: 24, 4: 24, 5: 20, 6: 20}
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(sizes_half_pt.get(level, 24)))
    rPr.append(sz)
    szCs = OxmlElement("w:szCs")
    szCs.set(qn("w:val"), str(sizes_half_pt.get(level, 24)))
    rPr.append(szCs)

    style_elem.append(rPr)
    doc.styles.element.append(style_elem)

    logger.info("Created heading style: %s", style_name)


def set_heading_level(
    doc: Document,
    paragraph_index: int,
    level: int,
) -> HeadingResult:
    """Convert a paragraph to a heading by setting its style.

    Also clears direct bold formatting since heading styles handle that.

    Args:
        doc: python-docx Document.
        paragraph_index: Index of the paragraph to convert.
        level: Heading level (1-9).

    Returns:
        HeadingResult with success/failure.
    """
    if level < 1 or level > 9:
        return HeadingResult(success=False, error=f"Invalid heading level: {level}")

    try:
        if paragraph_index >= len(doc.paragraphs):
            return HeadingResult(
                success=False,
                error=f"Paragraph index {paragraph_index} out of range",
            )

        paragraph = doc.paragraphs[paragraph_index]
        old_style = paragraph.style.name if paragraph.style else "Normal"
        style_name = f"Heading {level}"

        # Ensure the heading style definition exists
        _ensure_heading_style(doc, level)

        # Set the style reference directly via XML to avoid python-docx cache issues
        pPr = paragraph._element.get_or_add_pPr()
        pStyle = pPr.find(qn("w:pStyle"))
        if pStyle is None:
            pStyle = OxmlElement("w:pStyle")
            pPr.insert(0, pStyle)
        pStyle.set(qn("w:val"), f"Heading{level}")

        # Clear direct bold formatting since heading styles handle that
        for run in paragraph.runs:
            if run.bold is True:
                run.bold = None  # inherit from style

        change = f"p_{paragraph_index}: {old_style!r} -> {style_name!r} ({paragraph.text[:50]!r})"
        logger.info(change)
        return HeadingResult(success=True, changes=[change])

    except Exception as e:
        return HeadingResult(success=False, error=f"Failed to set heading: {e}")


def suggest_heading_level(
    para: ParagraphInfo,
    preceding_headings: list[tuple[str, int]],
) -> int:
    """Suggest a heading level based on font size and context.

    Args:
        para: The paragraph to suggest a level for.
        preceding_headings: List of (paragraph_id, level) for headings
            that come before this one.

    Returns:
        Suggested heading level (1-6).
    """
    if not preceding_headings:
        return 1

    last_level = preceding_headings[-1][1]

    # If font size suggests it's bigger than the previous heading, same or higher level
    if para.fake_heading_signals and para.fake_heading_signals.font_size_pt:
        size = para.fake_heading_signals.font_size_pt
        if size >= 20:
            return max(1, last_level)
        elif size >= 16:
            return min(last_level + 1, 6)
        else:
            return min(last_level + 1, 6)

    return min(last_level + 1, 6)
