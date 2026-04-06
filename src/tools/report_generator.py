"""Generate compliance reports from RemediationResult.

Produces an HTML report with two layers:
1. Human-centered summary (plain language for faculty)
2. Technical WCAG details (collapsible, for accessibility offices)
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from pathlib import Path

from src.models.pipeline import RemediationResult, VisualQAFinding, estimate_usage_cost
from src.tools.scanned_page_ocr import ScannedPageResult

logger = logging.getLogger(__name__)


# ── Plain-language descriptions for action types ──────────────────
_PLAIN_DESCRIPTIONS: dict[str, str] = {
    "set_language": "Set the document language so screen readers pronounce words correctly.",
    "set_title": "Added a document title so assistive technology can identify it.",
    "set_heading_level": "Organized the document with proper headings so screen reader users can navigate between sections.",
    "mark_header_rows": "Marked table headers so screen readers can announce column names while reading data.",
    "set_alt_text": "Added descriptions to images so blind users understand their content.",
    "set_decorative": "Marked decorative images so screen readers skip them instead of reading meaningless filenames.",
    "fix_contrast": "Adjusted text colors to ensure sufficient contrast for low-vision readers.",
    "fix_all_contrast": "Adjusted text colors to ensure sufficient contrast for low-vision readers.",
    "set_link_text": "Improved link text so users know where links go without seeing the surrounding context.",
    "convert_to_list": "Converted text that looks like a list into a proper list so screen readers announce it correctly.",
    "add_math_description": "Generated natural language descriptions for mathematical equations.",
}

# ── Impact categories for grouping "What We Did" ─────────────────
_IMPACT_CATEGORIES: dict[str, dict] = {
    "navigation": {
        "label": "Navigation & Structure",
        "types": {"set_heading_level", "convert_to_list", "set_title", "set_language", "mark_header_rows"},
    },
    "images": {
        "label": "Images & Media",
        "types": {"set_alt_text", "set_decorative"},
    },
    "text": {
        "label": "Text & Readability",
        "types": {"fix_contrast", "fix_all_contrast", "set_link_text"},
    },
    "math": {
        "label": "Mathematical Content",
        "types": {"add_math_description"},
    },
}


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes} min {secs} sec" if secs else f"{minutes} min"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours} hr {mins} min"


def _build_cost_section(result: RemediationResult) -> str:
    """Build the API cost section HTML."""
    cost = result.cost_summary
    if not cost.usage_records:
        return ""

    phase_names = {
        "comprehension": "Document Analysis",
        "comprehension_images": "Image Analysis",
        "strategy": "Remediation Planning",
        "review": "Quality Review",
    }

    rows = ""
    for u in cost.usage_records:
        phase_label = phase_names.get(u.phase, u.phase)
        item_cost = estimate_usage_cost(u)
        rows += f"""      <tr>
        <td>{_esc(phase_label)}</td>
        <td style="text-align:right;">${item_cost:.2f}</td>
      </tr>
"""

    return f"""<div class="section">
  <h2>Processing Details</h2>
  <table>
    <thead>
      <tr>
        <th>Phase</th>
        <th style="text-align:right;">Est. Cost</th>
      </tr>
    </thead>
    <tbody>
{rows}    </tbody>
    <tfoot>
      <tr style="font-weight:600; border-top: 2px solid #333;">
        <td>Total</td>
        <td style="text-align:right;">${cost.estimated_cost_usd:.2f}</td>
      </tr>
    </tfoot>
  </table>
