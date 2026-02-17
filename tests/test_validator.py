"""Tests for the WCAG validator."""

from pathlib import Path

import pytest

from src.models.document import (
    DocumentModel,
    DocumentStats,
    ImageInfo,
    LinkInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
)
from src.tools.docx_parser import parse_docx
from src.tools.validator import CheckStatus, format_report, validate_document


class TestValidateDocument:
    def test_simple_doc_passes_some(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        report = validate_document(result.document)
        assert report.total_checks == 7

        # simple_docx has title and language — those should pass
        title_check = next(c for c in report.checks if c.criterion == "2.4.2")
        assert title_check.status == CheckStatus.PASS

        lang_check = next(c for c in report.checks if c.criterion == "3.1.1")
        assert lang_check.status == CheckStatus.PASS

    def test_no_metadata_fails(self, no_metadata_docx: Path):
        result = parse_docx(no_metadata_docx)
        report = validate_document(result.document)

        title_check = next(c for c in report.checks if c.criterion == "2.4.2")
        assert title_check.status == CheckStatus.FAIL

    def test_missing_alt_text_fails(self, image_no_alt_docx: Path):
        result = parse_docx(image_no_alt_docx)
        report = validate_document(result.document)

        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.FAIL
        assert alt_check.issue_count >= 1

    def test_with_alt_text_passes(self, image_docx: Path):
        result = parse_docx(image_docx)
        report = validate_document(result.document)

        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.PASS

    def test_no_images_na(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        report = validate_document(result.document)

        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.NOT_APPLICABLE

    def test_skipped_headings_flagged(self, skipped_headings_docx: Path):
        result = parse_docx(skipped_headings_docx)
        report = validate_document(result.document)

        structure_check = next(c for c in report.checks if c.criterion == "1.3.1")
        assert structure_check.status == CheckStatus.FAIL

    def test_overall_status(self, no_metadata_docx: Path):
        result = parse_docx(no_metadata_docx)
        report = validate_document(result.document)
        assert report.overall_status == CheckStatus.FAIL
        assert report.failed >= 1

    def test_contrast_check(self, contrast_docx: Path):
        result = parse_docx(contrast_docx)
        report = validate_document(result.document)

        contrast_check = next(c for c in report.checks if c.criterion == "1.4.3")
        # The light gray text (#AAAAAA on white) should fail
        assert contrast_check.status == CheckStatus.FAIL
        assert contrast_check.issue_count >= 1


class TestValidateCleanDocument:
    """Test a document that should pass everything."""

    def test_fully_compliant(self):
        doc = DocumentModel(
            source_format="docx",
            source_path="clean.docx",
            metadata=MetadataInfo(title="Clean Doc", language="en"),
            paragraphs=[
                ParagraphInfo(id="p_0", text="Main Title", style_name="Heading 1", heading_level=1),
                ParagraphInfo(id="p_1", text="Body text here.", runs=[
                    RunInfo(text="Body text here.", color="#000000", font_size_pt=12.0),
                ]),
            ],
            stats=DocumentStats(paragraph_count=2, heading_count=1),
        )
        report = validate_document(doc)
        assert report.overall_status == CheckStatus.PASS
        assert report.failed == 0


class TestFormatReport:
    def test_format_output(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        report = validate_document(result.document)
        text = format_report(report)

        assert "WCAG 2.1 AA Validation Report" in text
        assert "1.1.1" in text
        assert "2.4.2" in text
        assert "[PASS]" in text or "[FAIL]" in text or "[WARN]" in text

    def test_format_includes_issues(self, no_metadata_docx: Path):
        result = parse_docx(no_metadata_docx)
        report = validate_document(result.document)
        text = format_report(report)

        assert "[FAIL]" in text
        assert "no title" in text.lower()
