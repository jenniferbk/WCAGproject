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
