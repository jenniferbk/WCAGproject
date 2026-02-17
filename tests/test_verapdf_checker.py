"""Tests for verapdf_checker: JSON parsing and result formatting.

Tests the parsing logic with sample veraPDF JSON output without
requiring veraPDF (Java) to be installed.
"""

from __future__ import annotations

import json

import pytest

from src.tools.verapdf_checker import (
    PdfUaViolation,
    VeraPdfResult,
    _parse_verapdf_json,
    _parse_validation_result,
    format_verapdf_report,
)


SAMPLE_COMPLIANT_JSON = json.dumps({
    "report": {
        "jobs": [{
            "validationResult": {
                "compliant": True,
                "details": {
                    "passedRules": 42,
                    "failedRules": 0,
                    "ruleSummaries": [],
                },
            },
        }],
    },
})

SAMPLE_NONCOMPLIANT_JSON = json.dumps({
    "report": {
        "jobs": [{
            "validationResult": {
                "compliant": False,
                "details": {
                    "passedRules": 38,
                    "failedRules": 4,
                    "ruleSummaries": [
                        {
                            "status": "failed",
                            "specification": "ISO_14289_1",
                            "clause": "6.1",
                            "testNumber": 1,
                            "description": "The document catalog dictionary shall include a MarkInfo dictionary",
                            "checks": [
                                {"status": "failed", "context": "root/0 CosDocument"},
                            ],
                        },
                        {
                            "status": "failed",
                            "specification": "ISO_14289_1",
                            "clause": "7.1",
                            "testNumber": 2,
                            "description": "Document shall be tagged",
                            "checks": [
                                {"status": "failed", "context": "root/0 PDDocument"},
                                {"status": "failed", "context": "root/0 PDPage[0]"},
                            ],
                        },
                        {
                            "status": "passed",
                            "specification": "ISO_14289_1",
                            "clause": "7.2",
                            "testNumber": 1,
                            "description": "Some passing rule",
                            "checks": [],
                        },
                    ],
                },
            },
        }],
    },
})

SAMPLE_OLDER_FORMAT_JSON = json.dumps({
    "validationResult": {
        "isCompliant": False,
        "assertions": [
            {
                "status": "failed",
                "ruleId": {
                    "specification": "ISO_14289_1",
                    "clause": "6.2",
                    "testNumber": 3,
                },
                "message": "Missing StructTreeRoot",
                "location": {"context": "root/CosDocument"},
            },
        ],
    },
})


class TestParseVerapdfJson:
    def test_compliant_pdf(self):
        result = _parse_verapdf_json(SAMPLE_COMPLIANT_JSON, "/test.pdf")
        assert result.success
        assert result.compliant
        assert result.violation_count == 0
        assert result.passed_rules == 42
        assert result.failed_rules == 0

    def test_noncompliant_pdf(self):
        result = _parse_verapdf_json(SAMPLE_NONCOMPLIANT_JSON, "/test.pdf")
        assert result.success
        assert not result.compliant
        assert result.violation_count == 3  # 1 from 6.1 + 2 from 7.1
        assert result.passed_rules == 38
        assert result.failed_rules == 4

    def test_violation_details(self):
        result = _parse_verapdf_json(SAMPLE_NONCOMPLIANT_JSON, "/test.pdf")
        violations = result.violations

        # First violation from clause 6.1
        v1 = violations[0]
        assert v1.rule_id == "6.1-1"
        assert v1.clause == "6.1"
        assert v1.test_number == 1
        assert "MarkInfo" in v1.description
        assert "CosDocument" in v1.context

        # Second and third from clause 7.1 (two failed checks)
        v2 = violations[1]
        assert v2.rule_id == "7.1-2"
        assert v2.clause == "7.1"

    def test_older_format(self):
        result = _parse_verapdf_json(SAMPLE_OLDER_FORMAT_JSON, "/old.pdf")
        assert result.success
        assert not result.compliant
        assert result.violation_count == 1
        assert result.violations[0].clause == "6.2"
        assert "StructTreeRoot" in result.violations[0].description

    def test_invalid_json(self):
        result = _parse_verapdf_json("not valid json", "/bad.pdf")
        assert not result.success
        assert "parse" in result.error.lower()

    def test_empty_json(self):
        result = _parse_verapdf_json("{}", "/empty.pdf")
        assert result.success
        assert result.compliant  # no failures found = compliant


class TestParseValidationResult:
    def test_all_passing(self):
        val = {
            "compliant": True,
            "details": {
                "passedRules": 10,
                "failedRules": 0,
                "ruleSummaries": [],
            },
        }
        compliant, passed, failed, violations = _parse_validation_result(val)
        assert compliant
        assert passed == 10
        assert failed == 0
        assert violations == []

    def test_with_failures(self):
        val = {
            "compliant": False,
            "details": {
                "passedRules": 5,
                "failedRules": 1,
                "ruleSummaries": [
                    {
                        "status": "failed",
                        "clause": "7.1",
                        "testNumber": 1,
                        "description": "Test rule",
                        "checks": [
                            {"status": "failed", "context": "test context"},
                        ],
                    },
                ],
            },
        }
        compliant, passed, failed, violations = _parse_validation_result(val)
        assert not compliant
        assert len(violations) == 1
        assert violations[0].context == "test context"


class TestVeraPdfResult:
    def test_success_result(self):
        result = VeraPdfResult(
            success=True,
            compliant=True,
            passed_rules=42,
            pdf_path="/test.pdf",
        )
        assert result.success
        assert result.compliant

    def test_error_result(self):
        result = VeraPdfResult(
            success=False,
            error="veraPDF not found",
            pdf_path="/test.pdf",
        )
        assert not result.success
        assert "not found" in result.error


class TestFormatVerapdfReport:
    def test_format_compliant(self):
        result = VeraPdfResult(
            success=True,
            compliant=True,
            passed_rules=42,
            failed_rules=0,
            violation_count=0,
            pdf_path="/test.pdf",
        )
        text = format_verapdf_report(result)
        assert "COMPLIANT" in text
        assert "Passed rules: 42" in text
        assert "Violations: 0" in text

    def test_format_noncompliant(self):
        result = VeraPdfResult(
            success=True,
            compliant=False,
            passed_rules=38,
            failed_rules=4,
            violations=[
                PdfUaViolation(
                    rule_id="6.1-1",
                    clause="6.1",
                    description="Missing MarkInfo",
                    context="root/CosDocument",
                ),
            ],
            violation_count=1,
            pdf_path="/test.pdf",
        )
        text = format_verapdf_report(result)
        assert "NON-COMPLIANT" in text
        assert "[6.1-1]" in text
        assert "Missing MarkInfo" in text

    def test_format_error(self):
        result = VeraPdfResult(
            success=False,
            error="timeout",
            pdf_path="/test.pdf",
        )
        text = format_verapdf_report(result)
        assert "failed" in text
        assert "timeout" in text
