"""Pipeline orchestrator: comprehend → strategize → execute → review.

Single entry point for end-to-end document remediation.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.models.pipeline import (
    CostSummary,
    RemediationAction,
    RemediationRequest,
    RemediationResult,
    RemediationStrategy,
)
from src.tools.docx_parser import parse_docx
from src.tools.pdf_parser import parse_pdf
from src.tools.pptx_parser import parse_pptx
from src.tools.report_generator import generate_report_html
from src.tools.validator import format_report, validate_document

from .comprehension import comprehend
from .executor import execute, execute_pdf
from .reviewer import review
from .strategy import strategize

logger = logging.getLogger(__name__)


def process(request: RemediationRequest) -> RemediationResult:
    """Run the full remediation pipeline on a document.

    Args:
        request: RemediationRequest with document path and context.

    Returns:
        RemediationResult with all artifacts and metrics.
    """
    start_time = time.time()
    doc_path = request.document_path

    logger.info("Starting remediation: %s", doc_path)

    # Validate input
    path = Path(doc_path)
    if not path.exists():
        return RemediationResult(
            success=False,
            input_path=doc_path,
            error=f"File not found: {doc_path}",
            processing_time_seconds=time.time() - start_time,
        )

    suffix = path.suffix.lower()
    if suffix not in (".docx", ".pptx", ".pdf"):
        return RemediationResult(
            success=False,
            input_path=doc_path,
            error=f"Unsupported format: {suffix}. Currently .docx, .pptx, and .pdf are supported.",
            processing_time_seconds=time.time() - start_time,
        )

    # ── Phase 0: Parse ──────────────────────────────────────────────
    logger.info("Phase 0: Parsing document")
    if suffix == ".pptx":
        parse_result = parse_pptx(doc_path)
    elif suffix == ".pdf":
        parse_result = parse_pdf(doc_path)
    else:
        parse_result = parse_docx(doc_path)
    if not parse_result.success:
        return RemediationResult(
            success=False,
            input_path=doc_path,
            error=f"Parse failed: {parse_result.error}",
            processing_time_seconds=time.time() - start_time,
        )

    doc_model = parse_result.document
    pre_report = validate_document(doc_model)
    pre_summary = format_report(pre_report)
    issues_before = pre_report.failed + pre_report.warnings

    logger.info(
        "Parsed: %d paragraphs, %d images, %d tables. Issues: %d",
        doc_model.stats.paragraph_count,
        doc_model.stats.image_count,
        doc_model.stats.table_count,
        issues_before,
    )

    # ── Phase 1: Comprehend (Gemini) ────────────────────────────────
    logger.info("Phase 1: Comprehension (Gemini)")
    comprehension = comprehend(
        doc_model,
        course_name=request.course_context.course_name,
        department=request.course_context.department,
        course_description=request.course_context.description,
    )
    logger.info(
        "Comprehension: type=%s, %d element purposes",
        comprehension.document_type.value,
        len(comprehension.element_purposes),
    )

    # ── Phase 2: Strategize (Claude) ────────────────────────────────
    logger.info("Phase 2: Strategy (Claude)")
    strategy = strategize(doc_model, comprehension)
    logger.info(
        "Strategy: %d actions, %d items for human review",
        len(strategy.actions),
        len(strategy.items_for_human_review),
    )

    # ── Phase 3: Execute ────────────────────────────────────────────
    logger.info("Phase 3: Execution")
    output_dir = request.output_dir or str(path.parent)
    if suffix == ".pdf":
        exec_result = execute_pdf(strategy, doc_model, output_dir)
    else:
        exec_result = execute(strategy, doc_path, output_dir, paragraphs=doc_model.paragraphs)

    if not exec_result.success:
        return RemediationResult(
            success=False,
            input_path=doc_path,
            comprehension=comprehension,
            strategy=strategy,
            pre_validation_summary=pre_summary,
            error=f"Execution failed: {exec_result.error}",
            processing_time_seconds=time.time() - start_time,
        )

    logger.info(
        "Executed: %d OK, %d failed, %d skipped",
        exec_result.actions_executed,
        exec_result.actions_failed,
        exec_result.actions_skipped,
    )
    if exec_result.companion_html_path:
        logger.info("Companion HTML: %s", exec_result.companion_html_path)

    # ── Phase 4: Review (Claude) ────────────────────────────────────
    logger.info("Phase 4: Review (Claude)")

    # Re-parse the remediated document
    if suffix == ".pptx":
        post_parse = parse_pptx(exec_result.output_path)
    elif suffix == ".pdf":
        post_parse = parse_pdf(exec_result.output_path)
    else:
        post_parse = parse_docx(exec_result.output_path)
    if post_parse.success:
        post_doc = post_parse.document
        post_report = validate_document(post_doc)
        post_summary = format_report(post_report)
        issues_after = post_report.failed + post_report.warnings
    else:
        post_doc = doc_model
        post_summary = "Could not re-parse remediated document"
        issues_after = issues_before

    # Run Claude review
    review_findings, review_usage = review(post_doc, exec_result.updated_actions)

    logger.info(
        "Review: %d findings. Issues: %d → %d (fixed %d)",
        len(review_findings),
        issues_before,
        issues_after,
        max(0, issues_before - issues_after),
    )

    # ── Build final result ──────────────────────────────────────────
    # Merge human review items from strategy and review
    human_review_items = list(strategy.items_for_human_review)
    for finding in review_findings:
        if finding.finding_type == "needs_human_review":
            human_review_items.append(finding.detail)

    # Update strategy actions with execution results
    updated_strategy = RemediationStrategy(
        actions=[
            RemediationAction(
                element_id=a["element_id"],
                action_type=a["action_type"],
                parameters=a["parameters"],
                rationale=a["rationale"],
                status=a["status"],
                result_detail=a["result_detail"],
            )
            for a in exec_result.updated_actions
        ],
        items_for_human_review=strategy.items_for_human_review,
        strategy_summary=strategy.strategy_summary,
    )

    elapsed = time.time() - start_time

    # ── Aggregate API costs ──────────────────────────────────────────
    all_usage = []
    all_usage.extend(comprehension.api_usage)
    all_usage.extend(strategy.api_usage)
    all_usage.extend(review_usage)
    cost_summary = CostSummary(usage_records=all_usage)
    logger.info(
        "API costs: %d calls, %d input tokens, %d output tokens, ~$%.4f",
        len(all_usage), cost_summary.total_input_tokens,
        cost_summary.total_output_tokens, cost_summary.estimated_cost_usd,
    )

    # ── Generate compliance report ──────────────────────────────────
    final_result = RemediationResult(
        success=True,
        input_path=doc_path,
        output_path=exec_result.output_path,
        companion_output_path=exec_result.companion_html_path,
        comprehension=comprehension,
        strategy=updated_strategy,
        review_findings=review_findings,
        pre_validation_summary=pre_summary,
        post_validation_summary=post_summary,
        issues_before=issues_before,
        issues_after=issues_after,
        issues_fixed=max(0, issues_before - issues_after),
        items_for_human_review=human_review_items,
        processing_time_seconds=elapsed,
        cost_summary=cost_summary,
    )

    # Write HTML compliance report
    report_path = Path(exec_result.output_path).with_suffix(".html")
    try:
        report_html = generate_report_html(final_result)
        report_path.write_text(report_html)
        final_result = RemediationResult(
            **{**final_result.model_dump(), "report_path": str(report_path)}
        )
        logger.info("Report saved: %s", report_path)
    except Exception as e:
        logger.warning("Failed to generate report: %s", e)

    logger.info("Pipeline complete in %.1fs: %s", elapsed, exec_result.output_path)
    return final_result
