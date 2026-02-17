"""Run axe-core WCAG checks on generated HTML via Playwright.

Wraps axe-playwright-python to validate the HTML output from html_builder.py
against WCAG 2.1 AA. This catches issues that our custom docx-level checks
might miss — axe-core is the industry standard used by most auditors.

Requires: playwright, axe-playwright-python
Install browsers: python -m playwright install chromium
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AxeViolation:
    """A single axe-core violation."""
    rule_id: str               # e.g. "color-contrast", "image-alt"
    impact: str                # "critical", "serious", "moderate", "minor"
    description: str           # what the rule checks
    help_text: str             # how to fix
    help_url: str              # link to deque docs
    wcag_criteria: list[str] = field(default_factory=list)  # e.g. ["1.1.1", "1.4.3"]
    affected_elements: list[str] = field(default_factory=list)  # HTML snippets
    node_count: int = 0


@dataclass
class AxeCheckResult:
    """Result of running axe-core on HTML."""
    success: bool
    violations: list[AxeViolation] = field(default_factory=list)
    passes_count: int = 0
    incomplete_count: int = 0
    inapplicable_count: int = 0
    violation_count: int = 0
    error: str = ""


def _extract_wcag_criteria(tags: list[str]) -> list[str]:
    """Extract WCAG criterion IDs from axe-core tags.

    axe tags like 'wcag111' map to criterion '1.1.1',
    'wcag143' maps to '1.4.3', etc.
    """
    criteria = []
    for tag in tags:
        if tag.startswith("wcag") and tag[4:].isdigit():
            digits = tag[4:]
            if len(digits) >= 3:
                parts = list(digits)
                criteria.append(".".join(parts))
    return criteria


def check_html_accessibility(
    html_string: str,
    standard: str = "wcag2aa",
) -> AxeCheckResult:
    """Run axe-core WCAG checks on an HTML string.

    Args:
        html_string: Complete HTML document string.
        standard: axe-core standard tag to filter by.
            Options: "wcag2a", "wcag2aa", "wcag2aaa", "best-practice"

    Returns:
        AxeCheckResult with violations and pass/fail counts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return AxeCheckResult(
            success=False,
            error="playwright not installed. Run: pip install playwright && python -m playwright install chromium",
        )

    try:
        from axe_playwright_python.sync_playwright import Axe
    except ImportError:
        return AxeCheckResult(
            success=False,
            error="axe-playwright-python not installed. Run: pip install axe-playwright-python",
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html_string)

                axe = Axe()
                results = axe.run(
                    page,
                    options={
                        "runOnly": {
                            "type": "tag",
                            "values": [standard],
                        },
                    },
                )

                violations = []
                for v in results.response.get("violations", []):
                    tags = v.get("tags", [])
                    nodes = v.get("nodes", [])
                    affected = [
                        node.get("html", "")[:200]
                        for node in nodes
                    ]
                    violations.append(AxeViolation(
                        rule_id=v.get("id", ""),
                        impact=v.get("impact", ""),
                        description=v.get("description", ""),
                        help_text=v.get("help", ""),
                        help_url=v.get("helpUrl", ""),
                        wcag_criteria=_extract_wcag_criteria(tags),
                        affected_elements=affected,
                        node_count=len(nodes),
                    ))

                return AxeCheckResult(
                    success=True,
                    violations=violations,
                    passes_count=len(results.response.get("passes", [])),
                    incomplete_count=len(results.response.get("incomplete", [])),
                    inapplicable_count=len(results.response.get("inapplicable", [])),
                    violation_count=len(violations),
                )
            finally:
                browser.close()

    except Exception as e:
        logger.exception("axe-core check failed")
        return AxeCheckResult(
            success=False,
            error=f"axe-core check failed: {e}",
        )


def format_axe_report(result: AxeCheckResult) -> str:
    """Format an axe check result as human-readable text."""
    if not result.success:
        return f"axe-core check failed: {result.error}"

    lines = [
        "axe-core WCAG 2.1 AA Report",
        f"Violations: {result.violation_count}",
        f"Passes: {result.passes_count}",
        f"Incomplete: {result.incomplete_count}",
        "",
    ]

    for v in result.violations:
        criteria_str = ", ".join(v.wcag_criteria) if v.wcag_criteria else "N/A"
        lines.append(f"[{v.impact.upper()}] {v.rule_id} (WCAG {criteria_str})")
        lines.append(f"  {v.description}")
        lines.append(f"  Fix: {v.help_text}")
        lines.append(f"  Affected: {v.node_count} element(s)")
        for elem in v.affected_elements[:3]:
            lines.append(f"    - {elem[:100]}")
        lines.append("")

    return "\n".join(lines)
