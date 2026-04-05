"""Tests for visual diff QA."""

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
