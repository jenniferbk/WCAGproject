#!/usr/bin/env python3
"""Batch end-to-end test runner for the remediation pipeline.

Runs each document in testdocs/ through orchestrator.process() and
generates a summary report with timing, costs, and issue counts.

Usage:
    python scripts/test_batch.py                        # all documents
    python scripts/test_batch.py --doc "Assignment 1.docx"  # single doc
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ── Environment setup (before imports that need it) ──────────────
# Set JAVA_HOME for iText PDF tagging
java_home = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
if Path(java_home).exists():
    os.environ.setdefault("JAVA_HOME", java_home)

# Load .env file for API keys
project_root = Path(__file__).resolve().parent.parent
env_path = project_root / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# Add project root to path so src imports work
sys.path.insert(0, str(project_root))

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch")

# ── Constants ────────────────────────────────────────────────────
TESTDOCS_DIR = project_root / "testdocs"
OUTPUT_DIR = TESTDOCS_DIR / "output"
COURSE_CONTEXT = CourseContext(
    course_name="EMAT 8030",
    department="Mathematics Education",
    description="Graduate seminar in mathematics education research and theory.",
)
SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".pptx"}


def run_single(doc_path: Path, output_dir: Path) -> dict:
    """Run the pipeline on a single document, return a result dict."""
    name = doc_path.name
    logger.info("=" * 60)
    logger.info("STARTING: %s (%.1f KB)", name, doc_path.stat().st_size / 1024)
    logger.info("=" * 60)

    request = RemediationRequest(
        document_path=str(doc_path),
        course_context=COURSE_CONTEXT,
        output_dir=str(output_dir),
    )

    start = time.time()
    try:
        result = process(request)
        elapsed = time.time() - start

        row = {
            "file": name,
            "format": doc_path.suffix.lower(),
            "size_kb": round(doc_path.stat().st_size / 1024, 1),
            "success": result.success,
            "error": result.error or "",
            "issues_before": result.issues_before,
            "issues_after": result.issues_after,
            "issues_fixed": result.issues_fixed,
            "human_review": len(result.items_for_human_review),
            "human_review_items": result.items_for_human_review,
            "actions_planned": len(result.strategy.actions) if result.strategy else 0,
            "time_seconds": round(elapsed, 1),
            "cost_usd": result.cost_summary.estimated_cost_usd if result.cost_summary else 0.0,
            "output_path": result.output_path or "",
            "report_path": result.report_path or "",
            "companion_path": result.companion_output_path or "",
        }

        status = "OK" if result.success else f"FAILED: {result.error}"
        logger.info(
            "DONE: %s | %s | %d→%d issues (-%d) | %d human review | %.1fs | $%.4f",
            name, status,
            row["issues_before"], row["issues_after"], row["issues_fixed"],
            row["human_review"], elapsed, row["cost_usd"],
        )
        return row

    except Exception as e:
        elapsed = time.time() - start
        logger.error("CRASHED: %s after %.1fs: %s", name, elapsed, e)
        traceback.print_exc()
        return {
            "file": name,
            "format": doc_path.suffix.lower(),
            "size_kb": round(doc_path.stat().st_size / 1024, 1),
            "success": False,
            "error": f"CRASH: {type(e).__name__}: {e}",
            "issues_before": 0,
            "issues_after": 0,
            "issues_fixed": 0,
            "human_review": 0,
            "human_review_items": [],
            "actions_planned": 0,
            "time_seconds": round(elapsed, 1),
            "cost_usd": 0.0,
            "output_path": "",
            "report_path": "",
            "companion_path": "",
        }


def generate_report(results: list[dict], output_dir: Path) -> str:
    """Generate a markdown summary table from batch results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_time = sum(r["time_seconds"] for r in results)
    total_cost = sum(r["cost_usd"] for r in results)
    passed = sum(1 for r in results if r["success"])

    lines = [
        f"# Batch Test Results — {now}",
        "",
        f"**Documents:** {len(results)} ({passed} passed, {len(results) - passed} failed)  ",
        f"**Total time:** {total_time:.0f}s ({total_time/60:.1f} min)  ",
        f"**Total cost:** ${total_cost:.4f}  ",
        f"**Output dir:** `{output_dir}`",
        "",
        "## Results",
        "",
        "| # | File | Fmt | Size | Status | Before | After | Fixed | Human Review | Time | Cost |",
        "|---|------|-----|------|--------|--------|-------|-------|--------------|------|------|",
    ]

    for i, r in enumerate(results, 1):
        status = "Pass" if r["success"] else "FAIL"
        lines.append(
            f"| {i} | {r['file']} | {r['format']} | {r['size_kb']}KB "
            f"| {status} | {r['issues_before']} | {r['issues_after']} "
            f"| {r['issues_fixed']} | {r['human_review']} "
            f"| {r['time_seconds']}s | ${r['cost_usd']:.4f} |"
        )

    lines.append("")
    lines.append(f"| | **Totals** | | | **{passed}/{len(results)}** | "
                 f"**{sum(r['issues_before'] for r in results)}** | "
                 f"**{sum(r['issues_after'] for r in results)}** | "
                 f"**{sum(r['issues_fixed'] for r in results)}** | "
                 f"**{sum(r['human_review'] for r in results)}** | "
                 f"**{total_time:.0f}s** | **${total_cost:.4f}** |")

    # Failures detail
    failures = [r for r in results if not r["success"]]
    if failures:
        lines.extend(["", "## Failures", ""])
        for r in failures:
            lines.append(f"- **{r['file']}**: {r['error']}")

    # Human review items detail
    has_human_review = [r for r in results if r["human_review_items"]]
    if has_human_review:
        lines.extend(["", "## Human Review Items", ""])
        for r in has_human_review:
            lines.append(f"### {r['file']}")
            for item in r["human_review_items"]:
                lines.append(f"- {item}")
            lines.append("")

    # Output files
    lines.extend(["", "## Output Files", ""])
    for r in results:
        if r["success"]:
            lines.append(f"### {r['file']}")
            if r["output_path"]:
                lines.append(f"- Remediated: `{Path(r['output_path']).name}`")
            if r["report_path"]:
                lines.append(f"- Report: `{Path(r['report_path']).name}`")
            if r["companion_path"]:
                lines.append(f"- Companion HTML: `{Path(r['companion_path']).name}`")
            lines.append("")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Batch test the remediation pipeline")
    parser.add_argument("--doc", type=str, help="Run a single document by filename")
    args = parser.parse_args()

    # Verify API keys
    missing_keys = []
    if not os.environ.get("GEMINI_API_KEY"):
        missing_keys.append("GEMINI_API_KEY")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing_keys.append("ANTHROPIC_API_KEY")
    if missing_keys:
        logger.error("Missing required environment variables: %s", ", ".join(missing_keys))
        logger.error("Set them in .env or export them before running.")
        sys.exit(1)

    # Gather documents
    if args.doc:
        doc_path = TESTDOCS_DIR / args.doc
        if not doc_path.exists():
            logger.error("Document not found: %s", doc_path)
            sys.exit(1)
        docs = [doc_path]
    else:
        docs = sorted(
            p for p in TESTDOCS_DIR.iterdir()
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith(".")
        )

    if not docs:
        logger.error("No documents found in %s", TESTDOCS_DIR)
        sys.exit(1)

    logger.info("Batch test: %d document(s)", len(docs))
    for d in docs:
        logger.info("  - %s (%.1f KB)", d.name, d.stat().st_size / 1024)

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Run each document
    results = []
    for i, doc_path in enumerate(docs, 1):
        logger.info("")
        logger.info(">>> Document %d/%d <<<", i, len(docs))
        row = run_single(doc_path, OUTPUT_DIR)
        results.append(row)

    # Generate and write summary report
    report_md = generate_report(results, OUTPUT_DIR)
    report_path = TESTDOCS_DIR / "batch_results.md"
    report_path.write_text(report_md)

    # Print summary to console
    logger.info("")
    logger.info("=" * 60)
    logger.info("BATCH COMPLETE")
    logger.info("=" * 60)
    passed = sum(1 for r in results if r["success"])
    logger.info("Results: %d/%d passed", passed, len(results))
    logger.info("Total time: %.0fs (%.1f min)",
                sum(r["time_seconds"] for r in results),
                sum(r["time_seconds"] for r in results) / 60)
    logger.info("Total cost: $%.4f", sum(r["cost_usd"] for r in results))
    logger.info("Report: %s", report_path)
    logger.info("Output: %s", OUTPUT_DIR)

    # Exit with error code if any failed
    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