</div>"""


def _parse_original_issues(result: RemediationResult) -> dict[str, list[str]]:
    """Parse raw validation report into grouped issues by criterion.

    Returns dict mapping criterion header -> list of detail strings.
    """
    grouped: dict[str, list[str]] = {}
    current_header = ""

    if not result.comprehension or not result.comprehension.raw_validation_report:
        return grouped

    for line in result.comprehension.raw_validation_report.split("\n"):
        line = line.strip()
        if line.startswith("[FAIL]") or line.startswith("[WARN]"):
            current_header = line
            if current_header not in grouped:
                grouped[current_header] = []
        elif line.startswith("- ") and current_header:
            grouped[current_header].append(line[2:].strip())

    return grouped


def _summarize_issue_group(header: str, details: list[str]) -> str:
    """Summarize an issue group into a concise human-readable line.

    Instead of listing every instance, give a count and representative examples.
    """
    is_fail = header.startswith("[FAIL]")
    icon = "&#10060;" if is_fail else "&#9888;&#65039;"
    criterion_text = header[6:].strip()

    # Filter out OCR noise from fake heading candidates
    real_details = []
    noise_count = 0
    for d in details:
        # Skip obvious OCR garbage in fake heading detection
        if "fake heading" in d.lower():
            text_start = d.find("'")
            text_end = d.rfind("'")
            if text_start >= 0 and text_end > text_start:
                text = d[text_start + 1:text_end]
                # Skip if it's mostly symbols, very short, or looks like noise
                alpha_chars = sum(1 for c in text if c.isalpha())
                if alpha_chars < 3 or len(text) < 3:
                    noise_count += 1
                    continue
            real_details.append(d)
        else:
            real_details.append(d)

    count = len(real_details) + noise_count
    html_parts = []
    html_parts.append(
        f'<div class="issue-group">'
        f'<div class="issue-header">{icon} <strong>{_esc(criterion_text)}</strong>'
        f' <span class="issue-count">{count} item{"s" if count != 1 else ""}</span></div>'
    )

    # Show up to 5 real details, summarize the rest
    shown = real_details[:5]
    hidden = len(real_details) - 5 + noise_count

    if shown:
        html_parts.append('<ul class="issue-details">')
        for d in shown:
            html_parts.append(f'  <li>{_esc(d)}</li>')
        if hidden > 0:
            html_parts.append(f'  <li class="more-items">...and {hidden} more</li>')
        html_parts.append("</ul>")

    html_parts.append("</div>")
    return "\n".join(html_parts)


def _get_action_description(action) -> str:
    """Get a human-readable description of what an action did."""
    detail = action.result_detail or ""

    if action.action_type == "set_title":
        title = action.parameters.get("title", "")
        return f"Set document title to \"{_esc(title)}\""

    elif action.action_type == "set_language":
        lang = action.parameters.get("language", "")
        lang_names = {"en": "English", "en-US": "English (US)", "es": "Spanish", "fr": "French"}
        lang_display = lang_names.get(lang, lang)
        return f"Set document language to {_esc(lang_display)}"

    elif action.action_type == "set_heading_level":
        level = action.parameters.get("level", "?")
        return f"Converted to Heading {level}"

    elif action.action_type == "set_alt_text":
        alt = action.parameters.get("alt_text", "")
        if len(alt) > 120:
            return f"Added alt text ({len(alt)} chars): \"{_esc(alt[:100])}...\""
        return f"Added alt text: \"{_esc(alt)}\""

    elif action.action_type == "set_decorative":
        return "Marked as decorative (empty alt text)"

    elif action.action_type == "mark_header_rows":
        count = action.parameters.get("header_count", 1)
        return f"Marked {count} header row{'s' if count > 1 else ''}"

    elif action.action_type == "fix_all_contrast":
        return detail or "Fixed low-contrast text colors"

    elif action.action_type == "set_link_text":
        new_text = action.parameters.get("new_text", "")
        return f"Changed link text to \"{_esc(new_text[:80])}\""

    return _esc(detail) if detail else _esc(action.action_type)


def _strip_element_ids(text: str) -> str:
    """Remove internal element IDs like 'ocr_p_72', 'img_3', 'tbl_1' from text."""
    # Remove patterns like "ocr_p_72", "p_12", "img_3", "tbl_0", "link_5"
    cleaned = re.sub(r'\b(ocr_)?[a-z]+_\d+\b', '', text)
    # Clean up leftover punctuation artifacts
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    cleaned = re.sub(r'^\s*[,;:]\s*', '', cleaned)
    cleaned = re.sub(r'\s*[,;:]\s*$', '', cleaned)
    return cleaned.strip()


def _build_what_we_did(result: RemediationResult) -> str:
    """Build the plain-language 'What We Did' section."""
    executed = [a for a in result.strategy.actions if a.status == "executed"]
    if not executed:
        return ""

    # Group executed actions by impact category
    categorized: dict[str, list] = {k: [] for k in _IMPACT_CATEGORIES}
    uncategorized: list = []

    for a in executed:
        placed = False
        for cat_key, cat_info in _IMPACT_CATEGORIES.items():
            if a.action_type in cat_info["types"]:
                categorized[cat_key].append(a)
                placed = True
                break
        if not placed:
            uncategorized.append(a)

    parts = []
    for cat_key, cat_info in _IMPACT_CATEGORIES.items():
        actions = categorized[cat_key]
        if not actions:
            continue

        # Deduplicate by action_type and build plain descriptions
        type_counts: dict[str, int] = {}
        for a in actions:
            type_counts[a.action_type] = type_counts.get(a.action_type, 0) + 1

        items_html = ""
        for action_type, count in type_counts.items():
            desc = _PLAIN_DESCRIPTIONS.get(action_type, action_type.replace("_", " ").title())
            count_note = f" ({count} items)" if count > 1 else ""
            items_html += f'<li>{_esc(desc)}{count_note}</li>\n'

        parts.append(
            f'<div class="impact-group">'
            f'<h3>{_esc(cat_info["label"])}</h3>'
            f'<ul>{items_html}</ul>'
            f'</div>'
        )

    if uncategorized:
        type_counts_unc: dict[str, int] = {}
        for a in uncategorized:
            type_counts_unc[a.action_type] = type_counts_unc.get(a.action_type, 0) + 1
        items_html = ""
        for action_type, count in type_counts_unc.items():
            desc = _PLAIN_DESCRIPTIONS.get(action_type, action_type.replace("_", " ").title())
            count_note = f" ({count} items)" if count > 1 else ""
            items_html += f'<li>{_esc(desc)}{count_note}</li>\n'
        parts.append(
            f'<div class="impact-group">'
            f'<h3>Other Improvements</h3>'
            f'<ul>{items_html}</ul>'
            f'</div>'
        )

    return "\n".join(parts)


def _build_needs_attention(result: RemediationResult) -> str:
    """Build the plain-language 'What Needs Your Attention' section."""
    attention_items: list[str] = []
    seen: set[str] = set()

    # Count images without alt text from review findings
    no_alt_count = 0
    failed_actions = [a for a in result.strategy.actions if a.status == "failed"]

    for f in result.review_findings:
        if f.finding_type in ("failure", "needs_human_review"):
            cleaned = _strip_element_ids(f.detail)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)

            # Categorize into plain language
            detail_lower = f.detail.lower()
            if "alt text" in detail_lower or "image description" in detail_lower or "without description" in detail_lower:
                no_alt_count += 1
            else:
                attention_items.append(cleaned)

    # Aggregate image issues into one item
    if no_alt_count > 0:
        attention_items.insert(
            0,
            f"We found {no_alt_count} image{'s' if no_alt_count != 1 else ''} that may need "
            f"better descriptions. Please review them and add descriptions, or confirm "
            f"they are decorative."
        )

    # Failed actions
    if failed_actions:
        fail_count = len(failed_actions)
        attention_items.append(
            f"{fail_count} fix{'es' if fail_count != 1 else ''} could not be applied "
            f"automatically. See the technical details below for specifics."
        )

    # Human review items (strip IDs)
    for item in result.items_for_human_review:
        cleaned = _strip_element_ids(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            attention_items.append(cleaned)

    if not attention_items:
        return ""

    items_html = "\n".join(f'<li>{_esc(item)}</li>' for item in attention_items)
    return f'<ul class="attention-list">{items_html}</ul>'


def _build_output_files(result: RemediationResult) -> str:
    """Build the 'Your Output Files' section based on what was produced."""
    # Infer source format from input path extension
    ext = Path(result.input_path).suffix.lower() if result.input_path else ""

    items = []

    if result.output_path:
        out_name = Path(result.output_path).name
        out_ext = Path(result.output_path).suffix.lower()
        if out_ext == ".pdf":
            items.append(
                f'<li><strong>{_esc(out_name)}</strong> — '
                f'The accessible PDF. Upload this to your LMS (Canvas, Blackboard, D2L).</li>'
            )
        elif out_ext == ".docx":
            items.append(
                f'<li><strong>{_esc(out_name)}</strong> — '
                f'The remediated Word document with accessibility fixes applied.</li>'
            )
        elif out_ext == ".pptx":
            items.append(
                f'<li><strong>{_esc(out_name)}</strong> — '
                f'The remediated PowerPoint with accessibility fixes applied.</li>'
            )
        else:
            items.append(
                f'<li><strong>{_esc(out_name)}</strong> — '
                f'The remediated file with accessibility fixes applied.</li>'
            )

    if result.companion_output_path:
        companion_name = Path(result.companion_output_path).name
        items.append(
            f'<li><strong>{_esc(companion_name)}</strong> — '
            f'An accessible HTML version. Give this to students who use screen readers '
            f'for the best experience.</li>'
        )

    if result.input_path:
        in_name = Path(result.input_path).name
        items.append(
            f'<li><strong>{_esc(in_name)}</strong> (original) — '
            f'Your original file is unchanged.</li>'
        )

    if not items:
        return ""

    return '<ul class="file-list">' + "\n".join(items) + '</ul>'


def _build_visual_qa_section(
    findings: list[VisualQAFinding],
    output_dir: str,
) -> str:
    """Build the 'Visual Quality Check' HTML section for the report.

    Only shows high and medium severity findings. Returns empty string
    if there are no high/medium findings (section is omitted from report).
    """
    import base64

    visible = [f for f in findings if f.severity in ("high", "medium")]
    if not visible:
        return ""

    qa_dir = Path(output_dir) / "visual_qa" if output_dir else None

    by_page: dict[int, list[VisualQAFinding]] = {}
    for f in visible:
        by_page.setdefault(f.original_page, []).append(f)

    pages_affected = len(by_page)
    total_findings = len(visible)

    html_parts = [
        '<div class="section">',
        '<h2>Visual Quality Check</h2>',
        f'<p>Visual comparison found <strong>{total_findings} content {"issue" if total_findings == 1 else "issues"}</strong> '
        f'across {pages_affected} {"page" if pages_affected == 1 else "pages"} that may need attention.</p>',
    ]

    for page_num in sorted(by_page.keys()):
        page_findings = by_page[page_num]
        html_parts.append('<div class="visual-qa-page" style="margin: 1.5em 0; padding: 1em; border: 1px solid #ddd; border-radius: 8px;">')
        html_parts.append(f'<h3>Page {page_num + 1}</h3>')

        html_parts.append('<div style="display: flex; gap: 1em; margin: 1em 0;">')

        orig_path = qa_dir / f"original_page_{page_num}.png" if qa_dir else None
        if orig_path and orig_path.exists():
            orig_b64 = base64.b64encode(orig_path.read_bytes()).decode("ascii")
            html_parts.append(
                f'<div style="flex: 1;"><p style="font-weight: bold; margin-bottom: 0.5em;">Original</p>'
                f'<img src="data:image/png;base64,{orig_b64}" style="max-width: 400px; border: 1px solid #ccc;" '
                f'alt="Original page {page_num + 1}"></div>'
            )

        rendered_page = page_findings[0].rendered_page
        if rendered_page is not None and qa_dir:
            rend_path = qa_dir / f"rendered_page_{rendered_page}.png"
            if rend_path.exists():
                rend_b64 = base64.b64encode(rend_path.read_bytes()).decode("ascii")
                html_parts.append(
                    f'<div style="flex: 1;"><p style="font-weight: bold; margin-bottom: 0.5em;">Rendered</p>'
                    f'<img src="data:image/png;base64,{rend_b64}" style="max-width: 400px; border: 1px solid #ccc;" '
                    f'alt="Rendered page {rendered_page + 1}"></div>'
                )

        html_parts.append('</div>')

        for f in page_findings:
            severity_color = "#d32f2f" if f.severity == "high" else "#f57c00"
            severity_label = f.severity.upper()
            html_parts.append(
                f'<p style="margin: 0.5em 0;"><span style="color: {severity_color}; font-weight: bold;">'
                f'[{severity_label}]</span> {f.description}</p>'
            )

        html_parts.append('</div>')

    html_parts.append('</div>')
    return "\n".join(html_parts)


def _generate_ocr_comparison_html(
    hybrid_result: ScannedPageResult,
    mistral_result: ScannedPageResult,
) -> str:
    """Generate HTML for the OCR engine comparison section."""
    hybrid_paras = hybrid_result.paragraphs
    hybrid_headings = [p for p in hybrid_paras if p.heading_level is not None]
    hybrid_tables = hybrid_result.tables

    mistral_headings = [p for p in mistral_result.paragraphs if p.heading_level is not None]

    # Find differences
    diffs = []
    h_diff = len(mistral_headings) - len(hybrid_headings)
    if h_diff > 0:
        diffs.append(f"Mistral found {h_diff} additional heading(s) not detected by the standard engine")
    elif h_diff < 0:
        diffs.append(f"Standard engine found {-h_diff} additional heading(s) not detected by Mistral")

    t_diff = len(mistral_result.tables) - len(hybrid_tables)
    if t_diff > 0:
        diffs.append(f"Mistral found {t_diff} additional table(s) not detected by the standard engine")
    elif t_diff < 0:
        diffs.append(f"Standard engine found {-t_diff} additional table(s) not detected by Mistral")

    diff_html = ""
    if diffs:
        diff_items = "".join(f"<li>{d}</li>" for d in diffs)
        diff_html = f"<h4>Differences Found</h4><ul>{diff_items}</ul>"

    return f"""
    <div class="section" style="border: 2px solid #e0e0e0; border-radius: 8px; background: #fafafa;">
        <h2>OCR Engine Comparison (Experimental)</h2>
        <p>This document was processed through two OCR engines for quality comparison.
        Only the standard engine's output was used for remediation.</p>
        <table style="border-collapse: collapse; width: 100%; margin: 1em 0;">
            <thead>
                <tr style="background: #f0f0f0;">
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Metric</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Standard (Hybrid)</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Experimental (Mistral)</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Paragraphs</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_paras)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_result.paragraphs)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Headings</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_headings)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_headings)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">Tables</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(hybrid_tables)}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{len(mistral_result.tables)}</td>
                </tr>
            </tbody>
        </table>
        {diff_html}
    </div>
    """


def generate_report_html(
    result: RemediationResult,
    visual_qa_findings: list[VisualQAFinding] | None = None,
    output_dir: str = "",
    hybrid_ocr_result: ScannedPageResult | None = None,
    mistral_ocr_result: ScannedPageResult | None = None,
) -> str:
    """Generate an HTML compliance report from a RemediationResult."""
    now = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    input_name = result.input_path.split("/")[-1] if result.input_path else "Unknown"
    output_name = result.output_path.split("/")[-1] if result.output_path else "N/A"
    time_display = _format_time(result.processing_time_seconds)

    # Categorize actions
    executed = [a for a in result.strategy.actions if a.status == "executed"]
    failed = [a for a in result.strategy.actions if a.status == "failed"]
    skipped = [a for a in result.strategy.actions if a.status == "skipped"]

    # Categorize review findings
    concerns = [f for f in result.review_findings if f.finding_type == "concern"]
    failures = [f for f in result.review_findings if f.finding_type == "failure"]
    human_review = [f for f in result.review_findings if f.finding_type == "needs_human_review"]

    # WCAG criteria descriptions
    wcag_names = {
        "1.1.1": "Non-text Content",
        "1.3.1": "Info and Relationships",
        "1.4.1": "Use of Color",
        "1.4.3": "Contrast (Minimum)",
        "2.4.1": "Bypass Blocks",
        "2.4.2": "Page Titled",
        "2.4.4": "Link Purpose",
        "2.4.6": "Headings and Labels",
        "3.1.1": "Language of Page",
        "3.1.2": "Language of Parts",
    }

    action_wcag = {
        "set_title": "2.4.2",
        "set_language": "3.1.1",
        "set_heading_level": "1.3.1",
        "mark_header_rows": "1.3.1",
        "set_alt_text": "1.1.1",
        "set_decorative": "1.1.1",
        "fix_contrast": "1.4.3",
        "fix_all_contrast": "1.4.3",
        "convert_to_list": "1.3.1",
        "set_link_text": "2.4.4",
    }

    # ── Status banner ──
    total_actions = len(executed) + len(failed) + len(skipped)
    if result.issues_before == 0:
        banner_class = "banner-pass"
        banner_text = "No Accessibility Issues Found"
        banner_sub = "This document meets WCAG 2.1 AA requirements."
    elif len(failed) == 0 and len(failures) == 0:
        banner_class = "banner-pass"
        banner_text = "Remediation Complete"
        banner_sub = f"All {len(executed)} fixes applied successfully."
    elif len(failures) > 0:
        banner_class = "banner-warn"
        banner_text = "Needs Attention"
        banner_sub = f"{len(executed)} fixes applied, {len(failures)} issue{'s' if len(failures) != 1 else ''} remaining."
    else:
        banner_class = "banner-warn"
        banner_text = "Partially Remediated"
        banner_sub = f"{len(executed)} of {total_actions} actions completed."

    # ── Original issues (grouped) ──
    issue_groups = _parse_original_issues(result)
    pre_issues_html = ""
    if issue_groups:
        parts = []
        for header, details in issue_groups.items():
            parts.append(_summarize_issue_group(header, details))
        pre_issues_html = "\n".join(parts)

    # ── Actions by category ──
    # Group actions by type for a cleaner view
    action_categories = {
        "metadata": {"label": "Document Metadata", "icon": "&#128196;", "actions": []},
        "contrast": {"label": "Color Contrast", "icon": "&#127912;", "actions": []},
        "links": {"label": "Link Text", "icon": "&#128279;", "actions": []},
        "structure": {"label": "Document Structure", "icon": "&#128209;", "actions": []},
        "images": {"label": "Image Descriptions", "icon": "&#128444;", "actions": []},
    }

    for a in result.strategy.actions:
        if a.action_type in ("set_title", "set_language"):
            action_categories["metadata"]["actions"].append(a)
        elif a.action_type in ("fix_contrast", "fix_all_contrast"):
            action_categories["contrast"]["actions"].append(a)
        elif a.action_type == "set_link_text":
            action_categories["links"]["actions"].append(a)
        elif a.action_type in ("set_heading_level", "mark_header_rows", "convert_to_list"):
            action_categories["structure"]["actions"].append(a)
        elif a.action_type in ("set_alt_text", "set_decorative"):
            action_categories["images"]["actions"].append(a)
        else:
            action_categories["structure"]["actions"].append(a)

    actions_html_parts = []
    for cat_key, cat in action_categories.items():
        if not cat["actions"]:
            continue

        cat_executed = [a for a in cat["actions"] if a.status == "executed"]
        cat_failed = [a for a in cat["actions"] if a.status == "failed"]

        status_badge = ""
        if cat_failed:
            status_badge = f'<span class="badge badge-fail">{len(cat_failed)} failed</span>'
        elif cat_executed:
            status_badge = f'<span class="badge badge-pass">{len(cat_executed)} done</span>'

        actions_html_parts.append(
            f'<div class="action-category">'
            f'<h3>{cat["icon"]} {cat["label"]} {status_badge}</h3>'
        )

        for a in cat["actions"]:
            criterion = action_wcag.get(a.action_type, "")
            criterion_label = f"{criterion} {wcag_names.get(criterion, '')}" if criterion else ""

            if a.status == "executed":
                icon = "&#9989;"
                row_class = "action-pass"
            elif a.status == "failed":
                icon = "&#10060;"
                row_class = "action-fail"
            else:
                icon = "&#9898;"
                row_class = "action-skip"

            desc = _get_action_description(a)
            actions_html_parts.append(
                f'<div class="action-item {row_class}">'
                f'{icon} {desc}'
                f'<span class="action-criterion">{_esc(criterion_label)}</span>'
                f'</div>'
            )

        actions_html_parts.append("</div>")

    actions_html = "\n".join(actions_html_parts)

    # ── Review findings (merged, deduplicated) ──
    review_html = ""
    # Collect all human review items and deduplicate
    all_human_items: list[str] = []
    seen_items: set[str] = set()

    for f in failures:
        detail = f.detail
        if detail not in seen_items:
            seen_items.add(detail)
            criterion_label = f"{f.criterion} {wcag_names.get(f.criterion, '')}" if f.criterion else ""
            review_html += (
                f'<div class="review-item review-failure">'
                f'<div class="review-icon">&#10060;</div>'
                f'<div class="review-content">'
                f'<strong>Issue:</strong> {_esc(detail)}'
                f'{"<span class=\"action-criterion\">" + _esc(criterion_label) + "</span>" if criterion_label else ""}'
                f'</div></div>\n'
            )

    for f in concerns:
        detail = f.detail
        if detail not in seen_items:
            seen_items.add(detail)
            criterion_label = f"{f.criterion} {wcag_names.get(f.criterion, '')}" if f.criterion else ""
            review_html += (
                f'<div class="review-item review-concern">'
                f'<div class="review-icon">&#9888;&#65039;</div>'
                f'<div class="review-content">'
                f'<strong>Note:</strong> {_esc(detail)}'
                f'{"<span class=\"action-criterion\">" + _esc(criterion_label) + "</span>" if criterion_label else ""}'
                f'</div></div>\n'
            )

    # Merge human review items from review findings and strategy
    for f in human_review:
        if f.detail not in seen_items:
            seen_items.add(f.detail)
            all_human_items.append(f.detail)

    for item in result.items_for_human_review:
        if item not in seen_items:
            seen_items.add(item)
            all_human_items.append(item)

    human_review_html = ""
    if all_human_items:
        human_review_html = '<div class="human-review-list"><h3>Items for Human Review</h3><ul>\n'
        for item in all_human_items:
            human_review_html += f'  <li>&#128269; {_esc(item)}</li>\n'
        human_review_html += "</ul></div>"

    has_review_content = review_html or human_review_html

    # ── Strategy summary ──
    strategy_summary = result.strategy.strategy_summary.strip()

    # ── Human-readable sections ──
    what_we_did_html = _build_what_we_did(result)
    needs_attention_html = _build_needs_attention(result)
    output_files_html = _build_output_files(result)

    visual_qa_html = ""
    if visual_qa_findings:
        visual_qa_html = _build_visual_qa_section(visual_qa_findings, output_dir)

    ocr_comparison_html = ""
    if hybrid_ocr_result and mistral_ocr_result and mistral_ocr_result.success:
        ocr_comparison_html = _generate_ocr_comparison_html(hybrid_ocr_result, mistral_ocr_result)

    # ── Build full HTML ──
    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Accessibility Report — {_esc(input_name)}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.6; color: #1a1a1a; background: #f5f5f5;
      padding: 2rem; max-width: 960px; margin: 0 auto;
    }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; color: #1a1a1a; }}
    h2 {{ font-size: 1.15rem; margin: 1.5rem 0 0.75rem; padding-bottom: 0.3rem;
          border-bottom: 2px solid #e0e0e0; color: #333; }}
    h3 {{ font-size: 1rem; margin: 0.75rem 0 0.5rem; color: #444; }}

    /* Status banner */
    .banner {{ padding: 1.25rem 1.5rem; border-radius: 8px; margin-bottom: 1.5rem;
               box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .banner-pass {{ background: #f0fdf4; border-left: 4px solid #16a34a; }}
    .banner-warn {{ background: #fffbeb; border-left: 4px solid #d97706; }}
    .banner-fail {{ background: #fef2f2; border-left: 4px solid #dc2626; }}
    .banner h1 {{ font-size: 1.4rem; }}
    .banner-pass h1 {{ color: #16a34a; }}
    .banner-warn h1 {{ color: #92400e; }}
    .banner-fail h1 {{ color: #dc2626; }}
    .banner-sub {{ color: #555; font-size: 0.95rem; margin-top: 0.25rem; }}

    .report-meta {{ color: #666; font-size: 0.85rem; margin-top: 0.5rem;
                    display: flex; flex-wrap: wrap; gap: 0.25rem 1.5rem; }}

    /* Summary cards */
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
                     gap: 0.75rem; margin: 1rem 0; }}
    .summary-card {{ background: white; padding: 0.75rem; border-radius: 8px; text-align: center;
                     box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .summary-card .number {{ font-size: 1.75rem; font-weight: 700; }}
    .summary-card .label {{ font-size: 0.75rem; color: #666; text-transform: uppercase;
                            letter-spacing: 0.03em; }}
    .status-pass .number {{ color: #16a34a; }}
    .status-partial .number {{ color: #d97706; }}
    .status-fail .number {{ color: #dc2626; }}

    .section {{ background: white; padding: 1.5rem; border-radius: 8px;
                margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}

    /* Human summary sections */
    .impact-group {{ margin-bottom: 0.75rem; }}
    .impact-group h3 {{ font-size: 0.95rem; color: #333; margin-bottom: 0.25rem; }}
    .impact-group ul {{ padding-left: 1.5rem; margin: 0; }}
    .impact-group li {{ padding: 0.2rem 0; font-size: 0.9rem; color: #444; }}

    .attention-list {{ padding-left: 1.5rem; margin: 0; }}
    .attention-list li {{ padding: 0.35rem 0; font-size: 0.9rem; color: #555;
                          line-height: 1.5; }}

    .file-list {{ padding-left: 0; margin: 0; list-style: none; }}
    .file-list li {{ padding: 0.4rem 0; font-size: 0.9rem; color: #444;
                     border-bottom: 1px solid #f0f0f0; }}
    .file-list li:last-child {{ border-bottom: none; }}

    /* Collapsible technical details */
    .technical-details {{ background: white; border-radius: 8px; margin-bottom: 1rem;
                          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .technical-details summary {{
      padding: 1rem 1.5rem; cursor: pointer; font-size: 1rem; font-weight: 600;
      color: #555; list-style: none; display: flex; align-items: center; gap: 0.5rem;
    }}
    .technical-details summary::-webkit-details-marker {{ display: none; }}
    .technical-details summary::before {{
      content: "\\25B6"; font-size: 0.7rem; transition: transform 0.2s;
    }}
    .technical-details[open] summary::before {{ transform: rotate(90deg); }}
    .technical-details .details-content {{ padding: 0 1.5rem 1.5rem; }}

    /* Issue groups */
    .issue-group {{ margin-bottom: 0.75rem; }}
    .issue-header {{ font-size: 0.95rem; margin-bottom: 0.25rem; }}
    .issue-count {{ font-size: 0.8rem; color: #888; font-weight: normal; }}
    .issue-details {{ padding-left: 1.75rem; margin: 0; }}
    .issue-details li {{ padding: 0.15rem 0; font-size: 0.88rem; color: #555;
                         list-style: disc; }}
    .more-items {{ color: #888; font-style: italic; list-style: none !important; }}

    /* Action categories */
    .action-category {{ margin-bottom: 1rem; }}
    .action-category h3 {{ font-size: 0.95rem; margin-bottom: 0.4rem;
                           padding-bottom: 0.25rem; border-bottom: 1px solid #eee; }}
    .action-item {{ padding: 0.35rem 0.5rem; margin: 0.15rem 0; border-radius: 4px;
                    font-size: 0.88rem; display: flex; align-items: baseline;
                    gap: 0.25rem; flex-wrap: wrap; }}
    .action-pass {{ background: #f0fdf4; }}
    .action-fail {{ background: #fef2f2; }}
    .action-skip {{ background: #f5f5f5; }}
    .action-criterion {{ font-size: 0.75rem; color: #888; margin-left: auto;
                         white-space: nowrap; }}

    .badge {{ display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px;
              font-size: 0.7rem; font-weight: 600; vertical-align: middle; margin-left: 0.5rem; }}
    .badge-pass {{ background: #dcfce7; color: #16a34a; }}
    .badge-fail {{ background: #fee2e2; color: #dc2626; }}

    /* Review items */
    .review-item {{ display: flex; gap: 0.5rem; padding: 0.6rem 0; margin: 0;
                    border-bottom: 1px solid #f0f0f0; font-size: 0.88rem; }}
    .review-item:last-child {{ border-bottom: none; }}
    .review-icon {{ flex-shrink: 0; font-size: 0.9rem; line-height: 1.5; }}
    .review-content {{ flex: 1; }}
    .review-failure .review-content {{ color: #991b1b; }}
    .review-concern .review-content {{ color: #78350f; }}

    .human-review-list {{ margin-top: 1rem; padding-top: 0.75rem;
                          border-top: 1px solid #e5e5e5; }}
    .human-review-list h3 {{ color: #2563eb; font-size: 0.95rem; }}
    .human-review-list ul {{ padding-left: 0; margin-top: 0.4rem; }}
    .human-review-list li {{ padding: 0.3rem 0; font-size: 0.88rem; color: #374151;
                             list-style: none; }}

    /* Cost table */
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: 0.5rem; }}
    th {{ text-align: left; padding: 0.4rem 0.5rem; background: #f8f8f8;
          border-bottom: 2px solid #e0e0e0; font-weight: 600; color: #555; }}
    td {{ padding: 0.4rem 0.5rem; border-bottom: 1px solid #eee; }}

    .footer {{ text-align: center; color: #aaa; font-size: 0.75rem; margin-top: 2rem;
               padding-top: 1rem; border-top: 1px solid #e5e5e5; }}

    /* Strategy summary */
    .strategy-summary {{ font-size: 0.9rem; color: #555; margin-bottom: 1rem;
                         line-height: 1.5; }}
  </style>
</head>
<body>

<!-- ═══ STATUS BANNER ═══ -->
<div class="banner {banner_class}">
  <h1>{banner_text}</h1>
  <div class="banner-sub">{_esc(banner_sub)}</div>
  <div class="report-meta">
    <span><strong>Document:</strong> {_esc(input_name)}</span>
    <span><strong>Date:</strong> {_esc(now)}</span>
    <span><strong>Time:</strong> {_esc(time_display)}</span>
    {f'<span><strong>Type:</strong> {_esc(result.comprehension.document_type.value)}</span>' if result.comprehension.document_type.value != 'other' else ''}
  </div>
</div>

<!-- ═══ SUMMARY CARDS ═══ -->
<div class="summary-grid">
  <div class="summary-card {'status-pass' if result.issues_before == 0 else 'status-partial'}">
    <div class="number">{result.issues_before}</div>
    <div class="label">Issues Found</div>
  </div>
  <div class="summary-card status-pass">
    <div class="number">{len(executed)}</div>
    <div class="label">Fixes Applied</div>
  </div>
  <div class="summary-card {'status-pass' if len(failures) == 0 else 'status-fail'}">
    <div class="number">{len(failures)}</div>
    <div class="label">Remaining</div>
  </div>
  <div class="summary-card">
    <div class="number">{len(all_human_items)}</div>
    <div class="label">Needs Review</div>
  </div>
</div>

<!-- ═══ HUMAN-CENTERED SUMMARY ═══ -->

{'<div class="section"><h2>What We Did</h2>' + ('<p class="strategy-summary">' + _esc(strategy_summary) + '</p>' if strategy_summary else '') + what_we_did_html + '</div>' if what_we_did_html else ''}

{visual_qa_html}

{ocr_comparison_html}

{'<div class="section"><h2>What Needs Your Attention</h2>' + needs_attention_html + '</div>' if needs_attention_html else ''}

{'<div class="section"><h2>Your Output Files</h2>' + output_files_html + '</div>' if output_files_html else ''}

<div class="section" style="font-size: 0.85rem; color: #666;">
  <p>Evaluated against <strong>WCAG 2.1 Level AA</strong> as required by the DOJ Title II ADA rule for public universities (compliance deadline: April 2026).</p>
</div>

<!-- ═══ TECHNICAL DETAILS (collapsible) ═══ -->
<details class="technical-details">
  <summary>WCAG 2.1 AA Compliance Details</summary>
  <div class="details-content">

    {'<h2>Remaining Issues &amp; Review</h2>' + review_html + human_review_html if has_review_content else ''}

    <h2>Actions by Category</h2>
    {actions_html}
    <p style="margin-top: 0.75rem; font-size: 0.82rem; color: #888;">
      {len(executed)} applied{f", {len(failed)} failed" if failed else ""}{f", {len(skipped)} skipped" if skipped else ""}
    </p>

    <h2>Original Issues</h2>
    {pre_issues_html if pre_issues_html else '<p style="color: #16a34a; font-size: 0.9rem;">No accessibility issues found in the original document.</p>'}

    {_build_cost_section(result)}

  </div>
</details>

<div class="footer">
  Generated by a11y-remediate
</div>

</body>
</html>"""

    return report_html
