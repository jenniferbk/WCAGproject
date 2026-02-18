"""WCAG 2.1 AA validation for document models.

Three-layer validation:
1. Custom docx-level checks (this module) — fast, checks the parsed DocumentModel
2. axe-core HTML checks (axe_checker.py) — industry-standard, checks generated HTML
3. veraPDF PDF/UA checks (verapdf_checker.py) — PDF-specific, checks generated PDFs

The validate_document() function runs layer 1. For full multi-layer validation,
use validate_full() which orchestrates all three layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from src.models.document import DocumentModel
from src.tools.contrast import analyze_document_contrast
from src.tools.headings import get_fake_heading_candidates, validate_heading_hierarchy
from src.tools.links import analyze_links
from src.tools.tables import analyze_all_tables

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    ERROR = "error"  # definite WCAG failure
    WARNING = "warning"  # likely failure, needs human judgment
    INFO = "info"  # informational, no failure


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class CheckResult:
    """Result of a single WCAG criterion check."""
    criterion: str  # e.g. "1.1.1"
    name: str
    status: CheckStatus
    issues: list[str] = field(default_factory=list)
    severity: Severity = Severity.ERROR
    item_count: int = 0  # how many items were checked
    issue_count: int = 0  # how many had issues


@dataclass
class ValidationReport:
    """Full WCAG validation report for a document."""
    source_path: str
    checks: list[CheckResult] = field(default_factory=list)
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    not_applicable: int = 0
    overall_status: CheckStatus = CheckStatus.PASS
    summary: str = ""


def validate_document(
    doc_model: DocumentModel,
    default_bg: str = "#FFFFFF",
) -> ValidationReport:
    """Run all WCAG 2.1 AA checks against a DocumentModel.

    Args:
        doc_model: The parsed document model.
        default_bg: Default background color for contrast checks.

    Returns:
        ValidationReport with results for each criterion.
    """
    checks: list[CheckResult] = []

    checks.append(_check_1_1_1_alt_text(doc_model))
    checks.append(_check_1_3_1_structure(doc_model))
    checks.append(_check_1_4_3_contrast(doc_model, default_bg))
    checks.append(_check_2_4_2_title(doc_model))
    checks.append(_check_2_4_4_link_purpose(doc_model))
    checks.append(_check_2_4_6_headings(doc_model))
    checks.append(_check_3_1_1_language(doc_model))

    passed = sum(1 for c in checks if c.status == CheckStatus.PASS)
    failed = sum(1 for c in checks if c.status == CheckStatus.FAIL)
    warnings = sum(1 for c in checks if c.status == CheckStatus.WARN)
    na = sum(1 for c in checks if c.status == CheckStatus.NOT_APPLICABLE)

    overall = CheckStatus.PASS
    if failed > 0:
        overall = CheckStatus.FAIL
    elif warnings > 0:
        overall = CheckStatus.WARN

    summary_parts = []
    if failed:
        summary_parts.append(f"{failed} failed")
    if warnings:
        summary_parts.append(f"{warnings} warnings")
    if passed:
        summary_parts.append(f"{passed} passed")
    if na:
        summary_parts.append(f"{na} N/A")

    return ValidationReport(
        source_path=doc_model.source_path,
        checks=checks,
        total_checks=len(checks),
        passed=passed,
        failed=failed,
        warnings=warnings,
        not_applicable=na,
        overall_status=overall,
        summary=", ".join(summary_parts),
    )


_AUTO_ALT_PATTERNS = [
    "description automatically generated",
    "a close up of",
    "a picture containing",
    "a screenshot of",
    "a group of",
    "a close-up of",
    "a blurry photo of",
    "a photo of a",
]


def _is_auto_generated_alt(alt_text: str) -> bool:
    """Check if alt text looks auto-generated (e.g., Microsoft's AI captions)."""
    lower = alt_text.lower().strip()
    return any(pattern in lower for pattern in _AUTO_ALT_PATTERNS)


def _check_1_1_1_alt_text(doc: DocumentModel) -> CheckResult:
    """1.1.1 Non-text Content: All images must have alt text.

    Also flags auto-generated alt text (e.g., 'A close up of text on a
    white background Description automatically generated') as these
    do not meaningfully describe the image content.
    """
    if not doc.images:
        return CheckResult(
            criterion="1.1.1",
            name="Non-text Content",
            status=CheckStatus.NOT_APPLICABLE,
            issues=["No images found in document"],
        )

    missing = [img for img in doc.images if not img.alt_text and not img.is_decorative]
    auto_gen = [
        img for img in doc.images
        if img.alt_text and not img.is_decorative and _is_auto_generated_alt(img.alt_text)
    ]

    issues = [
        f"{img.id} (in {img.paragraph_id}): missing alt text"
        for img in missing
    ]
    issues.extend(
        f"{img.id} (in {img.paragraph_id}): auto-generated alt text needs replacement"
        for img in auto_gen
    )

    problem_count = len(missing) + len(auto_gen)
    status = CheckStatus.PASS if not problem_count else CheckStatus.FAIL
    return CheckResult(
        criterion="1.1.1",
        name="Non-text Content",
        status=status,
        issues=issues,
        item_count=len(doc.images),
        issue_count=problem_count,
    )


def _check_1_3_1_structure(doc: DocumentModel) -> CheckResult:
    """1.3.1 Info and Relationships: Semantic structure check.

    Checks:
    - Fake headings that should be real headings
    - Tables without header rows
    - Heading hierarchy issues
    """
    issues: list[str] = []

    # Fake headings
    fake_candidates = get_fake_heading_candidates(doc.paragraphs, min_score=0.5)
    for para, score in fake_candidates:
        issues.append(
            f"Likely fake heading ({score:.0%}): {para.text[:60]!r}"
        )

    # Table headers
    table_analyses = analyze_all_tables(doc.tables)
    for analysis in table_analyses:
        issues.extend(analysis.issues)

    # Heading hierarchy
    heading_issues = validate_heading_hierarchy(doc.paragraphs)
    for h_issue in heading_issues:
        issues.append(f"Heading: {h_issue.detail}")

    status = CheckStatus.PASS
    if issues:
        # Fake headings and missing table headers are warnings (agent decides)
        # Skipped heading levels are errors
        has_errors = any(
            "skips from" in i or "no Heading 1" in i for i in issues
        )
        status = CheckStatus.FAIL if has_errors else CheckStatus.WARN

    return CheckResult(
        criterion="1.3.1",
        name="Info and Relationships",
        status=status,
        issues=issues,
        severity=Severity.WARNING if status == CheckStatus.WARN else Severity.ERROR,
        item_count=len(doc.paragraphs) + len(doc.tables),
        issue_count=len(issues),
    )


def _check_1_4_3_contrast(doc: DocumentModel, default_bg: str) -> CheckResult:
    """1.4.3 Contrast (Minimum): Text contrast >= 4.5:1 (3:1 for large text)."""
    if not doc.paragraphs:
        return CheckResult(
            criterion="1.4.3",
            name="Contrast (Minimum)",
            status=CheckStatus.NOT_APPLICABLE,
        )

    contrast_issues = analyze_document_contrast(doc.paragraphs, default_bg)
    issues = [
        f"{ci.paragraph_id} run {ci.run_index}: {ci.foreground} on {ci.background} = "
        f"{ci.contrast_ratio}:1 (need {ci.required_ratio}:1) — {ci.text_preview!r}"
        for ci in contrast_issues
    ]

    status = CheckStatus.PASS if not contrast_issues else CheckStatus.FAIL
    return CheckResult(
        criterion="1.4.3",
        name="Contrast (Minimum)",
        status=status,
        issues=issues,
        item_count=sum(len(p.runs) for p in doc.paragraphs),
        issue_count=len(contrast_issues),
    )


def _check_2_4_2_title(doc: DocumentModel) -> CheckResult:
    """2.4.2 Page Titled: Document must have a title."""
    has_title = bool(doc.metadata.title.strip())

    return CheckResult(
        criterion="2.4.2",
        name="Page Titled",
        status=CheckStatus.PASS if has_title else CheckStatus.FAIL,
        issues=[] if has_title else ["Document has no title in metadata"],
        item_count=1,
        issue_count=0 if has_title else 1,
    )


def _check_2_4_4_link_purpose(doc: DocumentModel) -> CheckResult:
    """2.4.4 Link Purpose: Link text must describe the destination."""
    if not doc.links:
        return CheckResult(
            criterion="2.4.4",
            name="Link Purpose (In Context)",
            status=CheckStatus.NOT_APPLICABLE,
            issues=["No links found in document"],
        )

    link_result = analyze_links(doc.links)
    issues = [
        f"{issue.link_id}: {issue.detail}"
        for issue in link_result.issues
    ]

    status = CheckStatus.PASS if not link_result.issues else CheckStatus.FAIL
    return CheckResult(
        criterion="2.4.4",
        name="Link Purpose (In Context)",
        status=status,
        issues=issues,
        item_count=link_result.total_links,
        issue_count=link_result.issue_count,
    )


def _check_2_4_6_headings(doc: DocumentModel) -> CheckResult:
    """2.4.6 Headings and Labels: Headings describe topic/purpose.

    This is partially automated — we can check if headings exist,
    but whether they're *descriptive* requires agent judgment.
    """
    headings = [p for p in doc.paragraphs if p.heading_level is not None]

    if not headings:
        return CheckResult(
            criterion="2.4.6",
            name="Headings and Labels",
            status=CheckStatus.WARN,
            issues=["Document has no headings — consider adding headings for navigation"],
            severity=Severity.WARNING,
            item_count=0,
            issue_count=1,
        )

    issues: list[str] = []
    for h in headings:
        # Flag very short or very generic headings
        text = h.text.strip()
        if len(text) < 2:
            issues.append(
                f"{h.id} (H{h.heading_level}): Heading text too short: {text!r}"
            )

    status = CheckStatus.PASS if not issues else CheckStatus.WARN
    return CheckResult(
        criterion="2.4.6",
        name="Headings and Labels",
        status=status,
        issues=issues,
        severity=Severity.WARNING,
        item_count=len(headings),
        issue_count=len(issues),
    )


def _check_3_1_1_language(doc: DocumentModel) -> CheckResult:
    """3.1.1 Language of Page: Document language must be set."""
    has_lang = bool(doc.metadata.language.strip())

    return CheckResult(
        criterion="3.1.1",
        name="Language of Page",
        status=CheckStatus.PASS if has_lang else CheckStatus.FAIL,
        issues=[] if has_lang else ["Document language not set in metadata"],
        item_count=1,
        issue_count=0 if has_lang else 1,
    )


@dataclass
class MultiLayerReport:
    """Combined validation report from all three layers."""
    docx_report: ValidationReport | None = None
    axe_report: object | None = None       # AxeCheckResult (avoid circular import)
    verapdf_report: object | None = None   # VeraPdfResult
    total_issues: int = 0
    summary: str = ""


def validate_full(
    doc_model: DocumentModel,
    html_string: str = "",
    pdf_path: str = "",
    default_bg: str = "#FFFFFF",
    axe_standard: str = "wcag2aa",
) -> MultiLayerReport:
    """Run all available validation layers.

    Layer 1: Custom docx-level checks (always runs)
    Layer 2: axe-core HTML checks (runs if html_string provided)
    Layer 3: veraPDF PDF/UA checks (runs if pdf_path provided)

    Args:
        doc_model: Parsed document model.
        html_string: Generated HTML to validate with axe-core.
        pdf_path: Generated PDF path to validate with veraPDF.
        default_bg: Default background color for contrast checks.
        axe_standard: axe-core standard to check against.

    Returns:
        MultiLayerReport combining all layer results.
    """
    report = MultiLayerReport()
    total_issues = 0
    summary_parts = []

    # Layer 1: Custom docx checks (always)
    docx_report = validate_document(doc_model, default_bg)
    report.docx_report = docx_report
    total_issues += docx_report.failed
    summary_parts.append(
        f"Docx: {docx_report.summary}"
    )

    # Layer 2: axe-core HTML checks (if HTML provided)
    if html_string:
        try:
            from src.tools.axe_checker import check_html_accessibility
            axe_result = check_html_accessibility(html_string, axe_standard)
            report.axe_report = axe_result
            if axe_result.success:
                total_issues += axe_result.violation_count
                summary_parts.append(
                    f"axe-core: {axe_result.violation_count} violations, "
                    f"{axe_result.passes_count} passes"
                )
            else:
                summary_parts.append(f"axe-core: {axe_result.error}")
        except Exception as e:
            logger.warning("axe-core check skipped: %s", e)
            summary_parts.append(f"axe-core: skipped ({e})")

    # Layer 3: veraPDF PDF/UA checks (if PDF provided)
    if pdf_path:
        try:
            from src.tools.verapdf_checker import check_pdf_ua
            vera_result = check_pdf_ua(pdf_path)
            report.verapdf_report = vera_result
            if vera_result.success:
                total_issues += vera_result.violation_count
                status = "compliant" if vera_result.compliant else "non-compliant"
                summary_parts.append(
                    f"veraPDF: {status}, {vera_result.violation_count} violations"
                )
            else:
                summary_parts.append(f"veraPDF: {vera_result.error}")
        except Exception as e:
            logger.warning("veraPDF check skipped: %s", e)
            summary_parts.append(f"veraPDF: skipped ({e})")

    report.total_issues = total_issues
    report.summary = " | ".join(summary_parts)
    return report


def format_report(report: ValidationReport) -> str:
    """Format a validation report as a human-readable string.

    Args:
        report: The validation report.

    Returns:
        Formatted report text.
    """
    lines: list[str] = []
    lines.append(f"WCAG 2.1 AA Validation Report")
    lines.append(f"Document: {report.source_path}")
    lines.append(f"Overall: {report.overall_status.value.upper()}")
    lines.append(f"Summary: {report.summary}")
    lines.append("")

    for check in report.checks:
        icon = {
            CheckStatus.PASS: "[PASS]",
            CheckStatus.FAIL: "[FAIL]",
            CheckStatus.WARN: "[WARN]",
            CheckStatus.NOT_APPLICABLE: "[ N/A]",
        }[check.status]

        lines.append(f"{icon} {check.criterion} {check.name}")
        if check.issues:
            for issue in check.issues:
                lines.append(f"       - {issue}")
        lines.append("")

    return "\n".join(lines)


def format_multi_layer_report(report: MultiLayerReport) -> str:
    """Format a multi-layer validation report as human-readable text."""
    lines = [
        "Multi-Layer WCAG Validation Report",
        f"Total issues: {report.total_issues}",
        f"Summary: {report.summary}",
        "=" * 60,
        "",
    ]

    # Layer 1: docx checks
    if report.docx_report:
        lines.append("--- Layer 1: Document Model Checks ---")
        lines.append(format_report(report.docx_report))

    # Layer 2: axe-core
    if report.axe_report:
        lines.append("--- Layer 2: axe-core HTML Checks ---")
        try:
            from src.tools.axe_checker import format_axe_report
            lines.append(format_axe_report(report.axe_report))
        except ImportError:
            lines.append("(axe-core formatter not available)")
        lines.append("")

    # Layer 3: veraPDF
    if report.verapdf_report:
        lines.append("--- Layer 3: veraPDF PDF/UA Checks ---")
        try:
            from src.tools.verapdf_checker import format_verapdf_report
            lines.append(format_verapdf_report(report.verapdf_report))
        except ImportError:
            lines.append("(veraPDF formatter not available)")
        lines.append("")

    return "\n".join(lines)
