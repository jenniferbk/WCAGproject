"""Detect and convert fake lists to real list markup in .docx files.

Fake lists are paragraphs that visually appear as list items but use
manual numbering ("1.", "2.") or bullet characters ("-", "*", "o")
instead of Word's built-in list styles.

Targets:
- 1.3.1: Lists use semantic markup
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from docx.document import Document

from src.models.document import ParagraphInfo

logger = logging.getLogger(__name__)


class ListType(str, Enum):
    BULLETED = "bulleted"
    NUMBERED = "numbered"


# Patterns for detecting fake list items
_BULLET_CHARS = re.compile(r"^[\u2022\u2023\u25E6\u2043\u2219\u25AA\u25CF\u25CB\u2013\u2014\-\*\>]\s+")
_NUMBERED_PATTERN = re.compile(r"^(\d{1,3})[.\)]\s+")
_LETTERED_PATTERN = re.compile(r"^[a-zA-Z][.\)]\s+")
_ROMAN_PATTERN = re.compile(r"^(?:i{1,3}|iv|vi{0,3}|ix|x{0,3})[.\)]\s+", re.IGNORECASE)


@dataclass
class FakeListCandidate:
    """A group of consecutive paragraphs that form a fake list."""
    paragraph_ids: list[str]
    paragraph_indices: list[int]
    list_type: ListType
    texts: list[str]
    confidence: float  # 0-1


@dataclass
class ListResult:
    """Result of a list conversion operation."""
    success: bool
    changes: list[str] = field(default_factory=list)
    error: str = ""


def _classify_fake_list_item(text: str) -> ListType | None:
    """Check if a paragraph's text looks like a fake list item.

    Returns the detected list type or None.
    """
    stripped = text.strip()
    if not stripped:
        return None

    if _BULLET_CHARS.match(stripped):
        return ListType.BULLETED

    if _NUMBERED_PATTERN.match(stripped):
        return ListType.NUMBERED

    if _LETTERED_PATTERN.match(stripped):
        return ListType.NUMBERED  # lettered lists are a form of ordered list

    if _ROMAN_PATTERN.match(stripped):
        return ListType.NUMBERED

    return None


def detect_fake_lists(paragraphs: list[ParagraphInfo]) -> list[FakeListCandidate]:
    """Detect groups of consecutive paragraphs that form fake lists.

    A fake list requires at least 2 consecutive paragraphs of the same
    list type (bulleted or numbered) that are not already marked as list items.

    Args:
        paragraphs: Parsed paragraphs from DocumentModel.

    Returns:
        List of FakeListCandidate groups.
    """
    candidates: list[FakeListCandidate] = []
    current_group: list[tuple[int, str, ParagraphInfo]] = []
    current_type: ListType | None = None

    for i, para in enumerate(paragraphs):
        # Skip paragraphs that are already list items or headings
        if para.is_list_item or para.heading_level is not None:
            _flush_group(current_group, current_type, candidates)
            current_group = []
            current_type = None
            continue

        detected = _classify_fake_list_item(para.text)

        if detected is not None:
            if current_type == detected:
                current_group.append((i, para.id, para))
            else:
                _flush_group(current_group, current_type, candidates)
                current_group = [(i, para.id, para)]
                current_type = detected
        else:
            _flush_group(current_group, current_type, candidates)
            current_group = []
            current_type = None

    _flush_group(current_group, current_type, candidates)
    return candidates


def _flush_group(
    group: list[tuple[int, str, ParagraphInfo]],
    list_type: ListType | None,
    candidates: list[FakeListCandidate],
) -> None:
    """Flush a group of consecutive fake list items into candidates if valid."""
    if len(group) >= 2 and list_type is not None:
        # Confidence based on group size and consistency
        confidence = min(0.5 + len(group) * 0.1, 1.0)

        candidates.append(FakeListCandidate(
            paragraph_ids=[pid for _, pid, _ in group],
            paragraph_indices=[idx for idx, _, _ in group],
            list_type=list_type,
            texts=[p.text for _, _, p in group],
            confidence=confidence,
        ))


def _strip_list_prefix(text: str) -> str:
    """Remove the fake list prefix (bullet char, number, letter) from text."""
    stripped = text.strip()
    for pattern in [_BULLET_CHARS, _NUMBERED_PATTERN, _LETTERED_PATTERN, _ROMAN_PATTERN]:
        match = pattern.match(stripped)
        if match:
            return stripped[match.end():]
    return stripped


def convert_to_list(
    doc: Document,
    paragraph_indices: list[int],
    list_type: ListType,
    strip_prefix: bool = True,
) -> ListResult:
    """Convert paragraphs to a real Word list by applying list styles.

    Args:
        doc: python-docx Document.
        paragraph_indices: Indices of paragraphs to convert.
        list_type: Whether to make a bulleted or numbered list.
        strip_prefix: Whether to strip the fake list prefix from text.

    Returns:
        ListResult with success/failure.
    """
    try:
        style_name = "List Bullet" if list_type == ListType.BULLETED else "List Number"

        # Check if the style exists
        try:
            target_style = doc.styles[style_name]
        except KeyError:
            return ListResult(
                success=False,
                error=f"List style {style_name!r} not found in document. "
                      "The document template may not include this style.",
            )

        changes: list[str] = []

        for idx in paragraph_indices:
            if idx >= len(doc.paragraphs):
                return ListResult(
                    success=False,
                    error=f"Paragraph index {idx} out of range",
                )

            para = doc.paragraphs[idx]
            old_style = para.style.name if para.style else "Normal"

            # Strip the fake prefix from the first run
            if strip_prefix and para.runs:
                full_text = para.text
                cleaned = _strip_list_prefix(full_text)
                if cleaned != full_text:
                    # Clear all runs and set cleaned text on first run
                    first_run_text = cleaned
                    for run_idx, run in enumerate(para.runs):
                        if run_idx == 0:
                            run.text = first_run_text
                            first_run_text = ""  # only set on first run
                        else:
                            run.text = ""

            para.style = target_style
            changes.append(
                f"p_{idx}: {old_style!r} -> {style_name!r} ({para.text[:50]!r})"
            )
            logger.info("Converted p_%d to %s", idx, style_name)

        return ListResult(success=True, changes=changes)

    except Exception as e:
        return ListResult(success=False, error=f"Failed to convert to list: {e}")
