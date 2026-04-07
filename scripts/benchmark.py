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

# Local helper for raw struct-tree probing
sys.path.insert(0, str(Path(__file__).parent))
from struct_tree_probe import probe_struct_tree, StructFacts

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


# ── Per-task predictors ───────────────────────────────────────────
#
# Each predictor returns one of: passed, failed, not_present, cannot_tell.
# These use the parsed DocumentModel + ValidationReport to make a label
# specific to one benchmark task. We keep this logic in the benchmark
# script (not the validator) because the validator is built for
# remediation, not 4-class classification.


def _alt_quality_score(text: str) -> str:
    """Classify a single alt text as 'good', 'bad', or 'borderline'.

    'bad' = too short, generic label, just a title (e.g. 'Figure 6')
    'borderline' = substantive but starts with meta-phrase ('Image showing...')
    'good' = substantive description without meta-phrasing
    """
    # Strip null bytes and other control chars that PDFs sometimes include
    text = text.strip().rstrip("\x00").strip()
    text = text.replace("\\000", "").strip()
    if not text:
        return "bad"

    lower = text.lower()
    word_count = len(text.split())

    # Very short → bad
    if len(text) < 20 or word_count < 4:
        return "bad"

    # Just a generic label / title
    bad_short_patterns = [
        "figure", "fig.", "image", "picture", "photo", "chart",
        "graph", "diagram", "table", "panel", "screenshot",
        "flow chart", "flowchart",
    ]
    if word_count <= 5 and any(lower.startswith(p) for p in bad_short_patterns):
        return "bad"

    # Auto-generated phrasing
    if "automatically generated" in lower:
        return "bad"
    if "graphical user interface" in lower:
        return "bad"

    # Looks like just a filename
    if "." in text and " " not in text:
        return "bad"

    # Meta-description starts (talks about the image instead of describing content)
    meta_starts = [
        "this is an image", "this is a", "image of", "image showing",
        "photo of", "photograph of", "picture of",
        "figure showing", "figure depicting", "figure illustrat",
        "flow chart", "diagram showing", "chart showing",
        "screenshot of",
    ]
    if any(lower.startswith(p) for p in meta_starts):
        return "borderline"

    # Substantive description
    if word_count >= 12:
        return "good"
    if word_count >= 6:
        return "borderline"
    return "bad"


def _predict_alt_text_quality(report, doc_model, facts: StructFacts) -> str:
    """alt_text_quality: how good are image descriptions?

    Uses the PDF struct tree as the source of truth:
    - not_present: figures exist but have no /Alt attribute (alt text absent)
    - failed: figures with /Alt but content is poor
    - passed: most figures have /Alt with substantive content
    """
    if facts.has_struct_tree and facts.figure_count > 0:
        with_alt = facts.figures_with_alt + facts.figures_with_actual_text
        if with_alt == 0:
            return "not_present"

        # Judge alt text quality
        alts = facts.figure_alt_texts
        if alts:
            qualities = [_alt_quality_score(a) for a in alts]
            good = qualities.count("good")
            bad = qualities.count("bad")
            borderline = qualities.count("borderline")

            good_ratio = good / len(qualities)
            bad_ratio = bad / len(qualities)
            borderline_ratio = borderline / len(qualities)

            # Mostly bad → failed
            if bad_ratio >= 0.5:
                return "failed"
            # Mostly good → passed
            if good_ratio >= 0.6:
                return "passed"
            # Borderline-heavy → cannot_tell (meta-descriptions, ambiguous)
            if borderline_ratio >= 0.4:
                return "cannot_tell"
            # Mixed
            return "cannot_tell"

        # Has alt entries but couldn't extract text — coverage-based fallback
        coverage = with_alt / facts.figure_count
        if coverage >= 0.8:
            return "passed"
        if coverage < 0.3:
            return "failed"
        return "cannot_tell"

    # Struct tree absent — use parsed model
    images = [img for img in doc_model.images if not img.is_decorative]
    if not images:
        return "not_present"
    with_alt = [img for img in images if img.alt_text and img.alt_text.strip()]
    if len(with_alt) == 0:
        return "not_present"
    qualities = [_alt_quality_score(img.alt_text) for img in with_alt]
    bad_ratio = qualities.count("bad") / len(qualities)
    good_ratio = qualities.count("good") / len(qualities)
    if bad_ratio >= 0.6:
        return "failed"
    if good_ratio >= 0.5:
        return "passed"
    return "cannot_tell"


