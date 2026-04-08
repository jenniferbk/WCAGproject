"""Phase 3: Execute remediation actions on the document.

Takes the remediation strategy and applies each action to the document
using the deterministic tools. Tracks success/failure of each action.

This is NOT an LLM agent loop — it's a deterministic executor that
runs the planned actions in order. The intelligence is in the strategy
phase; execution just applies the plan.
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from pptx import Presentation

from src.models.document import DocumentModel
from src.models.pipeline import RemediationAction, RemediationStrategy
from src.tools.alt_text import set_alt_text, set_alt_text_pptx, set_decorative
from src.tools.contrast import check_contrast, fix_all_document_contrast, fix_contrast
from src.tools.headings import set_heading_level
from src.tools.html_builder import build_html
from src.tools.itext_tagger import build_tagging_plan, tag_pdf
from src.tools.metadata import set_language, set_language_pptx, set_title, set_title_pptx
from src.tools.pdf_writer import (
    apply_contrast_fixes_to_pdf,
    apply_pdf_fixes,
    apply_pdf_ua_metadata,
    apply_pdf_ua_tail_polish,
    mark_untagged_content_as_artifact,
    populate_link_annotation_contents,
    repair_broken_uris_in_pdf,
    strip_struct_tree,
    update_existing_figure_alt_texts,
)
from src.tools.links import set_link_text
from src.tools.tables import mark_header_rows

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing the remediation strategy."""
    success: bool
    output_path: str = ""
    companion_html_path: str = ""
    actions_executed: int = 0
    actions_failed: int = 0
    actions_skipped: int = 0
    updated_actions: list[dict] = field(default_factory=list)
    error: str = ""


