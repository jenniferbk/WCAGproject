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
from src.tools.html_builder import build_html
from src.tools.validator import (
    CheckStatus,
    MultiLayerReport,
    format_multi_layer_report,
    format_report,
    score_alt_text_quality,
    validate_document,
    validate_full,
)


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


class TestValidateFull:
    """Test the multi-layer validate_full function."""

    def test_docx_only(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        report = validate_full(result.document)
        assert isinstance(report, MultiLayerReport)
        assert report.docx_report is not None
        assert report.axe_report is None  # no HTML provided
        assert report.verapdf_report is None  # no PDF provided
        assert "Docx:" in report.summary

    def test_with_html(self, simple_docx: Path):
        parse_result = parse_docx(simple_docx)
        html_result = build_html(parse_result.document)
        assert html_result.success

        report = validate_full(parse_result.document, html_string=html_result.html)
        assert report.docx_report is not None
        # axe_report may be None if playwright not installed, or populated if it is
        assert "Docx:" in report.summary

    def test_format_multi_layer(self, simple_docx: Path):
        result = parse_docx(simple_docx)
        report = validate_full(result.document)
        text = format_multi_layer_report(report)
        assert "Multi-Layer" in text
        assert "Layer 1" in text


class TestAltTextQualityScoring:
    """Ported from scripts/benchmark.py — same heuristic, now in production."""

    def test_empty_is_bad(self):
        assert score_alt_text_quality("") == "bad"
        assert score_alt_text_quality("   ") == "bad"

    def test_too_short_is_bad(self):
        assert score_alt_text_quality("Figure 3") == "bad"
        assert score_alt_text_quality("A chart") == "bad"

    def test_stub_label_is_bad(self):
        assert score_alt_text_quality("Figure 3 - a chart") == "bad"
        assert score_alt_text_quality("Image of something") == "bad"
        assert score_alt_text_quality("Screenshot A") == "bad"

    def test_auto_generated_is_bad(self):
        assert score_alt_text_quality(
            "A close up of text on a white background Description automatically generated"
        ) == "bad"

    def test_filename_is_bad(self):
        assert score_alt_text_quality("figure3.png") == "bad"
        assert score_alt_text_quality("my_graph.jpg") == "bad"

    def test_meta_only_short_is_borderline(self):
        # Meta-phrase opening + short (< 12 words) → borderline
        assert score_alt_text_quality(
            "This is an image of a student at the whiteboard"
        ) == "borderline"
        # 5-7 word generic description without stub-label opener → borderline via fallback
        assert score_alt_text_quality(
            "Students sitting together around tables"
        ) == "borderline"

    def test_substantive_short_description_is_good(self):
        # 8+ words without meta-phrase opening
        assert score_alt_text_quality(
            "Bar chart comparing test scores across three grade levels."
        ) == "good"

    def test_long_description_is_good_even_with_meta_start(self):
        # 15+ words — meta-phrase opening doesn't matter
        assert score_alt_text_quality(
            "This is an image showing three students working at a whiteboard "
            "solving quadratic equations, with the teacher standing beside them."
        ) == "good"

    def test_null_bytes_stripped(self):
        # PDF alt text sometimes has trailing null escapes
        assert score_alt_text_quality(
            "Bar chart comparing test scores across three grade levels.\x00"
        ) == "good"


class TestSevereContrastFlag:
    """Port 4: yellow-on-white is surfaced as SEVERE, not buried among ratio issues."""

    def test_yellow_on_white_is_severe(self):
        from src.tools.contrast import is_severe_contrast_failure
        # Pure yellow on white = 1.07:1 — archetypal severe failure
        assert is_severe_contrast_failure("#FFFF00", "#FFFFFF", 1.07)
        assert is_severe_contrast_failure("#FFEE00", "#FFFFFF", 1.15)

    def test_normal_low_contrast_is_not_severe(self):
        from src.tools.contrast import is_severe_contrast_failure
        # Light gray on white fails WCAG but is not the "severe" pattern
        assert not is_severe_contrast_failure("#AAAAAA", "#FFFFFF", 2.85)
        # Black text on off-white — passes, definitely not severe
        assert not is_severe_contrast_failure("#000000", "#FAFAFA", 20.4)

    def test_yellow_but_adequate_ratio_is_not_severe(self):
        from src.tools.contrast import is_severe_contrast_failure
        # Yellow-ish on dark grey has enough ratio — our flag triggers only
        # when ratio < 1.5, so this should not count
        assert not is_severe_contrast_failure("#FFFF00", "#333333", 14.0)

    def test_validator_surfaces_severe_prefix(self):
        # Paragraph with a yellow-on-white run should produce a SEVERE-prefixed issue
        doc = DocumentModel(
            source_format="docx",
            source_path="x.docx",
            metadata=MetadataInfo(title="T", language="en"),
            paragraphs=[ParagraphInfo(
                id="p_0", text="Low-contrast example",
                runs=[RunInfo(
                    text="Low-contrast example",
                    color="#FFFF00", font_size_pt=12.0,
                )],
            )],
            stats=DocumentStats(paragraph_count=1),
        )
        report = validate_document(doc, default_bg="#FFFFFF")
        contrast_check = next(c for c in report.checks if c.criterion == "1.4.3")
        assert contrast_check.status == CheckStatus.FAIL
        joined = "\n".join(contrast_check.issues)
        assert "SEVERE" in joined
        assert "severe contrast failure" in joined.lower()


class TestAltTextValidatorIntegration:
    """_check_1_1_1_alt_text now distinguishes missing/bad/borderline/good."""

    def _doc_with_alt(self, alt: str, decorative: bool = False) -> DocumentModel:
        return DocumentModel(
            source_format="docx",
            source_path="test.docx",
            metadata=MetadataInfo(title="T", language="en"),
            paragraphs=[ParagraphInfo(id="p_0", text="body", image_ids=["img_0"])],
            images=[ImageInfo(
                id="img_0", content_type="image/png", paragraph_id="p_0",
                alt_text=alt, is_decorative=decorative,
            )],
            stats=DocumentStats(paragraph_count=1, image_count=1),
        )

    def test_good_alt_passes(self):
        doc = self._doc_with_alt(
            "Bar chart comparing test scores across three grade levels."
        )
        report = validate_document(doc)
        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.PASS

    def test_bad_stub_fails(self):
        doc = self._doc_with_alt("Figure 3")
        report = validate_document(doc)
        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.FAIL
        assert "low-quality" in " ".join(alt_check.issues)

    def test_borderline_meta_warns(self):
        doc = self._doc_with_alt("This is an image of a student")
        report = validate_document(doc)
        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.WARN
        assert "borderline" in " ".join(alt_check.issues)

    def test_decorative_skipped(self):
        doc = self._doc_with_alt("", decorative=True)
        report = validate_document(doc)
        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        # Decorative image with empty alt is fine
        assert alt_check.status == CheckStatus.PASS

    def test_fail_dominates_warn(self):
        # If any image is missing/bad, status is FAIL even if others are borderline
        doc = DocumentModel(
            source_format="docx",
            source_path="test.docx",
            metadata=MetadataInfo(title="T", language="en"),
            paragraphs=[ParagraphInfo(id="p_0", text="body", image_ids=["img_0", "img_1"])],
            images=[
                ImageInfo(id="img_0", content_type="image/png", paragraph_id="p_0",
                          alt_text="This is an image of the thing"),  # borderline
                ImageInfo(id="img_1", content_type="image/png", paragraph_id="p_0",
                          alt_text="Figure 4"),  # bad
            ],
            stats=DocumentStats(paragraph_count=1, image_count=2),
        )
        report = validate_document(doc)
        alt_check = next(c for c in report.checks if c.criterion == "1.1.1")
        assert alt_check.status == CheckStatus.FAIL


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
