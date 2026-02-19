"""Set and fix document metadata for WCAG compliance.

Targets:
- 2.4.2: Document title in metadata
- 3.1.1: Document language set
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from docx.document import Document

logger = logging.getLogger(__name__)


@dataclass
class MetadataResult:
    """Result of a metadata operation."""
    success: bool
    changes: list[str] = field(default_factory=list)
    error: str = ""


def set_title(doc: Document, title: str) -> MetadataResult:
    """Set the document title in core properties (WCAG 2.4.2)."""
    if not title.strip():
        return MetadataResult(success=False, error="Title cannot be empty")

    try:
        old_title = doc.core_properties.title or ""
        doc.core_properties.title = title.strip()
        change = f"Title: {old_title!r} -> {title.strip()!r}" if old_title else f"Title set: {title.strip()!r}"
        logger.info(change)
        return MetadataResult(success=True, changes=[change])
    except Exception as e:
        return MetadataResult(success=False, error=f"Failed to set title: {e}")


def set_language(doc: Document, language: str) -> MetadataResult:
    """Set the document language in core properties (WCAG 3.1.1).

    Args:
        doc: python-docx Document.
        language: BCP 47 language tag, e.g. 'en', 'en-US', 'es'.
    """
    if not language.strip():
        return MetadataResult(success=False, error="Language cannot be empty")

    try:
        old_lang = doc.core_properties.language or ""
        doc.core_properties.language = language.strip()
        change = f"Language: {old_lang!r} -> {language.strip()!r}" if old_lang else f"Language set: {language.strip()!r}"
        logger.info(change)
        return MetadataResult(success=True, changes=[change])
    except Exception as e:
        return MetadataResult(success=False, error=f"Failed to set language: {e}")


def set_title_pptx(prs, title: str) -> MetadataResult:
    """Set the presentation title in core properties (WCAG 2.4.2)."""
    if not title.strip():
        return MetadataResult(success=False, error="Title cannot be empty")

    try:
        old_title = prs.core_properties.title or ""
        prs.core_properties.title = title.strip()
        change = f"Title: {old_title!r} -> {title.strip()!r}" if old_title else f"Title set: {title.strip()!r}"
        logger.info(change)
        return MetadataResult(success=True, changes=[change])
    except Exception as e:
        return MetadataResult(success=False, error=f"Failed to set pptx title: {e}")


def set_language_pptx(prs, language: str) -> MetadataResult:
    """Set the presentation language (WCAG 3.1.1).

    Sets core_properties.language and adds xml:lang to the
    presentation XML root element.
    """
    if not language.strip():
        return MetadataResult(success=False, error="Language cannot be empty")

    try:
        old_lang = prs.core_properties.language or ""
        prs.core_properties.language = language.strip()

        # Also set xml:lang on the presentation element
        prs_elem = prs._element
        prs_elem.set("{http://www.w3.org/XML/1998/namespace}lang", language.strip())

        change = f"Language: {old_lang!r} -> {language.strip()!r}" if old_lang else f"Language set: {language.strip()!r}"
        logger.info(change)
        return MetadataResult(success=True, changes=[change])
    except Exception as e:
        return MetadataResult(success=False, error=f"Failed to set pptx language: {e}")


def fix_metadata(doc: Document, title: str = "", language: str = "en") -> MetadataResult:
    """Fix document metadata, setting title and language if missing.

    Args:
        doc: python-docx Document.
        title: Title to set if missing. If empty string, skips title.
        language: Language to set if missing. Defaults to 'en'.

    Returns:
        MetadataResult with all changes made.
    """
    changes: list[str] = []

    # Fix title if missing and a title was provided
    if title and not (doc.core_properties.title or "").strip():
        result = set_title(doc, title)
        if result.success:
            changes.extend(result.changes)
        else:
            return result

    # Fix language if missing
    if not (doc.core_properties.language or "").strip():
        result = set_language(doc, language)
        if result.success:
            changes.extend(result.changes)
        else:
            return result

    if not changes:
        changes.append("No metadata changes needed")

    return MetadataResult(success=True, changes=changes)
