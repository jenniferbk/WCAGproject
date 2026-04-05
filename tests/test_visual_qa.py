"""Tests for visual diff QA."""

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.pipeline import VisualQAFinding, VisualQAResult, ApiUsage


class TestVisualQAModels:
    def test_finding_creation(self):
        finding = VisualQAFinding(
            original_page=4,
            rendered_page=3,
            finding_type="missing_table",
            description="Table 2 appears truncated",
            severity="high",
        )
        assert finding.original_page == 4
        assert finding.rendered_page == 3
        assert finding.finding_type == "missing_table"
        assert finding.severity == "high"

    def test_finding_no_rendered_page(self):
        finding = VisualQAFinding(
            original_page=7,
            rendered_page=None,
            finding_type="dropped_image",
            description="Figure on page 8 not found in rendered output",
            severity="medium",
        )
        assert finding.rendered_page is None

    def test_result_defaults(self):
        result = VisualQAResult()
        assert result.findings == []
        assert result.pages_checked == 0
        assert result.api_usage == []

    def test_result_with_findings(self):
        finding = VisualQAFinding(
            original_page=0,
            rendered_page=0,
            finding_type="truncated_text",
            description="Paragraph cut off",
            severity="medium",
        )
        result = VisualQAResult(
            findings=[finding],
            pages_checked=5,
        )
        assert len(result.findings) == 1
        assert result.pages_checked == 5


from src.tools.visual_qa import render_original_pages, render_html_to_page_pngs


class TestRenderOriginalPages:
    def test_empty_page_list(self):
        result = render_original_pages("dummy.pdf", [])
        assert result == {}

    def test_renders_mock_pdf(self):
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b'\x89PNG\r\n\x1a\nfake_png_data'
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__len__ = MagicMock(return_value=10)

        with patch("src.tools.visual_qa.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc
            result = render_original_pages("test.pdf", [0, 2, 5])

        assert len(result) == 3
        assert 0 in result and 2 in result and 5 in result


class TestRenderHtmlToPagePngs:
    def test_renders_simple_html(self):
        html_content = '<!DOCTYPE html><html lang="en"><head><title>Test</title></head><body><h1>Test</h1><p>Hello world.</p></body></html>'
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write(html_content)
            html_path = f.name

        try:
            result = render_html_to_page_pngs(html_path)
            assert len(result) >= 1
            assert result[0][:4] == b'\x89PNG'
        finally:
            os.unlink(html_path)

    def test_nonexistent_html_returns_empty(self):
        result = render_html_to_page_pngs("/nonexistent/path.html")
        assert result == []
