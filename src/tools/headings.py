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

        paragraph.style = doc.styles[style_name]

        # Clear direct bold formatting since heading styles handle that
        for run in paragraph.runs:
            if run.bold is True:
                run.bold = None  # inherit from style

        change = f"p_{paragraph_index}: {old_style!r} -> {style_name!r} ({paragraph.text[:50]!r})"
        logger.info(change)
        return HeadingResult(success=True, changes=[change])

    except KeyError:
        return HeadingResult(
            success=False,
            error=f"Heading style 'Heading {level}' not found in document",
        )
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
