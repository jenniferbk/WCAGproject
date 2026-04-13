"""Tests for complete struct tree tagging."""

import pytest
import shutil
from pathlib import Path

import fitz

from src.tools.pdf_writer import (
    Token,
    _tokenize_content_stream,
)

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"


class TestGetMaxMcidForPage:
    """Per-page MCID scanning from content streams."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_no_bdcs_returns_negative_one(self):
        """Page with no BDC markers has no MCIDs."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("BT (hello) Tj ET")
        assert _get_max_mcid_for_page(tokens) == -1

    def test_single_mcid(self):
        """One BDC with MCID 0."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("/P <</MCID 0>> BDC BT (hi) Tj ET EMC")
        assert _get_max_mcid_for_page(tokens) == 0

    def test_multiple_mcids_returns_max(self):
        """Multiple BDCs — return the highest MCID."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = (
            "/P <</MCID 0>> BDC BT (a) Tj ET EMC\n"
            "/P <</MCID 3>> BDC BT (b) Tj ET EMC\n"
            "/H1 <</MCID 1>> BDC BT (c) Tj ET EMC\n"
        )
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == 3

    def test_artifact_bdc_ignored(self):
        """/Artifact BDC has no MCID — should be ignored."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = "/Artifact <</Type /Pagination>> BDC BT (1) Tj ET EMC"
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == -1


class TestApplyContentTagWrappers:
    """Tests for _apply_content_tag_wrappers() — mixed /P and /Artifact wrapping."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_single_p_run(self):
        """One run tagged as /P with MCID 0."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (hello) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=0)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 0>> BDC" in text
        assert "EMC" in text
        assert "/Artifact" not in text

    def test_single_artifact_run(self):
        """One run tagged as /Artifact."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (3) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/Artifact", mcid=None)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/Artifact <</Type /Pagination>> BDC" in text
        assert "EMC" in text
        assert "/MCID" not in text

    def test_mixed_p_and_artifact(self):
        """Two runs — first /P, second /Artifact, separated by existing tagged content."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        # Existing tagged heading between two untagged runs forces them to be separate
        stream = (
            "BT (body text) Tj ET "
            "/H1 <</MCID 0>> BDC BT (heading) Tj ET EMC "
            "BT (3) Tj ET"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 2
        tagged_runs = [
            TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=1),
            TaggedRun(start=runs[1][0], end=runs[1][1], tag_type="/Artifact", mcid=None),
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 1>> BDC" in text
        assert "/Artifact <</Type /Pagination>> BDC" in text

    def test_no_runs_returns_original(self):
        """Empty run list returns original stream."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers,
        )
        tokens = self._tokenize("BT (x) Tj ET")
        result = _apply_content_tag_wrappers(tokens, [])
        text = result.decode("latin-1")
        assert "BDC" not in text

    def test_unique_mcids_per_run(self):
        """Each /P run gets its own MCID."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        # Three untagged runs separated by existing tagged headings
        stream = (
            "BT (a) Tj ET "
            "/H1 <</MCID 0>> BDC BT (h1) Tj ET EMC "
            "BT (b) Tj ET "
            "/H2 <</MCID 1>> BDC BT (h2) Tj ET EMC "
            "BT (c) Tj ET"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 3
        tagged_runs = [
            TaggedRun(start=r[0], end=r[1], tag_type="/P", mcid=i + 2)
            for i, r in enumerate(runs)
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 2>> BDC" in text
        assert "/P <</MCID 3>> BDC" in text
        assert "/P <</MCID 4>> BDC" in text


class TestExtractTextFromRun:
    """Tests for extracting text from token runs."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_simple_tj(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT (Hello world) Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello world" in text

    def test_tj_array(self):
        """TJ operator with array of strings and kerning."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT [(He) -10 (llo)] TJ ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text

    def test_no_text_ops_returns_empty(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("/Fm0 Do")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert text == ""

    def test_hex_string(self):
        """Hex-encoded string <48656C6C6F> = 'Hello'."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT <48656C6C6F> Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text


class TestIsPageFurniture:
    """Tests for page furniture detection."""

    def test_bare_page_number(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("3", set()) is True

    def test_page_number_with_dashes(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("- 3 -", set()) is True

    def test_roman_numeral(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("iv", set()) is True
        assert _is_page_furniture("XII", set()) is True

    def test_body_text_not_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture(
            "The results of this study demonstrate that",
            set(),
        ) is False

    def test_repeated_header(self):
        from src.tools.pdf_writer import _is_page_furniture
        furniture = {"Chapter 3: Methods"}
        assert _is_page_furniture("Chapter 3: Methods", furniture) is True

    def test_empty_string_is_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("", set()) is True
        assert _is_page_furniture("   ", set()) is True
