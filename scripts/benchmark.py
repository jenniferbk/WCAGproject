#!/usr/bin/env python3
"""Run our parser+validator against the PDF Accessibility Benchmark.

Benchmark: Kumar et al., ASSETS 2025
https://github.com/Anukriti12/PDF-Accessibility-Benchmark

The benchmark has 7 accessibility criteria, each with 5 PDFs in 3-4 labels:
- passed, failed, not_present, cannot_tell

For each (criterion, document) we predict a label using our validator output,
then compare to ground truth and report per-criterion accuracy.

Usage:
    python scripts/benchmark.py --benchmark-dir /tmp/PDF-Accessibility-Benchmark
    python scripts/benchmark.py --benchmark-dir /tmp/PDF-Accessibility-Benchmark --task alt_text_quality
    python scripts/benchmark.py --benchmark-dir /tmp/PDF-Accessibility-Benchmark --output benchmark_results.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add project root to path so src imports work
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.tools.pdf_parser import parse_pdf
from src.tools.validator import CheckStatus, validate_document

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")


# ── Mapping: benchmark task → our WCAG criterion(s) ─────────────────
#
# The benchmark has 7 tasks. Our validator produces results for 7 WCAG criteria.
# This mapping translates between the two.
TASK_TO_WCAG = {
    "alt_text_quality":      ["1.1.1"],
    "color_contrast":        ["1.4.3"],
    "fonts_readability":     [],  # We don't currently check this — will return cannot_tell
    "functional_hyperlinks": ["2.4.4"],
    "logical_reading_order": ["1.3.1"],  # Reading order is part of structure
    "semantic_tagging":      ["1.3.1", "2.4.6"],  # Headings + structure
    "table_structure":       ["1.3.1"],
}


# Published baselines from Kumar et al. ASSETS 2025
PUBLISHED_BASELINES = {
    "GPT-4-Turbo":   0.85,
    "GPT-4o-Vision": 0.81,
    "Gemini-1.5":    0.75,
    "Claude-3.5":    0.74,
    "Llama-3.2":     0.42,
}


def predict_label(report, task: str, doc_model) -> str:
    """Predict a benchmark label (passed/failed/not_present/cannot_tell) from
    our validator report for a given benchmark task.
    """
    wcag_criteria = TASK_TO_WCAG.get(task, [])

    # No mapping → we can't decide
    if not wcag_criteria:
        return "cannot_tell"

    # Pull out matching checks
    matching = [c for c in report.checks if c.criterion in wcag_criteria]
    if not matching:
        return "cannot_tell"

    # Task-specific "not_present" detection — no relevant elements at all
    if task == "alt_text_quality" and len(doc_model.images) == 0:
        return "not_present"
    if task == "table_structure" and len(doc_model.tables) == 0:
        return "not_present"
    if task == "functional_hyperlinks":
        link_count = sum(len(p.links) for p in doc_model.paragraphs)
        if link_count == 0:
            return "not_present"
    if task == "semantic_tagging":
        # If a doc has no headings AND no structured content, mark not_present
        heading_count = sum(1 for p in doc_model.paragraphs if p.heading_level)
        if heading_count == 0 and not doc_model.tables:
            return "not_present"

    # Aggregate status across matching checks
    statuses = [c.status for c in matching]
    if any(s == CheckStatus.FAIL for s in statuses):
        return "failed"
    if any(s == CheckStatus.WARN for s in statuses):
        return "cannot_tell"
    if all(s == CheckStatus.NOT_APPLICABLE for s in statuses):
        return "not_present"
    if any(s == CheckStatus.PASS for s in statuses):
        return "passed"
    return "cannot_tell"


def run_benchmark(benchmark_dir: Path, task_filter: str | None = None) -> dict:
    """Run the benchmark and return results."""
    dataset_path = benchmark_dir / "data" / "dataset.json"
    if not dataset_path.exists():
        print(f"ERROR: dataset.json not found at {dataset_path}", file=sys.stderr)
        sys.exit(1)

    with open(dataset_path) as f:
        dataset = json.load(f)

    print(f"Loaded benchmark: {dataset['name']} v{dataset['version']}")

    results: dict = {
        "per_task": {},
        "per_doc": [],
        "total_correct": 0,
        "total_count": 0,
        "elapsed_seconds": 0,
        "errors": [],
    }

    start_time = time.time()

    for task_name, task_data in dataset["tasks"].items():
        if task_filter and task_name != task_filter:
            continue

        task_results = {
            "labels_seen": defaultdict(lambda: defaultdict(int)),  # gold → predicted → count
            "correct": 0,
            "total": 0,
        }

        for gold_label, items in task_data.items():
            for item in items:
                pdf_rel_path = item.get("pdf_path", "")
                pdf_path = benchmark_dir / pdf_rel_path
                if not pdf_path.exists():
                    results["errors"].append(f"Missing: {pdf_rel_path}")
                    continue

                try:
                    parse_result = parse_pdf(str(pdf_path))
                    if not parse_result.success:
                        results["errors"].append(
                            f"Parse failed: {pdf_rel_path}: {parse_result.error}"
                        )
                        continue
                    doc_model = parse_result.document
                    report = validate_document(doc_model)
                    predicted = predict_label(report, task_name, doc_model)
                except Exception as e:
                    results["errors"].append(f"Error on {pdf_rel_path}: {e}")
                    continue

                is_correct = predicted == gold_label
                task_results["labels_seen"][gold_label][predicted] += 1
                task_results["total"] += 1
                if is_correct:
                    task_results["correct"] += 1

                results["per_doc"].append({
                    "task": task_name,
                    "doc": item.get("openalex_id", "?"),
                    "title": item.get("title", "")[:80],
                    "gold": gold_label,
                    "predicted": predicted,
                    "correct": is_correct,
                })

                results["total_count"] += 1
                if is_correct:
                    results["total_correct"] += 1

                marker = "✓" if is_correct else "✗"
                print(f"  {marker} {task_name}/{gold_label}: predicted={predicted} ({item.get('openalex_id', '?')})")

        if task_results["total"] > 0:
            task_results["accuracy"] = task_results["correct"] / task_results["total"]
        else:
            task_results["accuracy"] = 0.0
        # Convert defaultdicts for JSON serialization
        task_results["labels_seen"] = {
            k: dict(v) for k, v in task_results["labels_seen"].items()
        }
        results["per_task"][task_name] = task_results
        print(
            f"\n  → {task_name}: {task_results['correct']}/{task_results['total']} = "
            f"{task_results['accuracy']:.2%}\n"
        )

    results["elapsed_seconds"] = time.time() - start_time
    if results["total_count"] > 0:
        results["overall_accuracy"] = results["total_correct"] / results["total_count"]
    else:
        results["overall_accuracy"] = 0.0

    return results


def write_report(results: dict, output_path: Path) -> None:
    """Write a markdown report summarizing benchmark results."""
    lines = []
    lines.append("# PDF Accessibility Benchmark Results")
    lines.append("")
    lines.append("Benchmark: [Kumar et al., ASSETS 2025](https://github.com/Anukriti12/PDF-Accessibility-Benchmark)")
    lines.append("")
    lines.append(f"**Overall accuracy: {results['overall_accuracy']:.2%}** "
                 f"({results['total_correct']}/{results['total_count']} correct)")
    lines.append(f"**Elapsed: {results['elapsed_seconds']:.1f}s**")
    lines.append("")

    # Comparison to published baselines
    lines.append("## Comparison to Published Baselines")
    lines.append("")
    lines.append("| System | Overall Accuracy |")
    lines.append("|--------|-----------------|")
    lines.append(f"| **A11y Remediate (this tool)** | **{results['overall_accuracy']:.2%}** |")
    for system, acc in PUBLISHED_BASELINES.items():
        lines.append(f"| {system} | {acc:.2%} |")
    lines.append("")

    # Per-task breakdown
    lines.append("## Per-Task Accuracy")
    lines.append("")
    lines.append("| Task | Correct | Total | Accuracy |")
    lines.append("|------|---------|-------|----------|")
    for task, tr in results["per_task"].items():
        lines.append(f"| {task} | {tr['correct']} | {tr['total']} | {tr['accuracy']:.2%} |")
    lines.append("")

    # Confusion matrices per task
    lines.append("## Confusion Matrices")
    lines.append("")
    lines.append("Rows = ground truth, columns = predicted")
    lines.append("")
    for task, tr in results["per_task"].items():
        lines.append(f"### {task}")
        lines.append("")
        all_labels = sorted({label for gold in tr["labels_seen"].values() for label in gold} |
                            set(tr["labels_seen"].keys()))
        if not all_labels:
            lines.append("(no data)")
            continue
        lines.append("| gold ↓ / predicted → | " + " | ".join(all_labels) + " |")
        lines.append("|" + "---|" * (len(all_labels) + 1))
        for gold in all_labels:
            row = [gold]
            for pred in all_labels:
                count = tr["labels_seen"].get(gold, {}).get(pred, 0)
                row.append(str(count))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # Errors
    if results["errors"]:
        lines.append("## Errors")
        lines.append("")
        for err in results["errors"][:20]:
            lines.append(f"- {err}")
        if len(results["errors"]) > 20:
            lines.append(f"- ... and {len(results['errors']) - 20} more")
        lines.append("")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-dir", required=True, type=Path,
        help="Path to the cloned PDF-Accessibility-Benchmark repo",
    )
    parser.add_argument(
        "--task", default=None,
        help="Run only one task (e.g. alt_text_quality)",
    )
    parser.add_argument(
        "--output", default="benchmark_results.md", type=Path,
        help="Output markdown report path",
    )
    parser.add_argument(
        "--json", default=None, type=Path,
        help="Optional JSON output path with full per-doc results",
    )
    args = parser.parse_args()

    results = run_benchmark(args.benchmark_dir, task_filter=args.task)

    print(f"\n{'='*60}")
    print(f"OVERALL: {results['overall_accuracy']:.2%} "
          f"({results['total_correct']}/{results['total_count']})")
    print(f"Elapsed: {results['elapsed_seconds']:.1f}s")
    print(f"Errors: {len(results['errors'])}")
    print(f"{'='*60}")

    write_report(results, args.output)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, default=str))
        print(f"JSON results: {args.json}")


if __name__ == "__main__":
    main()
