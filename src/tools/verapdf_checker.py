"""Validate PDF/UA-1 compliance via veraPDF CLI.

veraPDF is a Java-based open-source PDF/A and PDF/UA validator.
This module wraps the CLI interface, parsing JSON output into
structured results.

Requires: veraPDF installed and on PATH (or path provided).
Download: https://verapdf.org/software/
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PdfUaViolation:
    """A single PDF/UA-1 validation failure."""
    rule_id: str              # veraPDF rule ID, e.g. "6.1-1"
    clause: str               # PDF/UA clause, e.g. "6.1"
    test_number: int = 0
    description: str = ""
    severity: str = ""        # from veraPDF
    object_type: str = ""     # e.g. "CosDocument", "PDFont"
    context: str = ""         # location/object path in PDF


@dataclass
class VeraPdfResult:
    """Result of running veraPDF on a PDF file."""
    success: bool
    compliant: bool = False
    violations: list[PdfUaViolation] = field(default_factory=list)
    violation_count: int = 0
    passed_rules: int = 0
    failed_rules: int = 0
    profile: str = "ua1"
    pdf_path: str = ""
    error: str = ""


def _find_verapdf() -> str | None:
    """Find veraPDF executable on PATH."""
    return shutil.which("verapdf")


def check_pdf_ua(
    pdf_path: str,
    verapdf_path: str | None = None,
    timeout_seconds: int = 120,
) -> VeraPdfResult:
    """Run veraPDF PDF/UA-1 validation on a PDF file.

    Args:
        pdf_path: Path to the PDF file to validate.
        verapdf_path: Path to veraPDF executable. If None, searches PATH.
        timeout_seconds: Maximum time to wait for veraPDF.

    Returns:
        VeraPdfResult with compliance status and any violations.
    """
    path = Path(pdf_path)
    if not path.exists():
        return VeraPdfResult(
            success=False,
            pdf_path=pdf_path,
            error=f"PDF file not found: {pdf_path}",
        )

    exe = verapdf_path or _find_verapdf()
    if not exe:
        return VeraPdfResult(
            success=False,
            pdf_path=pdf_path,
            error="veraPDF not found. Install from https://verapdf.org/software/ and add to PATH.",
        )

    try:
        result = subprocess.run(
            [exe, "-f", "ua1", "--format", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        if result.returncode not in (0, 1):
            # 0 = valid, 1 = invalid, other = error
            return VeraPdfResult(
                success=False,
                pdf_path=pdf_path,
                error=f"veraPDF exited with code {result.returncode}: {result.stderr[:500]}",
            )

        return _parse_verapdf_json(result.stdout, pdf_path)

    except subprocess.TimeoutExpired:
        return VeraPdfResult(
            success=False,
            pdf_path=pdf_path,
            error=f"veraPDF timed out after {timeout_seconds}s",
        )
    except Exception as e:
        logger.exception("veraPDF check failed")
        return VeraPdfResult(
            success=False,
            pdf_path=pdf_path,
            error=f"veraPDF check failed: {e}",
        )


def _parse_verapdf_json(json_str: str, pdf_path: str) -> VeraPdfResult:
    """Parse veraPDF JSON output into VeraPdfResult."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return VeraPdfResult(
            success=False,
            pdf_path=pdf_path,
            error=f"Failed to parse veraPDF JSON: {e}",
        )

    violations: list[PdfUaViolation] = []
    passed_rules = 0
    failed_rules = 0
    compliant = True

    # veraPDF JSON structure varies by version.
    # Handle the common report structure.
    report = data
    if "report" in data:
        report = data["report"]

    # Try to find the validation result in the report
    jobs = report.get("jobs", [])
    if not jobs:
        # Older format or single-file result
        val_result = report.get("validationResult", {})
        if val_result:
            compliant, passed_rules, failed_rules, violations = _parse_validation_result(val_result)
    else:
        # Newer multi-job format
        for job in jobs:
            val_result = job.get("validationResult", {})
            if val_result:
                compliant, passed_rules, failed_rules, violations = _parse_validation_result(val_result)
                break

    return VeraPdfResult(
        success=True,
        compliant=compliant,
        violations=violations,
        violation_count=len(violations),
        passed_rules=passed_rules,
        failed_rules=failed_rules,
        pdf_path=pdf_path,
    )


def _parse_validation_result(
    val_result: dict,
) -> tuple[bool, int, int, list[PdfUaViolation]]:
    """Parse a validationResult object from veraPDF JSON."""
    compliant = val_result.get("compliant", val_result.get("isCompliant", False))
    passed_rules = 0
    failed_rules = 0
    violations: list[PdfUaViolation] = []

    # Summary details
    details = val_result.get("details", {})
    if details:
        passed_rules = details.get("passedRules", 0)
        failed_rules = details.get("failedRules", 0)

        for rule in details.get("ruleSummaries", []):
            status = rule.get("status", "")
            if status == "failed":
                spec = rule.get("specification", "")
                clause = rule.get("clause", "")
                test_number = rule.get("testNumber", 0)
                description = rule.get("description", "")

                # Each rule can fail on multiple objects
                checks = rule.get("checks", [])
                for check in checks:
                    if check.get("status") == "failed":
                        violations.append(PdfUaViolation(
                            rule_id=f"{clause}-{test_number}",
                            clause=clause,
                            test_number=test_number,
                            description=description,
                            context=check.get("context", ""),
                        ))

    # Fallback: if no details, try assertions/ruleAssertions (older format)
    if not violations and not details:
        for assertion in val_result.get("assertions", val_result.get("ruleAssertions", [])):
            status = assertion.get("status", "")
            if status == "failed":
                rule_id = assertion.get("ruleId", {})
                spec = rule_id.get("specification", "") if isinstance(rule_id, dict) else ""
                clause = rule_id.get("clause", "") if isinstance(rule_id, dict) else str(rule_id)
                test_number = rule_id.get("testNumber", 0) if isinstance(rule_id, dict) else 0

                violations.append(PdfUaViolation(
                    rule_id=f"{clause}-{test_number}" if clause else "unknown",
                    clause=str(clause),
                    test_number=int(test_number) if test_number else 0,
                    description=assertion.get("message", ""),
                    context=assertion.get("location", {}).get("context", ""),
                ))
                failed_rules += 1

    return compliant, passed_rules, failed_rules, violations


def format_verapdf_report(result: VeraPdfResult) -> str:
    """Format a veraPDF result as human-readable text."""
    if not result.success:
        return f"veraPDF check failed: {result.error}"

    status = "COMPLIANT" if result.compliant else "NON-COMPLIANT"
    lines = [
        f"veraPDF PDF/UA-1 Report",
        f"File: {result.pdf_path}",
        f"Status: {status}",
        f"Passed rules: {result.passed_rules}",
        f"Failed rules: {result.failed_rules}",
        f"Violations: {result.violation_count}",
        "",
    ]

    for v in result.violations:
        lines.append(f"[{v.rule_id}] Clause {v.clause}")
        if v.description:
            lines.append(f"  {v.description}")
        if v.context:
            lines.append(f"  Context: {v.context[:200]}")
        lines.append("")

    return "\n".join(lines)
