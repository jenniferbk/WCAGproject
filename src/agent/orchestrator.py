"""Pipeline orchestrator: comprehend → strategize → execute → review.

Single entry point for end-to-end document remediation.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from pathlib import Path

from src.models.document import DocumentModel
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


def _apply_struct_tag_fixes(
    post_doc: DocumentModel,
    updated_actions: list[dict],
) -> DocumentModel:
    """Patch a re-parsed PDF DocumentModel with fixes that iText applied
    in the structure tree but that the PDF text-layer re-parser can't see.

    Specifically:
    - set_link_text actions: update link.text in the model
    - mark_header_rows actions: update table.header_row_count in the model

    Returns a new DocumentModel with patches applied (original is unchanged).
    """
    # Build lookups
    link_by_id = {lnk.id: i for i, lnk in enumerate(post_doc.links)}
    tbl_by_id = {tbl.id: i for i, tbl in enumerate(post_doc.tables)}

    # Collect patches
    link_patches: dict[int, str] = {}  # index -> new_text
    tbl_patches: dict[int, int] = {}   # index -> header_row_count

    for action in updated_actions:
        if action["status"] != "executed":
            continue

        if action["action_type"] == "set_link_text":
            eid = action["element_id"]
            idx = link_by_id.get(eid)
            if idx is not None:
                link_patches[idx] = action["parameters"].get("new_text", "")

        elif action["action_type"] == "mark_header_rows":
            eid = action["element_id"]
            idx = tbl_by_id.get(eid)
            if idx is not None:
                tbl_patches[idx] = action["parameters"].get("header_count", 1)

    if not link_patches and not tbl_patches:
        return post_doc

    # Build patched model via dict round-trip (Pydantic frozen models)
    model_dict = post_doc.model_dump()

    for idx, new_text in link_patches.items():
        model_dict["links"][idx]["text"] = new_text

    for idx, header_count in tbl_patches.items():
        model_dict["tables"][idx]["header_row_count"] = header_count

    try:
        patched = DocumentModel(**model_dict)
        logger.info(
            "Applied struct tag fixes: %d link(s), %d table(s)",
            len(link_patches), len(tbl_patches),
        )
        return patched
    except Exception as e:
        logger.warning("Failed to apply struct tag fixes: %s", e)
        return post_doc


def process(
    request: RemediationRequest,
    on_phase: Callable[[str, str], None] | None = None,
) -> RemediationResult:
    """Run the full remediation pipeline on a document.

    Args:
        request: RemediationRequest with document path and context.
        on_phase: Optional callback invoked with (phase_name, detail_message)
            at each pipeline stage.

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
    if on_phase:
        on_phase("parsing", "Reading document structure")
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
    if on_phase:
        on_phase(
            "comprehending",
            f"Analyzing {doc_model.stats.paragraph_count} paragraphs, "
            f"{doc_model.stats.image_count} images, "
            f"{doc_model.stats.table_count} tables",
        )
    logger.info("Phase 1: Comprehension (Gemini)")
    comprehension = comprehend(
        doc_model,
        course_name=request.course_context.course_name,
        department=request.course_context.department,
        course_description=request.course_context.description,
        on_progress=lambda detail: on_phase("comprehending", detail) if on_phase else None,
    )
    logger.info(
        "Comprehension: type=%s, %d element purposes",
        comprehension.document_type.value,
        len(comprehension.element_purposes),
    )

    # ── Phase 2: Strategize (Claude) ────────────────────────────────
    if on_phase:
        on_phase(
            "strategizing",
            f"Planning fixes for {comprehension.document_type.value}",
        )
    logger.info("Phase 2: Strategy (Claude)")
    strategy = strategize(doc_model, comprehension)
    logger.info(
        "Strategy: %d actions, %d items for human review",
        len(strategy.actions),
        len(strategy.items_for_human_review),
    )

    # ── Phase 3: Execute ────────────────────────────────────────────
    if on_phase:
        on_phase(
            "executing",
            f"Applying {len(strategy.actions)} accessibility fixes",
        )
    logger.info("Phase 3: Execution")
    output_dir = request.output_dir or str(path.parent)
    exec_progress = lambda detail: on_phase("executing", detail) if on_phase else None
    if suffix == ".pdf":
        exec_result = execute_pdf(strategy, doc_model, output_dir, on_progress=exec_progress)
    else:
        exec_result = execute(strategy, doc_path, output_dir, paragraphs=doc_model.paragraphs, on_progress=exec_progress)

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
    if on_phase:
        on_phase(
            "reviewing",
            f"Checking {exec_result.actions_executed} applied fixes",
        )
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
        # For PDFs, patch the re-parsed model with struct tag fixes that
        # the text-layer parser can't see (link text, table headers).
        if suffix == ".pdf":
            post_doc = _apply_struct_tag_fixes(post_doc, exec_result.updated_actions)
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
    if on_phase:
        on_phase("generating_report", "Building compliance report")
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