def execute(
    strategy: RemediationStrategy,
    input_path: str,
    output_dir: str = "",
    paragraphs: list | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """Execute all planned remediation actions on a document.

    Args:
        strategy: The remediation strategy with planned actions.
        input_path: Path to the input .docx or .pptx file.
        output_dir: Directory for output. Defaults to same directory as input.
        paragraphs: Parsed ParagraphInfo list (needed for fix_all_contrast).

    Returns:
        ExecutionResult with success/failure details.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        return ExecutionResult(
            success=False,
            error=f"Input file not found: {input_path}",
        )

    # Determine output path
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = input_file.parent

    output_path = out_dir / f"{input_file.stem}_remediated{input_file.suffix}"

    # Copy input to output location
    shutil.copy2(input_path, output_path)

    # Detect format and open accordingly
    is_pptx = input_file.suffix.lower() == ".pptx"

    try:
        if is_pptx:
            doc_or_prs = Presentation(str(output_path))
        else:
            doc_or_prs = Document(str(output_path))
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to open document: {e}",
        )

    executed = 0
    failed = 0
    skipped = 0
    updated_actions = []

    total_actions = len(strategy.actions)
    for i, action in enumerate(strategy.actions):
        if action.status == "skipped":
            skipped += 1
            updated_actions.append(_action_dict(action, "skipped", "Pre-skipped"))
            continue

        if on_progress:
            on_progress(f"Applying fix {i + 1} of {total_actions}: {action.action_type}")
        result = _execute_action(doc_or_prs, action, paragraphs=paragraphs, is_pptx=is_pptx)
        updated_actions.append(result)

        if result["status"] == "executed":
            executed += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    # Save the modified document
    try:
        if is_pptx:
            doc_or_prs.save(str(output_path))
        else:
            doc_or_prs.save(str(output_path))
        logger.info(
            "Document saved: %s (executed=%d, failed=%d, skipped=%d)",
            output_path, executed, failed, skipped,
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to save document: {e}",
            actions_executed=executed,
            actions_failed=failed,
            actions_skipped=skipped,
            updated_actions=updated_actions,
        )

    return ExecutionResult(
        success=True,
        output_path=str(output_path),
        actions_executed=executed,
        actions_failed=failed,
        actions_skipped=skipped,
        updated_actions=updated_actions,
    )


def execute_pdf(
    strategy: RemediationStrategy,
    doc_model: DocumentModel,
    output_dir: str = "",
    on_progress: Callable[[str], None] | None = None,
) -> ExecutionResult:
    """Execute remediation for PDF and LaTeX documents.

    For PDFs: uses iText for in-place tagging via position-based matching.
    For LaTeX: skips iText (no source PDF), generates accessible HTML + PDF.

    Also generates a companion accessible HTML version.

    Args:
        strategy: The remediation strategy with planned actions.
        doc_model: Parsed DocumentModel from the PDF or LaTeX.
        output_dir: Directory for output.

    Returns:
        ExecutionResult with tagged PDF and companion HTML.
    """
    input_path = Path(doc_model.source_path)
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = input_path.parent

    # Step 1: Apply model-level fixes (heading levels, alt text, etc.)
    image_data_map = {img.id: img.image_data for img in doc_model.images if img.image_data}
    model_dict = doc_model.model_dump()

    executed = 0
    failed = 0
    skipped = 0
    updated_actions: list[dict] = []

    # Build lookups
    para_id_to_idx = {p["id"]: i for i, p in enumerate(model_dict["paragraphs"])}
    img_id_to_idx = {img["id"]: i for i, img in enumerate(model_dict["images"])}
    tbl_id_to_idx = {t["id"]: i for i, t in enumerate(model_dict["tables"])}
    link_id_to_idx = {lnk["id"]: i for i, lnk in enumerate(model_dict.get("links", []))}

    # Collect fixes for pdf_writer fallback
    pdf_title = ""
    pdf_language = ""
    pdf_alt_texts: dict[str, str] = {}
    pdf_decorative_ids: set[str] = set()
    contrast_color_map: dict[str, str] = {}  # original_hex -> fixed_hex

    total_actions = len(strategy.actions)
    for i, action in enumerate(strategy.actions):
        if action.status == "skipped":
            skipped += 1
            updated_actions.append(_action_dict(action, "skipped", "Pre-skipped"))
            continue

        if on_progress:
            on_progress(f"Applying fix {i + 1} of {total_actions}: {action.action_type}")
        result = _apply_pdf_action(
            model_dict, action, para_id_to_idx, img_id_to_idx, tbl_id_to_idx,
            link_id_to_idx, contrast_color_map,
        )
        updated_actions.append(result)

        if result["status"] == "executed":
            executed += 1
            # Collect for pdf_writer fallback
            if action.action_type == "set_title":
                pdf_title = action.parameters.get("title", "")
            elif action.action_type == "set_language":
                pdf_language = action.parameters.get("language", "")
            elif action.action_type == "set_alt_text":
                alt = action.parameters.get("alt_text", "")
                if alt:
                    pdf_alt_texts[action.element_id] = alt
            elif action.action_type == "set_decorative":
                pdf_decorative_ids.add(action.element_id)
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    # Reconstruct fixed DocumentModel
    for img_dict in model_dict.get("images", []):
        img_id = img_dict.get("id")
        if img_id in image_data_map:
            img_dict["image_data"] = image_data_map[img_id]

    try:
        fixed_model = DocumentModel(**model_dict)
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to reconstruct DocumentModel: {e}",
            actions_executed=executed,
            actions_failed=failed,
            actions_skipped=skipped,
            updated_actions=updated_actions,
        )

    # ── Track 1: iText in-place tagging (skip for LaTeX — no source PDF) ──
    is_latex = input_path.suffix.lower() in (".tex", ".ltx", ".zip")
    tagged_pdf_path = str(out_dir / f"{input_path.stem}_remediated.pdf")

    if not is_latex:
        # Strip existing structure tree before iText processes the PDF.
        # This prevents duplicate /Figure elements when the original PDF
        # already has structure tags (e.g., from PowerPoint export).
        # iText will create a fresh, clean structure tree.
        stripped_input = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False, dir=str(out_dir),
            ) as tmp:
                stripped_input = tmp.name
            if strip_struct_tree(str(input_path), stripped_input):
                itext_input = stripped_input
            else:
                itext_input = str(input_path)
        except Exception:
            itext_input = str(input_path)

        if on_progress:
            on_progress("Tagging PDF structure")
        tagging_plan = build_tagging_plan(
            strategy, doc_model,
            input_path=itext_input,
            output_path=tagged_pdf_path,
        )
        tag_result = tag_pdf(tagging_plan)

        # Clean up temp file
        if stripped_input:
            try:
                os.unlink(stripped_input)
            except Exception:
                pass

        if tag_result.success:
            logger.info(
                "Track 1 (iText): %d tags applied to %s",
                tag_result.tags_applied, tagged_pdf_path,
            )
            # Apply contrast fixes to the tagged PDF via PyMuPDF
            if contrast_color_map:
                contrast_result = apply_contrast_fixes_to_pdf(
                    tagged_pdf_path, contrast_color_map, verify=True
                )
                if contrast_result.success and contrast_result.contrast_fixes_applied > 0:
                    logger.info(
                        "Contrast fixes applied: %d changes to %s",
                        contrast_result.contrast_fixes_applied, tagged_pdf_path,
                    )
                elif not contrast_result.success:
                    logger.warning(
                        "Contrast fix failed on tagged PDF: %s",
                        "; ".join(contrast_result.errors),
                    )

            # Repair syntactically broken link annotations (whitespace in
            # domain, triple slashes, etc.). Safe to run unconditionally —
            # the repair function is a no-op on URIs that are already fine
            # and conservative on ones it can't confidently fix.
            try:
                n_fixed, uri_repairs = repair_broken_uris_in_pdf(tagged_pdf_path)
                if n_fixed:
                    logger.info(
                        "Repaired %d broken link URI(s) in %s",
                        n_fixed, tagged_pdf_path,
                    )
                    for before, after in uri_repairs[:5]:
                        logger.info("  %r → %r", before[:80], after[:80])
            except Exception as exc:
                logger.warning("URI repair pass failed: %s", exc)

            # PDF/UA Track C — XMP pdfuaid:part=1, DisplayDocTitle,
            # /Metadata key on catalog. Eliminates rules 5-1, 7.1-8,
            # 7.1-10 across all docs at zero API cost. See spec
            # docs/superpowers/specs/2026-04-07-pdf-ua-compliance-fixes-design.md
            try:
                meta_result = apply_pdf_ua_metadata(tagged_pdf_path)
                if meta_result.success and meta_result.changes:
                    logger.info(
                        "PDF/UA metadata fixes applied to %s: %s",
                        tagged_pdf_path, ", ".join(meta_result.changes),
                    )
                elif not meta_result.success:
                    logger.warning(
                        "PDF/UA metadata fixes failed: %s", meta_result.error
                    )
            except Exception as exc:
                logger.warning("PDF/UA metadata pass failed: %s", exc)

            # PDF/UA Track A — wrap depth-0 untagged content and
            # convert orphan BDCs to /Artifact <</Type /Pagination>>.
            # Reduces rule 7.1-3 failed checks by 50–92% on the
            # benchmark depending on the source PDF's tagging quality.
            try:
                artifact_result = mark_untagged_content_as_artifact(tagged_pdf_path)
                if artifact_result.success:
                    if artifact_result.artifact_wrappers_inserted:
                        logger.info(
                            "Artifact-marked %d content region(s) "
                            "across %d page(s) and %d form XObject(s) in %s",
                            artifact_result.artifact_wrappers_inserted,
                            artifact_result.pages_modified,
                            artifact_result.form_xobjects_modified,
                            tagged_pdf_path,
                        )
                else:
                    logger.warning(
                        "Artifact marking failed: %s",
                        "; ".join(artifact_result.errors),
                    )
            except Exception as exc:
                logger.warning("Artifact marking pass failed: %s", exc)

            # PDF/UA link contents — set /Contents on every link
            # annotation that lacks one. Satisfies rule 7.18.5-2.
            try:
                link_result = populate_link_annotation_contents(tagged_pdf_path)
                if link_result.success and link_result.annotations_modified:
                    logger.info(
                        "Set /Contents on %d link annotation(s) in %s",
                        link_result.annotations_modified, tagged_pdf_path,
                    )
                elif not link_result.success:
                    logger.warning(
                        "Link /Contents pass failed: %s", link_result.error
                    )
            except Exception as exc:
                logger.warning("Link /Contents pass failed: %s", exc)

            # PDF/UA tail polish — catalog /Lang, page /Tabs /S,
            # /Figure /Alt fallback. Eliminates rules 7.2-34, 7.18.3-1,
            # and 7.3-1 across the long tail.
            try:
                doc_lang = (doc_model.metadata.language or "en-US").strip() or "en-US"
                tail_result = apply_pdf_ua_tail_polish(
                    tagged_pdf_path, default_lang=doc_lang
                )
                if tail_result.success and (
                    tail_result.lang_set
                    or tail_result.pages_tabs_fixed
                    or tail_result.figures_alt_filled
                ):
                    logger.info(
                        "PDF/UA tail polish: lang_set=%s, pages_tabs_fixed=%d, figures_alt_filled=%d",
                        tail_result.lang_set,
                        tail_result.pages_tabs_fixed,
                        tail_result.figures_alt_filled,
                    )
                elif not tail_result.success:
                    logger.warning(
                        "PDF/UA tail polish failed: %s", tail_result.error
                    )
            except Exception as exc:
                logger.warning("PDF/UA tail polish failed: %s", exc)
        else:
            # Fallback: use pdf_writer for Tier 1 (metadata + alt text)
            logger.warning(
                "Track 1 (iText) failed: %s. Falling back to pdf_writer.",
                "; ".join(tag_result.errors),
            )
            pdf_result = apply_pdf_fixes(
                source_path=doc_model.source_path,
                doc_model=doc_model,
                title=pdf_title,
                language=pdf_language,
                alt_texts=pdf_alt_texts,
                decorative_ids=pdf_decorative_ids,
                heading_actions=[],
                contrast_fixes=[],
                output_path=tagged_pdf_path,
                verify_visually=False,
            )
            if not pdf_result.success:
                return ExecutionResult(
                    success=False,
                    error=f"Both iText and pdf_writer failed: {'; '.join(pdf_result.errors)}",
                    actions_executed=executed,
                    actions_failed=failed,
                    actions_skipped=skipped,
                    updated_actions=updated_actions,
                )
            tagged_pdf_path = pdf_result.output_path

    # ── HTML output ─────────────────────────────────────────────────
    companion_html_path = ""

    html_result = build_html(fixed_model, embed_images=True)
    if html_result.success:
        html_path = out_dir / f"{input_path.stem}_accessible.html"
        try:
            html_path.write_text(html_result.html, encoding="utf-8")
            companion_html_path = str(html_path)
            logger.info("Companion HTML: %s", companion_html_path)
        except Exception as e:
            logger.warning("Failed to write companion HTML: %s", e)

    # For LaTeX: generate PDF from HTML via WeasyPrint (no source PDF to tag)
    if is_latex and html_result.success:
        try:
            from weasyprint import HTML as WeasyHTML
            WeasyHTML(string=html_result.html).write_pdf(
                tagged_pdf_path, pdf_variant="pdf/ua-1",
            )
            logger.info("LaTeX → PDF generated: %s", tagged_pdf_path)
        except Exception as e:
            logger.warning("WeasyPrint PDF generation failed: %s", e)
            # Fall back to HTML-only output
            tagged_pdf_path = companion_html_path

    logger.info(
        "%s remediated: %s (executed=%d, failed=%d, skipped=%d)",
        "LaTeX" if is_latex else "PDF",
        tagged_pdf_path, executed, failed, skipped,
    )

    return ExecutionResult(
        success=True,
        output_path=tagged_pdf_path,
        companion_html_path=companion_html_path,
        actions_executed=executed,
        actions_failed=failed,
        actions_skipped=skipped,
        updated_actions=updated_actions,
    )



def _apply_pdf_action(
    model_dict: dict,
    action: RemediationAction,
    para_id_to_idx: dict[str, int],
    img_id_to_idx: dict[str, int],
    tbl_id_to_idx: dict[str, int],
    link_id_to_idx: dict[str, int] | None = None,
    contrast_color_map: dict[str, str] | None = None,
) -> dict:
    """Apply a single remediation action to the model dict (in-memory)."""
    try:
        action_type = action.action_type
        params = action.parameters
        element_id = action.element_id

        if action_type == "set_alt_text":
            alt = params.get("alt_text", "")
            if not alt:
                return _action_dict(action, "failed", "Empty alt text")
            idx = img_id_to_idx.get(element_id)
            if idx is None:
                return _action_dict(action, "failed", f"Image not found: {element_id}")
            model_dict["images"][idx]["alt_text"] = alt
            return _action_dict(action, "executed", f"Set alt text: {alt[:80]}")

        elif action_type == "set_decorative":
            idx = img_id_to_idx.get(element_id)
            if idx is None:
                return _action_dict(action, "failed", f"Image not found: {element_id}")
            model_dict["images"][idx]["is_decorative"] = True
            model_dict["images"][idx]["alt_text"] = ""
            return _action_dict(action, "executed", "Marked as decorative")

        elif action_type == "set_heading_level":
            level = params.get("level")
            if level is None:
                return _action_dict(action, "failed", "Missing level")
            idx = para_id_to_idx.get(element_id)
            if idx is None:
                return _action_dict(action, "failed", f"Paragraph not found: {element_id}")
            model_dict["paragraphs"][idx]["heading_level"] = level
            return _action_dict(action, "executed", f"Set to Heading {level}")

        elif action_type == "mark_header_rows":
            count = params.get("header_count", 1)
            idx = tbl_id_to_idx.get(element_id)
            if idx is None:
                return _action_dict(action, "failed", f"Table not found: {element_id}")
            # Check if first row is all empty — don't mark empty rows as headers
            table_rows = model_dict["tables"][idx].get("rows", [])
            if table_rows and count >= 1:
                first_row = table_rows[0]
                all_empty = all(
                    not (cell.get("text") or "").strip() for cell in first_row
                )
                if all_empty:
                    return _action_dict(
                        action, "skipped",
                        "First row is all empty — not marking as header",
                    )
            model_dict["tables"][idx]["header_row_count"] = count
            return _action_dict(action, "executed", f"Marked {count} header row(s)")

        elif action_type == "set_title":
            title = params.get("title", "")
            if not title:
                return _action_dict(action, "failed", "Empty title")
            model_dict["metadata"]["title"] = title
            return _action_dict(action, "executed", f"Set title: {title}")

        elif action_type == "set_language":
            lang = params.get("language", "")
            if not lang:
                return _action_dict(action, "failed", "Empty language")
            model_dict["metadata"]["language"] = lang
            return _action_dict(action, "executed", f"Set language: {lang}")

        elif action_type == "set_link_text":
            new_text = params.get("new_text", "")
            if not new_text:
                return _action_dict(action, "failed", "Empty link text")
            if link_id_to_idx is None:
                return _action_dict(action, "failed", "No link lookup available")
            idx = link_id_to_idx.get(element_id)
            if idx is None:
                return _action_dict(action, "failed", f"Link not found: {element_id}")
            model_dict.setdefault("links", [])[idx]["text"] = new_text
            return _action_dict(action, "executed", f"Set link text: {new_text[:80]}")

        elif action_type == "add_math_description":
            description = params.get("description", "")
            if not description:
                return _action_dict(action, "failed", "Empty description")
            math_list = model_dict.get("math", [])
            math_idx = next(
                (i for i, m in enumerate(math_list) if m.get("id") == element_id),
                None,
            )
            if math_idx is None:
                return _action_dict(action, "failed", f"Math element not found: {element_id}")
            confidence = params.get("confidence", 0.95)
            model_dict["math"][math_idx]["description"] = description
            model_dict["math"][math_idx]["confidence"] = confidence
            return _action_dict(action, "executed", f"Set math description: {description[:80]}")

        elif action_type == "describe_tikz":
            tikz_source = params.get("tikz_source", "")
            if not tikz_source:
                return _action_dict(action, "failed", "No TikZ source provided")

            math_list = model_dict.get("math", [])
            math_idx = next(
                (i for i, m in enumerate(math_list) if m.get("id") == element_id),
                None,
            )
            if math_idx is None:
                return _action_dict(action, "failed", f"Math element not found: {element_id}")

            # Load prompt
            prompt_path = Path(__file__).parent.parent / "prompts" / "tikz_description.md"
            if prompt_path.exists():
                prompt_template = prompt_path.read_text(encoding="utf-8")
            else:
                prompt_template = (
                    "Describe this TikZ diagram thoroughly for a blind student. "
                    "Include all nodes, edges, labels, and relationships.\n\n"
                    "TikZ source:\n{tikz_source}"
                )
            prompt = prompt_template.replace("{tikz_source}", tikz_source)

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                return _action_dict(action, "failed", "ANTHROPIC_API_KEY not set — keeping placeholder")

            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=api_key)
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                )
                description = response.content[0].text.strip()
                model_dict["math"][math_idx]["description"] = description
                return _action_dict(action, "executed", f"TikZ described: {description[:80]}")
            except Exception as e:
                logger.warning("TikZ description failed for %s: %s", element_id, e)
                return _action_dict(action, "failed", f"Claude API failed: {e}")

        elif action_type == "fix_all_contrast":
            # For PDFs, check each run's color against background and fix if needed
            default_bg = params.get("default_bg", "#FFFFFF")
            fixed = 0
            for para in model_dict["paragraphs"]:
                for run in para.get("runs", []):
                    color = run.get("color")
                    if not color or not color.startswith("#"):
                        continue
                    font_size = run.get("font_size_pt")
                    is_bold = run.get("bold", False)
                    result = check_contrast(color, default_bg, font_size, is_bold)
                    if not result.passes:
                        fix_result = fix_contrast(color, default_bg, font_size, is_bold)
                        original_color = color
                        run["color"] = fix_result.fixed_color
                        # Collect for content stream color replacement
                        if contrast_color_map is not None:
                            contrast_color_map[original_color] = fix_result.fixed_color
                        fixed += 1
            return _action_dict(action, "executed", f"Fixed {fixed} contrast issues")

        else:
            return _action_dict(action, "skipped", f"Unknown action type: {action_type}")

    except Exception as e:
        logger.exception("PDF action failed: %s", action.action_type)
        return _action_dict(action, "failed", f"Exception: {e}")


def _execute_action(doc_or_prs, action: RemediationAction, paragraphs: list | None = None, is_pptx: bool = False) -> dict:
    """Execute a single remediation action."""
    try:
        action_type = action.action_type
        params = action.parameters

        if action_type == "set_alt_text":
            alt = params.get("alt_text", "")
            if not alt:
                return _action_dict(action, "failed", "Empty alt text")

            if is_pptx:
                slide_idx = params.get("slide_index")
                shape_idx = params.get("shape_index")
                if slide_idx is None or shape_idx is None:
                    return _action_dict(action, "failed", "Missing slide_index or shape_index for pptx")
                result = set_alt_text_pptx(doc_or_prs, slide_idx, shape_idx, alt)
            else:
                para_idx = params.get("paragraph_index")
                draw_idx = params.get("drawing_index", 0)
                if para_idx is None:
                    return _action_dict(action, "failed", "Missing paragraph_index")
                result = set_alt_text(doc_or_prs, para_idx, alt, draw_idx)

            if result.success:
                return _action_dict(action, "executed", f"Set alt text: {alt[:80]}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_decorative":
            if is_pptx:
                slide_idx = params.get("slide_index")
                shape_idx = params.get("shape_index")
                if slide_idx is None or shape_idx is None:
                    return _action_dict(action, "failed", "Missing slide_index or shape_index for pptx")
                result = set_alt_text_pptx(doc_or_prs, slide_idx, shape_idx, "")
            else:
                para_idx = params.get("paragraph_index")
                draw_idx = params.get("drawing_index", 0)
                if para_idx is None:
                    return _action_dict(action, "failed", "Missing paragraph_index")
                result = set_decorative(doc_or_prs, para_idx, draw_idx)

            if result.success:
                return _action_dict(action, "executed", "Marked as decorative")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_heading_level":
            if is_pptx:
                return _action_dict(action, "skipped", "Heading levels not modifiable in PPTX")

            para_idx = params.get("paragraph_index")
            level = params.get("level")

            if para_idx is None:
                return _action_dict(action, "failed", "Missing paragraph_index")
            if level is None:
                return _action_dict(action, "failed", "Missing level")

            result = set_heading_level(doc_or_prs, para_idx, level)
            if result.success:
                return _action_dict(action, "executed", f"Set to Heading {level}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "mark_header_rows":
            if is_pptx:
                return _action_dict(action, "skipped", "Table headers not modifiable in PPTX")

            tbl_idx = params.get("table_index")
            count = params.get("header_count", 1)

            if tbl_idx is None:
                return _action_dict(action, "failed", "Missing table_index")

            result = mark_header_rows(doc_or_prs, tbl_idx, count)
            if result.success:
                return _action_dict(action, "executed", f"Marked {count} header row(s)")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_title":
            title = params.get("title", "")
            if not title:
                return _action_dict(action, "failed", "Empty title")

            if is_pptx:
                result = set_title_pptx(doc_or_prs, title)
            else:
                result = set_title(doc_or_prs, title)

            if result.success:
                return _action_dict(action, "executed", f"Set title: {title}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_language":
            lang = params.get("language", "")
            if not lang:
                return _action_dict(action, "failed", "Empty language")

            if is_pptx:
                result = set_language_pptx(doc_or_prs, lang)
            else:
                result = set_language(doc_or_prs, lang)

            if result.success:
                return _action_dict(action, "executed", f"Set language: {lang}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_link_text":
            if is_pptx:
                return _action_dict(action, "skipped", "Link text modification not yet supported for PPTX")

            new_text = params.get("new_text", "")
            if not new_text:
                return _action_dict(action, "failed", "Empty new_text")

            # Parse link index from element_id (e.g., "link_5" → 5)
            try:
                link_index = int(action.element_id.split("_", 1)[1])
            except (IndexError, ValueError):
                return _action_dict(action, "failed", f"Cannot parse link index from: {action.element_id}")

            result = set_link_text(doc_or_prs, link_index, new_text)
            if result.success:
                return _action_dict(action, "executed", f"Set link text: {new_text[:80]}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "add_math_description":
            # Math descriptions live on the DocumentModel, not the docx/pptx file directly.
            # The executor acknowledges the action; the model update happens in execute_pdf.
            return _action_dict(action, "skipped", "Math descriptions apply to document model only (not docx/pptx)")

        elif action_type == "fix_all_contrast":
            if is_pptx:
                return _action_dict(action, "skipped", "Contrast fix not yet supported for PPTX")

            if paragraphs is None:
                return _action_dict(action, "failed", "No parsed paragraphs available for contrast analysis")

            default_bg = params.get("default_bg", "#FFFFFF")
            result = fix_all_document_contrast(doc_or_prs, paragraphs, default_bg)
            if result.fixes_applied > 0 or result.success:
                detail = f"Fixed {result.fixes_applied} contrast issues"
                if result.fixes_failed > 0:
                    detail += f" ({result.fixes_failed} failed)"
                return _action_dict(action, "executed", detail)
            return _action_dict(action, "failed", f"Contrast fix failed: {'; '.join(result.errors)}")

        else:
            return _action_dict(action, "skipped", f"Unknown action type: {action_type}")

    except Exception as e:
        logger.exception("Action execution failed: %s", action.action_type)
        return _action_dict(action, "failed", f"Exception: {e}")


def _action_dict(action: RemediationAction, status: str, detail: str) -> dict:
    """Build a result dict for an action."""
    return {
        "element_id": action.element_id,
        "action_type": action.action_type,
        "parameters": action.parameters,
        "rationale": action.rationale,
        "status": status,
        "result_detail": detail,
    }
