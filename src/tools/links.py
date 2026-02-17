"""Analyze and improve link text for accessibility.

Detects vague link text like "click here", "link", bare URLs, and
single-word generic links. Provides analysis and suggestions.

Targets:
- 2.4.4: Link purpose determinable from link text
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from src.models.document import LinkInfo

logger = logging.getLogger(__name__)


class LinkIssueType(str, Enum):
    VAGUE_TEXT = "vague_text"  # "click here", "read more", etc.
    BARE_URL = "bare_url"  # link text is a URL
    GENERIC_TEXT = "generic_text"  # "link", "here", "this"
    EMPTY_TEXT = "empty_text"  # no link text at all
    SAME_TEXT_DIFFERENT_URL = "same_text_different_url"  # multiple links with same text but different URLs


# Common vague link phrases (case-insensitive)
_VAGUE_PHRASES = {
    "click here",
    "click",
    "here",
    "read more",
    "more",
    "learn more",
    "info",
    "more info",
    "more information",
    "details",
    "more details",
    "link",
    "this link",
    "this",
    "go",
    "go here",
    "see more",
    "view",
    "view more",
    "download",
    "open",
    "continue",
    "next",
    "previous",
}


@dataclass
class LinkIssue:
    """An accessibility issue with a link."""
    link_id: str
    paragraph_id: str
    text: str
    url: str
    issue_type: LinkIssueType
    detail: str


@dataclass
class LinkAnalysisResult:
    """Result of analyzing all links in a document."""
    total_links: int
    issues: list[LinkIssue] = field(default_factory=list)
    issue_count: int = 0


def _is_url(text: str) -> bool:
    """Check if text looks like a URL."""
    return bool(re.match(
        r"^(https?://|www\.|ftp://|mailto:)", text.strip(), re.IGNORECASE
    ))


def _is_vague(text: str) -> bool:
    """Check if link text is vague/generic."""
    normalized = text.strip().lower().rstrip(".")
    return normalized in _VAGUE_PHRASES


def analyze_links(links: list[LinkInfo]) -> LinkAnalysisResult:
    """Analyze all links in a document for accessibility issues.

    Checks for:
    - Empty link text
    - Bare URLs as link text
    - Vague/generic text ("click here", "read more", etc.)
    - Same text pointing to different URLs

    Args:
        links: List of LinkInfo from the DocumentModel.

    Returns:
        LinkAnalysisResult with issues found.
    """
    issues: list[LinkIssue] = []

    # Track text -> URLs for duplicate detection
    text_to_urls: dict[str, list[LinkInfo]] = {}

    for link in links:
        text = link.text.strip()

        # Empty text
        if not text:
            issues.append(LinkIssue(
                link_id=link.id,
                paragraph_id=link.paragraph_id,
                text=link.text,
                url=link.url,
                issue_type=LinkIssueType.EMPTY_TEXT,
                detail="Link has no text — screen readers cannot describe its purpose",
            ))
            continue

        # Bare URL as text
        if _is_url(text):
            issues.append(LinkIssue(
                link_id=link.id,
                paragraph_id=link.paragraph_id,
                text=link.text,
                url=link.url,
                issue_type=LinkIssueType.BARE_URL,
                detail=f"Link text is a raw URL: {text[:60]}",
            ))
            continue

        # Vague text
        if _is_vague(text):
            issues.append(LinkIssue(
                link_id=link.id,
                paragraph_id=link.paragraph_id,
                text=link.text,
                url=link.url,
                issue_type=LinkIssueType.VAGUE_TEXT,
                detail=f"Vague link text: {text!r} — does not describe the link destination",
            ))
            continue

        # Track for duplicate detection
        normalized = text.lower().strip()
        text_to_urls.setdefault(normalized, []).append(link)

    # Check for same text -> different URLs
    for text, link_group in text_to_urls.items():
        if len(link_group) < 2:
            continue
        urls = {l.url for l in link_group}
        if len(urls) > 1:
            for link in link_group:
                issues.append(LinkIssue(
                    link_id=link.id,
                    paragraph_id=link.paragraph_id,
                    text=link.text,
                    url=link.url,
                    issue_type=LinkIssueType.SAME_TEXT_DIFFERENT_URL,
                    detail=f"Link text {link.text!r} used for {len(urls)} different URLs",
                ))

    return LinkAnalysisResult(
        total_links=len(links),
        issues=issues,
        issue_count=len(issues),
    )
