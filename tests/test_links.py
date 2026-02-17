"""Tests for link analysis."""

import pytest

from src.models.document import LinkInfo
from src.tools.links import LinkIssueType, analyze_links


class TestAnalyzeLinks:
    def test_empty_link_text(self):
        links = [LinkInfo(id="link_0", text="", url="https://example.com", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 1
        assert result.issues[0].issue_type == LinkIssueType.EMPTY_TEXT

    def test_bare_url(self):
        links = [LinkInfo(id="link_0", text="https://example.com/page", url="https://example.com/page", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 1
        assert result.issues[0].issue_type == LinkIssueType.BARE_URL

    def test_vague_click_here(self):
        links = [LinkInfo(id="link_0", text="click here", url="https://example.com", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 1
        assert result.issues[0].issue_type == LinkIssueType.VAGUE_TEXT

    def test_vague_read_more(self):
        links = [LinkInfo(id="link_0", text="Read More", url="https://example.com", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 1
        assert result.issues[0].issue_type == LinkIssueType.VAGUE_TEXT

    def test_vague_here(self):
        links = [LinkInfo(id="link_0", text="here", url="https://example.com", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 1

    def test_good_link_text(self):
        links = [LinkInfo(id="link_0", text="Course Syllabus PDF", url="https://example.com/syllabus.pdf", paragraph_id="p_0")]
        result = analyze_links(links)
        assert result.issue_count == 0

    def test_same_text_different_urls(self):
        links = [
            LinkInfo(id="link_0", text="Download", url="https://a.com/file1", paragraph_id="p_0"),
            LinkInfo(id="link_1", text="Download", url="https://b.com/file2", paragraph_id="p_1"),
        ]
        result = analyze_links(links)
        # "Download" is a vague word, so it'll trigger vague_text
        # But also check for same-text-different-url if it passes vague check
        assert result.issue_count >= 2

    def test_same_text_same_url_ok(self):
        links = [
            LinkInfo(id="link_0", text="Course Website", url="https://example.com", paragraph_id="p_0"),
            LinkInfo(id="link_1", text="Course Website", url="https://example.com", paragraph_id="p_1"),
        ]
        result = analyze_links(links)
        assert result.issue_count == 0

    def test_no_links(self):
        result = analyze_links([])
        assert result.total_links == 0
        assert result.issue_count == 0

    def test_multiple_issues(self):
        links = [
            LinkInfo(id="link_0", text="click here", url="https://a.com", paragraph_id="p_0"),
            LinkInfo(id="link_1", text="", url="https://b.com", paragraph_id="p_1"),
            LinkInfo(id="link_2", text="https://c.com", url="https://c.com", paragraph_id="p_2"),
            LinkInfo(id="link_3", text="Assignment Guidelines", url="https://d.com", paragraph_id="p_3"),
        ]
        result = analyze_links(links)
        assert result.total_links == 4
        assert result.issue_count == 3  # the 4th link is fine
