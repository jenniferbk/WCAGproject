"""Generate compliance reports from RemediationResult.

Produces an HTML report showing:
1. Original accessibility issues found
2. What actions were taken to fix them
3. Remaining issues and items for human review
4. Before/after summary
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from src.models.pipeline import RemediationResult

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def generate_report_html(result: RemediationResult) -> str:
    """Generate an HTML compliance report from a RemediationResult.

    Args:
        result: The completed pipeline result.

    Returns:
        Complete HTML document string.
    """
    now = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    input_name = result.input_path.split("/")[-1] if result.input_path else "Unknown"
    output_name = result.output_path.split("/")[-1] if result.output_path else "N/A"

    # Categorize actions
    executed = [a for a in result.strategy.actions if a.status == "executed"]
    failed = [a for a in result.strategy.actions if a.status == "failed"]
    skipped = [a for a in result.strategy.actions if a.status == "skipped"]

    # Categorize review findings
    passes = [f for f in result.review_findings if f.finding_type == "pass"]
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

    # Action type to human-readable
    action_names = {
        "set_title": "Set document title",
        "set_language": "Set document language",
        "set_heading_level": "Convert to semantic heading",
        "mark_header_rows": "Mark table header rows",
        "set_alt_text": "Add image alt text",
        "set_decorative": "Mark image as decorative",
        "fix_contrast": "Fix color contrast",
        "convert_to_list": "Convert to semantic list",
    }

    # Action type to WCAG criterion
    action_wcag = {
        "set_title": "2.4.2",
        "set_language": "3.1.1",
        "set_heading_level": "1.3.1",
        "mark_header_rows": "1.3.1",
        "set_alt_text": "1.1.1",
        "set_decorative": "1.1.1",
        "fix_contrast": "1.4.3",
        "convert_to_list": "1.3.1",
    }

    sections = []

    # ── Summary ──
    if result.issues_before == 0:
        status_class = "status-pass"
        status_text = "No Issues Found"
    elif result.issues_after == 0:
        status_class = "status-pass"
        status_text = "All Issues Resolved"
    elif result.issues_fixed > 0:
        status_class = "status-partial"
        status_text = "Partially Remediated"
    else:
        status_class = "status-fail"
        status_text = "Remediation Incomplete"

    # ── Pre-remediation issues section ──
    pre_issues_html = ""
    if result.comprehension and result.comprehension.raw_validation_report:
        # Parse the validation report to extract issues
        pre_lines = []
        for line in result.comprehension.raw_validation_report.split("\n"):
            line = line.strip()
            if line.startswith("[FAIL]") or line.startswith("[WARN]"):
                pre_lines.append(line)
            elif line.startswith("- ") and pre_lines:
                pre_lines.append(line)

        if pre_lines:
            pre_issues_html = "<ul>\n"
            for line in pre_lines:
                if line.startswith("[FAIL]") or line.startswith("[WARN]"):
                    icon = "&#10060;" if line.startswith("[FAIL]") else "&#9888;&#65039;"
                    pre_issues_html += f'  <li class="issue-item"><strong>{icon} {_esc(line[6:].strip())}</strong></li>\n'
                elif line.startswith("- "):
                    pre_issues_html += f'  <li class="issue-detail">{_esc(line[2:].strip())}</li>\n'
            pre_issues_html += "</ul>\n"

    # ── Actions table ──
    actions_rows = ""
    for a in result.strategy.actions:
        criterion = action_wcag.get(a.action_type, "")
        criterion_name = wcag_names.get(criterion, "")
        action_label = action_names.get(a.action_type, a.action_type)

        if a.status == "executed":
            status_icon = "&#9989;"
            row_class = "action-pass"
        elif a.status == "failed":
            status_icon = "&#10060;"
            row_class = "action-fail"
        else:
            status_icon = "&#9898;"
            row_class = "action-skip"

        detail = a.result_detail or a.rationale
        actions_rows += f"""      <tr class="{row_class}">
        <td>{status_icon}</td>
        <td>{_esc(action_label)}</td>
        <td>{_esc(a.element_id)}</td>
        <td>{_esc(criterion)} {_esc(criterion_name)}</td>
        <td>{_esc(detail)}</td>
      </tr>
