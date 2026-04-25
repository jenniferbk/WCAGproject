"""Pipeline orchestrator: comprehend → strategize → execute → review.

Single entry point for end-to-end document remediation.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from src.models.document import (
    ContentOrderItem,
    ContentType,
    DocumentModel,
    DocumentStats,
    ImageInfo,
    ParagraphInfo,
    TableInfo,
)
from src.models.pipeline import (
    CostSummary,
    RemediationAction,
    RemediationRequest,
    RemediationResult,
    RemediationStrategy,
)
from src.tools.docx_parser import parse_docx
from src.tools.latex_parser import parse_latex
from src.tools.pdf_parser import parse_pdf
from src.tools.pptx_parser import parse_pptx
from src.tools.report_generator import generate_report_html
from src.tools.mistral_ocr import process_scanned_pages_mistral
from src.tools.scanned_page_ocr import ScannedPageResult, process_scanned_pages
from src.tools.validator import format_report, validate_document
from src.tools.visual_qa import run_visual_qa

from .comprehension import comprehend
from .executor import execute, execute_pdf
from .reviewer import review
from .strategy import strategize, strategize_deterministic

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


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for duplicate comparison.

    OCR of the same content from different columns can produce slight
    differences: extra spaces, hyphenation, punctuation variants.
    Normalize these away for comparison.
    """
    # Collapse all whitespace to single spaces
    s = re.sub(r'\s+', ' ', text.strip())
    # Remove soft hyphens and hyphenation at line breaks ("pro- cessing" → "processing")
    s = re.sub(r'(\w)- (\w)', r'\1\2', s)
    # Normalize dashes
    s = s.replace('—', '-').replace('–', '-')
    # Normalize quotes
    s = s.replace('\u201c', '"').replace('\u201d', '"')
    s = s.replace('\u2018', "'").replace('\u2019', "'")
    return s.lower()


def _deduplicate_ocr_paragraphs(
    paragraphs: list[ParagraphInfo],
) -> list[ParagraphInfo]:
    """Remove duplicate paragraphs from OCR output.

    Gemini sometimes assigns full-width content to both column 1 and column 2,
    producing exact or near-duplicate text after column-aware sorting.  We keep
    the first occurrence (which may have better formatting) and drop subsequent
    duplicates.  Only considers paragraphs with substantial text (>50 chars)
    to avoid accidentally deduplicating legitimately repeated short phrases.
    """
    seen: set[str] = set()
    seen_prefixes: set[str] = set()  # first 60 normalized chars for fuzzy matching
    result: list[ParagraphInfo] = []

    for para in paragraphs:
        text = para.text.strip()
        # Short text may legitimately repeat (e.g., table cells, list markers)
        if len(text) <= 50:
            result.append(para)
            continue

        normalized = _normalize_for_dedup(text)
        if normalized in seen:
            logger.debug("Dedup: removed exact duplicate %r (%s)", text[:60], para.id)
            continue

        # Fuzzy check: if the first 60 normalized chars match an existing
        # paragraph, it's likely the same content with minor OCR variations
        prefix = normalized[:60]
        if len(prefix) >= 60 and prefix in seen_prefixes:
            logger.debug("Dedup: removed near-duplicate %r (%s)", text[:60], para.id)
            continue

        seen.add(normalized)
        if len(prefix) >= 60:
            seen_prefixes.add(prefix)
        result.append(para)

    return result


def _deduplicate_ocr_tables(
    tables: list[TableInfo],
) -> list[TableInfo]:
    """Remove duplicate tables from OCR output.

    Gemini sometimes extracts the same table twice (e.g., a table spanning
    both columns gets read as two separate tables). Compares tables by their
    normalized cell text content — if two tables share >80% of cell texts,
    the second is dropped as a duplicate.
    """
    if len(tables) <= 1:
        return tables

    def _cell_texts(table: TableInfo) -> set[str]:
        texts = set()
        for row in table.rows:
            for cell in row:
                normalized = cell.text.strip().lower()
                if normalized:
                    texts.add(normalized)
        return texts

    result: list[TableInfo] = []
    seen_cell_sets: list[set[str]] = []

    for table in tables:
        cells = _cell_texts(table)
        if not cells:
            result.append(table)
            continue

        is_dup = False
        for seen in seen_cell_sets:
            if not seen:
                continue
            overlap = len(cells & seen)
            total = max(len(cells), len(seen))
            if total > 0 and overlap / total >= 0.8:
                logger.debug(
                    "Table dedup: removed %s (%.0f%% overlap with existing table)",
                    table.id, 100 * overlap / total,
                )
                is_dup = True
                break

        if not is_dup:
            result.append(table)
            seen_cell_sets.append(cells)

    return result


