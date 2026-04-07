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

from docx.document import Document
from docx.oxml.ns import qn

from src.models.document import LinkInfo

logger = logging.getLogger(__name__)


class LinkIssueType(str, Enum):
    VAGUE_TEXT = "vague_text"  # "click here", "read more", etc.
    BARE_URL = "bare_url"  # link text is a URL
    GENERIC_TEXT = "generic_text"  # "link", "here", "this"
    EMPTY_TEXT = "empty_text"  # no link text at all
    SAME_TEXT_DIFFERENT_URL = "same_text_different_url"  # multiple links with same text but different URLs
    BROKEN_URI = "broken_uri"  # URI is syntactically malformed (whitespace in domain, triple slashes, etc.)


def repair_uri(uri: str) -> str | None:
    """Return a repaired version of a syntactically malformed URI, or None
    if the URI is already well-formed (no change needed).

    Handles the broken patterns we see in real PDFs where the source
    document's PDF producer inserted spurious whitespace or slashes into
    link annotations:

    - ``http:////dx.doi.org/...`` → ``http://dx.doi.org/...`` (extra slashes)
    - ``http:/dx.doi.org/...``    → ``http://dx.doi.org/...`` (missing slash)
    - ``http://d x.doi.org/...``  → ``http://dx.doi.org/...`` (whitespace in domain)
    - ``http://dx.doi.org/ 10.1103/...`` → ``http://dx.doi.org/10.1103/...`` (whitespace in path)
    - ``mailto: user@ example.com`` → ``mailto:user@example.com`` (whitespace in mailto)

    Returns None for URIs that are already OK, empty, or non-fixable (e.g.
    relative URIs without a protocol). Callers should treat None as a signal
    to leave the annotation alone.
    """
    if not uri:
        return None
    original = uri
    s = uri.strip()

    # Protocol normalization
    proto_match = re.match(r"^(https?):(/+)", s, re.IGNORECASE)
    if proto_match:
        proto = proto_match.group(1).lower()
        rest = s[proto_match.end():]
        s = f"{proto}://{rest}"

    # mailto: handling
    if s.lower().startswith("mailto:"):
        addr = s[7:]
        # Remove all whitespace inside the address — mailto addresses cannot
        # contain literal spaces, so any whitespace is certainly a PDF
        # producer artifact rather than meaningful content.
        fixed_addr = re.sub(r"\s+", "", addr)
        s = "mailto:" + fixed_addr
        return s if s != original.strip() else None

    # http/https: strip whitespace from the domain (between protocol and first /)
    m = re.match(r"^(https?://)([^/]*)(.*)$", s, re.IGNORECASE)
    if m:
        scheme, domain, path = m.group(1), m.group(2), m.group(3)
        domain_fixed = re.sub(r"\s+", "", domain)
        # Path: remove whitespace that appears DIRECTLY after a slash or
        # between runs of non-whitespace characters. Spaces in query strings
        # are technically invalid too, so strip them as well.
        path_fixed = re.sub(r"\s+", "", path)
        s = scheme + domain_fixed + path_fixed

    return s if s != original.strip() else None


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

        # Broken URI: report separately from text issues. A link can have
        # fine display text AND a malformed URL (e.g. PDF producer inserted
        # whitespace into the target). We report this per-link regardless of
        # the text content below.
        repaired = repair_uri(link.url) if link.url else None
        if repaired and repaired != link.url:
            issues.append(LinkIssue(
                link_id=link.id,
                paragraph_id=link.paragraph_id,
                text=link.text,
                url=link.url,
                issue_type=LinkIssueType.BROKEN_URI,
                detail=f"Link URL is syntactically malformed: {link.url[:80]!r}",
            ))

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


@dataclass
class LinkResult:
    """Result of modifying a link in the document."""
    success: bool
    error: str = ""
    old_text: str = ""
    new_text: str = ""


def set_link_text(doc: Document, link_index: int, new_text: str) -> LinkResult:
    """Replace the display text of a hyperlink in a .docx document.

    Iterates all w:hyperlink elements in document body order (same order
    as docx_parser.py) and replaces text at the given index.

    Args:
        doc: An open python-docx Document.
        link_index: 0-based index of the hyperlink to modify.
        new_text: The new display text for the link.

    Returns:
        LinkResult with success/failure details.
    """
    if not new_text or not new_text.strip():
        return LinkResult(success=False, error="New text must not be empty")

    # Collect all hyperlinks in document body order
    hyperlinks = list(doc.element.body.iter(qn("w:hyperlink")))

    if link_index < 0 or link_index >= len(hyperlinks):
        return LinkResult(
            success=False,
            error=f"Link index {link_index} out of range (document has {len(hyperlinks)} hyperlinks)",
        )

    hyperlink = hyperlinks[link_index]

    # Get old text for reporting
    old_parts = []
    for r_elem in hyperlink.findall(qn("w:r")):
        for t_elem in r_elem.findall(qn("w:t")):
            if t_elem.text:
                old_parts.append(t_elem.text)
    old_text = "".join(old_parts)

    # Replace text: set first run's text, clear the rest
    runs = hyperlink.findall(qn("w:r"))
    if not runs:
        return LinkResult(success=False, error="Hyperlink has no runs")

    first_run_set = False
    for r_elem in runs:
        t_elems = r_elem.findall(qn("w:t"))
        for t_elem in t_elems:
            if not first_run_set:
                t_elem.text = new_text
                # Preserve spaces
                t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                first_run_set = True
            else:
                t_elem.text = ""

    if not first_run_set:
        return LinkResult(success=False, error="Hyperlink runs have no text elements")

    logger.info("Set link %d text: %r → %r", link_index, old_text, new_text)
    return LinkResult(success=True, old_text=old_text, new_text=new_text)