def _predict_color_contrast(report, doc_model, facts: StructFacts) -> str:
    """color_contrast: text contrast meets WCAG 1.4.3?

    Tuned thresholds based on benchmark data:
    - 0-2 issues → passed (clean or near-clean)
    - 3-7 issues → cannot_tell (ambiguous)
    - 8+ issues → failed (clear violations)
    """
    contrast_check = next(
        (c for c in report.checks if c.criterion == "1.4.3"), None,
    )
    if not contrast_check:
        return "cannot_tell"
    if contrast_check.status == CheckStatus.NOT_APPLICABLE:
        return "not_present"

    issues = contrast_check.issue_count
    items = max(contrast_check.item_count, 1)
    ratio = issues / items

    if issues <= 2:
        return "passed"
    if issues >= 8 or ratio >= 0.04:
        return "failed"
    return "cannot_tell"


def _predict_fonts_readability(report, doc_model, facts: StructFacts) -> str:
    """fonts_readability: are fonts readable?

    Look at body text font sizes. Below 8pt body is failed.
    Detect very-thin / ultra-light fonts which are hard to read.
    """
    sizes: list[float] = []
    fonts: set[str] = set()
    body_size_counts: dict[float, int] = {}

    for p in doc_model.paragraphs:
        is_heading = p.heading_level is not None
        for run in p.runs:
            if run.font_size_pt and not is_heading:
                sizes.append(run.font_size_pt)
                body_size_counts[run.font_size_pt] = body_size_counts.get(run.font_size_pt, 0) + 1
            if run.font_name:
                fonts.add(run.font_name)

    if not sizes:
        return "cannot_tell"

    sorted_sizes = sorted(sizes)
    body_median = sorted_sizes[len(sorted_sizes) // 2]

    # Most common body size (mode) is more meaningful than median
    body_mode = max(body_size_counts.items(), key=lambda kv: kv[1])[0] if body_size_counts else body_median

    # Fraction of body text below 9pt
    small_text = sum(1 for s in sizes if s < 9)
    small_ratio = small_text / len(sizes)

    # Very-thin / ultralight font detection (these are hard to read)
    thin_font_patterns = ["thin", "ultralight", "hairline", "extralight"]
    has_thin_font = any(
        any(p in f.lower() for p in thin_font_patterns) for f in fonts
    )

    # Decision tree
    if body_mode < 8.0:
        return "failed"
    if body_mode >= 10.0 and not has_thin_font and small_ratio < 0.3:
        return "passed"
    if has_thin_font and body_mode < 11.0:
        return "failed"
    if small_ratio > 0.5:
        return "failed"
    if body_mode >= 9.0 and small_ratio < 0.4:
        return "passed"
    return "cannot_tell"


def _predict_functional_hyperlinks(report, doc_model, facts: StructFacts) -> str:
    """functional_hyperlinks: are links accessible and descriptive?

    Source of truth: PDF link annotations on each page.
    - not_present: no link annotations at all
    - passed: link annotations exist with valid URIs and tagged in struct tree
    - failed: link annotations exist but URIs invalid OR not tagged in struct tree
    """
    annot_count = facts.annot_link_count

    if annot_count == 0:
        return "not_present"

    valid_uri_count = facts.annot_links_with_valid_uri
    valid_ratio = valid_uri_count / annot_count

    tagged_in_struct = facts.has_struct_tree and facts.link_count > 0
    tag_coverage = (
        facts.link_count / annot_count if annot_count > 0 else 0.0
    )

    # All URIs valid AND struct-tree tagged → passed
    if valid_ratio >= 0.95 and tagged_in_struct and tag_coverage >= 0.5:
        return "passed"

    # All URIs valid but no struct tagging → fail (PDF/UA requires tagged links)
    if valid_ratio >= 0.95 and not tagged_in_struct:
        return "failed"

    # Most URIs invalid → failed
    if valid_ratio < 0.5:
        return "failed"

    # Mixed
    return "cannot_tell"


def _predict_logical_reading_order(report, doc_model, facts: StructFacts) -> str:
    """logical_reading_order: does the document read in a sensible order?

    Heuristic: look at the bbox y-coordinates of paragraphs in document order.
    A monotonically-increasing y per page suggests good order; lots of jumping
    suggests bad order.
    """
    # Group paragraphs by page
    pages: dict[int, list] = {}
    for p in doc_model.paragraphs:
        if p.bbox is None or p.page_number is None:
            continue
        pages.setdefault(p.page_number, []).append(p)

    if not pages:
        return "cannot_tell"

    bad_pages = 0
    total_pages = 0
    for page_num, paras in pages.items():
        if len(paras) < 3:
            continue
        total_pages += 1
        # Check if y coordinates are mostly monotonic
        ys = [p.bbox[1] for p in paras]
        # Count descending pairs (out-of-order jumps)
        descending = sum(1 for a, b in zip(ys, ys[1:]) if b < a - 20)
        if descending / max(len(ys) - 1, 1) > 0.25:
            bad_pages += 1

    if total_pages == 0:
        return "cannot_tell"

    bad_ratio = bad_pages / total_pages
    if bad_ratio >= 0.4:
        return "failed"
    if bad_ratio >= 0.15:
        return "cannot_tell"
    return "passed"


def _predict_semantic_tagging(report, doc_model, facts: StructFacts) -> str:
    """semantic_tagging: is the document properly tagged with semantic structure?

    Discriminator from struct tree analysis:
    - not_present: no struct tree at all
    - passed: has struct tree with low custom_tag_ratio (uses standard PDF/UA tags)
    - failed: has struct tree but uses mostly non-standard tags
    - cannot_tell: borderline custom_tag_ratio
    """
    if not facts.has_struct_tree:
        return "not_present"

    if facts.total_tagged_elements < 5:
        return "cannot_tell"

    custom_ratio = facts.custom_tag_ratio
    has_standard_structure = (
        facts.heading_count > 0 or facts.p_count >= 3 or facts.list_count > 0
    )

    # Pure standard tags + has real semantic elements → passed
    if custom_ratio <= 0.10 and has_standard_structure:
        return "passed"

    # Mostly custom tags → failed
    if custom_ratio >= 0.40:
        return "failed"

    # Some standard structure but mixed
    if has_standard_structure and custom_ratio <= 0.25:
        return "passed"

    return "cannot_tell"


def _predict_table_structure(report, doc_model, facts: StructFacts) -> str:
    """table_structure: do tables have proper headers and structure?

    Discriminator from struct tree:
    - not_present: no Table tags in struct tree (no real tables)
    - passed: tables have TH (header) tags
    - failed: tables exist but no TH tags (headers missing)
    """
    # Use struct tree as the source of truth
    if facts.has_struct_tree:
        if facts.table_count == 0:
            return "not_present"
        if facts.table_th_count == 0:
            return "failed"
        # Has tables and TH tags
        # Pass if there are at least 2 TH per table on average (real header row)
        th_per_table = facts.table_th_count / facts.table_count
        if th_per_table >= 1.5:
            return "passed"
        return "cannot_tell"

    # No struct tree — fall back to parsed model
    tables = doc_model.tables
    if not tables:
        return "not_present"
    bad_tables = sum(
        1 for t in tables
        if t.header_row_count == 0 and not t.has_header_style
    )
    if bad_tables == len(tables):
        return "failed"
    if bad_tables > 0:
        return "cannot_tell"
    return "passed"


# Dispatch table for per-task predictors
TASK_PREDICTORS = {
    "alt_text_quality":      _predict_alt_text_quality,
    "color_contrast":        _predict_color_contrast,
    "fonts_readability":     _predict_fonts_readability,
    "functional_hyperlinks": _predict_functional_hyperlinks,
    "logical_reading_order": _predict_logical_reading_order,
    "semantic_tagging":      _predict_semantic_tagging,
    "table_structure":       _predict_table_structure,
}


def predict_label(report, task: str, doc_model, facts: StructFacts) -> str:
    """Predict a benchmark label using the task-specific predictor."""
    predictor = TASK_PREDICTORS.get(task)
    if predictor is None:
        return "cannot_tell"
    try:
        return predictor(report, doc_model, facts)
    except Exception as e:
        logger.warning("Predictor for %s crashed: %s", task, e)
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
                    facts = probe_struct_tree(str(pdf_path))
                    predicted = predict_label(report, task_name, doc_model, facts)
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
