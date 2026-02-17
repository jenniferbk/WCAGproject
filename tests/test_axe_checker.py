"""Tests for axe_checker: WCAG criteria extraction and result formatting.

These tests validate the parsing/formatting logic without requiring
Playwright or axe-core to be installed. Integration tests with real
browsers are skipped if Playwright is not available.
"""

from __future__ import annotations

import pytest

from src.tools.axe_checker import (
    AxeCheckResult,
    AxeViolation,
    _extract_wcag_criteria,
    format_axe_report,
)


class TestExtractWcagCriteria:
    def test_single_criterion(self):
        assert _extract_wcag_criteria(["wcag111"]) == ["1.1.1"]

    def test_multiple_criteria(self):
        tags = ["wcag111", "wcag143", "wcag242"]
        result = _extract_wcag_criteria(tags)
        assert "1.1.1" in result
        assert "1.4.3" in result
        assert "2.4.2" in result

    def test_non_wcag_tags_ignored(self):
        tags = ["best-practice", "wcag2aa", "cat.color"]
        result = _extract_wcag_criteria(tags)
        assert result == []

    def test_mixed_tags(self):
        tags = ["wcag111", "best-practice", "wcag143", "cat.forms"]
        result = _extract_wcag_criteria(tags)
        assert result == ["1.1.1", "1.4.3"]

    def test_empty_tags(self):
        assert _extract_wcag_criteria([]) == []

    def test_wcag_without_digits(self):
        assert _extract_wcag_criteria(["wcag"]) == []
        assert _extract_wcag_criteria(["wcag2aa"]) == []


class TestAxeCheckResult:
    def test_success_result(self):
        result = AxeCheckResult(
            success=True,
            violations=[
                AxeViolation(
                    rule_id="image-alt",
                    impact="critical",
                    description="Images must have alt text",
                    help_text="Ensure every image has an alt attribute",
                    help_url="https://dequeuniversity.com/rules/axe/4.7/image-alt",
                    wcag_criteria=["1.1.1"],
                    affected_elements=["<img src='test.png'>"],
                    node_count=1,
                ),
            ],
            passes_count=15,
            incomplete_count=2,
            inapplicable_count=5,
            violation_count=1,
        )
        assert result.success
        assert result.violation_count == 1
        assert result.passes_count == 15

    def test_failure_result(self):
        result = AxeCheckResult(
            success=False,
            error="playwright not installed",
        )
        assert not result.success
        assert "playwright" in result.error

    def test_empty_result(self):
        result = AxeCheckResult(success=True)
        assert result.violation_count == 0
        assert result.violations == []


class TestFormatAxeReport:
    def test_format_success(self):
        result = AxeCheckResult(
            success=True,
            violations=[
                AxeViolation(
                    rule_id="color-contrast",
                    impact="serious",
                    description="Elements must have sufficient color contrast",
                    help_text="Ensure the contrast ratio is at least 4.5:1",
                    help_url="https://example.com",
                    wcag_criteria=["1.4.3"],
                    affected_elements=["<p style='color: gray'>text</p>"],
                    node_count=3,
                ),
            ],
            passes_count=10,
            violation_count=1,
        )
        text = format_axe_report(result)
        assert "Violations: 1" in text
        assert "Passes: 10" in text
        assert "[SERIOUS]" in text
        assert "color-contrast" in text
        assert "WCAG 1.4.3" in text
        assert "3 element(s)" in text

    def test_format_error(self):
        result = AxeCheckResult(success=False, error="browser crashed")
        text = format_axe_report(result)
        assert "failed" in text
        assert "browser crashed" in text

    def test_format_clean(self):
        result = AxeCheckResult(success=True, passes_count=20)
        text = format_axe_report(result)
        assert "Violations: 0" in text
        assert "Passes: 20" in text


class TestCheckHtmlAccessibility:
    """Test the actual check function — skipped if playwright not installed."""

    @pytest.fixture
    def _has_playwright(self):
        try:
            import playwright  # noqa: F401
            from axe_playwright_python.sync_playwright import Axe  # noqa: F401
            return True
        except ImportError:
            pytest.skip("playwright/axe-playwright-python not installed")

    def test_missing_playwright_returns_error(self):
        """When playwright is not installed, should return error not raise."""
        from src.tools.axe_checker import check_html_accessibility

        result = check_html_accessibility("<html></html>")
        # If playwright IS installed, this will succeed
        # If not, it returns an error result
        assert isinstance(result, AxeCheckResult)
        if not result.success:
            assert "not installed" in result.error
