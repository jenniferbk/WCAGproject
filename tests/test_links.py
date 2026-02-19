"""Tests for link analysis and link text modification."""

import shutil
from pathlib import Path

import pytest
from docx import Document

from src.models.document import LinkInfo
from src.tools.links import LinkIssueType, LinkResult, analyze_links, set_link_text


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


# ── set_link_text tests ────────────────────────────────────────────────

TESTDOCS = Path(__file__).parent.parent / "testdocs"
DOCX_WITH_LINKS = TESTDOCS / "Wesley Olivia Day 4.docx"


class TestSetLinkText:
    """Tests for set_link_text() on .docx documents."""

    @pytest.fixture
    def doc_with_links(self, tmp_path):
        if not DOCX_WITH_LINKS.exists():
            pytest.skip("Test docx with links not found")
        dest = tmp_path / "links_test.docx"
        shutil.copy2(DOCX_WITH_LINKS, dest)
        return Document(str(dest)), dest

    def test_set_link_text_success(self, doc_with_links):
        doc, _ = doc_with_links
        result = set_link_text(doc, 0, "Descriptive Link Text")
        assert result.success
        assert result.new_text == "Descriptive Link Text"
        assert result.old_text  # should have had some text before

    def test_set_link_text_persists_after_save(self, doc_with_links):
        doc, path = doc_with_links
        set_link_text(doc, 0, "Updated Link")
        doc.save(str(path))

        # Re-open and verify
        doc2 = Document(str(path))
        from docx.oxml.ns import qn
        hyperlinks = list(doc2.element.body.iter(qn("w:hyperlink")))
        first_hl = hyperlinks[0]
        texts = []
        for r in first_hl.findall(qn("w:r")):
            for t in r.findall(qn("w:t")):
                if t.text:
                    texts.append(t.text)
        assert "Updated Link" in "".join(texts)

    def test_set_link_text_index_out_of_range(self, doc_with_links):
        doc, _ = doc_with_links
        result = set_link_text(doc, 9999, "Some Text")
        assert not result.success
        assert "out of range" in result.error

    def test_set_link_text_negative_index(self, doc_with_links):
        doc, _ = doc_with_links
        result = set_link_text(doc, -1, "Some Text")
        assert not result.success
        assert "out of range" in result.error

    def test_set_link_text_empty_text(self, doc_with_links):
        doc, _ = doc_with_links
        result = set_link_text(doc, 0, "")
        assert not result.success
        assert "empty" in result.error.lower()

    def test_set_link_text_whitespace_only(self, doc_with_links):
        doc, _ = doc_with_links
        result = set_link_text(doc, 0, "   ")
        assert not result.success
        assert "empty" in result.error.lower()
