"""Tests for link analysis and link text modification."""

import shutil
from pathlib import Path

import pytest
from docx import Document

from src.models.document import LinkInfo
from src.tools.links import (
    LinkIssueType,
    LinkResult,
    analyze_links,
    repair_uri,
    set_link_text,
)


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


class TestRepairUri:
    def test_quadruple_slash(self):
        assert repair_uri("http:////dx.doi.org/10.1119/1.10903") == \
            "http://dx.doi.org/10.1119/1.10903"

    def test_triple_slash(self):
        assert repair_uri("http:///dx.doi.org/10.1103/PhysRevB.88.184404") == \
            "http://dx.doi.org/10.1103/PhysRevB.88.184404"

    def test_single_slash_protocol(self):
        assert repair_uri("http:/dx.doi.org/10.1021/acs") == \
            "http://dx.doi.org/10.1021/acs"

    def test_whitespace_in_domain(self):
        assert repair_uri("http://d x.doi.org/10.1103/PhysRevE.77") == \
            "http://dx.doi.org/10.1103/PhysRevE.77"

    def test_whitespace_after_path_slash(self):
        assert repair_uri("http://dx.doi.org/ 10.1103/PhysRevLett.118.147201") == \
            "http://dx.doi.org/10.1103/PhysRevLett.118.147201"

    def test_multiple_whitespace_throughout(self):
        assert repair_uri("http://d x.do. i.org /{10.1063/. 1.3695642}") == \
            "http://dx.do.i.org/{10.1063/.1.3695642}"

    def test_mailto_whitespace(self):
        assert repair_uri("mailto: kessler@ dave.ph.biu.ac.il") == \
            "mailto:kessler@dave.ph.biu.ac.il"

    def test_mailto_clean_returns_none(self):
        assert repair_uri("mailto:clean@example.com") is None

    def test_clean_http_returns_none(self):
        assert repair_uri("http://dx.doi.org/10.1000/clean") is None

    def test_empty_returns_none(self):
        assert repair_uri("") is None

    def test_none_input_returns_none(self):
        # Defensive: should not raise even if caller passes None-ish
        assert repair_uri(None) is None  # type: ignore[arg-type]

    def test_https_whitespace_in_domain(self):
        assert repair_uri("https://ex ample.com/path") == "https://example.com/path"


class TestAnalyzeLinksBrokenUri:
    def test_broken_uri_detected(self):
        links = [LinkInfo(
            id="link_0",
            text="See the paper",
            url="http:////dx.doi.org/10.1119/1.10903",
            paragraph_id="p_0",
        )]
        result = analyze_links(links)
        types = {i.issue_type for i in result.issues}
        assert LinkIssueType.BROKEN_URI in types

    def test_broken_uri_and_bare_text_coexist(self):
        # A single link can have both a broken URL AND bad display text.
        # Both issues should be reported.
        links = [LinkInfo(
            id="link_0",
            text="http:////dx.doi.org/10.1119/1.10903",
            url="http:////dx.doi.org/10.1119/1.10903",
            paragraph_id="p_0",
        )]
        result = analyze_links(links)
        types = {i.issue_type for i in result.issues}
        assert LinkIssueType.BROKEN_URI in types
        assert LinkIssueType.BARE_URL in types

    def test_clean_uri_not_flagged(self):
        links = [LinkInfo(
            id="link_0",
            text="Official paper",
            url="https://doi.org/10.1000/xyz",
            paragraph_id="p_0",
        )]
        result = analyze_links(links)
        types = {i.issue_type for i in result.issues}
        assert LinkIssueType.BROKEN_URI not in types


class TestRepairBrokenUrisInPdf:
    def test_repair_round_trip_on_benchmark_pdf(self, tmp_path):
        """Repair broken URIs in a real benchmark PDF and verify persistence."""
        import fitz
        from src.tools.pdf_writer import repair_broken_uris_in_pdf

        src = Path(
            "/tmp/PDF-Accessibility-Benchmark/data/processed/"
            "functional_hyperlinks/failed/W2893185172.pdf"
        )
        if not src.exists():
            pytest.skip("Benchmark PDF not available")

        dst = tmp_path / "W2893185172_test.pdf"
        shutil.copy(src, dst)

        # Count broken URIs before
        doc = fitz.open(str(dst))
        before_broken = 0
        for page in doc:
            for link in page.links():
                u = link.get("uri", "") or ""
                if not u:
                    continue
                if " " in u or ("http:/" in u and not u.startswith("http://")) or "http:///" in u:
                    before_broken += 1
        doc.close()
        assert before_broken >= 1, "expected at least one broken URI in source PDF"

        # Repair
        n_fixed, repairs = repair_broken_uris_in_pdf(dst)
        assert n_fixed >= 1
        assert len(repairs) == n_fixed
        for before, after in repairs:
            assert before != after

        # Re-open and verify no broken URIs remain
        doc = fitz.open(str(dst))
        after_broken = 0
        for page in doc:
            for link in page.links():
                u = link.get("uri", "") or ""
                if not u:
                    continue
                if " " in u or ("http:/" in u and not u.startswith("http://")) or "http:///" in u:
                    after_broken += 1
        doc.close()
        assert after_broken == 0, f"expected 0 broken URIs after repair, got {after_broken}"

    def test_no_op_on_clean_pdf(self, tmp_path):
        """Running repair on a PDF with no broken URIs should be a no-op."""
        import fitz
        from src.tools.pdf_writer import repair_broken_uris_in_pdf

        # Create a minimal PDF with one clean link
        src = tmp_path / "clean.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Clean link test")
        page.insert_link({
            "kind": 2,  # LINK_URI
            "from": fitz.Rect(72, 72, 200, 90),
            "uri": "https://example.com/clean",
        })
        doc.save(str(src))
        doc.close()

        n_fixed, repairs = repair_broken_uris_in_pdf(src)
        assert n_fixed == 0
        assert repairs == []