def _merge_ocr_into_model(
    doc_model: DocumentModel,
    ocr_result: ScannedPageResult,
    scanned_page_numbers: list[int],
) -> DocumentModel:
    """Replace synthetic scanned-page placeholders with OCR-extracted content.

    Removes the empty ``ScannedPageAnchor`` paragraphs and their full-page
    ``ImageInfo`` entries for scanned pages, then inserts the real paragraphs,
    tables, and figure images produced by the OCR step.

    Returns a new ``DocumentModel`` (the original is unchanged).
    """
    scanned_set = set(scanned_page_numbers)

    # ── Collect IDs to remove ────────────────────────────────────
    # Synthetic anchor paragraphs for scanned pages
    remove_para_ids: set[str] = set()
    # Full-page images attached to those anchors
    remove_img_ids: set[str] = set()

    for para in doc_model.paragraphs:
        if (
            para.style_name == "ScannedPageAnchor"
            and para.page_number is not None
            and para.page_number in scanned_set
        ):
            remove_para_ids.add(para.id)
            remove_img_ids.update(para.image_ids)

    # ── Build filtered lists ─────────────────────────────────────
    kept_paras = [p for p in doc_model.paragraphs if p.id not in remove_para_ids]
    kept_images = [i for i in doc_model.images if i.id not in remove_img_ids]
    kept_tables = list(doc_model.tables)

    # ── Insert OCR content ───────────────────────────────────────
    # Group OCR paragraphs by page for content_order building.
    # Built from original list; content_order loop filters by deduped_ids.
    ocr_paras_by_page: dict[int, list[ParagraphInfo]] = {}
    for p in ocr_result.paragraphs:
        pg = p.page_number if p.page_number is not None else 0
        ocr_paras_by_page.setdefault(pg, []).append(p)

    logger.debug(
        "OCR page distribution: %s",
        {pg: len(paras) for pg, paras in sorted(ocr_paras_by_page.items())},
    )

    deduped_tables = _deduplicate_ocr_tables(list(ocr_result.tables))
    if len(deduped_tables) < len(ocr_result.tables):
        removed = len(ocr_result.tables) - len(deduped_tables)
        logger.info("Table deduplication removed %d duplicate tables", removed)

    ocr_tables_by_page: dict[int, list[TableInfo]] = {}
    for t in deduped_tables:
        pg = t.page_number if t.page_number is not None else 0
        ocr_tables_by_page.setdefault(pg, []).append(t)

    # ── Deduplicate OCR paragraphs ────────────────────────────────
    # Gemini sometimes assigns full-width content to both columns,
    # causing exact duplicate paragraphs after column-aware sorting.
    deduped_paras = _deduplicate_ocr_paragraphs(ocr_result.paragraphs)
    if len(deduped_paras) < len(ocr_result.paragraphs):
        removed = len(ocr_result.paragraphs) - len(deduped_paras)
        logger.info("Deduplication removed %d duplicate paragraphs", removed)

    # Add OCR-extracted items to the model lists
    new_paras = kept_paras + deduped_paras
    new_tables = kept_tables + deduped_tables
    new_images = kept_images + ocr_result.figures

    # ── Rebuild content_order ────────────────────────────────────
    # Keep non-scanned-page items in their original order, then append
    # OCR items grouped by page in reading order.
    new_order: list[ContentOrderItem] = []

    # Existing items that are NOT from scanned pages
    for item in doc_model.content_order:
        if item.id not in remove_para_ids:
            new_order.append(item)

    # OCR items: iterate scanned pages in order (only deduped paragraphs)
    deduped_ids = {p.id for p in deduped_paras}
    for page_num in sorted(scanned_set):
        for para in ocr_paras_by_page.get(page_num, []):
            if para.id in deduped_ids:
                new_order.append(ContentOrderItem(
                    content_type=ContentType.PARAGRAPH, id=para.id,
                ))
        for tbl in ocr_tables_by_page.get(page_num, []):
            new_order.append(ContentOrderItem(
                content_type=ContentType.TABLE, id=tbl.id,
            ))

    # ── Rebuild stats ────────────────────────────────────────────
    heading_count = sum(
        1 for p in new_paras if p.heading_level is not None
    )
    fake_heading_count = sum(
        1 for p in new_paras
        if p.fake_heading_signals and p.fake_heading_signals.score >= 0.5
    )
    images_missing_alt = sum(
        1 for i in new_images if not i.alt_text and not i.is_decorative
    )
    new_stats = DocumentStats(
        paragraph_count=len(new_paras),
        table_count=len(new_tables),
        image_count=len(new_images),
        link_count=len(doc_model.links),
        heading_count=heading_count,
        images_missing_alt=images_missing_alt,
        fake_heading_candidates=fake_heading_count,
    )

    try:
        merged = DocumentModel(
            source_format=doc_model.source_format,
            source_path=doc_model.source_path,
            metadata=doc_model.metadata,
            paragraphs=new_paras,
            tables=new_tables,
            images=new_images,
            links=doc_model.links,
            content_order=new_order,
            contrast_issues=doc_model.contrast_issues,
            stats=new_stats,
            parse_warnings=doc_model.parse_warnings,
        )
        logger.info(
            "Merged OCR: removed %d anchor paras + %d page images, "
            "added %d paragraphs, %d tables, %d figures",
            len(remove_para_ids), len(remove_img_ids),
            len(ocr_result.paragraphs), len(ocr_result.tables),
            len(ocr_result.figures),
        )
        return merged
    except Exception as e:
        logger.warning("Failed to merge OCR into model: %s", e)
        return doc_model


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
    if suffix not in (".docx", ".pptx", ".pdf", ".tex", ".ltx", ".zip"):
        return RemediationResult(
            success=False,
            input_path=doc_path,
            error=f"Unsupported format: {suffix}. Currently .docx, .pptx, .pdf, .tex, and .zip are supported.",
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
    elif suffix in (".tex", ".ltx", ".zip"):
        parse_result = parse_latex(doc_path)
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

    # ── Phase 0.5: Scanned Page OCR (Gemini) ───────────────────────
    # For PDFs with scanned pages, run OCR to extract real text content
    # before comprehension so the rest of the pipeline sees paragraphs,
    # not image placeholders.
    ocr_usage: list = []
    ocr_result = None
    mistral_result = None
    if suffix == ".pdf" and hasattr(parse_result, "scanned_page_numbers") and parse_result.scanned_page_numbers:
        n_scanned = len(parse_result.scanned_page_numbers)
        logger.info("Detected %d scanned page(s) — running OCR", n_scanned)
        if on_phase:
            on_phase("ocr", f"Extracting text from {n_scanned} scanned page(s)")

        course_ctx = ""
        if request.course_context:
            parts = []
            if request.course_context.course_name:
                parts.append(request.course_context.course_name)
            if request.course_context.department:
                parts.append(request.course_context.department)
            if request.course_context.description:
                parts.append(request.course_context.description)
            course_ctx = " — ".join(parts)

        import os

        mistral_api_key = os.environ.get("MISTRAL_API_KEY")
        primary_engine = None  # "mistral" or "hybrid" — which one feeds the pipeline

        # ── Try Mistral first (primary) ─────────────────────────────
        if mistral_api_key:
            try:
                logger.info("OCR: trying Mistral (primary)")
                mistral_try = process_scanned_pages_mistral(
                    pdf_path=Path(doc_path),
                    scanned_page_numbers=parse_result.scanned_page_numbers,
                )
                if mistral_try.success and (mistral_try.paragraphs or mistral_try.tables):
                    logger.info(
                        "Mistral OCR: %d paragraphs, %d tables from %d pages",
                        len(mistral_try.paragraphs),
                        len(mistral_try.tables),
                        len(mistral_try.pages_processed),
                    )
                    ocr_result = mistral_try
                    mistral_result = mistral_try
                    primary_engine = "mistral"
                else:
                    logger.warning(
                        "Mistral OCR returned no content — falling back to hybrid pipeline",
                    )
                    mistral_result = None
            except Exception as e:
                logger.warning("Mistral OCR failed: %s — falling back to hybrid pipeline", e)
                mistral_result = None

        # ── Fall back to hybrid if Mistral failed or no API key ─────
        if ocr_result is None:
            logger.info("OCR: running hybrid pipeline (Tesseract + Gemini + Haiku)")
            ocr_result = process_scanned_pages(
                pdf_path=doc_path,
                scanned_page_numbers=parse_result.scanned_page_numbers,
                course_context=course_ctx,
                on_progress=lambda detail: on_phase("ocr", detail) if on_phase else None,
            )
            primary_engine = "hybrid"

        if ocr_result.success and (ocr_result.paragraphs or ocr_result.tables):
            doc_model = _merge_ocr_into_model(
                doc_model, ocr_result, parse_result.scanned_page_numbers,
            )
            ocr_usage = list(ocr_result.api_usage)
            logger.info(
                "OCR merged (%s): %d paragraphs, %d tables, %d figures from %d pages",
                primary_engine,
                len(ocr_result.paragraphs), len(ocr_result.tables),
                len(ocr_result.figures), len(ocr_result.pages_processed),
            )
        elif not ocr_result.success:
            logger.warning("OCR failed: %s — falling back to image descriptions", ocr_result.error)

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

    # ── Phase 2: Strategize (Claude or deterministic mapper) ────────
    strategy_mode = os.environ.get("STRATEGY_MODE", "llm").lower()
    if on_phase:
        on_phase(
            "strategizing",
            f"Planning fixes for {comprehension.document_type.value}",
        )
    if strategy_mode == "deterministic":
        logger.info("Phase 2: Strategy (deterministic mapper, no LLM)")
        strategy = strategize_deterministic(doc_model, comprehension)
    else:
        logger.info("Phase 2: Strategy (Claude)")
        strategy = strategize(doc_model, comprehension)
    logger.info(
        "Strategy: %d actions, %d items for human review",
        len(strategy.actions),
        len(strategy.items_for_human_review),
    )

    # ── Auto-generate TikZ description actions ─────────────────────
    if hasattr(doc_model, "math") and doc_model.math:
        tikz_actions = []
        for math_info in doc_model.math:
            if math_info.tikz_source:
                tikz_actions.append(RemediationAction(
                    element_id=math_info.id,
                    action_type="describe_tikz",
                    parameters={"tikz_source": math_info.tikz_source},
                    wcag_criterion="1.1.1",
                    description=f"Generate thorough description for TikZ diagram {math_info.id}",
                ))
        if tikz_actions:
            logger.info("Added %d TikZ description action(s)", len(tikz_actions))
            strategy = RemediationStrategy(
                actions=tikz_actions + list(strategy.actions),
                items_for_human_review=strategy.items_for_human_review,
                strategy_summary=strategy.strategy_summary,
                api_usage=strategy.api_usage,
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
    if suffix in (".pdf", ".tex", ".ltx", ".zip"):
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

    # ── Phase 3.5: Visual Diff QA (scanned PDFs only) ──────────────
    visual_qa_result = None
    if (
        suffix == ".pdf"
        and hasattr(parse_result, "scanned_page_numbers")
        and parse_result.scanned_page_numbers
        and exec_result.companion_html_path
    ):
        if on_phase:
            on_phase("visual_qa", "Visual quality check")
        logger.info(
            "Phase 3.5: Visual Diff QA (%d scanned pages)",
            len(parse_result.scanned_page_numbers),
        )
        try:
            import os
            from google import genai
            gemini_key = os.environ.get("GEMINI_API_KEY")
            if gemini_key:
                gemini_client = genai.Client(api_key=gemini_key)
                visual_qa_result = run_visual_qa(
                    pdf_path=doc_path,
                    html_path=exec_result.companion_html_path,
                    scanned_page_numbers=parse_result.scanned_page_numbers,
                    client=gemini_client,
                    model="gemini-2.5-flash",
                    output_dir=output_dir,
                )
                logger.info(
                    "Visual QA: %d findings (%d pages checked)",
                    len(visual_qa_result.findings), visual_qa_result.pages_checked,
                )
            else:
                logger.warning("Visual QA: GEMINI_API_KEY not set, skipping")
        except Exception as e:
            logger.warning("Visual QA failed (non-fatal): %s", e)

    # ── Phase 4: Review (Claude) ────────────────────────────────────
    if on_phase:
        on_phase(
            "reviewing",
            f"Checking {exec_result.actions_executed} applied fixes",
        )
    logger.info("Phase 4: Review (Claude)")

    # Re-parse the remediated document
    # LaTeX output is HTML — no re-parser available, use the fixed model directly
    if suffix in (".tex", ".ltx", ".zip"):
        post_parse = type("FakeParseResult", (), {"success": False})()
    elif suffix == ".pptx":
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
    all_usage.extend(ocr_usage)
    all_usage.extend(comprehension.api_usage)
    all_usage.extend(strategy.api_usage)
    all_usage.extend(review_usage)
    if visual_qa_result and visual_qa_result.api_usage:
        all_usage.extend(visual_qa_result.api_usage)
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
        visual_qa_findings = visual_qa_result.findings if visual_qa_result else None
        report_html = generate_report_html(
            final_result,
            visual_qa_findings=visual_qa_findings,
            output_dir=output_dir,
            hybrid_ocr_result=None,  # No longer running comparison
            mistral_ocr_result=None,
        )
        report_path.write_text(report_html)
        final_result = RemediationResult(
            **{**final_result.model_dump(), "report_path": str(report_path)}
        )
        logger.info("Report saved: %s", report_path)
    except Exception as e:
        logger.warning("Failed to generate report: %s", e)

    logger.info("Pipeline complete in %.1fs: %s", elapsed, exec_result.output_path)
    return final_result
