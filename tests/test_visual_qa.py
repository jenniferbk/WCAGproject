"""Tests for visual diff QA."""

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.pipeline import VisualQAFinding, VisualQAResult, ApiUsage
from src.tools.report_generator import _build_visual_qa_section


class TestBuildVisualQASection:
    def test_returns_empty_for_no_findings(self):
        result = _build_visual_qa_section([], "")
        assert result == ""

    def test_returns_empty_for_low_only(self):
        findings = [
            VisualQAFinding(
                original_page=0, rendered_page=0,
                finding_type="other", description="Minor", severity="low",
            ),
        ]
        result = _build_visual_qa_section(findings, "")
        assert result == ""

    def test_renders_high_findings(self):
        findings = [
            VisualQAFinding(
                original_page=4, rendered_page=3,
                finding_type="missing_table",
                description="Table 2 is missing from rendered output",
                severity="high",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        assert "Visual Quality Check" in result
        assert "Table 2 is missing" in result
        assert "Page 5" in result  # 0-based -> 1-based display

    def test_renders_medium_findings(self):
        findings = [
            VisualQAFinding(
                original_page=1, rendered_page=1,
                finding_type="truncated_text",
                description="Paragraph appears cut off",
                severity="medium",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        assert "Visual Quality Check" in result
        assert "Paragraph appears cut off" in result

    def test_summary_line(self):
        findings = [
            VisualQAFinding(
                original_page=0, rendered_page=0,
                finding_type="missing_table", description="A", severity="high",
            ),
            VisualQAFinding(
                original_page=2, rendered_page=1,
                finding_type="truncated_text", description="B", severity="medium",
            ),
            VisualQAFinding(
                original_page=3, rendered_page=2,
                finding_type="other", description="C", severity="low",
            ),
        ]
        result = _build_visual_qa_section(findings, "/tmp/output")
        # Should show 2 high/medium findings (low filtered out from display)
        assert "2 content issue" in result


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


from src.tools.visual_qa import render_original_pages, render_html_to_page_pngs, compare_pages, run_visual_qa


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


class TestComparePages:
    def test_returns_findings_on_issues(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "findings": [
                {
                    "original_page": 5,
                    "rendered_page": 4,
                    "type": "missing_table",
                    "description": "Table 2 not found in rendered output",
                    "severity": "high",
                }
            ]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        original_pngs = {4: b"fake_png_page5"}
        rendered_pngs = [b"fake_png_rendered1", b"fake_png_rendered2"]

        findings, usage = compare_pages(
            original_pngs, rendered_pngs, mock_client, "gemini-2.5-flash",
        )

        assert len(findings) == 1
        assert findings[0].original_page == 4  # converted from 1-based to 0-based
        assert findings[0].rendered_page == 3  # converted from 1-based to 0-based
        assert findings[0].finding_type == "missing_table"
        assert findings[0].severity == "high"

    def test_returns_empty_on_no_issues(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"findings": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []

    def test_handles_gemini_exception(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []
        assert usage is None

    def test_handles_none_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        findings, usage = compare_pages(
            {0: b"fake"}, [b"fake"], mock_client, "gemini-2.5-flash",
        )
        assert findings == []


class TestRunVisualQA:
    def test_full_flow_with_mocks(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "findings": [{
                "original_page": 3,
                "rendered_page": 2,
                "type": "truncated_text",
                "description": "Paragraph cut off at bottom of page",
                "severity": "medium",
            }]
        })
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.tools.visual_qa.render_original_pages") as mock_render_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_render_html:

            mock_render_orig.return_value = {
                0: b"fake_page_1",
                1: b"fake_page_2",
                2: b"fake_page_3",
            }
            mock_render_html.return_value = [b"fake_rendered_1", b"fake_rendered_2"]

            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0, 1, 2],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

                assert result.pages_checked == 3
                assert len(result.findings) == 1
                assert result.findings[0].finding_type == "truncated_text"

                qa_dir = Path(tmpdir) / "visual_qa"
                assert qa_dir.exists()

    def test_skips_when_no_rendered_pages(self):
        with patch("src.tools.visual_qa.render_original_pages") as mock_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_html:
            mock_orig.return_value = {0: b"fake"}
            mock_html.return_value = []

            mock_client = MagicMock()

            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

            assert result.pages_checked == 0
            assert result.findings == []
            mock_client.models.generate_content.assert_not_called()

    def test_saves_findings_json(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"findings": []}'
        mock_response.usage_metadata = None
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.tools.visual_qa.render_original_pages") as mock_orig, \
             patch("src.tools.visual_qa.render_html_to_page_pngs") as mock_html:
            mock_orig.return_value = {0: b"fake"}
            mock_html.return_value = [b"fake"]

            with tempfile.TemporaryDirectory() as tmpdir:
                run_visual_qa(
                    pdf_path="test.pdf",
                    html_path="test.html",
                    scanned_page_numbers=[0],
                    client=mock_client,
                    model="gemini-2.5-flash",
                    output_dir=tmpdir,
                )

                findings_path = Path(tmpdir) / "visual_qa_findings.json"
                assert findings_path.exists()
                data = json.loads(findings_path.read_text())
                assert "findings" in data
                assert "pages_checked" in data