"""

    # ── Review findings ──
    review_html = ""
    if concerns or failures or human_review:
        review_html = "<ul>\n"
        for f in failures:
            criterion_name = wcag_names.get(f.criterion, "")
            review_html += f'  <li class="finding-failure">&#10060; <strong>{_esc(f.element_id)}</strong> ({_esc(f.criterion)} {_esc(criterion_name)}): {_esc(f.detail)}</li>\n'
        for f in concerns:
            criterion_name = wcag_names.get(f.criterion, "")
            review_html += f'  <li class="finding-concern">&#9888;&#65039; <strong>{_esc(f.element_id)}</strong> ({_esc(f.criterion)} {_esc(criterion_name)}): {_esc(f.detail)}</li>\n'
        for f in human_review:
            review_html += f'  <li class="finding-human">&#128269; {_esc(f.detail)}</li>\n'
        review_html += "</ul>\n"

    # ── Human review items ──
    human_items_html = ""
    if result.items_for_human_review:
        human_items_html = "<ul>\n"
        for item in result.items_for_human_review:
            human_items_html += f'  <li>{_esc(item)}</li>\n'
        human_items_html += "</ul>\n"

    # ── Build full HTML ──
    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Accessibility Compliance Report — {_esc(input_name)}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.6; color: #1a1a1a; background: #f5f5f5;
      padding: 2rem; max-width: 900px; margin: 0 auto;
    }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; color: #1a1a1a; }}
    h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.75rem; padding-bottom: 0.3rem;
          border-bottom: 2px solid #e0e0e0; color: #333; }}
    h3 {{ font-size: 1rem; margin: 1rem 0 0.5rem; color: #555; }}

    .report-header {{ background: white; padding: 1.5rem; border-radius: 8px;
                      margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .report-meta {{ color: #666; font-size: 0.9rem; margin-top: 0.5rem; }}
    .report-meta span {{ margin-right: 1.5rem; }}

    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                     gap: 1rem; margin: 1rem 0; }}
    .summary-card {{ background: white; padding: 1rem; border-radius: 8px; text-align: center;
                     box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .summary-card .number {{ font-size: 2rem; font-weight: bold; }}
    .summary-card .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; }}

    .status-pass .number {{ color: #16a34a; }}
    .status-partial .number {{ color: #d97706; }}
    .status-fail .number {{ color: #dc2626; }}

    .section {{ background: white; padding: 1.5rem; border-radius: 8px;
                margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}

    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; margin-top: 0.5rem; }}
    th {{ text-align: left; padding: 0.5rem; background: #f8f8f8; border-bottom: 2px solid #e0e0e0;
          font-weight: 600; color: #555; }}
    td {{ padding: 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }}
    .action-pass {{ background: #f0fdf4; }}
    .action-fail {{ background: #fef2f2; }}
    .action-skip {{ background: #f5f5f5; }}

    ul {{ list-style: none; padding: 0; }}
    li {{ padding: 0.4rem 0; }}
    .issue-item {{ font-weight: 500; }}
    .issue-detail {{ padding-left: 1.5rem; color: #555; font-size: 0.9rem; }}
    .finding-failure {{ color: #dc2626; }}
    .finding-concern {{ color: #d97706; }}
    .finding-human {{ color: #2563eb; }}

    .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px;
              font-size: 0.75rem; font-weight: 600; }}
    .badge-pass {{ background: #dcfce7; color: #16a34a; }}
    .badge-partial {{ background: #fef3c7; color: #d97706; }}
    .badge-fail {{ background: #fee2e2; color: #dc2626; }}

    .footer {{ text-align: center; color: #999; font-size: 0.8rem; margin-top: 2rem; }}
  </style>
</head>
<body>

<div class="report-header">
  <h1>Accessibility Compliance Report</h1>
  <div class="report-meta">
    <span><strong>Document:</strong> {_esc(input_name)}</span>
    <span><strong>Date:</strong> {_esc(now)}</span>
  </div>
  <div class="report-meta">
    <span><strong>Output:</strong> {_esc(output_name)}</span>
    <span><strong>Processing time:</strong> {result.processing_time_seconds:.1f}s</span>
  </div>
  {f'<div class="report-meta"><span><strong>Document type:</strong> {_esc(result.comprehension.document_type.value)}</span></div>' if result.comprehension.document_type.value != 'other' else ''}
</div>

<div class="summary-grid">
  <div class="summary-card {status_class}">
    <div class="number">{result.issues_before}</div>
    <div class="label">Issues Found</div>
  </div>
  <div class="summary-card status-pass">
    <div class="number">{result.issues_fixed}</div>
    <div class="label">Issues Fixed</div>
  </div>
  <div class="summary-card {'status-pass' if result.issues_after == 0 else 'status-partial'}">
    <div class="number">{result.issues_after}</div>
    <div class="label">Remaining</div>
  </div>
  <div class="summary-card">
    <div class="number">{len(result.items_for_human_review)}</div>
    <div class="label">Human Review</div>
  </div>
</div>

<div class="section">
  <h2>Original Issues</h2>
  <p>The following accessibility issues were identified in the original document:</p>
  {pre_issues_html if pre_issues_html else '<p style="color: #16a34a;">No issues found in original document.</p>'}
</div>

<div class="section">
  <h2>Remediation Actions</h2>
  <p>{_esc(result.strategy.strategy_summary[:300])}</p>
  <table>
    <thead>
      <tr>
        <th style="width:2rem;"></th>
        <th>Action</th>
        <th>Element</th>
        <th>WCAG Criterion</th>
        <th>Detail</th>
      </tr>
    </thead>
    <tbody>
{actions_rows}    </tbody>
  </table>
  <p style="margin-top: 0.75rem; font-size: 0.85rem; color: #666;">
    {len(executed)} executed, {len(failed)} failed, {len(skipped)} skipped
  </p>
</div>

{'<div class="section"><h2>Post-Remediation Review</h2>' + review_html + '</div>' if review_html else ''}

{'<div class="section"><h2>Items for Human Review</h2><p>The following items could not be fully resolved by automated remediation and require human judgment:</p>' + human_items_html + '</div>' if human_items_html else ''}

<div class="section">
  <h2>Compliance Standard</h2>
  <p>This document was evaluated against <strong>WCAG 2.1 Level AA</strong> criteria relevant to digital documents,
  as required by the DOJ Title II ADA rule (April 2024) for public universities.</p>
</div>

<div class="footer">
  <p>Generated by a11y-remediate &mdash; AI-powered WCAG accessibility remediation</p>
</div>

</body>
</html>"""

    return report_html
